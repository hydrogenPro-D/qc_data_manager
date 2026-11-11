"""Which frozen rows the app offers to correct, and the ones it must not.

The planner decides what gets written to production without anybody looking,
so most of these tests are about what it refuses to touch.
"""

import unittest

from src.data_access.electrodes.archive_repair import REPAIR_LIMIT, stale_rows
from src.data_access.electrodes.electrode_columns import ElectrodeStatus

RELEASED = str(ElectrodeStatus.RELEASED)
SCRAPPED = str(ElectrodeStatus.SCRAPPED)
TESTED_RELEASED = str(ElectrodeStatus.TESTED_AND_RELEASED)
NOT_TESTED = str(ElectrodeStatus.NOT_TESTED)
NEEDS_TESTING = str(ElectrodeStatus.NEEDS_TESTING)
ONGOING = str(ElectrodeStatus.TEST_ONGOING)
WAITING = str(ElectrodeStatus.WAITING_FOR_SEM)


def _frozen(status, caused_by, at="2026-09-24 12:04:14"):
    return {"status": status, "from": caused_by, "at": at, "priority": False}


def _plan(shown, archived, sources=None, held=None, on_board=(), by_hand=()):
    return stale_rows(shown, held or {}, archived, sources or {},
                      set(on_board), set(by_hand))


class TestWhatItCorrects(unittest.TestCase):

    def test_a_placeholder_a_later_verdict_has_claimed(self):
        plan = _plan({"B15523": RELEASED},
                     {"B15523": _frozen(NOT_TESTED, "B15375")},
                     sources={"B15523": "B15532"})
        self.assertEqual(plan, [{"electrode": "B15523", "was": NOT_TESTED,
                                 "status": RELEASED, "caused_by": "B15375",
                                 "archived_at_utc": "2026-09-24 12:04:14"}])

    def test_the_row_keeps_the_provenance_it_was_frozen_with(self):
        """Correcting caused_by as well regroups the stretches and moves the
        finalised dates on screen, so only the status is ever rewritten."""
        plan = _plan({"B16222": RELEASED},
                     {"B16222": _frozen(NEEDS_TESTING, "B16165")},
                     sources={"B16222": "B16225"})
        self.assertEqual(plan[0]["caused_by"], "B16165")

    def test_an_electrode_no_row_has_ever_named_is_inserted(self):
        plan = _plan({"B15164": SCRAPPED},
                     {"B15176": _frozen("Tested and Scrapped", "B15176",
                                        "2026-09-14 12:55:12")},
                     sources={"B15164": "B15176"})
        self.assertEqual(plan[0]["was"], None)
        self.assertEqual(plan[0]["caused_by"], "B15176")

    def test_an_inserted_row_settles_when_its_verdict_did(self):
        """Not today. The electrode was condemned the day the verdict was."""
        plan = _plan({"B15164": SCRAPPED},
                     {"B15176": _frozen("Tested and Scrapped", "B15176",
                                        "2026-09-14 12:55:12")},
                     sources={"B15164": "B15176"})
        self.assertEqual(plan[0]["archived_at_utc"], "2026-09-14 12:55:12")

    def test_it_reads_in_production_order(self):
        shown = {"B200": RELEASED, "B1000": RELEASED, "B30": RELEASED}
        archived = {m: _frozen(NOT_TESTED, "B1") for m in shown}
        plan = _plan(shown, archived, sources={m: "B9999" for m in shown})
        self.assertEqual([row["electrode"] for row in plan],
                         ["B30", "B200", "B1000"])


class TestWhatItLeavesAlone(unittest.TestCase):

    def test_an_electrodes_own_verdict_is_never_rewritten(self):
        """A real measurement outranks anything the sweeps make of it."""
        plan = _plan({"B16017": RELEASED},
                     {"B16017": _frozen(TESTED_RELEASED, "B16017")},
                     sources={"B16017": "B16017"})
        self.assertEqual(plan, [])

    def test_a_row_that_already_agrees(self):
        plan = _plan({"B15523": RELEASED},
                     {"B15523": _frozen(RELEASED, "B15375")})
        self.assertEqual(plan, [])

    def test_an_electrode_a_live_card_is_holding(self):
        plan = _plan({"B15523": RELEASED},
                     {"B15523": _frozen(NOT_TESTED, "B15375")},
                     sources={"B15523": "B15532"}, held={"B15523": "B15532"})
        self.assertEqual(plan, [])

    def test_an_electrode_on_the_board(self):
        plan = _plan({"B16773": RELEASED},
                     {"B16773": _frozen(NOT_TESTED, "B16700")},
                     sources={"B16773": "B16800"}, on_board=["B16773"])
        self.assertEqual(plan, [])

    def test_a_status_set_by_hand(self):
        """Those live in electrode_status, which is not the archive's to answer
        for, and a correction is a statement about that one electrode."""
        plan = _plan({"B15723": RELEASED},
                     {"B15723": _frozen(NOT_TESTED, "B15700")},
                     sources={"B15723": "B15800"}, by_hand=["B15723"])
        self.assertEqual(plan, [])

    def test_a_status_that_is_never_stored(self):
        shown = {"B1": ONGOING, "B2": WAITING}
        archived = {"B1": _frozen(NOT_TESTED, "B0"),
                    "B2": _frozen(NOT_TESTED, "B0")}
        self.assertEqual(_plan(shown, archived, sources={"B1": "B9", "B2": "B9"}),
                         [])

    def test_an_unrecorded_electrode_nothing_claims(self):
        """No row and no decision behind it: there is nothing to write down."""
        self.assertEqual(_plan({"B15164": SCRAPPED}, {}), [])

    def test_an_unrecorded_electrode_that_is_not_decided(self):
        self.assertEqual(
            _plan({"B15164": NOT_TESTED}, {}, sources={"B15164": "B15176"}), [])

    def test_one_placeholder_is_never_written_over_another(self):
        """Which of the two an electrode reads depends on the queueing rules,
        which differ between databases and move when a setting does. 470 rows
        on the test database were exactly this."""
        plan = _plan({"B15070": NOT_TESTED},
                     {"B15070": _frozen(NEEDS_TESTING, "B15025")},
                     sources={"B15070": "B15025"})
        self.assertEqual(plan, [])

    def test_a_settled_electrode_is_still_corrected(self):
        """The same stretch, the electrodes that did settle."""
        plan = _plan({"B15041": RELEASED},
                     {"B15041": _frozen(NEEDS_TESTING, "B15025")},
                     sources={"B15041": "B15025"})
        self.assertEqual(len(plan), 1)


class TestTheGuardRail(unittest.TestCase):

    def test_the_limit_is_far_below_the_size_of_the_archive(self):
        """It is a correction, not a rebuild. The app refuses a plan bigger
        than this rather than writing it to production."""
        self.assertLess(REPAIR_LIMIT, 1000)

    def test_every_row_carries_what_it_expects_to_find(self):
        """The procedure guards on it, so a plan without it would overwrite
        whatever a finalise has written since."""
        plan = _plan({"B15523": RELEASED, "B15525": RELEASED},
                     {"B15523": _frozen(NOT_TESTED, "B15375"),
                      "B15525": _frozen(NEEDS_TESTING, "B15375")},
                     sources={"B15523": "B15532", "B15525": "B15532"})
        self.assertEqual([row["was"] for row in plan],
                         [NOT_TESTED, NEEDS_TESTING])


class TestItWaitsForTheVats(unittest.TestCase):
    """``DbManager._stale_archive_rows`` without standing a database up.

    A sweep run without the vats is a different rule reaching different
    electrodes, so a plan made then would write one app's opinion over
    another's. It has to come back empty.
    """

    @staticmethod
    def _manager(vats, pending, cache="rows"):
        from src.data_access.db_manager import DbManager

        fake = object.__new__(DbManager)
        fake._vats = vats
        fake._vats_pending = pending
        fake._metadata_cache = cache
        return fake

    def test_no_plan_while_the_process_log_is_still_being_read(self):
        from src.data_access.db_manager import DbManager

        fake = self._manager({"B1": "28"}, True)
        self.assertEqual(DbManager._stale_archive_rows(fake), [])

    def test_no_plan_where_there_are_no_vats_at_all(self):
        from src.data_access.db_manager import DbManager

        fake = self._manager({}, False)
        self.assertEqual(DbManager._stale_archive_rows(fake), [])

    def test_no_plan_before_the_electrodes_have_been_read(self):
        from src.data_access.db_manager import DbManager

        fake = self._manager({"B1": "28"}, False, cache=None)
        self.assertEqual(DbManager._stale_archive_rows(fake), [])


if __name__ == "__main__":
    unittest.main()
