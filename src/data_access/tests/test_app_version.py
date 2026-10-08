"""The version number: how it is read, how it is bumped, when it is written.

The bump is minor only and not semver, so 1.10 comes after 1.9 and the parts
have to be compared as integers. That is the part worth pinning down, along
with every way the file can be absent or broken, since none of those may stop
the app starting.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.app_version import (UNKNOWN, next_version, read_version_info,
                             write_version_file)


class TestTheBump(unittest.TestCase):
    """Minor only, and ordered by integer rather than by string."""

    def test_nothing_yet_starts_at_one(self) -> None:
        self.assertEqual(next_version(None), "1.0")
        self.assertEqual(next_version(""), "1.0")

    def test_the_minor_part_moves(self) -> None:
        self.assertEqual(next_version("1.0"), "1.1")
        self.assertEqual(next_version("2.7"), "2.8")

    def test_it_goes_past_nine(self) -> None:
        """1.10, not 2.0. The string order is wrong here and the number is not."""
        self.assertEqual(next_version("1.9"), "1.10")
        self.assertEqual(next_version("1.10"), "1.11")

    def test_the_order_holds_when_the_parts_are_integers(self) -> None:
        as_numbers = lambda text: tuple(int(part) for part in text.split("."))
        self.assertGreater(as_numbers("1.10"), as_numbers("1.9"))

    def test_anything_unreadable_starts_again(self) -> None:
        for previous in ("broken", "1", "1.x", "..."):
            self.assertEqual(next_version(previous), "1.0", previous)


class TestReadingIt(unittest.TestCase):
    """Never an error. A build from source has no release to name."""

    def _read(self, write=None) -> dict:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config" / "version.json"
            if write is not None:
                path.parent.mkdir(parents=True)
                path.write_text(write, encoding="utf-8")
            with mock.patch("src.app_version.version_file_path",
                            return_value=path):
                return read_version_info()

    def test_no_file_reads_as_a_dev_build(self) -> None:
        self.assertEqual(self._read(), {"version": UNKNOWN, "released_at": ""})

    def test_a_half_written_file_does_too(self) -> None:
        """It must not stop the app starting."""
        self.assertEqual(self._read('{"version": "1.2"')["version"], UNKNOWN)

    def test_a_file_missing_the_fields_does_too(self) -> None:
        self.assertEqual(self._read("{}")["version"], UNKNOWN)

    def test_a_good_file_is_read(self) -> None:
        info = self._read('{"version": "1.4", "released_at": "2026-10-08 09:00:00"}')
        self.assertEqual(info, {"version": "1.4",
                                "released_at": "2026-10-08 09:00:00"})


class TestWritingIt(unittest.TestCase):

    def test_it_records_the_number_and_the_moment(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config" / "version.json"
            info = write_version_file(path, "1.3")
            self.assertEqual(info["version"], "1.3")
            self.assertTrue(info["released_at"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), info)


class TestTheBuildScript(unittest.TestCase):
    """Ask before the slow part, write only once there is something built."""

    def setUp(self) -> None:
        self.source = (Path(__file__).resolve().parents[3]
                       / "tools" / "generate_executable.py"
                       ).read_text(encoding="utf-8")

    def _position(self, needle: str) -> int:
        where = self.source.find(needle)
        self.assertNotEqual(where, -1, f"{needle} is gone from the build script")
        return where

    def test_a_test_build_keeps_the_number(self) -> None:
        from tools.generate_executable import KNOWN_DATABASES, choose_version

        with mock.patch("tools.generate_executable._current_version",
                        return_value="1.4"):
            self.assertEqual(choose_version(KNOWN_DATABASES[0]), "1.4")

    def test_a_release_asks_and_takes_no_for_an_answer(self) -> None:
        from tools.generate_executable import choose_version

        with mock.patch("tools.generate_executable._current_version",
                        return_value="1.4"), \
                mock.patch("builtins.input", return_value=""):
            self.assertEqual(choose_version("dataloggingDev"), "1.4")

    def test_a_release_bumps_when_told_to(self) -> None:
        from tools.generate_executable import choose_version

        with mock.patch("tools.generate_executable._current_version",
                        return_value="1.9"), \
                mock.patch("builtins.input", return_value="y"):
            self.assertEqual(choose_version("dataloggingDev"), "1.10")

    def test_the_question_comes_before_pyinstaller_runs(self) -> None:
        """A prompt after a five minute build means sitting there to answer it."""
        body = self.source[self._position("def build_executable("):]
        self.assertLess(body.find("choose_version(database)"),
                        body.find("_build_from_existing_spec()"))

    def test_and_the_file_is_written_after(self) -> None:
        """A build that failed must not record a release that never shipped."""
        body = self.source[self._position("def build_executable("):]
        self.assertGreater(body.find("_write_version_file("),
                           body.find("_build_from_existing_spec()"))


if __name__ == "__main__":
    unittest.main()
