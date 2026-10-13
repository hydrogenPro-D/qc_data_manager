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
from src.data_access.electrodes.status_propagation import (claimable,
                                                            electrode_number)
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


def _nearest(marking: str, cards) -> str:
    """Whichever of these cards is closest to this electrode, by number.

    Several can hold the same row. The operator needs the one whose result will
    actually reach it, and that is the near one: naming whichever happened to
    come first out of a set sent them to an electrode far up the table while
    the one next door was the test they were waiting on. Sorted first, so a tie
    is settled the same way every render.
    """
    ordered = sorted(cards)
    if not ordered:
        return ""
    here = electrode_number(marking)
    if here is None:
        return ordered[0]
    return min(ordered, key=lambda card: abs((electrode_number(card) or 0) - here))


def _card_inside(marking: str, source: str, cards: dict[int, str]) -> str:
    """The nearest card standing between these two electrodes, or "".

    Inclusive at the inference, exclusive at the source. An electrode on the
    board is inside the stretch its own frozen status covers, but the verdict
    that stretch was drawn from is not part of it: re-testing that verdict is a
    separate matter, and its old result goes on driving the stretch until a new
    result replaces it.
    """
    here, there = electrode_number(marking), electrode_number(source)
    if here is None or there is None:
        return ""
    low, high = min(here, there), max(here, there)
    return _nearest(marking, [card for number, card in cards.items()
                              if low <= number <= high and number != there])


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
                    # Feature by presence: a database still on 015 returns no
                    # such columns and every frozen verdict reads as having had
                    # nothing written down, which is what it was.
                    **{field: _finding(row.get(field), field)
                       for field in FINDING_FIELDS},
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

    def stale_inferences(self, board: set[str] | None = None) -> set[str]:
        """Finalised sweeps that no longer stand.

        ``board`` is which cards to count as holding a stretch, defaulting to
        everything on it. ``finalise`` passes the cards that will REMAIN, so a
        stretch another operator still has open is not frozen by somebody
        else's "mark all as done".
        """
        return set(self._unfrozen(board))

    def reopened_sources(self, board: set[str] | None = None) -> set[str]:
        """The verdicts whose frozen sweeps no longer stand.

        A finalise has to let exactly these sweep again. The far half of a
        reopened stretch is held by one of them rather than by the card being
        finalised, so without them half the stretch takes the new date and half
        keeps the date of the sweep the card interrupted. Only these, because
        letting every verdict ever recorded sweep would rewrite rows that have
        nothing to do with the card in hand.
        """
        return {str(self._archived[marking].get("from") or "")
                for marking in self._unfrozen(board)} - {""}

    def revisited_by(self) -> dict[str, str]:
        """``{electrode: the card on the board it is now moving with}``.

        A frozen sweep dropped because of a card changes what these electrodes
        read, whether or not the new sweep then claims them: one on the far
        side may end up settled by a different pass entirely. They are moving
        with that card, so they lock with it and show no settled date until it
        is marked as done.
        """
        return {marking: why for marking, why in self._unfrozen().items() if why}

    def _unfrozen(self, board: set[str] | None = None) -> dict[str, str]:
        """``{frozen inference that no longer stands: what unfroze it}``.

        The value is the card on the board responsible, or empty where the
        inference simply gave way to a newer verdict and no card is involved.

        Three ways an inference stops standing:

        * **Its verdict is being re-tested.** A card sitting in Passed or
          Failed is the current answer, so a sweep frozen from that electrode's
          previous answer gives way at once rather than condemning its
          neighbours on a result that no longer stands.
        * **A card is inside the stretch it belongs to.** Whatever that test
          says governs the stretch: a scrap there condemns the electrodes
          either side, which is the opposite of the release that froze them. So
          the whole stretch reads Not Tested until the card is marked as done.
          Recognised two ways, an electrode standing between an inference and
          the verdict it was drawn from, or one swept by that same verdict,
          which is the rest of its own stretch.
        * **Its verdict was replaced by a newer one.** By time rather than by
          value: an inference older than the verdict it names was drawn from a
          previous version of it, while one carrying the same timestamp was
          written by the sweep that verdict just ran.
        """
        board = self.on_board() if board is None else set(board)
        verdict_at = {marking: str(entry.get("at") or "")
                      for marking, entry in self._archived.items()
                      if (entry.get("from") or "") in ("", marking)}
        # Only the decided ones replace a verdict. An electrode back on the
        # board carrying a verdict of its OWN goes on driving its neighbours
        # until a new result lands, which is settled behaviour elsewhere.
        replaced = {marking for marking in self.status_overrides()
                    if marking in board}

        held_at = {}
        for marking in board:
            number = electrode_number(marking)
            if number is not None:
                held_at[number] = marking
        # Which cards were swept by which verdict, so the rest of that verdict's
        # stretch gives way with them. All of them, not the first one found: a
        # row is held by the nearest, worked out per row below.
        shadowed: dict[str, list[str]] = {}
        for marking in board:
            source = str(self._archived.get(marking, {}).get("from") or "")
            if source and source != marking:
                shadowed.setdefault(source, []).append(marking)

        unfrozen: dict[str, str] = {}
        for marking, entry in self._archived.items():
            source = str(entry.get("from") or "")
            if not source or source == marking:
                continue
            if source in replaced:
                unfrozen[marking] = source
                continue
            # Everything below is a card standing in the stretch, and a card
            # cannot move a scrapped electrode: no sweep claims one. Those rows
            # are settled by the verdict that condemned them and stay settled,
            # with the date that verdict was finalised on.
            if not claimable(normalize_status(str(entry.get("status") or ""))):
                continue
            if source in shadowed:
                unfrozen[marking] = _nearest(marking, shadowed[source])
                continue
            inside = _card_inside(marking, source, held_at)
            if inside:
                unfrozen[marking] = inside
                continue
            current = verdict_at.get(source, "")
            if current and current > str(entry.get("at") or ""):
                unfrozen[marking] = ""
        return unfrozen

    def archived_statuses(self) -> dict[str, str]:
        """``{marking: status}`` for everything already finalised."""
        return {marking: normalize_status(entry["status"])
                for marking, entry in self._archived.items() if entry.get("status")}

    def archived_times(self) -> dict[str, str]:
        """``{marking: when the verdict was frozen}``, to order it against a
        hand set status covering the same electrode."""
        return {marking: str(entry.get("at") or "")
                for marking, entry in self._archived.items() if entry.get("status")}

    def settled_times(self) -> dict[str, str]:
        """``{marking: when its status last settled}``, restamped where it moved.

        ``archived_times`` says when a row was written. That is not always when
        the status it carries settled, and the difference is visible on screen.

        A stretch released between two passes is frozen in one go. Put one of
        its electrodes on the board and the whole stretch unfreezes and reads
        Not Tested; release that electrode and finalise it, and the stretch is
        released again, by the newer evidence this time. The rows were never
        rewritten, so they still carried the date of the sweep the test
        interrupted, and an operator reading the column saw a stretch that had
        been Not Tested an hour ago dated to the day before.

        So a verdict frozen INSIDE a stretch, later than the stretch itself,
        restamps the whole of it. An electrode only earns a verdict by going on
        the board, and going on the board is what unfroze the stretch, so a
        later verdict in the middle of one is exactly the evidence that it went
        away and came back.

        The whole stretch, not the electrodes on one side: it was unfrozen as
        one and came back as one. Each row on its own only reaches as far as the
        verdict it names, which would have restamped the half below a test and
        left the half above it dated a day earlier.
        """
        times = self.archived_times()
        verdicts = {}
        for marking, entry in self._archived.items():
            source = str(entry.get("from") or "")
            if entry.get("status") and source in ("", marking):
                number = electrode_number(marking)
                if number is not None:
                    verdicts[number] = str(entry.get("at") or "")

        for members, (low, high) in self._stretches().items():
            latest = max((at for number, at in verdicts.items()
                          if low <= number <= high), default="")
            for marking in members:
                if latest > times.get(marking, ""):
                    times[marking] = latest
        return times

    def _stretches(self) -> dict[tuple[str, ...], tuple[int, int]]:
        """``{the inferences of one sweep: the electrodes they span}``.

        Grouped by the verdict they were drawn from and when they were frozen,
        which is one "mark all as done" over one sweep. The span takes in that
        verdict, since the stretch runs up to it.
        """
        groups: dict[tuple[str, str], list[str]] = {}
        for marking, entry in self._archived.items():
            source = str(entry.get("from") or "")
            if not source or source == marking or not entry.get("status"):
                continue
            groups.setdefault((source, str(entry.get("at") or "")), []).append(marking)

        spans: dict[tuple[str, ...], tuple[int, int]] = {}
        for (source, _at), members in groups.items():
            numbers = [number for number in
                       map(electrode_number, [*members, source])
                       if number is not None]
            if numbers:
                spans[tuple(members)] = (min(numbers), max(numbers))
        return spans

    def archived_priorities(self) -> set[str]:
        """Finalised test-first flags, so the instruction outlives the board entry."""
        return {marking for marking, entry in self._archived.items()
                if entry.get("priority")}

    def findings(self, marking: str) -> dict:
        """What has been observed about one electrode so far.

        The live card first, then the frozen one. An electrode is rarely both,
        and where it is, the card is the newer of the two: it is on the board
        again precisely because the old answer is being revisited.
        """
        key = str(marking).strip()
        entry = self._entries.get(key)
        if entry is None:
            entry = self._archived.get(key) or {}
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
