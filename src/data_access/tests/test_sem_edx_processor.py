"""SEM EDX processor — pure data in, data out, no CSV and no Qt.

The point of these: anodes and cathodes measure different elements, so the same
code has to produce different frames from the layout it is handed.
"""

import unittest

import pandas as pd

from src.data_access.sem_edx.sem_edx_columns import (
    SemEdxColumns,
    Quantity,
    average_column,
    electrode_average_column,
    element_column,
    element_columns,
    element_symbol,
    point_column,
)
from src.data_access.sem_edx.sem_edx_processor import SemEdxProcessor, marking_number

ANODE_LAYOUT = ["Ni", "S", "Ni:S", "Co", "Ni:Co"]
CATHODE_LAYOUT = ["Ni", "S", "Ni:S", "Co", "Ni:Co", "C", "Ni:C", "N", "Ni:N", "S:N"]

NI = element_column("Ni")
S = element_column("S")
CO = element_column("Co")
CARBON = element_column("C")
NITROGEN = element_column("N")
ELECTRODE = str(SemEdxColumns.ELECTRODE)
POSITION = str(SemEdxColumns.POSITION)


def measurement(electrode="B14020", position="top right", point=1,
                electrode_type="CATHODE", operator="Tamas Pakai",
                note="Microcracks", zoom="x100", **elements) -> dict:
    """One row of the measurement frame. Elements are passed by symbol."""
    row = {
        ELECTRODE: electrode,
        str(SemEdxColumns.ELECTRODE_TYPE): electrode_type,
        POSITION: position,
        str(SemEdxColumns.ZOOM): zoom,
        str(SemEdxColumns.MEASUREMENT): point,
        str(SemEdxColumns.OPERATOR): operator,
        str(SemEdxColumns.NOTE): note,
        str(SemEdxColumns.DATE): "",
        str(SemEdxColumns.FOLDER): "",
    }
    defaults = {"Ni": 30.0, "S": 10.0, "Co": 1.0}
    defaults.update(elements)
    row.update({element_column(symbol): value for symbol, value in defaults.items()})
    return row


class TestQuantity(unittest.TestCase):
    """A quantity is named the way the workbook names it."""

    def test_an_element_maps_to_its_column(self) -> None:
        self.assertFalse(Quantity("Ni").is_ratio)
        self.assertEqual(Quantity("Ni").column, "ni_pct")
        self.assertEqual(Quantity("Ni").elements, ("Ni",))

    def test_a_ratio_maps_to_its_column_and_both_elements(self) -> None:
        self.assertTrue(Quantity("S:N").is_ratio)
        self.assertEqual(Quantity("S:N").column, "s_n")
        self.assertEqual(Quantity("S:N").elements, ("S", "N"))

    def test_a_symbol_survives_the_round_trip(self) -> None:
        for symbol in ("Ni", "S", "Co", "C", "N", "Cl"):
            self.assertEqual(element_symbol(element_column(symbol)), symbol)

    def test_a_column_that_is_not_an_element_has_no_symbol(self) -> None:
        self.assertIsNone(element_symbol(str(SemEdxColumns.OPERATOR)))

    def test_a_ratio_pulls_in_elements_the_layout_never_lists(self) -> None:
        """S:N needs nitrogen even on a sheet with no 'N atomic %' column."""
        self.assertIn(element_column("N"), element_columns(["Ni", "S", "S:N"]))


class TestSummaryColumns(unittest.TestCase):
    """The layout decides which columns exist, and in what order."""

    def test_the_sheets_produce_different_columns(self) -> None:
        anode = SemEdxProcessor(ANODE_LAYOUT).summary_columns()
        cathode = SemEdxProcessor(CATHODE_LAYOUT).summary_columns()
        self.assertNotIn(average_column(CARBON), anode)
        self.assertIn(average_column(CARBON), cathode)
        self.assertLess(len(anode), len(cathode))

    def test_an_element_gets_its_points_then_its_average(self) -> None:
        columns = SemEdxProcessor(ANODE_LAYOUT).summary_columns()
        start = columns.index(point_column(NI, 1))
        self.assertEqual(columns[start:start + 4],
                         [point_column(NI, 1), point_column(NI, 2),
                          point_column(NI, 3), average_column(NI)])

    def test_a_ratio_carries_only_its_average(self) -> None:
        columns = SemEdxProcessor(CATHODE_LAYOUT).summary_columns()
        self.assertIn(average_column("ni_s"), columns)
        self.assertNotIn(point_column("ni_s", 1), columns)

    def test_the_fixed_columns_bracket_the_measured_ones(self) -> None:
        columns = SemEdxProcessor(ANODE_LAYOUT).summary_columns()
        self.assertEqual(columns[:4], [ELECTRODE, str(SemEdxColumns.ELECTRODE_TYPE),
                                       POSITION, str(SemEdxColumns.ZOOM)])
        # The per electrode average sits where the workbook puts it, after the
        # operator and before the note.
        self.assertEqual(columns[-6:], [str(SemEdxColumns.OPERATOR),
                                        electrode_average_column("ni_s"),
                                        str(SemEdxColumns.DATE),
                                        str(SemEdxColumns.FOLDER),
                                        str(SemEdxColumns.IMAGES),
                                        str(SemEdxColumns.NOTE)])


class TestProcess(unittest.TestCase):
    """One row per sample: each element's points, then its average."""

    def setUp(self) -> None:
        self.processor = SemEdxProcessor(CATHODE_LAYOUT)

    def test_empty_input_keeps_the_columns(self) -> None:
        out = self.processor.process(pd.DataFrame())
        self.assertTrue(out.empty)
        self.assertEqual(list(out.columns), self.processor.summary_columns())

    def test_none_input_keeps_the_columns(self) -> None:
        self.assertEqual(list(self.processor.process(None).columns),
                         self.processor.summary_columns())

    def test_measurements_collapse_to_one_row_per_sample(self) -> None:
        out = self.processor.process(pd.DataFrame([
            measurement(point=1, Ni=30.0), measurement(point=2, Ni=32.0),
            measurement(point=3, Ni=34.0),
        ]))
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out.iloc[0][average_column(NI)], 32.0)

    def test_every_point_is_kept_in_its_own_column(self) -> None:
        out = self.processor.process(pd.DataFrame([
            measurement(point=1, Ni=30.0), measurement(point=2, Ni=32.0),
            measurement(point=3, Ni=34.0),
        ]))
        self.assertEqual([out.iloc[0][point_column(NI, p)] for p in (1, 2, 3)],
                         [30.0, 32.0, 34.0])

    def test_the_newer_elements_are_carried_too(self) -> None:
        out = self.processor.process(pd.DataFrame([
            measurement(point=1, C=29.0, N=11.5),
            measurement(point=2, C=31.5, N=12.7),
        ]))
        self.assertAlmostEqual(out.iloc[0][average_column(CARBON)], 30.25)
        self.assertAlmostEqual(out.iloc[0][average_column(NITROGEN)], 12.1)

    def test_a_missing_point_reads_as_blank_rather_than_shifting(self) -> None:
        out = self.processor.process(pd.DataFrame([
            measurement(point=1, Ni=30.0), measurement(point=2, Ni=32.0),
        ]))
        self.assertTrue(pd.isna(out.iloc[0][point_column(NI, 3)]))

    def test_a_fourth_point_gets_its_own_column(self) -> None:
        out = self.processor.process(pd.DataFrame([
            measurement(point=p, Ni=30.0 + p) for p in (1, 2, 3, 4)]))
        self.assertIn(point_column(NI, 4), out.columns)
        self.assertEqual(out.iloc[0][point_column(NI, 4)], 34.0)

    def test_a_position_is_its_own_sample(self) -> None:
        out = self.processor.process(pd.DataFrame([
            measurement(position="top right"), measurement(position="center"),
        ]))
        self.assertEqual(len(out), 2)
        self.assertEqual(set(out[POSITION]), {"top right", "center"})

    def test_the_ratio_average_averages_the_ratios_like_the_workbook(self) -> None:
        """Averaging the percentages first would give 2.857, the sheet gives 3.25."""
        out = self.processor.process(pd.DataFrame([
            measurement(point=1, Ni=20.0, S=5.0),     # 4.0
            measurement(point=2, Ni=40.0, S=16.0),    # 2.5
        ]))
        self.assertAlmostEqual(out.iloc[0][average_column("ni_s")], 3.25)

    def test_a_ratio_between_two_non_nickel_elements(self) -> None:
        out = self.processor.process(pd.DataFrame([measurement(S=6.0, N=12.0)]))
        self.assertAlmostEqual(out.iloc[0][average_column("s_n")], 0.5)

    def test_a_zero_denominator_gives_no_ratio_rather_than_an_error(self) -> None:
        out = self.processor.process(pd.DataFrame([measurement(Co=0.0)]))
        self.assertTrue(pd.isna(out.iloc[0][average_column("ni_co")]))

    def test_an_element_the_rows_lack_reads_as_blank(self) -> None:
        """Anode rows carry no carbon, so a cathode layout over them is blank."""
        anode_rows = pd.DataFrame([measurement(electrode_type="ANODE")])
        out = self.processor.process(anode_rows)
        self.assertTrue(pd.isna(out.iloc[0][average_column(CARBON)]))

    def test_per_sample_fields_are_carried_through(self) -> None:
        out = self.processor.process(pd.DataFrame([
            measurement(point=1), measurement(point=2),
        ]))
        self.assertEqual(out.iloc[0][str(SemEdxColumns.OPERATOR)], "Tamas Pakai")
        self.assertEqual(out.iloc[0][str(SemEdxColumns.NOTE)], "Microcracks")

    def test_newest_electrode_last(self) -> None:
        out = self.processor.process(pd.DataFrame([
            measurement(electrode="B999"), measurement(electrode="B14020"),
            measurement(electrode="B13871"),
        ]))
        self.assertEqual(list(out[ELECTRODE]), ["B999", "B13871", "B14020"])

    def test_samples_keep_their_own_values_after_the_sort(self) -> None:
        """Widening pivots on the keys, which sorts them, so the rows have to be
        put back in order without their values following the old positions."""
        out = self.processor.process(pd.DataFrame([
            measurement(electrode="B300", Ni=10.0), measurement(electrode="B100", Ni=20.0),
            measurement(electrode="B200", Ni=30.0),
        ]))
        values = dict(zip(out[ELECTRODE], out[point_column(NI, 1)]))
        self.assertEqual(values, {"B100": 20.0, "B200": 30.0, "B300": 10.0})


class TestMeasurements(unittest.TestCase):
    """The individual points behind one sample."""

    def setUp(self) -> None:
        self.processor = SemEdxProcessor(ANODE_LAYOUT)
        self.df = pd.DataFrame([
            measurement(electrode="B14020", position="top right", point=1, Ni=30.0),
            measurement(electrode="B14020", position="top right", point=2, Ni=33.0),
            measurement(electrode="B14020", position="center", point=1, Ni=20.0),
            measurement(electrode="B13871", position="top right", point=1, Ni=10.0),
        ])

    def test_returns_only_the_asked_for_sample(self) -> None:
        out = self.processor.measurements(self.df, "B14020", "top right")
        self.assertEqual(len(out), 2)
        self.assertEqual(list(out[str(SemEdxColumns.MEASUREMENT)]), [1, 2])

    def test_ratios_are_per_measurement_here(self) -> None:
        out = self.processor.measurements(self.df, "B14020", "top right")
        self.assertAlmostEqual(out.iloc[0]["ni_s"], 3.0)
        self.assertAlmostEqual(out.iloc[1]["ni_s"], 3.3)

    def test_only_the_layout_s_quantities_come_back(self) -> None:
        out = self.processor.measurements(self.df, "B14020", "top right")
        self.assertEqual(list(out.columns), self.processor.detail_columns())
        self.assertNotIn(CARBON, out.columns)

    def test_an_unknown_sample_gives_an_empty_frame_with_columns(self) -> None:
        out = self.processor.measurements(self.df, "B99999", "top right")
        self.assertTrue(out.empty)
        self.assertEqual(list(out.columns), self.processor.detail_columns())

    def test_empty_input_is_safe(self) -> None:
        self.assertTrue(self.processor.measurements(pd.DataFrame(), "B1", "x").empty)


class TestMarkingNumber(unittest.TestCase):
    """Markings sort by their number, not as the strings they render as."""

    def test_digits_are_read_out_of_the_marking(self) -> None:
        self.assertEqual(marking_number("B14020"), 14020)

    def test_a_marking_without_digits_sorts_last(self) -> None:
        self.assertEqual(marking_number("unknown"), -1)


if __name__ == "__main__":
    unittest.main()
