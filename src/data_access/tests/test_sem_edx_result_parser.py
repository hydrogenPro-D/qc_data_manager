"""Reading a SEM EDX result folder as it comes off the microscope.

The folder layouts and the filenames are not under our control, and both have
already changed once, so these lean on the shapes actually seen rather than on
one canonical form.
"""

import tempfile
import unittest
from pathlib import Path


from src.data_access.sem_edx.sem_edx_result_parser import (
    POSITIONS,
    ParsedFolder,
    ParsedMeasurement,
    _check_completeness,
    _composition,
    _electrode_of,
    _loose_name,
    _number_points,
    _paragraph_texts,
    _parse_name,
    _reports_directories,
    candidate_images,
    parse_result_folder,
    result_images,
    select_images,
)


class TestParseName(unittest.TestCase):
    """The filename is the only thing naming the electrode, spot and point."""

    def test_the_point_can_be_its_own_segment(self) -> None:
        self.assertEqual(_parse_name("B14050_TR_1_1553"),
                         ("B14050", "top right", 1))

    def test_the_point_can_be_stuck_to_the_position(self) -> None:
        """B14134_TR1 is the scheme the July batches use."""
        self.assertEqual(_parse_name("B14134_TR1_2026-07-31_18-29-18"),
                         ("B14134", "top right", 1))
        self.assertEqual(_parse_name("B14134_TR3_2026-07-31_18-30-38"),
                         ("B14134", "top right", 3))

    def test_spaces_and_hyphens_separate_just_as_well(self) -> None:
        """B757 is filed with spaces and a hyphen, spaced inconsistently."""
        self.assertEqual(_parse_name("B757 BL -1_0857"),
                         ("B757", "bottom left", 1))
        self.assertEqual(_parse_name("B757 TL - 1_0851"),
                         ("B757", "top left", 1))
        self.assertEqual(_parse_name("B757 BL  - 2_2025-07-22_12-26-29"),
                         ("B757", "bottom left", 2))

    def test_a_spot_with_no_point_at_all(self) -> None:
        self.assertEqual(_parse_name("B394 midel"), ("B394", "midel", None))

    def test_a_padded_marking_is_the_same_electrode(self) -> None:
        """B1039 holds files called B001039. Read literally those are two
        electrodes, and none of the captures matched their own folder."""
        self.assertEqual(_parse_name("B001039_TL_1_1265"),
                         ("B1039", "top left", 1))

    def test_a_capture_named_for_the_spot_alone(self) -> None:
        """Some folders leave the marking off entirely."""
        self.assertEqual(_parse_name("bottom left_0551"),
                         ("", "bottom left", None))
        self.assertEqual(_parse_name("TR_1_0880"), ("", "top right", 1))

    def test_only_a_position_it_recognises_may_stand_alone(self) -> None:
        """Otherwise any word at the start of a filename becomes a sample."""
        self.assertIsNone(_parse_name("summary_of_results"))
        self.assertIsNone(_parse_name("notes"))

    def test_every_position_code_is_spelled_out(self) -> None:
        for code, spelled in POSITIONS.items():
            self.assertEqual(_parse_name(f"B1_{code}1_x"), ("B1", spelled, 1))

    def test_an_unknown_code_is_kept_as_written(self) -> None:
        """Better an odd sample name than a measurement under the wrong spot."""
        self.assertEqual(_parse_name("B14050_TRC1_x"), ("B14050", "TRC", 1))

    def test_a_missing_point_is_allowed(self) -> None:
        self.assertEqual(_parse_name("B14050_TR"), ("B14050", "top right", None))

    def test_a_capture_number_is_not_mistaken_for_a_point(self) -> None:
        """1553 is the capture counter, not a point; only 1 and 2 digits count."""
        self.assertEqual(_parse_name("B14050_TR_1553"),
                         ("B14050", "top right", None))

    def test_a_date_is_not_mistaken_for_a_point(self) -> None:
        self.assertEqual(_parse_name("B14050_TR_2026-07-27_10-23-16"),
                         ("B14050", "top right", None))

    def test_a_name_that_is_not_a_measurement(self) -> None:
        self.assertIsNone(_parse_name("notes"))
        self.assertIsNone(_parse_name("B14050"))
        self.assertIsNone(_parse_name("summary_of_results"))


class TestLooseName(unittest.TestCase):
    """A report is never refused for what it is called.

    The naming has changed twice already, and a folder someone put a report in
    is a better statement of intent than a filename convention.
    """

    def test_a_spot_named_rather_than_coded(self) -> None:
        self.assertEqual(_loose_name("A567_Site 1_2026-07-14_09-16-58"),
                         ("A567", "Site 1", None))

    def test_two_sites_stay_two_samples(self) -> None:
        """'Site 1' reads as a label, not as a code with a point on the end, so
        Site 1 and Site 3 are different spots rather than points of one."""
        self.assertNotEqual(_loose_name("A567_Site 1_x")[1],
                            _loose_name("A567_Site 3_x")[1])

    def test_a_name_with_no_marking(self) -> None:
        self.assertEqual(_loose_name("scan_centre_2026")[0], "")
        self.assertEqual(_loose_name("scan_centre_2026")[1], "centre")

    def test_a_name_with_nothing_after_the_marking(self) -> None:
        self.assertEqual(_loose_name("B13871"), ("B13871", "sample", None))

    def test_a_name_with_no_structure_at_all(self) -> None:
        self.assertEqual(_loose_name("report"), ("", "sample", None))


class TestElectrodeOf(unittest.TestCase):
    """The picked folder is named for the electrode, sometimes with extras."""

    def test_a_plain_marking(self) -> None:
        self.assertEqual(_electrode_of(Path("B14134")), "B14134")

    def test_a_marking_with_something_after_it(self) -> None:
        self.assertEqual(_electrode_of(Path("B14134 retest")), "B14134")

    def test_a_folder_with_no_marking_keeps_its_name(self) -> None:
        self.assertEqual(_electrode_of(Path("scratch")), "scratch")


class TestReportsDirectories(unittest.TestCase):
    """Where the reports are, however the export was filed.

    Deliberately plural: a plate mapped at five positions is exported as five
    complete result folders nested inside one, and the operator picks the outer.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "B14134"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reports_directly_inside_the_picked_folder(self) -> None:
        expected = self.root / "reports"
        expected.mkdir()
        self.assertEqual(_reports_directories(self.root), [expected])

    def test_an_exactly_named_inner_folder(self) -> None:
        expected = self.root / "B14134" / "reports"
        expected.mkdir(parents=True)
        self.assertEqual(_reports_directories(self.root), [expected])

    def test_an_inner_folder_named_for_something_else(self) -> None:
        """B13871's reports sit under a folder called A567."""
        expected = self.root / "A567" / "reports"
        expected.mkdir(parents=True)
        self.assertEqual(_reports_directories(self.root), [expected])

    def test_one_result_folder_per_position(self) -> None:
        """B13951's shape: five positions, each a complete export."""
        for code in ("BL", "BR", "C", "TL", "TR"):
            (self.root / f"B13951_{code}" / f"B13951_{code}" / "reports").mkdir(
                parents=True)
        found = _reports_directories(self.root)
        self.assertEqual(len(found), 5)
        self.assertTrue(all(d.name == "reports" for d in found))

    def test_a_folder_with_no_reports_anywhere(self) -> None:
        (self.root / "data").mkdir()
        self.assertEqual(_reports_directories(self.root), [])


class TestNestedCaptures(unittest.TestCase):
    """Captures are not always at the top of the folder that was picked."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "B13951"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_captures_one_level_down_are_found(self) -> None:
        for code in ("BL", "TR"):
            inner = self.root / f"B13951_{code}"
            inner.mkdir()
            for point in (1, 2, 3):
                (inner / f"B13951_{code}_{point}_15{point}.tif").write_bytes(b"")
                (inner / f"B13951_{code}_{point}_15{point}_raw.tif").write_bytes(b"")
        self.assertEqual(len(candidate_images(self.root)), 6)

    def test_each_position_still_gets_only_its_own(self) -> None:
        for code in ("BL", "TR"):
            inner = self.root / f"B13951_{code}"
            inner.mkdir()
            for point in (1, 2, 3):
                (inner / f"B13951_{code}_{point}_15{point}.tif").write_bytes(b"")
        found = result_images(self.root, "B13951", "bottom left")
        self.assertEqual(len(found), 3)
        self.assertTrue(all("_BL_" in p.name for p in found))

    def test_the_instrument_s_own_data_folder_is_skipped(self) -> None:
        data = self.root / "B13951_TR" / "B13951_TR" / "data"
        data.mkdir(parents=True)
        (data / "not-a-capture.tif").write_bytes(b"")
        self.assertEqual(candidate_images(self.root), [])

    def test_a_raw_twin_is_only_a_twin_in_its_own_folder(self) -> None:
        """Two positions can hold captures with the same stem."""
        for code in ("BL", "TR"):
            inner = self.root / f"B13951_{code}"
            inner.mkdir()
            (inner / "capture.tif").write_bytes(b"")
        (self.root / "B13951_BL" / "capture_raw.tif").write_bytes(b"")
        self.assertEqual(len(candidate_images(self.root)), 2)


class TestNumberPoints(unittest.TestCase):
    """A file that names a spot but not a point still belongs to that sample."""

    @staticmethod
    def _folder(points) -> ParsedFolder:
        return ParsedFolder(
            folder=Path("B1"), electrode="B1",
            measurements=[ParsedMeasurement("B1", "top right", p) for p in points])

    def test_missing_points_are_numbered_in_order(self) -> None:
        result = self._folder([None, None, None])
        _number_points(result)
        self.assertEqual([m.point for m in result.measurements], [1, 2, 3])

    def test_numbering_skips_the_points_already_taken(self) -> None:
        result = self._folder([2, None, None])
        _number_points(result)
        self.assertEqual([m.point for m in result.measurements], [2, 1, 3])

    def test_positions_are_numbered_independently(self) -> None:
        result = ParsedFolder(
            folder=Path("B1"), electrode="B1",
            measurements=[ParsedMeasurement("B1", "top right", None),
                          ParsedMeasurement("B1", "center", None)])
        _number_points(result)
        self.assertEqual([m.point for m in result.measurements], [1, 1])


class TestParagraphTexts(unittest.TestCase):
    """The composition is not always at the top level of the document."""

    @staticmethod
    def _document_with_control():
        """A document whose composition sits inside a content control, which is
        how B14134 came out: someone typed into the notes box and the table
        ended up inside it."""
        from docx import Document
        from docx.oxml import OxmlElement

        document = Document()
        document.add_paragraph("Map Sum Spectrum")
        buried = document.add_paragraph("Ni\t32.6")

        control = OxmlElement("w:sdt")
        content = OxmlElement("w:sdtContent")
        buried._p.addprevious(control)
        control.append(content)
        content.append(buried._p)
        return document

    def test_a_buried_paragraph_is_read(self) -> None:
        document = self._document_with_control()
        # The top level walk misses it, which is the bug this guards against.
        self.assertNotIn("Ni\t32.6", [p.text for p in document.paragraphs])
        self.assertIn("Ni\t32.6", _paragraph_texts(document))

    def test_a_buried_composition_is_parsed(self) -> None:
        self.assertEqual(
            _composition(_paragraph_texts(self._document_with_control())),
            {"Ni": 32.6})


class TestComposition(unittest.TestCase):
    """Elements are read by symbol, never by row: the reports sort them by
    abundance and any of them can be absent."""

    def test_the_order_does_not_matter(self) -> None:
        rows = ["Element\tAtomic %\tσ", "Ni\t32.6", "C\t30.1", "S\t8.7"]
        self.assertEqual(_composition(rows), {"Ni": 32.6, "C": 30.1, "S": 8.7})

    def test_the_header_row_is_not_an_element(self) -> None:
        self.assertEqual(_composition(["Element\tAtomic %\tσ"]), {})

    def test_two_letter_symbols_survive(self) -> None:
        self.assertEqual(_composition(["Co\t1.0", "Cl\t0.5"]),
                         {"Co": 1.0, "Cl": 0.5})

    def test_the_first_reading_of_a_symbol_wins(self) -> None:
        """A second block would be another spectrum, not a correction."""
        self.assertEqual(_composition(["Ni\t32.6", "Ni\t99.9"]), {"Ni": 32.6})


class TestCompleteness(unittest.TestCase):
    """What a folder was short of, said once rather than per element."""

    @staticmethod
    def _folder(elements_per_report) -> ParsedFolder:
        return ParsedFolder(
            folder=Path("B1"), electrode="B1",
            measurements=[
                ParsedMeasurement("B1", "top right", point, elements=elements,
                                  zoom="x100", source=f"B1_TR{point}.docx")
                for point, elements in enumerate(elements_per_report, start=1)])

    def test_a_report_with_no_composition_is_called_out(self) -> None:
        """The whole block missing means the export left the results out, and
        the numbers exist nowhere in the folder."""
        result = self._folder([{"Ni": 24.5}, {}, {}])
        _check_completeness(result, ["Ni", "S"])
        self.assertTrue(any("no composition table" in p for p in result.problems))
        self.assertTrue(any("B1_TR2.docx" in p for p in result.problems))

    def test_one_missing_element_is_not_worth_a_line(self) -> None:
        """It shows as a blank cell, which says the same thing."""
        result = self._folder([{"Ni": 24.5}] * 3)
        _check_completeness(result, ["Ni", "S", "Co"])
        self.assertFalse(any("composition" in p for p in result.problems))

    def test_a_sample_with_fewer_points_is_not_a_fault(self) -> None:
        """Three is the Gen 4.1 habit, not a rule: over half the Gen 3.1
        samples hold one or two, and warning on each would drown the rest."""
        result = self._folder([{"Ni": 24.5}, {"Ni": 24.0}])
        _check_completeness(result, ["Ni"])
        self.assertEqual(result.problems, [])


class TestResultImages(unittest.TestCase):
    """One folder holds every position mapped on an electrode."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "B14175"
        self.folder.mkdir()
        for name in ("B14175_TR_1_1553", "B14175_TR_2_1554", "B14175_TR_3_1555",
                     "B14175_BL_1_1560", "B14175_BL_2_1561", "B14175_BL_3_1562"):
            (self.folder / f"{name}.tif").write_bytes(b"")
            (self.folder / f"{name}_raw.tif").write_bytes(b"")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_without_a_position_everything_comes_back(self) -> None:
        self.assertEqual(len(result_images(self.folder)), 6)

    def test_a_position_narrows_it_to_that_sample(self) -> None:
        names = [p.name for p in result_images(self.folder, position="top right")]
        self.assertEqual(names, ["B14175_TR_1_1553.tif", "B14175_TR_2_1554.tif",
                                 "B14175_TR_3_1555.tif"])

    def test_the_other_position_is_its_own_three(self) -> None:
        names = [p.name for p in result_images(self.folder, position="bottom left")]
        self.assertEqual(len(names), 3)
        self.assertTrue(all("_BL_" in name for name in names))

    def test_the_numbering_suffix_is_ignored_when_matching(self) -> None:
        """The ETL writes 'top right (2)' where a workbook electrode has two
        blocks under one label; no filename carries that."""
        self.assertEqual(len(result_images(self.folder, position="top right (2)")), 3)

    def test_a_position_nothing_was_mapped_at(self) -> None:
        """Some capture does name this electrode, so an empty answer means the
        spot was genuinely not photographed."""
        self.assertEqual(result_images(self.folder, position="center"), [])

    def test_captures_following_no_convention_are_still_shown(self) -> None:
        """A folder full of images must never report that it holds none."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "B13871"
            folder.mkdir()
            for name in ("A567_Site 1.tif", "A567_Site 3.tif"):
                (folder / name).write_bytes(b"")
            self.assertEqual(len(result_images(folder, position="top right")), 2)

    def test_raw_captures_are_never_returned(self) -> None:
        self.assertFalse(any("_raw" in p.name for p in result_images(self.folder)))

    def test_recorded_filenames_win_over_the_position(self) -> None:
        """The row says which captures are its own, so a folder holding several
        positions needs no guessing."""
        names = ["B14175_TR_1_1553.tif", "B14175_BL_2_1561.tif"]
        found = result_images(self.folder, position="top right", names=names)
        self.assertEqual([p.name for p in found], sorted(names))

    def test_a_recorded_name_that_has_gone_is_skipped(self) -> None:
        found = result_images(self.folder,
                              names=["B14175_TR_1_1553.tif", "deleted.tif"])
        self.assertEqual([p.name for p in found], ["B14175_TR_1_1553.tif"])

    def test_a_recorded_raw_capture_is_still_refused(self) -> None:
        """It is the unprocessed twin of one already listed."""
        self.assertEqual(result_images(self.folder,
                                       names=["B14175_TR_1_1553_raw.tif"]), [])


class TestPositionSubfolders(unittest.TestCase):
    """A plate mapped at five spots files each spot in its own subfolder."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "B13853"
        for code, number in (("BL", 877), ("TR", 880), ("C", 871)):
            spot = self.folder / f"B13853_{code}"
            spot.mkdir(parents=True)
            for point in (1, 2, 3):
                stem = f"B13853_{code}_{point}_{number + point - 1:04d}"
                (spot / f"{stem}.tif").write_bytes(b"")
                (spot / f"{stem}_raw.tif").write_bytes(b"")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_captures_below_the_folder_are_found(self) -> None:
        self.assertEqual(len(candidate_images(self.folder)), 9)

    def test_a_raw_twin_is_dropped_within_its_own_subfolder(self) -> None:
        self.assertFalse(any("_raw" in p.name
                             for p in candidate_images(self.folder)))

    def test_a_sample_gets_only_its_own_three(self) -> None:
        found = result_images(self.folder, position="bottom left")
        self.assertEqual([p.name for p in found],
                         ["B13853_BL_1_0877.tif", "B13853_BL_2_0878.tif",
                          "B13853_BL_3_0879.tif"])

    def test_the_position_is_matched_ignoring_case(self) -> None:
        """The workbook writes 'Center' on one row and 'center' on another."""
        self.assertEqual(len(result_images(self.folder, position="Center")), 3)

    def test_selecting_from_an_already_listed_folder(self) -> None:
        """The same answer without walking the share again."""
        candidates = candidate_images(self.folder)
        found = select_images(candidates, "B13853", "top right")
        self.assertEqual([p.name for p in found],
                         ["B13853_TR_1_0880.tif", "B13853_TR_2_0881.tif",
                          "B13853_TR_3_0882.tif"])

    def test_a_spot_that_was_never_photographed(self) -> None:
        self.assertEqual(select_images(candidate_images(self.folder),
                                       "B13853", "bottom right"), [])

    def test_a_padded_folder_still_matches_its_captures(self) -> None:
        """B1039 holds B001039 files, which used to match nothing, so every
        spot fell back to showing the whole folder."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "B1039"
            folder.mkdir()
            for code, point in (("TL", 1), ("TL", 2), ("BR", 1)):
                (folder / f"B001039_{code}_{point}_126{point}.tif").write_bytes(b"")
            found = result_images(folder, position="top left")
            self.assertEqual(len(found), 2)


class TestCandidateImages(unittest.TestCase):
    """What the picker offers, and what the automatic match records."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "B14050"
        self.folder.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, *names) -> None:
        for name in names:
            (self.folder / name).write_bytes(b"")

    def test_a_raw_twin_is_dropped(self) -> None:
        self._write("B14050_TR_1.tif", "B14050_TR_1_raw.tif")
        self.assertEqual([p.name for p in candidate_images(self.folder)],
                         ["B14050_TR_1.tif"])

    def test_a_raw_with_no_twin_is_kept(self) -> None:
        """It is the only copy of that capture, so losing it would lose it."""
        self._write("B14050_TR_2_raw.tif")
        self.assertEqual([p.name for p in candidate_images(self.folder)],
                         ["B14050_TR_2_raw.tif"])

    def test_only_captures_are_offered(self) -> None:
        self._write("B14050_TR_1.tif", "B14050_TR_1.txt", "notes.docx")
        self.assertEqual([p.name for p in candidate_images(self.folder)],
                         ["B14050_TR_1.tif"])

    def test_a_folder_that_is_not_there(self) -> None:
        self.assertEqual(candidate_images(self.folder / "gone"), [])


class TestMissingFolder(unittest.TestCase):
    """A folder that is not there is reported, never raised."""

    def test_a_path_that_is_not_a_folder(self) -> None:
        result = parse_result_folder(Path("no") / "such" / "folder")
        self.assertEqual(result.measurements, [])
        self.assertTrue(result.problems)

    def test_images_of_a_folder_that_has_gone(self) -> None:
        self.assertEqual(result_images(Path("no") / "such" / "folder"), [])

    def test_images_of_no_folder_at_all(self) -> None:
        self.assertEqual(result_images(""), [])


if __name__ == "__main__":
    unittest.main()
