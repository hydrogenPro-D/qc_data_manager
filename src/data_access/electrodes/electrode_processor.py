"""Processor: raw dbo.MetaData rows → the GUI-ready electrode frame.

Pure ``DataFrame -> DataFrame`` (no Qt, no DB), so it unit-tests trivially.
Selects and orders the columns the screen needs and appends the derived Status.
"""

from __future__ import annotations

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    DECIDED,
    FAILED_CRACK,
    FAILED_IC,
    FAILURE_COLUMN,
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    PRIORITY_COLUMN,
    PRIORITY_FLAG,
    LOCKED_BY_COLUMN,
    VAT_COLUMN,
    FINALISED_COLUMN,
    NOTHING_LEFT_TO_ASK,
)
from src.data_access.electrodes.status_propagation import propagate_detailed
from src.data_access.electrodes.waiting_status import waiting_statuses
from src.data_access.qc_requests.request_columns import (DECLINED,
                                                         EXPIRED,
                                                         SENT,
                                                         QC_COUPONS_COLUMN,
                                                         QC_REQUESTED_COLUMN,
                                                         QC_STATUS_COLUMN,
                                                         state_label)
from src.data_access.qc_board.qc_board_columns import (IC_FAILED,
                                                       IC_RESULT,
                                                       SEM_CRACKED,
                                                       SEM_RESULT)


# WHAT process() WORKS OUT rather than reads off the row. The Electrodes tab
# patches a changed row by copying these across from a freshly processed frame,
# so a derived column missing from the list is computed and then thrown away,
# leaving a stale cell on screen. That went wrong with the finalised date, then
# with the QC request state, then with the coupons, each time because a new
# column was added here and not there. So there is ONE list and both ends read
# it; adding a derived column to process() means adding it here, once.
DERIVED_COLUMNS = (STATUS_COLUMN, PRIORITY_COLUMN, LOCKED_BY_COLUMN,
                   FAILURE_COLUMN, FINALISED_COLUMN, QC_STATUS_COLUMN,
                   QC_REQUESTED_COLUMN, QC_COUPONS_COLUMN)


# The ends a request can come to with no electrode arriving. Sent is not one
# of them: there the coupons describe what QC actually has.
_NOTHING_COMING = frozenset({DECLINED, EXPIRED})


def _coupons_worth_showing(request: dict | None) -> str:
    """What a request asks be cut, while that is still worth knowing.

    Blank once production is not sending the electrode, whether they said so
    or ran out of time to. The icon says what QC will get from this one, and
    from an electrode that is not coming it will get nothing, so the coupons
    an answered question asked for are only noise on the row.
    """
    request = request or {}
    if str(request.get("state", "")).strip() in _NOTHING_COMING:
        return ""
    return request.get("coupons", "")


class ElectrodeProcessor:
    """Reshapes raw electrode rows into the frame the Electrodes tab renders."""

    def __init__(self, status_by_marking: dict[str, str] | None = None,
                 overrides_provider=None, base_overrides_provider=None,
                 settled_priorities_provider=None,
                 board_provider=None,
                 vat_provider=None,
                 vat_pending_provider=None,
                 ongoing_provider=None,
                 pinned_provider=None,
                 finalised_provider=None,
                 findings_provider=None,
                 revisited_provider=None,
                 qc_request_provider=None,
                 unqueued_provider=None) -> None:
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
        # Which electrodes still hold a card, decided or not, as opposed to
        # verdicts already finalised. A card can still be dragged somewhere
        # else, and every row it drives moves with it, so it is what locks.
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
        # Which scraps QC does not want tested around, worked out from what
        # failed and what the coupon settings say that failure is worth. Their
        # margin is condemned as always; the queue past it is not created.
        self._unqueued_provider = unqueued_provider
        # Which statuses an operator set by hand. No sweep may claim one, and
        # none of them can be beaten by a frozen status covering the same
        # electrode: if a row is not locked, the status you pick is the status
        # you get.
        self._pinned_provider = pinned_provider
        # {marking: when its status settled}. Read on every process(), so a
        # finalise on another machine fills the column on the next poll.
        self._finalised_provider = finalised_provider
        # marking -> the SEM and IC findings recorded against a card, for
        # telling the two waits apart.
        self._findings_provider = findings_provider
        # {marking: the live verdict whose re-test unfroze it}. Those move
        # with that card even when another decision settles them.
        self._revisited_provider = revisited_provider
        # {marking: request state}. Read on every process(), so a decision
        # taken in the other application shows up on the next poll.
        self._qc_request_provider = qc_request_provider

    def set_base_status(self, marking: str, status: str) -> None:
        """Record a status set by hand, so the next process() shows it.

        The backend has already written it; this keeps the copy read at startup
        in step without re-reading every electrode.
        """
        self._status_by_marking[str(marking).strip()] = str(status)

    def base_status(self) -> dict[str, str]:
        """The status of every electrode before any live board decision.

        A hand set status is re-applied last, over the finalised ones. Both are
        settled and both cover the same electrode, so the operator's correction
        is the later statement by definition, and ordering them by their two
        clocks made a machine running slightly behind the server lose its edits.
        """
        base = dict(self._status_by_marking)
        if self._base_overrides_provider:
            base.update(self._base_overrides_provider())
        for marking in self.pinned():
            if marking in self._status_by_marking:
                base[marking] = self._status_by_marking[marking]
        return base

    def pinned(self) -> set[str]:
        """Electrodes whose status was set by hand, so no sweep may claim them."""
        return set(self._pinned_provider() or ()) if self._pinned_provider else set()

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
        out[FAILURE_COLUMN] = [
            self._failure_codes(marking, status)
            for marking, status in zip(markings, out[STATUS_COLUMN].astype(str))]
        # Blank wherever nothing has actually settled, whatever the database
        # holds, and it can hold a stale date for either case:
        #
        # * The status is not an outcome. Not Tested, Needs testing, both waits
        #   and Test ongoing are all steps on the way to one, so there is
        #   nothing to date: the electrode has not finished.
        # * Held by a card still on the board. That card has not been marked as
        #   done, so this status can still change with it. The date underneath
        #   belongs to an older sweep the card interrupted, which is worse than
        #   no date: it says the row has read this since a day it did not.
        #
        # A hand set status is the exception to the second: nothing holds it,
        # and the date on it is when the operator set it.
        outcomes = {str(status) for status in DECIDED}
        held = out[LOCKED_BY_COLUMN].astype(str) != ""
        by_hand = markings.isin(self.pinned())
        settled = (out[STATUS_COLUMN].astype(str).isin(outcomes)
                   & (~held | by_hand))
        # The one place it is dropped, so the column, the filter and the report
        # all agree.
        out[FINALISED_COLUMN] = markings.map(self._finalised()).fillna("").where(
            settled, "")
        requests = (dict(self._qc_request_provider() or {})
                    if self._qc_request_provider else {})
        out[QC_STATUS_COLUMN] = markings.map(
            lambda marking: state_label(
                (requests.get(marking) or {}).get("state", "")))
        # Carried on the frame but not in the display map, so the pull list can
        # show when an electrode was asked for without the main table, which
        # already has nine columns, growing a tenth.
        out[QC_REQUESTED_COLUMN] = markings.map(
            lambda marking: (requests.get(marking) or {}).get("requested", ""))
        out[QC_COUPONS_COLUMN] = markings.map(
            lambda marking: _coupons_worth_showing(requests.get(marking)))
        # QUIET ON AN ELECTRODE THERE IS NOTHING TO ASK ABOUT. A released or
        # scrapped one is finished, and one on a rig is already at QC, so a
        # request against it is a question nobody is waiting on an answer to.
        # Sent stays: that is a fact about where the electrode went.
        done = out[STATUS_COLUMN].astype(str).isin(
            {str(status) for status in NOTHING_LEFT_TO_ASK})
        quiet = done & (out[QC_STATUS_COLUMN] != state_label(SENT))
        out[QC_STATUS_COLUMN] = out[QC_STATUS_COLUMN].where(~quiet, "")
        # And with it the icon, which would otherwise be left standing on its
        # own in a cell that no longer says anything.
        out[QC_COUPONS_COLUMN] = out[QC_COUPONS_COLUMN].where(~quiet, "")
        vats = self._vats()
        if vats or self._vats_pending():
            # A dict lookup per marking, never a join against the process log.
            # An A marking, or a B one the log does not cover, reads empty.
            out[VAT_COLUMN] = markings.map(vats).fillna("")
        return out.reindex(columns=columns).reset_index(drop=True)

    def _vats(self) -> dict[str, str]:
        """``{marking: vat}``, empty when the process log is out of reach."""
        return dict(self._vat_provider() or {}) if self._vat_provider else {}

    def _failure_codes(self, marking: str, status: str) -> str:
        """Why this electrode failed, or "" where there is nothing to say.

        Only for one somebody actually put on a rig. A neighbour swept into
        Rejected was never tested and never observed, so it has no reason and
        must not borrow the tested one's.

        Both can be true at once, and neither has to be: an operator who left
        the findings blank leaves a failure with no reason, which is shown as
        no badge rather than as a guess.
        """
        if status != str(ElectrodeStatus.TESTED_AND_SCRAPPED) or not self._findings_provider:
            return ""
        found = self._findings_provider(marking) or {}
        codes = []
        if str(found.get(IC_RESULT, "")) == IC_FAILED:
            codes.append(FAILED_IC)
        if str(found.get(SEM_RESULT, "")) == SEM_CRACKED:
            codes.append(FAILED_CRACK)
        return ",".join(codes)

    def _finalised(self) -> dict[str, str]:
        """``{marking: when its status settled}``, empty where nothing has."""
        return dict(self._finalised_provider() or {}) if self._finalised_provider else {}

    def _vats_pending(self) -> bool:
        """Whether the vat join has yet to answer for the first time."""
        return bool(self._vat_pending_provider()) if self._vat_pending_provider else False

    def display_columns(self) -> list[str]:
        """Every column the frame carries, in order. Not every one is shown.

        process() reindexes onto this, so a column left out of it is dropped
        before the frame reaches the screen. What the Electrodes TABLE shows is
        a second, narrower list, its display map; the last two here are carried
        for the pull list and the coupon icon and never get a column of their
        own. Both were missing from the panel until they were named here.

        The vat only earns a column when there are vats to put in it.
        """
        columns = [c.value for c in ElectrodeColumns]
        if self._vats() or self._vats_pending():
            columns.append(VAT_COLUMN)
        return columns + list(DERIVED_COLUMNS)

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
            if self._ongoing_provider else set(),
            pinned=self.pinned(),
            unqueued=set(self._unqueued_provider() or ())
            if self._unqueued_provider else set())

        # Test first belongs on an electrode that is waiting to be tested, and
        # nowhere else: it means "of the ones in the queue, do this one first".
        # Once it has been tested, or scrapped, or somebody is testing it right
        # now, there is nothing left to do first. Worth stating as one rule,
        # because a flag finalised in an earlier round comes back through
        # ``settled`` and would otherwise ride on top of the new verdict.
        # A FROZEN ROW CARRIES ITS OWN FLAG, set or not. ``settled`` is the
        # frozen rows that say test first; one frozen WITHOUT the flag said so
        # at the finalise, and the live sweep re-flagging it contradicts the
        # record it is standing on. B13831 read "to be tested first" against a
        # frozen row that says it is not, with no open card to explain it.
        #
        # So the sweep only flags electrodes whose status is not settled. A
        # stale frozen row is not settled either, so one the sweep has taken
        # back falls through to the sweep's answer, which is what it should do.
        standing = (set(self._base_overrides_provider())
                    if self._base_overrides_provider else set())
        waiting = str(ElectrodeStatus.NEEDS_TESTING)
        flagged = {marking for marking in settled | (priorities - standing)
                   if effective.get(marking) == waiting}

        # What the rest of the zone is really waiting on. After ``flagged``,
        # which needs the plain status to find the queue, and never over a
        # test-first electrode: that one is the instruction, not a wait.
        effective.update(waiting_statuses(
            effective, causes, set(self._ongoing_provider() or ())
            if self._ongoing_provider else set(),
            flagged, self._findings_provider))
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

        # Unfrozen by a re-test but claimed by nobody, so the sweeps credited
        # them to nothing. setdefault, since a real cause is the better answer
        # where there is one.
        revisited = self._revisited_provider() if self._revisited_provider else {}
        for marking, source in (revisited or {}).items():
            if source:
                locked_by.setdefault(marking, source)
        return (markings.map(effective).fillna(str(ElectrodeStatus.NOT_TESTED)),
                flagged, locked_by)
