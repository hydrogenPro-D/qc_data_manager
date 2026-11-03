"""What QC wants cut, and what follows from asking for nothing.

Three settings, one per reason a request gets raised: automatic sampling, a SEM
failure and an IC failure. Each is all five coupons, the top right one, or
nothing, and nothing is not a smaller version of the other two. It turns the
reason off: no request, and around a failure no queue of neighbours either.

Two rules are worth pinning down because they are easy to state and easy to get
backwards. An electrode that failed both tests takes the STRICTER of the two
settings, not the first one looked at. And suppressing the queue must not
suppress the margin: the electrodes beside a scrap are condemned by the fault,
which is a statement about what happened rather than a request for work.
"""

import unittest

from src.data_access.electrodes.electrode_columns import ElectrodeStatus
from src.data_access.electrodes.status_propagation import propagate_detailed
from src.data_access.qc_requests.coupon_columns import (
    ALL_FIVE,
    DEFAULTS,
    NONE,
    REASON_IC,
    REASON_SEM,
    TOP_RIGHT,
    for_failure,
    normalize,
    strictest,
    wanted,
    with_defaults,
)

SCRAPPED = str(ElectrodeStatus.TESTED_AND_SCRAPPED)
NEEDS_TESTING = str(ElectrodeStatus.NEEDS_TESTING)
SCRAP_SPREAD = str(ElectrodeStatus.SCRAPPED)
NOT_TESTED = str(ElectrodeStatus.NOT_TESTED)

MARKINGS = [f"B{13820 + n}" for n in range(24)]
VATS = {marking: "29" for marking in MARKINGS}
FAILURE = "B13830"


def swept(unqueued=()) -> dict:
    return propagate_detailed(MARKINGS, {}, {FAILURE: SCRAPPED}, vats=VATS,
                              unqueued=set(unqueued))[0]


class TestReadingAValue(unittest.TestCase):
    """Tolerant on the way in, for the same reason the other application is."""

    def test_the_spellings_the_other_application_may_send(self) -> None:
        for text in ("top_right", "top-right", "Top Right", "TOPRIGHT"):
            self.assertEqual(normalize(text), TOP_RIGHT, text)
        for text in ("all_five", "all-five", "All Five", "all5"):
            self.assertEqual(normalize(text), ALL_FIVE, text)

    def test_anything_unrecognised_reads_as_nothing(self) -> None:
        """Not as an error. A value nobody can make sense of is not a reason
        to refuse to draw the screen."""
        for text in ("", None, "bottom_left", "???"):
            self.assertEqual(normalize(text), NONE, repr(text))

    def test_missing_settings_fall_back_to_what_qc_does_today(self) -> None:
        self.assertEqual(with_defaults({}), DEFAULTS)
        self.assertEqual(with_defaults({REASON_IC: ALL_FIVE})[REASON_IC],
                         ALL_FIVE)


class TestWhichRuleWinsWhenTwoApply(unittest.TestCase):
    """The stricter, where strictness runs nothing, top right, all five."""

    def test_the_stricter_of_two(self) -> None:
        self.assertEqual(strictest(NONE, TOP_RIGHT), TOP_RIGHT)
        self.assertEqual(strictest(TOP_RIGHT, ALL_FIVE), ALL_FIVE)
        self.assertEqual(strictest(NONE, ALL_FIVE), ALL_FIVE)

    def test_it_does_not_depend_on_the_order_asked(self) -> None:
        self.assertEqual(strictest(ALL_FIVE, NONE), strictest(NONE, ALL_FIVE))

    def test_both_tests_failed_takes_the_stricter_setting(self) -> None:
        """The case the rule exists for: IC asks for nothing and SEM asks for
        the top right, and an electrode that failed both is still worth
        cutting."""
        settings = {REASON_SEM: TOP_RIGHT, REASON_IC: NONE}
        self.assertEqual(for_failure(True, True, settings), TOP_RIGHT)

    def test_one_test_failed_takes_that_test_s_setting(self) -> None:
        settings = {REASON_SEM: TOP_RIGHT, REASON_IC: NONE}
        self.assertEqual(for_failure(True, False, settings), TOP_RIGHT)
        self.assertEqual(for_failure(False, True, settings), NONE)

    def test_neither_recorded_takes_the_stricter_of_the_two(self) -> None:
        """Scrapped for some other reason. Nothing says which rule applies, so
        it asks for more rather than silently for nothing."""
        settings = {REASON_SEM: TOP_RIGHT, REASON_IC: NONE}
        self.assertEqual(for_failure(False, False, settings), TOP_RIGHT)

    def test_nothing_is_the_only_value_that_means_do_not_ask(self) -> None:
        self.assertFalse(wanted(NONE))
        self.assertTrue(wanted(TOP_RIGHT))
        self.assertTrue(wanted(ALL_FIVE))


class TestSuppressingTheQueue(unittest.TestCase):
    """A failure QC wants nothing from marks no neighbours for testing."""

    def test_normally_the_failure_queues_the_electrodes_past_its_margin(self) -> None:
        """The behaviour this is a condition on, so it cannot quietly go."""
        effective = swept()
        self.assertIn(NEEDS_TESTING, set(effective.values()))

    def test_unqueued_leaves_nobody_needing_testing(self) -> None:
        effective = swept(unqueued=[FAILURE])
        self.assertNotIn(NEEDS_TESTING, set(effective.values()))

    def test_the_margin_is_condemned_either_way(self) -> None:
        """The electrodes beside it are condemned by the fault. That is what
        happened, not a request for work, so it is not QC's to turn off."""
        margin = {marking for marking, status in swept().items()
                  if status == SCRAP_SPREAD}
        self.assertTrue(margin)
        self.assertEqual(
            {marking for marking, status in swept(unqueued=[FAILURE]).items()
             if status == SCRAP_SPREAD},
            margin)

    def test_the_failure_itself_still_reads_as_one(self) -> None:
        self.assertEqual(swept(unqueued=[FAILURE])[FAILURE], SCRAPPED)

    def test_everything_past_the_margin_reads_not_tested(self) -> None:
        effective = swept(unqueued=[FAILURE])
        beyond = {status for marking, status in effective.items()
                  if status not in (SCRAPPED, SCRAP_SPREAD)}
        self.assertEqual(beyond, {NOT_TESTED})


if __name__ == "__main__":
    unittest.main()
