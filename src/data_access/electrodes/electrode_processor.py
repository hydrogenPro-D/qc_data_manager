"""Processor: raw dbo.MetaData rows → the GUI-ready electrode frame.

Pure ``DataFrame -> DataFrame`` (no Qt, no DB), so it unit-tests trivially.
Selects and orders the columns the screen needs and appends the derived Status.
"""

from __future__ import annotations

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    PRIORITY_COLUMN,
    PRIORITY_FLAG,
    LOCKED_BY_COLUMN,
    VAT_COLUMN,
)
from src.data_access.electrodes.status_propagation import propagate_detailed


class ElectrodeProcessor:
    """Reshapes raw electrode rows into the frame the Electrodes tab renders."""

    def __init__(self, status_by_marking: dict[str, str] | None = None,
                 overrides_provider=None, base_overrides_provider=None,
                 settled_priorities_provider=None,
                 board_provider=None,
                 vat_provider=None,
                 vat_pending_provider=None,
                 ongoing_provider=None) -> None:
        # Handed in, read once per backend rather than per refresh. The
        # backend owns where they come from, which keeps this class pure data
        # in, data out and lets tests pass a dict.
        self._status_by_marking = dict(status_by_marking or {})
        # Called on every process() so a QC-board decision made seconds ago
        # wins over the file without an app restart.
        self._overrides_provider = overrides_provider
        # Statuses that are already settled — finalised sweeps and by-hand
        # corrections. They join the base layer rather than the decision layer:
        # they act as boundaries a sweep stops at, but they never sweep.
        self._base_overrides_provider = base_overrides_provider
        # Test-first flags frozen by an earlier finalise, so the instruction
        # survives the decision leaving the board.
        self._settled_priorities_provider = settled_priorities_provider
        # {marking: vat}, joined in from the process log. Absent when that log
        # is not readable, and then the column is left off entirely rather than
        # shown empty for every row.
        # Which decisions are still cards on the board, as opposed to
        # verdicts already finalised. Both sweep; only the first locks a row,
        # because only the first can still be dragged somewhere else.
        self._board_provider = board_provider
        self._vat_provider = vat_provider
        # The column is kept, empty, while the process log is still being read,
        # so it can be shown as loading. Without this it appears from nowhere a
        # second or two after the window opens.
        self._vat_pending_provider = vat_pending_provider
        # Which electrodes are on the QC board mid workflow. Read on every
        # process(), so a card added on another machine shows up on the next
        # poll rather than at the next restart.
        self._ongoing_provider = ongoing_provider

    def set_base_status(self, marking: str, status: str) -> None:
        """Record a status set by hand, so the next process() shows it.

        The backend has already written it; this keeps the copy read at startup
        in step without re-reading every electrode.
        """
        self._status_by_marking[str(marking).strip()] = str(status)

    def base_status(self) -> dict[str, str]:
        """The status of every electrode before any live board decision."""
        base = dict(self._status_by_marking)
        if self._base_overrides_provider:
            base.update(self._base_overrides_provider())
        return base

    def process(self, df: pd.DataFrame | None) -> pd.DataFrame:
        """Raw rows → display frame: known columns in display order, plus Status.

        An empty/None input still yields the full column set so the table keeps
        its headers.
        """
        columns = self.display_columns()
        if df is None or df.empty:
            return pd.DataFrame(columns=columns)

        out = df.copy()
        statuses, priorities, locked_by = self._resolve(out)
        markings = out[str(ElectrodeColumns.MARKING)].astype(str).str.strip()
        out[STATUS_COLUMN] = statuses
        out[PRIORITY_COLUMN] = markings.map(
            lambda marking: PRIORITY_FLAG if marking in priorities else "")
        out[LOCKED_BY_COLUMN] = markings.map(locked_by).fillna("")
        vats = self._vats()
        if vats or self._vats_pending():
            # A dict lookup per marking, never a join against the process log.
            # An A marking, or a B one the log does not cover, reads empty.
            out[VAT_COLUMN] = markings.map(vats).fillna("")
        return out.reindex(columns=columns).reset_index(drop=True)

    def _vats(self) -> dict[str, str]:
        """``{marking: vat}``, empty when the process log is out of reach."""
        return dict(self._vat_provider() or {}) if self._vat_provider else {}

    def _vats_pending(self) -> bool:
        """Whether the vat join has yet to answer for the first time."""
        return bool(self._vat_pending_provider()) if self._vat_pending_provider else False

    def display_columns(self) -> list[str]:
        """Column order for the table: the stored columns, then the derived ones.

        The vat only earns a column when there are vats to put in it.
        """
        columns = [c.value for c in ElectrodeColumns]
        if self._vats() or self._vats_pending():
            columns.append(VAT_COLUMN)
        return columns + [STATUS_COLUMN, PRIORITY_COLUMN, LOCKED_BY_COLUMN]

    def status_for(self, df: pd.DataFrame) -> pd.Series:
        """Per-electrode QC status, joined on the marking.

        A QC-board decision (Release / Scrapped) wins over the status file — it's
        the more recent, human-made call — and spreads to the untested
        electrodes around it (see ``status_propagation``). An electrode no
        source mentions reads as Not Tested.
        """
        return self._resolve(df)[0]

    def _resolve(self, df: pd.DataFrame) -> tuple[pd.Series, set[str], dict[str, str]]:
        """Statuses, the test-first set, and which live decision drives each row."""
        markings = df[str(ElectrodeColumns.MARKING)].astype(str).str.strip()
        base = self.base_status()
        settled = set(self._settled_priorities_provider()) \
            if self._settled_priorities_provider else set()

        # No shortcut when there are no decisions. Test ongoing is applied by
        # the propagation too, and taking the last card out of Release or
        # Scrapped used to skip the whole thing: the sweeps went, correctly, and
        # every electrode being tested went with them.
        decisions = self._overrides_provider() if self._overrides_provider else {}

        effective, sources, priorities, causes = propagate_detailed(
            list(markings), base, decisions, vats=self._vats(),
            ongoing=set(self._ongoing_provider() or ())
            if self._ongoing_provider else set())

        # Test first belongs on an electrode that is waiting to be tested, and
        # nowhere else: it means "of the ones in the queue, do this one first".
        # Once it has been tested, or scrapped, or somebody is testing it right
        # now, there is nothing left to do first. Worth stating as one rule,
        # because a flag finalised in an earlier round comes back through
        # ``settled`` and would otherwise ride on top of the new verdict.
        waiting = str(ElectrodeStatus.NEEDS_TESTING)
        flagged = {marking for marking in settled | priorities
                   if effective.get(marking) == waiting}
        # Locked by, not caused by. A finalised verdict causes plenty of
        # these statuses but cannot move any more, so pinning one of its
        # electrodes by hand is safe and the operator is left free to do it.
        on_board = set(self._board_provider() or ()) if self._board_provider else set()
        locked_by = {}
        for marking, why in causes.items():
            live = why & on_board
            if not live:
                continue
            # Name the primary cause when that is the live one, so the tooltip
            # matches the "because of" the rest of the app shows.
            primary = sources.get(marking, "")
            locked_by[marking] = primary if primary in live else sorted(live)[0]
        return (markings.map(effective).fillna(str(ElectrodeStatus.NOT_TESTED)),
                flagged, locked_by)
