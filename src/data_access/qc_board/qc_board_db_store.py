"""The QC board, held in the database instead of a local JSON file.

Same methods as :class:`QcBoardStore`, so the GUI cannot tell which one it was
given. The difference that matters is not the interface, it is that two
operators finally see the same board.

    reads   qc_data.vw_qc_board, vw_qc_board_archive
    writes  qc_data.usp_qc_board_*, usp_electrode_status_set

THE WHOLE BOARD IS READ AT ONCE and kept in memory. A render asks for each of
sixteen cells in turn, so a query per cell would be sixteen round trips every
time a card moves. Each mutation calls its procedure and then patches the local
copy, which is what makes the card land instantly.

UNDO STAYS IN MEMORY, one step, for this session only. It is editing state, not
a record of anything: reversing somebody else's change an hour later, from
another machine, is not undo, it is a second edit made blind.
"""

from __future__ import annotations

import datetime as dt
import json
import time

from src.data_access.electrodes.electrode_columns import normalize_status
from src.data_access.electrodes.status_propagation import electrode_number
from src.data_access.qc_board.qc_board_columns import (
    ElectrodeType,
    QcStage,
    STAGE_STATUS,
    RETIRED_STAGES,
    FINDING_FIELDS,
    FINDING_VALUES,
    PENDING,
    SEM_CRACKED,
    SEM_RESULT,
)

SCHEMA = "qc_data"


def _finding(value, field: str) -> str:
    """One finding, as one of the three values its field allows.

    Anything unrecognised, a NULL, a column that is not there yet, reads as
    not tested, which is the honest answer for a value nobody wrote.
    """
    text = str(value or "").strip()
    return text if text in FINDING_VALUES.get(field, ()) else PENDING


class QcBoardDbStore:
    """Reads and writes the board through views and procedures."""

    def __init__(self, session) -> None:
        self._session = session
        self._entries: dict[str, dict] = {}
        self._archived: dict[str, dict] = {}
        self._last_action: dict = {}
        # When this store last wrote. A snapshot read before that is older than
        # what we know and is thrown away, see apply().
        self._written_at = 0.0
        self.reload()

    # ---- persistence --------------------------------------------------
    def fetch(self) -> dict:
        """Read the whole board and hand it back without touching this store.

        Split from :meth:`apply` so a caller can read on one thread and adopt
        on another, which is what the QC tab does: the query runs on the
        polling thread and the store is filled on the UI thread.
        """
        # Stamped BEFORE the first query, not after the last. The rows reflect
        # the moment the read ran, so a read that began before somebody dropped
        # a card is stale even if it finishes afterwards.
        started_at = time.monotonic()

        stages = {str(s) for s in QcStage}
        entries: dict[str, dict] = {}
        for row in self._session.rows(f"SELECT * FROM [{SCHEMA}].[vw_qc_board]"):
            marking = str(row.get("electrode") or "").strip()
            stage = str(row.get("stage") or "")
            # A board written before a column was retired still names it.
            if stage not in stages and stage in RETIRED_STAGES:
                stage = str(RETIRED_STAGES[stage])
            if not marking or stage not in stages:
                continue

            entries[marking] = {
                "stage": stage,
                "type": str(row.get("electrode_type") or ""),
                "updated": str(row.get("updated_at_utc") or ""),
                # Feature by presence: a database still on 013's predecessor
                # returns no such column and every finding reads as not tested.
                **{field: _finding(row.get(field), field)
                   for field in FINDING_FIELDS},
            }

        archived: dict[str, dict] = {}
        for row in self._session.rows(
                f"SELECT * FROM [{SCHEMA}].[vw_qc_board_archive]"):
            marking = str(row.get("electrode") or "").strip()
            if marking:
                archived[marking] = {
                    "status": str(row.get("status") or ""),
                    "from": str(row.get("caused_by") or marking),
                    "at": str(row.get("archived_at_utc") or ""),
                    "priority": bool(row.get("test_first")),
                }

        # Ordered by production number rather than by when a card was dropped
        # on the board, so the cells read the same way the Electrodes tab does.
        return {
            "entries": dict(sorted(
                entries.items(),
                key=lambda item: (electrode_number(item[0]) or 0, item[0]))),
            "archived": archived,
            "fetched_at": started_at,
        }

    def apply(self, snapshot: dict) -> bool:
        """Adopt a board read by :meth:`fetch`. False if it was already stale.

        A snapshot read before our own most recent write does not contain that
        write, so adopting it would put a card the operator just dragged back
        where it came from until the next poll. The polling worker reads on a
        background thread and can easily be mid-read when somebody drops a
        card, so this is the common case, not a corner one.

        Safe to call from the polling thread: each line rebinds one already
        built dictionary, so a render either sees the old board or the new one,
        never one being filled in.
        """
        if snapshot.get("fetched_at", 0.0) < self._written_at:
            return False
        self._entries = dict(snapshot.get("entries") or {})
        self._archived = dict(snapshot.get("archived") or {})
        return True

    def reload(self) -> None:
        """Read the board and adopt it, both here. Startup, and any caller that
        is already on the thread it wants the result on."""
        self.apply(self.fetch())

    def save(self) -> None:
        """Nothing to do: every mutation has already been written by its
        procedure. Kept so the two stores really are interchangeable."""

    def _write(self, procedure: str, **params) -> list[dict]:
        """Run one procedure, and note when, so a poll already in flight cannot
        come back and undo it.

        This used to advance the polling baselines as well, so our own write
        would not look like somebody else's. That cost four round trips on the
        UI thread per card drop, to save one cheap reload on a background one.
        """
        result = self._session.call(f"[{SCHEMA}].[{procedure}]", **params)
        self._written_at = time.monotonic()
        return result

    # ---- queries ------------------------------------------------------
    def contains(self, marking: str) -> bool:
        return str(marking).strip() in self._entries

    def on_board(self) -> set[str]:
        """Every electrode holding a card, decided or not.

        The union of ``ongoing_electrodes`` and the decided ones: what they
        share is that the card can still be dragged, so nothing about them is
        settled yet.
        """
        return set(self._entries)

    def stage_of(self, marking: str) -> QcStage | None:
        entry = self._entries.get(str(marking).strip())
        return QcStage(entry["stage"]) if entry else None

    def markings(self, stage: QcStage, electrode_type: ElectrodeType) -> list[str]:
        """The electrodes in one cell of the board."""
        return [
            marking for marking, entry in self._entries.items()
            if entry["stage"] == str(stage) and entry["type"] == str(electrode_type)
        ]

    def status_overrides(self) -> dict[str, str]:
        """``{marking: status}`` for electrodes the board has decided on.

        Only the terminal stages produce one, an electrode mid-workflow has no
        verdict yet.
        """
        return {
            marking: STAGE_STATUS[QcStage(entry["stage"])]
            for marking, entry in self._entries.items()
            if QcStage(entry["stage"]) in STAGE_STATUS
        }

    def ongoing_electrodes(self) -> set[str]:
        """Everything on the board that no stage has decided yet.

        The terminal stages produce a verdict and are handled by
        ``status_overrides``; these are the ones still being worked on, which
        the Electrodes tab shows as Test ongoing rather than as a guess about
        them made by somebody else's scrap.
        """
        return {marking for marking, entry in self._entries.items()
                if QcStage(entry["stage"]) not in STAGE_STATUS}

    def archived_verdicts(self) -> dict[str, str]:
        """The finalised statuses an electrode earned itself, not by inference.

        Told apart by the record of what each was drawn from: a verdict names
        itself, a swept neighbour names the electrode that condemned it.
        """
        return {marking: normalize_status(entry["status"])
                for marking, entry in self._archived.items()
                if entry.get("status") and (entry.get("from") or "") in ("", marking)}

    def stale_inferences(self) -> set[str]:
        """Finalised sweeps drawn from a verdict that has since been replaced.

        A finalised status records what it was drawn from and when. Re-testing
        that electrode writes a new verdict but does not touch the neighbours
        the old one condemned, so they keep a status inferred from a verdict
        that no longer exists, and the new one finds nothing left to claim: the
        sweeps only ever take an electrode that has no status of its own.

        Recognised by time rather than by value. An inference older than the
        verdict it names was drawn from a previous version of it; one carrying
        the same timestamp was written by the sweep that verdict just ran.
        """
        verdict_at = {marking: str(entry.get("at") or "")
                      for marking, entry in self._archived.items()
                      if (entry.get("from") or "") in ("", marking)}

        # A verdict being revisited on the board counts as replaced too, not
        # only an archived one. The card sitting in Passed or Failed is the
        # current answer, so the sweep frozen from its previous answer has to
        # give way to it at once, rather than leaving the electrodes around it
        # condemned by a result that no longer stands until somebody finalises.
        live = set(self.status_overrides())

        stale = set()
        for marking, entry in self._archived.items():
            source = str(entry.get("from") or "")
            if not source or source == marking:
                continue
            if source in live:
                stale.add(marking)
                continue
            current = verdict_at.get(source, "")
            if current and current > str(entry.get("at") or ""):
                stale.add(marking)
        return stale

    def revisited_by(self) -> dict[str, str]:
        """``{electrode: the live verdict that unfroze it}``.

        Dropping a frozen sweep because its verdict is being re-tested changes
        what these electrodes read, whether or not the new sweep then claims
        them: one on the far side may end up settled by a different pass
        entirely. They are still moving with that card, so they lock with it.
        """
        live = set(self.status_overrides())
        return {marking: str(entry.get("from") or "")
                for marking, entry in self._archived.items()
                if (entry.get("from") or "") in live
                and (entry.get("from") or "") != marking}

    def archived_statuses(self) -> dict[str, str]:
        """``{marking: status}`` for everything already finalised."""
        return {marking: normalize_status(entry["status"])
                for marking, entry in self._archived.items() if entry.get("status")}

    def archived_times(self) -> dict[str, str]:
        """``{marking: when the verdict was frozen}``, to order it against a
        hand set status covering the same electrode."""
        return {marking: str(entry.get("at") or "")
                for marking, entry in self._archived.items() if entry.get("status")}

    def archived_priorities(self) -> set[str]:
        """Finalised test-first flags, so the instruction outlives the board entry."""
        return {marking for marking, entry in self._archived.items()
                if entry.get("priority")}

    def findings(self, marking: str) -> dict:
        """What has been observed about one electrode so far."""
        entry = self._entries.get(str(marking).strip()) or {}
        return {field: _finding(entry.get(field), field) for field in FINDING_FIELDS}

    def can_undo(self) -> bool:
        return bool(self._last_action)

    def undo_label(self) -> str:
        """What the next undo would reverse, for the button's tooltip."""
        return str(self._last_action.get("label", ""))

    # ---- mutations ----------------------------------------------------
    def add(self, marking: str, electrode_type: ElectrodeType,
            stage: QcStage = QcStage.WAITING) -> None:
        """Put an electrode on the board. Re-adding one moves it instead."""
        key = str(marking).strip()
        self._record_entry(key, f"adding {key}")
        self._write(
            "usp_qc_board_add",
            electrode=key, electrode_type=str(electrode_type), stage=str(stage))
        self._entries[key] = {
            "stage": str(stage), "type": str(electrode_type),
            "updated": self._now(),
            **{field: PENDING for field in FINDING_FIELDS},
        }

    def move(self, marking: str, stage: QcStage) -> bool:
        """Move an electrode to another stage. False if it isn't on the board."""
        key = str(marking).strip()
        entry = self._entries.get(key)
        if entry is None:
            return False
        self._record_entry(key, f"moving {key}")
        self._write("usp_qc_board_move", electrode=key, stage=str(stage))
        entry["stage"] = str(stage)
        entry["updated"] = self._now()
        return True

    def set_findings(self, marking: str, findings: dict) -> bool:
        """Record findings against an electrode. False if it isn't on the board
        or nothing actually changed.

        All three travel together, since an operator writes down what they know
        in one go and the procedure only touches the row when one differs.
        """
        key = str(marking).strip()
        entry = self._entries.get(key)
        if entry is None:
            return False

        wanted = {field: _finding(findings.get(field), field)
                  for field in FINDING_FIELDS}
        if all(_finding(entry.get(field), field) == value
               for field, value in wanted.items()):
            return False

        self._record_entry(key, f"the findings on {key}")
        self._write("usp_qc_board_set_findings", electrode=key,
                    **{field: value or None for field, value in wanted.items()})
        entry.update(wanted)
        entry["updated"] = self._now()
        return True

    def remove(self, marking: str) -> bool:
        """Take an electrode off the board entirely."""
        key = str(marking).strip()
        if key not in self._entries:
            return False
        self._record_entry(key, f"deleting {key}")
        self._write("usp_qc_board_remove", electrode=key)
        self._entries.pop(key, None)
        return True

    def set_electrode_status(self, marking: str, status: str) -> None:
        """Set one electrode's status by hand, in its own row.

        Deliberately does not propagate, and deliberately not undoable: a
        correction is a statement about that electrode, not a board move. There
        is no way to clear one, see migration 010.
        """
        self._write("usp_electrode_status_set",
                    electrode=str(marking).strip(), status=status)

    def archive(self, frozen: dict[str, tuple[str, str]],
                priorities: set[str] | None = None,
                markings: set[str] | None = None) -> int:
        """Freeze statuses and clear the electrodes that produced them.

        Args:
            frozen: ``{marking: (status, caused_by)}``, the decided electrodes
                and everything their sweeps touched. Worked out by
                ``status_propagation``; this only writes it down.
            priorities: Markings flagged to test first, frozen alongside.
            markings: Which decided electrodes to consume. ``None`` means every
                decided one.

        Returns:
            How many electrodes were frozen.
        """
        stamped_at = self._now()
        priorities = priorities or set()
        decided = {
            marking: dict(entry) for marking, entry in self._entries.items()
            if QcStage(entry["stage"]) in STAGE_STATUS
            and (markings is None or marking in markings)
        }

        payload = [{"electrode": marking, "status": status,
                    "caused_by": caused_by,
                    "test_first": 1 if marking in priorities else 0}
                   for marking, (status, caused_by) in frozen.items()]
        if not payload:
            return 0

        self._write(
            "usp_qc_board_archive",
            frozen=json.dumps(payload),
            consumed=None if markings is None else json.dumps(sorted(decided)))

        for marking, (status, caused_by) in frozen.items():
            self._archived[marking] = {
                "status": status, "from": caused_by, "at": stamped_at,
                "priority": marking in priorities}
        for marking in decided:
            self._entries.pop(marking, None)

        count = len(decided)
        self._last_action = {
            "kind": "archive", "at": stamped_at,
            "label": f"marking {count} electrode{'s' if count != 1 else ''} as done",
            "markings": sorted(frozen),
            "board": decided,
        }
        return len(frozen)

    # ---- undo ---------------------------------------------------------
    def _record_entry(self, marking: str, label: str) -> None:
        """Snapshot one electrode before changing it. Call before the mutation:
        a missing entry is itself the state to restore, so an add undoes to
        nothing and a delete undoes back to what was there."""
        previous = self._entries.get(str(marking).strip())
        self._last_action = {
            "kind": "entry", "label": label, "at": self._now(),
            "marking": str(marking).strip(),
            "previous": dict(previous) if previous else None,
        }

    def undo(self) -> bool:
        """Reverse the last board change of this session. False if there is none."""
        action, self._last_action = self._last_action, {}
        if not action:
            return False

        if action.get("kind") == "entry":
            marking, previous = action.get("marking", ""), action.get("previous")
            self._write(
            "usp_qc_board_restore",
                electrode=marking,
                stage=(previous or {}).get("stage"),
                electrode_type=(previous or {}).get("type"),
                **{field: (previous or {}).get(field) or None
                   for field in FINDING_FIELDS})
            if previous is None:
                self._entries.pop(marking, None)
            else:
                self._entries[marking] = dict(previous)

        elif action.get("kind") == "archive":
            board = action.get("board") or {}
            self._write(
            "usp_qc_board_unarchive",
                markings=json.dumps(action.get("markings") or []),
                board=json.dumps([
                    {"electrode": marking, "stage": entry.get("stage"),
                     "electrode_type": entry.get("type"),
                     **{field: entry.get(field) or None
                        for field in FINDING_FIELDS}}
                    for marking, entry in board.items()]))
            for marking in action.get("markings") or []:
                self._archived.pop(marking, None)
            for marking, entry in board.items():
                self._entries[marking] = dict(entry)

        return True

    @staticmethod
    def _now() -> str:
        return dt.datetime.now().replace(microsecond=0).isoformat()
