"""SEM EDX tab — the sub tab nesting, rendering and the detail hand off."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from src.data_access.sem_edx.sem_edx_columns import (
    SemEdxColumns,
    average_column,
    element_column,
    point_column,
)
from src.gui.sem_edx_gui import (
    GEN4_1_LABEL,
    ImagePickerDialog,
    ImportSummaryDialog,
    QuantityNames,
    SemEdxTypeWidget,
    SemSampleDialog,
    SemImageViewerDialog,
    SemEdxWidget,
    FILTER_OPERATOR,
    FILTER_POSITION,
    format_number,
    type_label,
)

_app = QApplication.instance() or QApplication([])


class _ParsedStub:
    """Stands in for a ParsedFolder, so the tab can be tested without one."""

    def __init__(self) -> None:
        self.measurements: list = []
        self.problems: list = []
        self.electrode_types: dict = {}


class _Measured:
    """The two fields the import summary reads off a measurement."""

    def __init__(self, electrode: str, position: str) -> None:
        self.electrode = electrode
        self.position = position

ANODE_LAYOUT = ["Ni", "S", "Ni:S", "Co", "Ni:Co"]
CATHODE_LAYOUT = ["Ni", "S", "Ni:S", "Co", "Ni:Co", "C", "Ni:C", "N", "Ni:N", "S:N"]
LAYOUT = {"ANODE": ANODE_LAYOUT, "CATHODE": CATHODE_LAYOUT}
# Gen 3.1 measures less: no cobalt, and no carbon or nitrogen either.
GEN3_1 = "Gen3.1"
GEN3_1_LAYOUT = {"ANODE": ["Ni", "S", "Ni:S"], "CATHODE": ["Ni", "S", "Ni:S"]}
LAYOUTS = {GEN3_1: GEN3_1_LAYOUT, GEN4_1_LABEL: LAYOUT}

NI = element_column("Ni")
S = element_column("S")


def sample(electrode="B14020", position="top right", electrode_type="ANODE",
           ni=(30.0, 31.0, 32.0), sulphur=(10.0, 10.0, 10.0),
           cobalt=(1.0, 1.0, 1.0), operator="Tamas Pakai", folder="",
           images="") -> dict:
    """One row of the summary frame the processor hands a type widget."""
    row = {
        str(SemEdxColumns.ELECTRODE): electrode,
        str(SemEdxColumns.ELECTRODE_TYPE): electrode_type,
        str(SemEdxColumns.POSITION): position,
        str(SemEdxColumns.ZOOM): "x100",
    }
    for symbol, values in (("Ni", ni), ("S", sulphur), ("Co", cobalt)):
        column = element_column(symbol)
        for point, value in enumerate(values, start=1):
            row[point_column(column, point)] = value
        row[average_column(column)] = sum(values) / len(values)
    # A ratio carries only its average, matching what the processor emits.
    for name, top, bottom in (("ni_s", ni, sulphur), ("ni_co", ni, cobalt)):
        ratios = [a / b for a, b in zip(top, bottom)]
        row[average_column(name)] = sum(ratios) / len(ratios)
    row[str(SemEdxColumns.OPERATOR)] = operator
    row[str(SemEdxColumns.DATE)] = "2026-07-27"
    row[str(SemEdxColumns.FOLDER)] = folder
    row[str(SemEdxColumns.IMAGES)] = images
    row[str(SemEdxColumns.NOTE)] = "Microcracks"
    return row


class TestQuantityNames(unittest.TestCase):
    """Headers and rounding are derived from the layout, not written down."""

    def setUp(self) -> None:
        self.names = QuantityNames(CATHODE_LAYOUT)

    def test_a_point_column_is_named_after_its_element(self) -> None:
        self.assertEqual(self.names.display_name(point_column(NI, 2)), "Ni 2")
        self.assertEqual(self.names.display_name(average_column(NI)), "Ni avg")

    def test_a_ratio_average_needs_no_qualifier(self) -> None:
        """It is the only column that ratio has, so "average" would be noise."""
        self.assertEqual(self.names.display_name(average_column("ni_s")), "Ni:S")
        self.assertEqual(self.names.display_name(average_column("s_n")), "S:N")

    def test_a_stored_column_uses_its_display_map_entry(self) -> None:
        self.assertEqual(self.names.display_name(str(SemEdxColumns.POSITION)),
                         "Sample location")
        self.assertEqual(self.names.display_name(str(SemEdxColumns.FOLDER)),
                         "SEM images")

    def test_the_longest_base_wins_the_match(self) -> None:
        """ni_pct must not be read as a suffix of another quantity."""
        self.assertEqual(self.names.split(point_column(NI, 1))[0].name, "Ni")
        self.assertEqual(self.names.split(average_column("ni_co"))[0].name, "Ni:Co")

    def test_a_column_that_is_not_a_quantity_has_no_parts(self) -> None:
        self.assertIsNone(self.names.split(str(SemEdxColumns.OPERATOR)))

    def test_percentages_and_ratios_round_differently(self) -> None:
        self.assertEqual(self.names.decimals(average_column(NI)), 1)
        self.assertEqual(self.names.decimals(average_column("ni_s")), 2)
        self.assertIsNone(self.names.decimals(str(SemEdxColumns.OPERATOR)))

    def test_an_anode_layout_does_not_know_carbon(self) -> None:
        anode = QuantityNames(ANODE_LAYOUT)
        self.assertIsNone(anode.split(average_column(element_column("C"))))


class TestSemEdxTypeWidget(unittest.TestCase):
    """One electrode type's table: renders what it is given, filters combine."""

    def setUp(self) -> None:
        self.widget = SemEdxTypeWidget(generation=GEN4_1_LABEL,
                                       electrode_type="ANODE",
                                       layout=ANODE_LAYOUT)

    def tearDown(self) -> None:
        self.widget.deleteLater()

    def _headers(self) -> list[str]:
        return [self.widget.table.horizontalHeaderItem(i).text()
                for i in range(self.widget.table.columnCount())]

    def _cell(self, row: int, column: str) -> str:
        return self.widget.table.item(row, self._headers().index(column)).text()

    def test_starts_empty(self) -> None:
        self.assertEqual(self.widget.table.rowCount(), 0)
        self.assertTrue(self.widget.get_data().empty)

    def test_renders_a_frame_under_the_display_names(self) -> None:
        self.widget.load_data(pd.DataFrame([sample(), sample(electrode="B14023")]))
        self.assertEqual(self.widget.table.rowCount(), 2)
        self.assertIn("Electrode", self._headers())
        self.assertNotIn(str(SemEdxColumns.ELECTRODE), self._headers())

    def test_every_point_gets_its_own_column(self) -> None:
        self.widget.load_data(pd.DataFrame([sample(ni=(30.0, 31.0, 32.0))]))
        for point, value in ((1, "30.0"), (2, "31.0"), (3, "32.0")):
            self.assertEqual(self._cell(0, f"Ni {point}"), value)
        self.assertEqual(self._cell(0, "Ni avg"), "31.0")

    def test_a_ratio_has_one_column_not_one_per_point(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]))
        self.assertIn("Ni:S", self._headers())
        self.assertNotIn("Ni:S 1", self._headers())

    def test_an_anode_table_has_no_carbon_columns(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]))
        self.assertNotIn("C avg", self._headers())
        self.assertNotIn("Ni:C", self._headers())

    def test_percentages_and_ratios_are_rounded_differently(self) -> None:
        self.widget.load_data(pd.DataFrame([
            sample(ni=(33.333,) * 3, sulphur=(9.5,) * 3)]))
        self.assertEqual(self._cell(0, "Ni avg"), "33.3")
        self.assertEqual(self._cell(0, "Ni:S"), "3.51")

    def test_reload_with_an_empty_frame_clears_the_table(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]))
        self.widget.load_data(pd.DataFrame())
        self.assertEqual(self.widget.table.rowCount(), 0)

    def test_none_is_treated_as_empty(self) -> None:
        self.widget.load_data(None)
        self.assertEqual(self.widget.table.rowCount(), 0)

    def test_a_dropdown_narrows_the_table(self) -> None:
        self.widget.load_data(pd.DataFrame([
            sample(electrode="B14020", position="top right"),
            sample(electrode="B13871", position="center"),
        ]))
        combo = self.widget.filter_bar._combos[FILTER_POSITION]
        combo.setCurrentIndex(combo.findData("center"))
        self.assertEqual(self.widget.table.rowCount(), 1)
        self.assertEqual(self._cell(0, "Electrode"), "B13871")

    def test_filters_combine(self) -> None:
        self.widget.load_data(pd.DataFrame([
            sample(electrode="B14020", position="center", operator="Emil Andersen"),
            sample(electrode="B14023", position="center", operator="Tamas Pakai"),
        ]))
        for key, value in ((FILTER_POSITION, "center"),
                           (FILTER_OPERATOR, "Tamas Pakai")):
            combo = self.widget.filter_bar._combos[key]
            combo.setCurrentIndex(combo.findData(value))
        self.assertEqual(self.widget.table.rowCount(), 1)
        self.assertEqual(self._cell(0, "Electrode"), "B14023")

    def test_the_note_column_is_never_truncated(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]))
        self.assertEqual(self.widget.table.uncapped_columns, {"Notes"})

    def test_a_row_without_a_folder_shows_a_dash_not_a_button(self) -> None:
        self.widget.load_data(pd.DataFrame([sample(folder="")]))
        column = self._headers().index("SEM images")
        self.assertIsNone(self.widget.table.cellWidget(0, column))
        self.assertEqual(self._cell(0, "SEM images"), "—")

    def test_a_row_with_a_folder_gets_a_button(self) -> None:
        self.widget.load_data(pd.DataFrame([sample(folder=r"C:\results\B14050")]))
        column = self._headers().index("SEM images")
        self.assertIsNotNone(self.widget.table.cellWidget(0, column))

    def test_detail_is_asked_for_by_type_electrode_and_position(self) -> None:
        asked = []
        self.widget.on_get_measurements = (
            lambda g, t, e, p: asked.append((g, t, e, p)) or pd.DataFrame())
        self.widget.load_data(pd.DataFrame([sample(electrode="B13951", position="center")]))
        # The dialog is modal, so it is stubbed: what matters here is which
        # sample the tab asked the backend for.
        with patch("src.gui.sem_edx_gui.SemSampleDialog") as dialog:
            self.widget._on_row_activated(0, 0)
        self.assertEqual(asked, [(GEN4_1_LABEL, "ANODE", "B13951", "center")])
        dialog.return_value.exec.assert_called_once()

    def test_a_failing_detail_callback_does_not_propagate(self) -> None:
        def boom(_generation, _type, _electrode, _position):
            raise RuntimeError("no file")

        self.widget.on_get_measurements = boom
        self.widget.load_data(pd.DataFrame([sample()]))
        with patch("src.gui.sem_edx_gui.SemSampleDialog"):
            self.widget._on_row_activated(0, 0)   # must not raise

    def test_activating_a_row_that_is_gone_is_a_noop(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]))
        with patch("src.gui.sem_edx_gui.SemSampleDialog") as dialog:
            self.widget._on_row_activated(99, 0)
        dialog.assert_not_called()

    def test_the_viewer_is_handed_the_recorded_filenames(self) -> None:
        """The row records which captures are its own, so the viewer never has
        to guess from a folder holding every position."""
        asked = []
        self.widget.on_list_images = (
            lambda folder, position, images:
            asked.append((folder, position, images)) or [])
        self.widget.load_data(pd.DataFrame([sample(
            position="bottom left", folder=r"C:\results\B14175",
            images="B14175_BL_1.tif|B14175_BL_2.tif")]))
        with patch("src.gui.sem_edx_gui.ValidationErrorScrollDialog"):
            self.widget._on_show_images(0)
        self.assertEqual(asked, [(r"C:\results\B14175", "bottom left",
                                  "B14175_BL_1.tif|B14175_BL_2.tif")])

    def test_the_filenames_are_never_a_column(self) -> None:
        self.widget.load_data(pd.DataFrame([sample(images="a.tif|b.tif")]))
        self.assertNotIn("images", [h.lower() for h in self._headers()])

    def test_the_viewer_is_not_opened_without_images(self) -> None:
        self.widget.on_list_images = lambda *_args: []
        self.widget.load_data(pd.DataFrame([sample(folder=r"C:\gone")]))
        with patch("src.gui.sem_edx_gui.SemImageViewerDialog") as viewer, \
                patch("src.gui.sem_edx_gui.ValidationErrorScrollDialog") as report:
            self.widget._on_show_images(0)
        viewer.assert_not_called()
        report.return_value.exec.assert_called_once()


class TestSemEdxWidget(unittest.TestCase):
    """Generation outside, electrode type inside."""

    def setUp(self) -> None:
        self.widget = SemEdxWidget(layouts=LAYOUTS)

    def tearDown(self) -> None:
        self.widget.deleteLater()

    def test_the_generations_are_in_order_oldest_first(self) -> None:
        tabs = self.widget.generation_tabs
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())],
                         [GEN3_1, GEN4_1_LABEL])

    def test_each_electrode_type_gets_a_sub_tab(self) -> None:
        tabs = self.widget.generations[GEN4_1_LABEL].sub_tabs
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())],
                         ["Anodes", "Cathodes"])

    def test_the_generations_build_different_columns(self) -> None:
        """Gen 3.1 measures no cobalt, so its table has no cobalt columns."""
        for generation in (GEN3_1, GEN4_1_LABEL):
            self.widget.load_data(pd.DataFrame([sample()]), "ANODE", generation)
        table = self.widget.generations[GEN3_1].types["ANODE"].table
        headers = [table.horizontalHeaderItem(i).text()
                   for i in range(table.columnCount())]
        self.assertNotIn("Co avg", headers)
        self.assertNotIn("Ni:Co", headers)

        table = self.widget.generations[GEN4_1_LABEL].types["ANODE"].table
        headers = [table.horizontalHeaderItem(i).text()
                   for i in range(table.columnCount())]
        self.assertIn("Co avg", headers)

    def test_the_type_is_never_a_column(self) -> None:
        """The sub tab you are looking at already says anode or cathode."""
        self.widget.load_data(pd.DataFrame([sample()]), "ANODE", GEN4_1_LABEL)
        table = self.widget.generations[GEN4_1_LABEL].types["ANODE"].table
        headers = [table.horizontalHeaderItem(i).text()
                   for i in range(table.columnCount())]
        self.assertNotIn("Type", headers)

    def test_load_data_reaches_the_right_generation_and_type(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]), "ANODE", GEN4_1_LABEL)
        gen4 = self.widget.generations[GEN4_1_LABEL]
        self.assertEqual(gen4.types["ANODE"].table.rowCount(), 1)
        self.assertEqual(gen4.types["CATHODE"].table.rowCount(), 0)
        self.assertEqual(
            self.widget.generations[GEN3_1].types["ANODE"].table.rowCount(), 0)
        self.assertEqual(len(self.widget.get_data("ANODE", GEN4_1_LABEL)), 1)

    def test_no_generation_named_means_the_first(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]), "ANODE")
        self.assertEqual(
            self.widget.generations[GEN3_1].types["ANODE"].table.rowCount(), 1)

    def test_an_unknown_type_is_ignored(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]), "MM1", GEN4_1_LABEL)
        self.assertEqual(
            self.widget.generations[GEN4_1_LABEL].types["ANODE"].table.rowCount(), 0)
        self.assertTrue(self.widget.get_data("MM1", GEN4_1_LABEL).empty)

    def test_an_unknown_generation_is_ignored(self) -> None:
        self.widget.load_data(pd.DataFrame([sample()]), "ANODE", generation="Gen3")
        self.assertEqual(self.widget.generations[GEN4_1_LABEL].types["ANODE"].table.rowCount(), 0)

    def test_a_change_in_a_type_reaches_the_tab(self) -> None:
        seen = []
        self.widget.data_changed.connect(lambda: seen.append(True))
        self.widget.generations[GEN4_1_LABEL].types["CATHODE"].data_changed.emit()
        self.assertEqual(len(seen), 1)

    def test_one_import_button_serves_both_types(self) -> None:
        """A folder's type is looked up, not chosen, so the button belongs to
        the generation rather than to a type tab."""
        generation = self.widget.generations[GEN4_1_LABEL]
        self.assertIsNotNone(generation.add_button)
        for widget in generation.types.values():
            self.assertFalse(hasattr(widget, "add_button"))

    def test_the_import_is_handed_ways_to_ask_the_operator(self) -> None:
        """Two things the folder cannot answer: the type when the database has
        not heard of the electrode, and which captures belong to the sample."""
        asked = []
        self.widget.generations[GEN4_1_LABEL].on_import_folder = (
            lambda generation, folder, ask_type, pick:
            asked.append((generation, folder, callable(ask_type),
                          callable(pick)))
            or (_ParsedStub(), []))
        with patch("src.gui.sem_edx_gui.QFileDialog.getExistingDirectory",
                   return_value=r"C:\results\B14050"), \
                patch("src.gui.sem_edx_gui.ImportSummaryDialog"):
            self.widget.generations[GEN4_1_LABEL]._on_add_clicked()
        self.assertEqual(asked, [(GEN4_1_LABEL, r"C:\results\B14050", True, True)])

    def test_a_cancelled_picker_keeps_the_suggestion(self) -> None:
        with patch("src.gui.sem_edx_gui.ImagePickerDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            picked = self.widget.generations[GEN4_1_LABEL]._pick_images(
                "B14050", "top right", [], ["a.tif"])
        self.assertIsNone(picked)

    def test_the_picker_returns_what_was_ticked(self) -> None:
        with patch("src.gui.sem_edx_gui.ImagePickerDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.chosen = ["a.tif", "b.tif"]
            picked = self.widget.generations[GEN4_1_LABEL]._pick_images(
                "B14050", "top right", [], ["a.tif"])
        self.assertEqual(picked, ["a.tif", "b.tif"])

    def test_the_operator_is_asked_for_and_stamped_on_what_was_imported(self) -> None:
        """Nothing in the export names them, so the summary collects it once
        and applies it to everything just added."""
        result = _ParsedStub()
        result.measurements = [_Measured("B14134", "top right") for _ in range(3)]
        self.widget.generations[GEN4_1_LABEL].on_import_folder = lambda *_a: (result, [])

        saved = []
        self.widget.generations[GEN4_1_LABEL].on_update_sample = (
            lambda g, e, p, values: saved.append((g, e, p, values)))

        with patch("src.gui.sem_edx_gui.QFileDialog.getExistingDirectory",
                   return_value=r"C:\results\B14134"), \
                patch("src.gui.sem_edx_gui.ImportSummaryDialog") as dialog:
            dialog.return_value.operator = "Tamas Pakai"
            self.widget.generations[GEN4_1_LABEL]._on_add_clicked()

        # Once per sample, not once per measurement.
        self.assertEqual(saved, [(GEN4_1_LABEL, "B14134", "top right",
                                  {str(SemEdxColumns.OPERATOR): "Tamas Pakai"})])
        self.assertTrue(dialog.call_args.kwargs["ask_operator"])

    def test_an_empty_operator_stamps_nothing(self) -> None:
        result = _ParsedStub()
        result.measurements = [_Measured("B14134", "top right")]
        self.widget.generations[GEN4_1_LABEL].on_import_folder = lambda *_a: (result, [])

        saved = []
        self.widget.generations[GEN4_1_LABEL].on_update_sample = (
            lambda *args: saved.append(args))
        with patch("src.gui.sem_edx_gui.QFileDialog.getExistingDirectory",
                   return_value=r"C:\results\B14134"), \
                patch("src.gui.sem_edx_gui.ImportSummaryDialog") as dialog:
            dialog.return_value.operator = ""
            self.widget.generations[GEN4_1_LABEL]._on_add_clicked()
        self.assertEqual(saved, [])

    def test_the_operator_is_not_asked_for_when_nothing_was_added(self) -> None:
        self.widget.generations[GEN4_1_LABEL].on_import_folder = lambda *_a: (_ParsedStub(), [])
        with patch("src.gui.sem_edx_gui.QFileDialog.getExistingDirectory",
                   return_value=r"C:\results\B14134"), \
                patch("src.gui.sem_edx_gui.ImportSummaryDialog") as dialog:
            self.widget.generations[GEN4_1_LABEL]._on_add_clicked()
        self.assertFalse(dialog.call_args.kwargs["ask_operator"])

    def test_an_import_switches_to_the_tab_it_landed_on(self) -> None:
        """The type is looked up, so importing a cathode while looking at
        Anodes otherwise leaves an unchanged table on screen."""
        generation = self.widget.generations[GEN4_1_LABEL]
        generation.sub_tabs.setCurrentWidget(generation.types["ANODE"])
        self.widget.load_data(
            pd.DataFrame([sample(electrode="B14050", electrode_type="CATHODE")]),
            "CATHODE", GEN4_1_LABEL)

        generation._reveal([("B14050", "top right")], {"B14050": "CATHODE"})
        self.assertIs(generation.sub_tabs.currentWidget(),
                      generation.types["CATHODE"])

    def test_a_sample_not_on_screen_is_not_scrolled_to(self) -> None:
        generation = self.widget.generations[GEN4_1_LABEL]
        self.widget.load_data(pd.DataFrame([sample()]), "ANODE", GEN4_1_LABEL)
        # Must not raise looking for a row that is filtered out or absent.
        generation.types["ANODE"].scroll_to([("B99999", "center")])

    def test_an_unresolved_type_switches_to_nothing(self) -> None:
        generation = self.widget.generations[GEN4_1_LABEL]
        before = generation.sub_tabs.currentWidget()
        generation._reveal([("B99999", "top right")], {})
        self.assertIs(generation.sub_tabs.currentWidget(), before)

    def test_the_summary_carries_no_report_notes(self) -> None:
        """The report's own notes boxes describe a batch, not a sample."""
        result = _ParsedStub()
        result.measurements = [_Measured("B14134", "top right")]
        _title, lines = self.widget.generations[GEN4_1_LABEL]._import_summary(result, [], "B14134")
        self.assertEqual(len(lines), 1)
        self.assertIn("Added 1 measurements", lines[0])

    def test_a_cancelled_folder_dialog_imports_nothing(self) -> None:
        asked = []
        self.widget.generations[GEN4_1_LABEL].on_import_folder = lambda *args: asked.append(args)
        with patch("src.gui.sem_edx_gui.QFileDialog.getExistingDirectory",
                   return_value=""):
            self.widget.generations[GEN4_1_LABEL]._on_add_clicked()
        self.assertEqual(asked, [])

    def test_the_summary_dialog_offers_the_operator_field_only_when_asked(self) -> None:
        without = ImportSummaryDialog(None, title="Imported", lines=["done"])
        self.assertIsNone(without.operator_input)
        self.assertTrue(without.ok_button.isEnabled())

    def test_ok_stays_shut_until_a_name_is_typed(self) -> None:
        dialog = ImportSummaryDialog(
            None, title="Imported", lines=["done"], ask_operator=True)
        self.assertFalse(dialog.ok_button.isEnabled())
        dialog.operator_input.setText("  Tamas Pakai  ")
        self.assertTrue(dialog.ok_button.isEnabled())
        dialog._on_accept()
        self.assertEqual(dialog.operator, "Tamas Pakai")

    def test_whitespace_alone_does_not_open_it(self) -> None:
        dialog = ImportSummaryDialog(
            None, title="Imported", lines=["done"], ask_operator=True)
        dialog.operator_input.setText("   ")
        self.assertFalse(dialog.ok_button.isEnabled())
        dialog._on_accept()          # Enter in an empty field must not close it
        self.assertEqual(dialog.result(), 0)

    def test_a_skipped_type_prompt_returns_nothing(self) -> None:
        with patch("src.gui.sem_edx_gui.ElectrodeTypeDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.assertEqual(
                self.widget.generations[GEN4_1_LABEL]._ask_type("B99999", ["ANODE", "CATHODE"]), "")

    def test_the_summary_names_what_was_added(self) -> None:
        result = _ParsedStub()
        result.measurements = [_Measured("B14050", "top right")]
        title, lines = self.widget.generations[GEN4_1_LABEL]._import_summary(result, [], "B14050")
        self.assertEqual(title, "Imported")
        self.assertIn("B14050 top right", lines[0])

    def test_the_type_gets_a_row_of_its_own(self) -> None:
        """It is looked up rather than chosen, so it also tells you which tab
        the sample just landed on."""
        result = _ParsedStub()
        result.measurements = [_Measured("B14050", "top right")]
        result.electrode_types = {"B14050": "CATHODE"}
        _title, lines = self.widget.generations[GEN4_1_LABEL]._import_summary(result, [], "B14050")
        self.assertIn("Electrode type: Cathode", lines)

    def test_a_mixed_folder_names_each_electrode(self) -> None:
        result = _ParsedStub()
        result.measurements = [_Measured("B14050", "top right"),
                               _Measured("B13871", "top right")]
        result.electrode_types = {"B14050": "CATHODE", "B13871": "ANODE"}
        _title, lines = self.widget.generations[GEN4_1_LABEL]._import_summary(result, [], "batch")
        self.assertIn("Electrode type: B13871 anode, B14050 cathode", lines)

    def test_an_unresolved_type_drops_the_row(self) -> None:
        result = _ParsedStub()
        result.measurements = [_Measured("B99999", "top right")]
        _title, lines = self.widget.generations[GEN4_1_LABEL]._import_summary(result, [], "B99999")
        self.assertFalse(any("Electrode type" in line for line in lines))

    def test_the_summary_says_when_a_sample_was_already_there(self) -> None:
        result = _ParsedStub()
        result.measurements = [_Measured("B14050", "top right")]
        _title, lines = self.widget.generations[GEN4_1_LABEL]._import_summary(
            result, ["B14050 top right"], "B14050")
        self.assertTrue(any("left alone" in line for line in lines))

    def test_nothing_to_poll(self) -> None:
        self.assertFalse(self.widget.check_for_external_updates())


class TestImageViewer(unittest.TestCase):
    """Paging stops at the ends rather than wrapping."""

    @staticmethod
    def _viewer(count: int) -> SemImageViewerDialog:
        return SemImageViewerDialog(
            None, title="B14200", images=[f"B14200_TR_{i}.tif"
                                          for i in range(1, count + 1)])

    def test_it_opens_on_the_first_capture(self) -> None:
        viewer = self._viewer(3)
        self.assertFalse(viewer.previous_button.isEnabled())
        self.assertTrue(viewer.next_button.isEnabled())

    def test_the_middle_can_go_either_way(self) -> None:
        viewer = self._viewer(3)
        viewer._step(1)
        self.assertTrue(viewer.previous_button.isEnabled())
        self.assertTrue(viewer.next_button.isEnabled())

    def test_the_last_capture_closes_next(self) -> None:
        viewer = self._viewer(3)
        viewer._step(1)
        viewer._step(1)
        self.assertTrue(viewer.previous_button.isEnabled())
        self.assertFalse(viewer.next_button.isEnabled())

    def test_stepping_past_the_end_stays_put(self) -> None:
        viewer = self._viewer(3)
        for _ in range(5):
            viewer._step(1)
        self.assertEqual(viewer._index, 2)

    def test_stepping_before_the_start_stays_put(self) -> None:
        viewer = self._viewer(3)
        for _ in range(5):
            viewer._step(-1)
        self.assertEqual(viewer._index, 0)

    def test_a_single_capture_has_both_buttons_shut(self) -> None:
        viewer = self._viewer(1)
        self.assertFalse(viewer.previous_button.isEnabled())
        self.assertFalse(viewer.next_button.isEnabled())

    def test_no_captures_at_all(self) -> None:
        viewer = SemImageViewerDialog(None, title="B14200", images=[])
        self.assertFalse(viewer.previous_button.isEnabled())
        self.assertFalse(viewer.next_button.isEnabled())
        viewer._step(1)          # must not raise


class TestSampleDialog(unittest.TestCase):
    """Correcting the measured percentages of one sample."""

    NI = element_column("Ni")
    S = element_column("S")

    def _dialog(self) -> "SemSampleDialog":
        detail = pd.DataFrame([
            {str(SemEdxColumns.MEASUREMENT): 1, self.NI: 35.1, self.S: 8.7,
             "ni_s": 35.1 / 8.7},
            {str(SemEdxColumns.MEASUREMENT): 2, self.NI: 32.3, self.S: 9.1,
             "ni_s": 32.3 / 9.1},
        ])
        return SemSampleDialog(
            None, names=QuantityNames(["Ni", "S", "Ni:S"]),
            electrode="B13698", position="top right", measurements=detail)

    def _column(self, dialog, name: str) -> int:
        return list(dialog._detail.columns).index(name)

    def test_percentages_are_editable_and_ratios_are_not(self) -> None:
        """A percentage was read off the instrument; a ratio is arithmetic."""
        dialog = self._dialog()
        editable = dialog.table.item(0, self._column(dialog, self.NI))
        derived = dialog.table.item(0, self._column(dialog, "ni_s"))
        self.assertTrue(editable.flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertFalse(derived.flags() & Qt.ItemFlag.ItemIsEditable)

    def test_the_point_number_is_not_editable(self) -> None:
        dialog = self._dialog()
        point = dialog.table.item(0, self._column(
            dialog, str(SemEdxColumns.MEASUREMENT)))
        self.assertFalse(point.flags() & Qt.ItemFlag.ItemIsEditable)

    def test_correcting_a_percentage_moves_its_ratio(self) -> None:
        """So the two never contradict each other while the window is open."""
        dialog = self._dialog()
        dialog.table.item(0, self._column(dialog, self.NI)).setText("43.5")
        self.assertEqual(
            dialog.table.item(0, self._column(dialog, "ni_s")).text(), "5.00")

    def test_a_correction_is_read_back_per_point(self) -> None:
        dialog = self._dialog()
        dialog.table.item(1, self._column(dialog, self.NI)).setText("40.0")
        dialog._on_save()
        self.assertEqual(dialog.measurements[2][self.NI], 40.0)
        self.assertEqual(dialog.measurements[1][self.NI], 35.1)

    def test_a_comma_is_accepted_as_a_decimal_point(self) -> None:
        """Danish, Hungarian and Polish keyboards all produce one."""
        dialog = self._dialog()
        dialog.table.item(0, self._column(dialog, self.NI)).setText("35,9")
        dialog._on_save()
        self.assertEqual(dialog.measurements[1][self.NI], 35.9)

    def test_nonsense_refuses_to_save(self) -> None:
        dialog = self._dialog()
        dialog.table.item(0, self._column(dialog, self.NI)).setText("abc")
        with patch("src.gui.sem_edx_gui.ValidationErrorScrollDialog") as complaint:
            dialog._on_save()
        self.assertEqual(dialog.measurements, {})
        self.assertEqual(dialog.result(), 0)
        complaint.return_value.exec.assert_called_once()

    def test_a_blank_is_a_reading_never_taken(self) -> None:
        """Different from nonsense: it stores as missing rather than refusing."""
        dialog = self._dialog()
        dialog.table.item(0, self._column(dialog, self.S)).setText("")
        dialog._on_save()
        self.assertIsNone(dialog.measurements[1][self.S])


class TestImagePicker(unittest.TestCase):
    """Choosing the captures that belong to a sample, as a list."""

    NAMES = ["B14175_TR_1.tif", "B14175_TR_2.tif", "B14175_BL_1.tif"]

    def _picker(self) -> ImagePickerDialog:
        return ImagePickerDialog(
            None, electrode="B14175", position="top right", images=self.NAMES)

    def test_nothing_is_chosen_to_begin_with(self) -> None:
        """A pre-ticked guess gets accepted without being read."""
        picker = self._picker()
        self.assertEqual(picker.list.selectedItems(), [])
        self.assertEqual(picker.counter.text(), "0 of 3 chosen")

    def test_accepting_none_asks_before_closing(self) -> None:
        """Ticking nothing is a likelier slip than a decision, and the sample
        would import with no captures and nothing saying why."""
        picker = self._picker()
        with patch("src.gui.sem_edx_gui.ConfirmationDialog") as confirm:
            confirm.return_value.exec.return_value = QDialog.DialogCode.Rejected
            picker._on_accept()
        self.assertTrue(confirm.called)
        self.assertEqual(picker.chosen, [])

    def test_accepting_none_goes_through_once_confirmed(self) -> None:
        picker = self._picker()
        with patch("src.gui.sem_edx_gui.ConfirmationDialog") as confirm:
            confirm.return_value.exec.return_value = QDialog.DialogCode.Accepted
            picker._on_accept()
        self.assertEqual(picker.chosen, [])

    def test_the_rows_are_numbered(self) -> None:
        picker = self._picker()
        self.assertTrue(picker.list.item(0).text().startswith("1"))
        self.assertIn(self.NAMES[0], picker.list.item(0).text())

    def test_the_counter_follows_the_selection(self) -> None:
        picker = self._picker()
        picker.list.item(0).setSelected(True)
        self.assertEqual(picker.counter.text(), "1 of 3 chosen")
        picker.list.selectAll()
        self.assertEqual(picker.counter.text(), "3 of 3 chosen")
        picker.list.clearSelection()
        self.assertEqual(picker.counter.text(), "0 of 3 chosen")

    def test_choosing_one_row_does_not_clear_the_others(self) -> None:
        """Multi selection, so a picker toggles rather than replaces."""
        picker = self._picker()
        picker.list.item(0).setSelected(True)
        picker.list.item(2).setSelected(True)
        picker._on_accept()
        self.assertEqual(picker.chosen, [self.NAMES[0], self.NAMES[2]])

    def test_select_all_then_none_come_back_after_clearing(self) -> None:
        picker = self._picker()
        picker.list.selectAll()
        picker.list.clearSelection()
        self.assertEqual(picker.counter.text(), "0 of 3 chosen")
        with patch("src.gui.sem_edx_gui.ConfirmationDialog") as confirm:
            confirm.return_value.exec.return_value = QDialog.DialogCode.Accepted
            picker._on_accept()
        self.assertEqual(picker.chosen, [])

    def test_the_captures_come_back_in_list_order(self) -> None:
        """Click order must not reshuffle the order they were taken in."""
        picker = self._picker()
        picker.list.item(2).setSelected(True)
        picker.list.item(0).setSelected(True)
        picker._on_accept()
        self.assertEqual(picker.chosen, [self.NAMES[0], self.NAMES[2]])


class TestHelpers(unittest.TestCase):
    """Small pieces the rest leans on."""

    def test_a_type_becomes_a_plural_tab_label(self) -> None:
        self.assertEqual(type_label("CATHODE"), "Cathodes")
        self.assertEqual(type_label("MM1"), "Mm1")

    def test_rounds_to_the_asked_for_decimals(self) -> None:
        self.assertEqual(format_number(3.14159, 2), "3.14")

    def test_missing_reads_as_a_dash(self) -> None:
        self.assertEqual(format_number(None, 2), "—")
        self.assertEqual(format_number(float("nan"), 1), "—")

    def test_text_that_is_not_a_number_reads_as_a_dash(self) -> None:
        self.assertEqual(format_number("n/a", 1), "—")


if __name__ == "__main__":
    unittest.main()
