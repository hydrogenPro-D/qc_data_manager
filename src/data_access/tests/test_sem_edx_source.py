"""Storing and resolving the result folder of a SEM sample.

The absolute path is personal: it starts with the operator's own user folder.
Everything below the shared library root is the same for everyone, so that tail
is what gets stored and each machine resolves it against its own root, found by
``sem_edx_library``.
"""

import tempfile
import unittest
from pathlib import Path

from src.data_access.sem_edx import sem_edx_library
from src.data_access.sem_edx.sem_edx_source import portable_folder, resolve_folder


class TestPortableFolder(unittest.TestCase):
    """What goes into the database."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "Production SEM images"
        self.sample = self.root / "B productionline" / "Gen4.1" / "B14134"
        self.sample.mkdir(parents=True)
        sem_edx_library.set_root(self.root)

    def tearDown(self) -> None:
        sem_edx_library.forget_root()
        self._tmp.cleanup()

    def test_a_folder_under_the_root_is_stored_as_its_tail(self) -> None:
        self.assertEqual(portable_folder(self.sample),
                         str(Path("B productionline") / "Gen4.1" / "B14134"))

    def test_the_tail_never_carries_the_root(self) -> None:
        """The tail has to mean the same thing on a machine that syncs the
        library at another level, so it starts below the root."""
        self.assertNotIn("Production SEM images", portable_folder(self.sample))

    def test_a_folder_outside_the_library_is_refused(self) -> None:
        """Storing the absolute path would look like a successful import and
        then open for exactly one person, so nothing is stored instead."""
        with tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(portable_folder(elsewhere), "")

    def test_a_folder_is_refused_when_the_library_cannot_be_found(self) -> None:
        sem_edx_library.set_root(None)
        self.assertEqual(portable_folder(self.sample), "")

    def test_the_picker_is_asked_only_when_the_library_is_missing(self) -> None:
        asked = []

        def ask() -> str:
            asked.append(True)
            return str(self.root)

        sem_edx_library.set_root(None)
        self.assertEqual(portable_folder(self.sample, ask),
                         str(Path("B productionline") / "Gen4.1" / "B14134"))
        # Answered once and remembered, not asked again per capture.
        portable_folder(self.sample, ask)
        self.assertEqual(len(asked), 1)

    def test_a_tail_is_left_alone(self) -> None:
        """Re-saving must not resolve an already portable path against the
        working directory."""
        tail = str(Path("B productionline") / "Gen4.1" / "B14134")
        self.assertEqual(portable_folder(tail), tail)

    def test_nothing_stays_nothing(self) -> None:
        self.assertEqual(portable_folder(""), "")
        self.assertEqual(portable_folder(None), "")


class TestResolveFolder(unittest.TestCase):
    """Where a stored folder is on this machine."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "Production SEM images"
        self.sample = self.root / "Gen4.1" / "B14134"
        self.sample.mkdir(parents=True)
        sem_edx_library.set_root(self.root)

    def tearDown(self) -> None:
        sem_edx_library.forget_root()
        self._tmp.cleanup()

    def test_a_tail_resolves_against_this_machine_s_root(self) -> None:
        resolved = resolve_folder(str(Path("Gen4.1") / "B14134"))
        self.assertEqual(resolved, self.sample)

    def test_an_absolute_path_is_honoured(self) -> None:
        """A row written before the library could be found still opens for
        whoever wrote it."""
        self.assertEqual(resolve_folder(str(self.sample)), self.sample)

    def test_an_absolute_path_that_has_gone(self) -> None:
        self.assertIsNone(resolve_folder(r"C:\no\such\folder"))

    def test_a_tail_that_is_not_there(self) -> None:
        self.assertIsNone(resolve_folder(str(Path("Gen9") / "B99999")))

    def test_a_tail_with_no_library_at_all(self) -> None:
        sem_edx_library.set_root(None)
        self.assertIsNone(resolve_folder(str(Path("Gen4.1") / "B14134")))

    def test_nothing_resolves_to_nothing(self) -> None:
        self.assertIsNone(resolve_folder(""))
        self.assertIsNone(resolve_folder(None))


class TestDiscoverRoot(unittest.TestCase):
    """Finding the library however OneDrive named it."""

    def _library(self, home: Path, *segments: str) -> Path:
        root = home.joinpath(*segments) / "Production SEM images"
        (root / "B productionline").mkdir(parents=True)
        return root

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self) -> None:
        sem_edx_library.forget_root()
        self._tmp.cleanup()

    def test_the_document_library_spelling(self) -> None:
        """Syncing the whole library gives '- Documents' with General inside."""
        root = self._library(self.home, "HydrogenPro ASA",
                             "Advanced Surface Plating ApS - Documents", "General",
                             "SEM images")
        self.assertEqual(sem_edx_library.discover_root(self.home), root)

    def test_the_general_folder_spelling(self) -> None:
        """Syncing the General folder gives '- General' and no General level.
        This is the one that broke, and neither spelling is written down."""
        root = self._library(self.home, "HydrogenPro ASA",
                             "Advanced Surface Plating ApS - General", "SEM images")
        self.assertEqual(sem_edx_library.discover_root(self.home), root)

    def test_a_folder_named_right_but_holding_nothing(self) -> None:
        """A stray copy without B productionline is not the library."""
        (self.home / "Advanced Surface Plating ApS - General" / "SEM images"
         / "Production SEM images").mkdir(parents=True)
        self.assertIsNone(sem_edx_library.discover_root(self.home))

    def test_no_library_on_this_machine(self) -> None:
        self.assertIsNone(sem_edx_library.discover_root(self.home))


if __name__ == "__main__":
    unittest.main()
