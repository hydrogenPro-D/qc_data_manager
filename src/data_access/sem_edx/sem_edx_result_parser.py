"""Reads a SEM EDX result folder as it comes off the microscope.

Everything that knows the shape of that folder, or the shape of the Word
reports inside it, lives here and nowhere else. The rest of the app only sees
:class:`ParsedFolder`.

The layout, where the picked folder is named for the electrode::

    B14050/                                  <- picked
    |-- B14050_TR_1_1553.tif                 <- shown in the image viewer
    |-- B14050_TR_1_1553.txt                 <- sidecar, holds the magnification
    |-- B14050_TR_1_1553_raw.tif             <- ignored, the unprocessed capture
    |-- ...
    `-- B14050/
        |-- data/                            <- the instrument's own binaries
        `-- reports/
            |-- B14050_TR_1_2026-07-27_10-23-16.docx
            |-- B14050_TR_2_...docx
            `-- B14050_TR_3_...docx

Three things make this worth parsing carefully rather than by position:

* the report lists elements sorted by abundance, so which row holds Ni changes
  from report to report, and an element can be absent altogether,
* the reports and the images are named on different schemes, timestamp against
  capture number, so they are matched on ``<electrode>_<position>_<point>``,
* the files usually live on a synced share, so a read can fail on a lock.

A folder is never rejected outright. Whatever can be read is returned, and
everything that could not is reported in :attr:`ParsedFolder.problems`.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from src.data_access.electrodes.electrode_columns import normalize_marking

REPORTS_DIR = "reports"
IMAGE_SUFFIX = ".tif"
SIDECAR_SUFFIX = ".txt"

# How far below the picked folder to look. A plate mapped at five positions is
# filed as five complete result folders nested inside one, so the captures sit
# two levels down and the reports three. Bounded so a folder picked by mistake
# somewhere near the root cannot start crawling a whole drive.
MAX_SEARCH_DEPTH = 4

# How many directories a single search may look at. A result folder is one
# electrode, at most five positions deep, so a few dozen. Picking one folder too
# high, the library rather than the electrode, used to walk every electrode in
# it on the UI thread: minutes of stat calls over a synced share with the window
# frozen behind them. The budget turns that into an immediate "this is not a
# result folder" instead.
MAX_SEARCH_DIRECTORIES = 300
# The unprocessed capture sits beside every image; only the processed one is
# worth showing.
RAW_MARKER = "_raw"

# Every element the report lists is kept. Which of them a table shows is the
# layout's business, not this module's, and an element thrown away here would
# need a re-import to get back.


# What the microscope writes into a filename, and what the app calls it. The
# workbook spells these out, so an import lines up with what is already there
# instead of splitting the sample filter in two.
POSITIONS = {
    "TR": "top right",
    "TL": "top left",
    "C": "center",
    "BR": "bottom right",
    "BL": "bottom left",
}

# A marking: letters then digits, as in B14134. Also how the electrode is
# recovered from a folder name that carries more than the marking.
MARKING_PATTERN = re.compile(r"[A-Za-z]+\d+")

# The marking, the spot and the point, however they were separated. Every one
# of these is in the library:
#     B14050_TR_1_1553          B757 BL -1_0857
#     B14134_TR1_2026-07-31     B757 TL - 1_0851
# so the separator is any run of spaces, underscores and hyphens, and it may be
# absent entirely between the spot and its point.
#
# The point is bounded to two digits and must not be followed by another, so
# the capture counter in B14050_TR_1553 and the date in B14134_TR_2026-07-31
# are never mistaken for one.
# A leading word before the marking is allowed and thrown away: some exports
# are named "EDX_B14599_TR_1". Without that, such a name matched nothing and
# fell through to _loose_name, which took the SECOND segment as the spot and
# filed the sample under "B14599" as if the marking were a place on the plate.
NAME_PATTERN = re.compile(
    r"^\s*(?:[A-Za-z]+[\s_-]+)?(?P<electrode>[A-Za-z]+\d+)"
    r"[\s_-]+(?P<position>[A-Za-z]+)"
    r"(?:[\s_-]*(?P<point>\d{1,2})(?!\d))?")

# Some folders leave the marking off the captures entirely and name them for
# the spot alone, "bottom left_0551.tif". Matched only against the positions
# actually known, longest first so "bottom left" wins over "bottom": without
# that guard any word at the start of a filename would become a sample.
POSITION_ONLY_PATTERN = re.compile(
    r"^\s*(?P<position>"
    + "|".join(re.escape(name) for name in
               sorted({*POSITIONS, *POSITIONS.values()}, key=len, reverse=True))
    + r")(?:[\s_-]*(?P<point>\d{1,2})(?!\d))?",
    re.IGNORECASE)

# The suffix the ETL adds when a workbook electrode has two blocks under one
# sample name, as B14175 does. No filename carries it.
NUMBERED_POSITION = re.compile(r"\s*\(\d+\)$")

# What a sample is called when the filename says nothing about where on the
# plate it came from.
UNNAMED_POSITION = "sample"

# The spots a folder may be split on, for is_known_position below.
_KNOWN_POSITIONS = {name.lower() for name in POSITIONS.values()} | {UNNAMED_POSITION}

# A composition row: the symbol, a tab, the atomic percent. Trailing tabs are
# the empty sigma column.
ELEMENT_PATTERN = re.compile(r"^\s*([A-Z][a-z]?)\s*\t\s*([0-9]*\.?[0-9]+)")

# The report has Project Notes and Specimen Notes boxes. They are deliberately
# not read: what they hold describes a batch rather than one spot on a plate,
# and the note that belongs to a sample is a judgement someone enters by hand.

# "Magnification = 100" in the image sidecar, which is UTF-16 with spaces
# between every character when read wrongly.
MAGNIFICATION_PATTERN = re.compile(r"^\s*Magnification\s*=\s*(\d+)", re.MULTILINE)

DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class ParsedMeasurement:
    """One point of one sample, read from a single Word report."""

    electrode: str
    position: str
    point: int
    # ``{'Ni': 24.5, 'S': 6.4, ...}``, every element the report listed. Which
    # ones a report holds varies: they are sorted by abundance and an element
    # can be absent, so this is never indexed by position.
    elements: dict[str, float] = field(default_factory=dict)
    date: str = ""
    zoom: str = ""
    source: str = ""

    def missing_elements(self, expected) -> list[str]:
        """Which of the expected symbols this report did not list."""
        return [symbol for symbol in expected if symbol not in self.elements]


@dataclass
class ParsedFolder:
    """Everything one result folder yielded, and everything it did not."""

    folder: Path
    electrode: str = ""
    measurements: list[ParsedMeasurement] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # ``{marking: 'CATHODE'}``. Filled in by the caller, not here: nothing in a
    # result folder says what an electrode is, that comes from the database.
    electrode_types: dict[str, str] = field(default_factory=dict)

    @property
    def samples(self) -> list[str]:
        """The distinct positions this folder holds, in the order found."""
        seen: list[str] = []
        for measurement in self.measurements:
            if measurement.position not in seen:
                seen.append(measurement.position)
        return seen


def candidate_images(folder) -> list[Path]:
    """Every capture in a folder that is worth showing, sorted by name.

    A ``_raw`` file is the unprocessed version of the capture beside it, so it
    is dropped *when that twin exists*. One with no twin is the only copy of
    that capture and is kept rather than lost.
    """
    folder = Path(folder) if folder else None
    if folder is None or not folder.is_dir():
        return []

    images = _files(folder, IMAGE_SUFFIX)
    # Keyed by folder as well as name: a plate mapped at five positions holds
    # five sets of captures, and only a twin in the same folder is a twin.
    processed = {(p.parent, p.stem.lower()) for p in images
                 if not p.stem.lower().endswith(RAW_MARKER)}
    return [p for p in images
            if not (p.stem.lower().endswith(RAW_MARKER)
                    and (p.parent, p.stem.lower()[: -len(RAW_MARKER)]) in processed)]


def result_images(folder, electrode: str = "", position: str = "",
                  names=None) -> list[Path]:
    """The captures of one sample, in the order they should be paged through.

    Three ways of deciding what belongs to a sample, in descending order of
    trust:

    * ``names``, the filenames recorded on the row. Chosen by hand at import,
      or filled in wholesale for a sample the ETL matched to a folder.
    * ``position``, for rows stored before that column existed. One folder
      holds every position mapped on the electrode, so this narrows it down.
    * neither, meaning everything in the folder.

    Called long after the import, when the viewer needs the files again, so it
    tolerates the folder having moved or gone: an empty list, not an error.
    """
    folder = Path(folder) if folder else None
    if folder is None or not folder.is_dir():
        return []

    if names:
        wanted = {str(n).strip().lower() for n in names if str(n).strip()}
        return [p for p in candidate_images(folder) if p.name.lower() in wanted]
    return _images(folder, electrode or _electrode_of(folder), position)


def parse_result_folder(folder, expected_elements=()) -> ParsedFolder:
    """Read one result folder. Never raises on a malformed one, reports instead.

    Args:
        folder: The folder named for the electrode.
        expected_elements: Symbols to warn about when a report omits them.
            Everything a report does list is kept regardless.
    """
    folder = Path(folder)
    result = ParsedFolder(folder=folder, electrode="")

    if not folder.is_dir():
        result.problems.append(f"{folder} is not a folder")
        return result

    result.electrode = _electrode_of(folder)
    directories, too_big = _find_reports(folder)
    if too_big:
        # Stop here rather than reading whatever was found on the way: a pick
        # this wide is a wrong pick, and the samples under it belong to other
        # electrodes.
        result.problems.append(
            f"{folder.name} is not one electrode's result folder.")
        return result
    if not directories:
        result.problems.append(
            f"No '{REPORTS_DIR}' folder found under {folder.name}, so no "
            f"measurements could be read")
    for reports in directories:
        for report in sorted(reports.glob("*.docx")):
            if report.name.startswith("~$"):   # Word's lock file
                continue
            measurement, problem = _read_report(report, result.electrode)
            if problem:
                result.problems.append(problem)
            if measurement is not None:
                result.measurements.append(measurement)

    result.images = _images(folder, result.electrode)
    if not result.images:
        result.problems.append(f"No images found in {folder.name}")

    _number_points(result)
    _apply_zoom(folder, result)
    _check_completeness(result, expected_elements)
    result.measurements.sort(key=lambda m: (m.position, m.point or 0))
    return result


def _number_points(result: ParsedFolder) -> None:
    """Give a point number to any measurement whose filename carried none.

    Numbering runs within a position, in the order the reports were read, and
    skips numbers already taken so a mixed batch cannot collide.
    """
    for position in result.samples:
        of_position = [m for m in result.measurements if m.position == position]
        taken = {m.point for m in of_position if m.point is not None}
        next_point = 1
        for measurement in of_position:
            if measurement.point is not None:
                continue
            while next_point in taken:
                next_point += 1
            measurement.point = next_point
            taken.add(next_point)


# ---- folder layout ----------------------------------------------------
def _electrode_of(folder: Path) -> str:
    """The marking a folder is named for. ``'B14134 retest'`` -> ``'B14134'``."""
    match = MARKING_PATTERN.search(folder.name)
    return normalize_marking(match.group(0)) if match else folder.name.strip()


class _Budget:
    """How much of a search is left, shared down one walk.

    A plain counter rather than a return value: the walk is a generator and its
    caller has to be able to ask, afterwards, whether it stopped because it ran
    out rather than because it finished.
    """

    def __init__(self, directories: int = MAX_SEARCH_DIRECTORIES) -> None:
        self.left = directories

    @property
    def exhausted(self) -> bool:
        return self.left <= 0


def _walk(folder: Path, depth: int = 0, budget=None):
    """Every directory at or below one, breadth first, bounded both ways.

    Bounded by depth and by how many directories it may visit at all. Without
    the second bound one wrong pick is unbounded work.
    """
    budget = _Budget() if budget is None else budget
    if depth > MAX_SEARCH_DEPTH or budget.exhausted:
        return
    budget.left -= 1
    yield folder
    try:
        children = sorted(child for child in folder.iterdir() if child.is_dir())
    except OSError:
        return                          # unreadable or still syncing
    for child in children:
        yield from _walk(child, depth + 1, budget)


def sample_locations(folder) -> list[tuple[str, str]]:
    """``[(electrode, position), ...]`` a result folder holds, cheaply.

    One folder can carry several spots off one electrode, each in its own
    subfolder, and they are imported one at a time. Listing them has to be
    cheap enough to do for the whole library when the picker opens, so this
    reads REPORT FILENAMES ONLY and opens nothing: parsing the documents costs
    about 700ms a folder, which over six hundred of them is a wait nobody
    would sit through.

    Only the reports are looked at, not every file. The captures beside them
    carry names like "Site" and "DESKTOP" that read as spots and are not.
    """
    found: list[tuple[str, str]] = []
    for reports in reports_directories(folder):
        for report in sorted(reports.glob("*.docx")):
            parsed = _parse_name(report.stem)
            if parsed is None:
                continue
            entry = (parsed[0], parsed[1] or UNNAMED_POSITION)
            if entry not in found:
                found.append(entry)
    return found


def is_known_position(position: str) -> bool:
    """Whether a spot name is one the plate is actually mapped at.

    _parse_name keeps a code it does not recognise rather than dropping it,
    which is right when reading a folder: better an oddly named sample than a
    measurement filed under the wrong spot. It is wrong when GUESSING at a
    folder's contents from filenames alone, where "Site", "EDX" and "DESKTOP"
    would each become a spot to import.
    """
    return base_position(position) in _KNOWN_POSITIONS


def base_position(position: str) -> str:
    """A position as it compares: no case, no ETL-added "(2)" suffix."""
    return _base_position(position)


def reports_directories(folder) -> list[Path]:
    """Public wrapper, for telling an EDX export from the other studies filed
    beside it in the library."""
    folder = Path(folder) if folder else None
    return _reports_directories(folder) if folder and folder.is_dir() else []


def _reports_directories(folder: Path) -> list[Path]:
    """Every reports folder at or below the one picked, in a stable order.

    Deliberately plural. A single sample is exported as one result folder, but
    a plate mapped at five positions is exported as five of them nested inside
    a folder named for the electrode, and the operator picks the outer one. So
    the answer is however many are down there, not the first one found.

    Searching rather than matching names: the inner folder is usually named for
    the electrode, sometimes with a suffix, sometimes for the specimen instead,
    and none of that changes where the reports are.
    """
    return _find_reports(folder)[0]


def _find_reports(folder: Path, budget=None) -> tuple[list[Path], bool]:
    """``(reports folders, was the search cut short)``.

    Cut short means the pick was not a result folder at all: no electrode holds
    three hundred directories.
    """
    budget = _Budget() if budget is None else budget
    found = [directory for directory in _walk(folder, budget=budget)
             if directory.name.lower() == REPORTS_DIR]
    return found, budget.exhausted


def _files(folder: Path, suffix: str) -> list[Path]:
    """Every file with one suffix at or below a folder, nearest first.

    Sorted by how deep it sits and then by name, so a single sample's captures
    keep their order and a five position plate reads position by position.
    """
    found: list[tuple[int, str, Path]] = []
    for directory in _walk(folder):
        # The instrument's own binaries live in a data folder; nothing there is
        # worth opening and there can be forty files per position.
        if directory.name.lower() == "data":
            continue
        try:
            entries = sorted(directory.glob(f"*{suffix}"))
        except OSError:
            continue
        depth = len(directory.relative_to(folder).parts)
        found.extend((depth, path.name, path) for path in entries)
    return [path for _depth, _name, path in sorted(found)]


def _images(folder: Path, electrode: str, position: str = "") -> list[Path]:
    """The captures of one sample, found by walking the folder."""
    return select_images(candidate_images(folder), electrode, position)


def select_images(candidates, electrode: str = "",
                  position: str = "") -> list[Path]:
    """Which of an already listed set of captures belong to one sample.

    Split out from the walk so a caller holding a folder's captures can pick a
    sample's out of them without listing the folder again. An electrode mapped
    at five positions would otherwise be walked five times, and these folders
    are on a synced share.

    A capture with no point number in its name still belongs to its sample, so
    it sorts by filename after the numbered ones rather than being dropped.
    """
    candidates = list(candidates)
    found: list[tuple[tuple[str, int, str], Path]] = []
    for image in candidates:
        parts = _parse_name(image.stem)
        if parts is None or not _same_electrode(parts[0], electrode):
            continue
        if position and not _same_position(parts[1], position):
            continue
        found.append(((parts[1], parts[2] or 99, image.name), image))

    if found:
        return [image for _key, image in sorted(found)]
    # Nothing matched by name. If some capture did match the electrode, the
    # position genuinely was not photographed and an empty list is the answer.
    # If none did, the names follow no convention this knows, and showing the
    # folder beats reporting that a folder full of images contains none.
    named = any(_parse_name(i.stem) and _same_electrode(
        _parse_name(i.stem)[0], electrode) for i in candidates)
    return [] if named else candidates


def _same_electrode(found: str, expected: str) -> bool:
    """Whether a filename's marking is the folder's.

    Compared through the normaliser rather than as text: the library pads
    markings as freely as the workbook does, and B1039 holds files called
    B001039. Read literally those are different electrodes, so none of the
    captures matched their own folder.
    """
    # A capture naming no marking at all belongs to the folder it is in, which
    # is the only thing that says which electrode it is.
    if not expected or not found:
        return True
    return (normalize_marking(found).lower()
            == normalize_marking(expected).lower())


def _same_position(found: str, expected: str) -> bool:
    """Whether two spellings of a position mean the same spot.

    The stored position can carry a number the ETL added, "top right (2)",
    where a workbook electrode has two blocks under one label. No filename ever
    carries that, so it is dropped before comparing.
    """
    return _base_position(found) == _base_position(expected)


def _base_position(position: str) -> str:
    return NUMBERED_POSITION.sub("", str(position).strip().lower()).strip()


def _parse_name(stem: str) -> tuple[str, str, int | None] | None:
    """The marking, the spot and the point out of a filename.

    All of these are the same three things, and all of them occur::

        B14050_TR_1_1553                    underscores throughout
        B14134_TR1_2026-07-31_18-29-18      point stuck to the position
        B757 BL -1_0857                     spaces, and a hyphen
        B757 TL - 1_0851                    the same, spaced differently
        B13853_BL_1_0877                    in a per position subfolder

    So the separator between the three is any run of spaces, underscores and
    hyphens rather than one particular character. Nobody is going to rename
    six thousand files to match a convention.

    None when the name does not begin with a marking and a spot. The point can
    be None: a file naming a spot but not which of its measurements it is still
    belongs to that sample, and takes its number from the reading order.
    """
    match = NAME_PATTERN.match(str(stem))
    if match is None:
        # No marking. Some folders name their captures for the spot alone.
        match = POSITION_ONLY_PATTERN.match(str(stem))
        if match is None:
            return None
        code = match.group("position")
        return ("", POSITIONS.get(code.upper(), code),
                int(match.group("point")) if match.group("point") else None)

    point = match.group("point")
    code = match.group("position")
    # An unrecognised code is kept as written rather than dropped: better an
    # odd sample name than a measurement filed under the wrong spot. The
    # marking is normalized, so B001039 and B1039 are the one electrode they
    # actually are.
    return (normalize_marking(match.group("electrode")),
            POSITIONS.get(code.upper(), code),
            int(point) if point else None)


# ---- the Word report --------------------------------------------------
def _read_report(report: Path, electrode: str
                 ) -> tuple[ParsedMeasurement | None, str]:
    """One report -> one measurement, or a description of why not."""
    # A report is never refused for what it is called. The naming has already
    # changed twice, and a folder someone put a report in is a better statement
    # of intent than a filename convention: whatever is in there gets read.
    parts = _parse_name(report.stem) or _loose_name(report.stem)
    file_electrode, position, point = parts

    problem = ""
    if file_electrode and not _same_electrode(file_electrode, electrode):
        problem = (f"{report.name} names {file_electrode}, filed under "
                   f"{electrode} because that is the folder it is in")

    document = _open_document(report)
    if document is None:
        return None, f"{report.name}: could not be opened"

    # The operator is deliberately not read. The document's only candidate is
    # `last_modified_by`, which is whoever last saved the file, and in practice
    # that is a shared machine account like "DK Lab2 | HydrogenPro". Nothing in
    # the export names the person who ran the microscope, so it is left blank
    # and filled in by hand.
    #
    # The folder's marking wins over the filename's, so a batch with one oddly
    # cased name still files under one electrode.
    measurement = ParsedMeasurement(
        electrode=electrode or file_electrode, position=position, point=point,
        elements=_composition(_paragraph_texts(document)),
        date=_report_date(document, report),
        source=report.name)
    return measurement, problem


def _loose_name(stem: str) -> tuple[str, str, int | None]:
    """What to call a report whose name follows no convention at all.

    ``'A567_Site 1_2026-07-14_09-16-58'`` -> ``('A567', 'Site 1', None)``. The
    segment AFTER the marking is taken as the spot exactly as written, since a
    name like "Site 1" is a label rather than a code with a point stuck on the
    end. The point is left for the numbering pass to fill in from the reading
    order.

    Found rather than assumed to be first: a name that opens with something
    else, "EDX_B14599_TR", would otherwise hand back the marking itself as the
    spot, and the sample would be filed at a place called B14599.
    """
    segments = [s.strip() for s in str(stem).split("_") if s.strip()]
    marking = next((s for s in segments if MARKING_PATTERN.fullmatch(s)), "")
    after = segments[segments.index(marking) + 1:] if marking else segments[1:]
    position = after[0] if after else ""
    return marking, position or UNNAMED_POSITION, None


def _paragraph_texts(document) -> list[str]:
    """Every paragraph in the document, however deeply it is nested.

    Not ``document.paragraphs``, which walks only the top level. These reports
    wrap parts of the body in content controls, the grey "Click here to enter
    text" boxes, and where they do the whole composition table ends up inside
    one. B14050 has it at the top level and B14134 has it inside a control, so
    reading only the top level silently loses every number in half the batch.
    """
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    return [Paragraph(element, document).text
            for element in document.element.body.iter(qn("w:p"))]


def _open_document(report: Path):
    """Open a .docx, working around a lock held by Word or the sync client."""
    from docx import Document          # imported here so the app starts without it

    try:
        return Document(str(report))
    except Exception:
        pass
    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
            copy = Path(handle.name)
        shutil.copy2(report, copy)
        try:
            return Document(str(copy))
        finally:
            copy.unlink(missing_ok=True)
    except Exception as e:
        print(f"Could not read {report.name}: {e}")
        return None


def _composition(paragraphs: list[str]) -> dict[str, float]:
    """``{'Ni': 24.5, 'S': 6.4, ...}`` from the Map Sum Spectrum block.

    Keyed by symbol because the report sorts elements by abundance, so a row
    number means nothing, and an element can be missing entirely.
    """
    values: dict[str, float] = {}
    for text in paragraphs:
        match = ELEMENT_PATTERN.match(text)
        if match is None:
            continue
        symbol, amount = match.group(1), match.group(2)
        # The first reading wins: a second block would be another spectrum.
        values.setdefault(symbol, float(amount))
    return values


def _report_date(document, report: Path) -> str:
    """The measurement date as ISO, from the page header, the document
    properties or the filename, in that order of trust."""
    for section in document.sections:
        for paragraph in section.header.paragraphs:
            match = DATE_PATTERN.search(paragraph.text)
            if match:
                return match.group(1)

    modified = getattr(document.core_properties, "modified", None)
    if isinstance(modified, dt.datetime):
        return modified.date().isoformat()

    match = DATE_PATTERN.search(report.stem)
    return match.group(1) if match else ""


# ---- the image sidecar ------------------------------------------------
def _apply_zoom(folder: Path, result: ParsedFolder) -> None:
    """Fill each measurement's zoom from its image's sidecar.

    The sidecar belongs to the image, not the report, and the two are named on
    different schemes, so they are matched on electrode, position and point.
    """
    by_key: dict[tuple[str, int | None], str] = {}
    seen: set[str] = set()

    for sidecar in _files(folder, SIDECAR_SUFFIX):
        if sidecar.stem.lower().endswith(RAW_MARKER):
            continue
        zoom = _magnification(sidecar)
        if not zoom:
            continue
        seen.add(zoom)

        parts = _parse_name(sidecar.stem)
        if parts is None:
            continue
        by_key.setdefault((parts[1], parts[2]), zoom)
        # A sidecar with no point still speaks for its whole sample, and every
        # capture of one sample shares a magnification anyway.
        by_key.setdefault((parts[1], None), zoom)

    # When every sidecar in the folder agrees, that answer holds even for a
    # capture whose name matched nothing. Where they disagree there is a real
    # question to answer, so it is left blank for someone to fill in.
    shared = seen.pop() if len(seen) == 1 else ""

    for measurement in result.measurements:
        measurement.zoom = (by_key.get((measurement.position, measurement.point))
                            or by_key.get((measurement.position, None))
                            or shared)


def _magnification(sidecar: Path) -> str:
    """``Magnification = 100`` -> ``'x100'``. Empty when it cannot be read."""
    for encoding in ("utf-16", "utf-8", "latin-1"):
        try:
            text = sidecar.read_text(encoding=encoding)
        except (OSError, UnicodeError):
            continue
        match = MAGNIFICATION_PATTERN.search(text)
        if match:
            return f"x{match.group(1)}"
    return ""


# ---- reporting --------------------------------------------------------
def _check_completeness(result: ParsedFolder, expected_elements=()) -> None:
    """Say plainly what a folder was short of, without rejecting it."""
    if not result.measurements:
        return

    # How many points a sample carries is deliberately not checked. Three is
    # the Gen 4.1 habit, not a rule: over half of the Gen 3.1 samples hold one
    # or two, and warning on every one of those teaches people to skip the
    # message that matters.

    # A report missing one element is not worth a line, it shows as a blank
    # cell. A report missing the composition block entirely is a different
    # thing: it means the export was made without the quantitative results, and
    # the numbers exist nowhere in the folder. Named plainly: what an operator
    # needs is which file to export again, and the rest was in the way of it.
    empty = [m.source for m in result.measurements if not m.elements]
    if empty:
        result.problems.append(f"Missing data from {', '.join(sorted(empty))}")

    without_zoom = [m for m in result.measurements if not m.zoom]
    if without_zoom:
        result.problems.append(
            f"{len(without_zoom)} of {len(result.measurements)} measurements have "
            f"no magnification, the image sidecar was missing or unreadable")
