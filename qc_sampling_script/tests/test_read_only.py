"""The read-only wrapper, which is the only thing standing between this tool
and the production database.

Worth testing properly. The tool now opens the real database, and the promise
that it never writes is only worth what the code enforces.
"""

import unittest

from qc_sampling_script.data.read_only import ReadOnlyBackend


class FakeBackend:
    """A backend with both kinds of method on it."""

    def __init__(self) -> None:
        self.saved = []

    # reads
    def get_metadata(self):
        return "electrodes"

    def load_vats(self):
        return {"B1": "28"}

    def vats_pending(self):
        return False

    def database_name(self):
        return "dataloggingDev"

    def plant_database_name(self):
        return "dataloggingDev"

    # writes, none of which must be reachable
    def set_electrode_status(self, marking, status):
        self.saved.append((marking, status))

    def finalise_qc_decisions(self, marking=None):
        self.saved.append(("finalise", marking))

    def save_sem_edx(self, *args, **kwargs):
        self.saved.append(("sem", args))


class TestTheReadsGoThrough(unittest.TestCase):
    def setUp(self) -> None:
        self.inner = FakeBackend()
        self.backend = ReadOnlyBackend(self.inner)

    def test_the_four_calls_the_window_makes(self) -> None:
        self.assertEqual(self.backend.get_metadata(), "electrodes")
        self.assertEqual(self.backend.load_vats(), {"B1": "28"})
        self.assertFalse(self.backend.vats_pending())
        self.assertEqual(self.backend.database_name(), "dataloggingDev")

    def test_the_two_database_names(self) -> None:
        self.assertEqual(self.backend.databases(),
                         ("dataloggingDev", "dataloggingDev"))

    def test_a_plant_database_of_its_own_is_reported_separately(self) -> None:
        """The banner has to name both when they differ, or it tells half the
        truth about where the numbers came from."""
        self.inner.plant_database_name = lambda: "dataloggingProd"
        self.assertEqual(self.backend.databases(),
                         ("dataloggingDev", "dataloggingProd"))


class TestEverythingElseIsRefused(unittest.TestCase):
    def setUp(self) -> None:
        self.inner = FakeBackend()
        self.backend = ReadOnlyBackend(self.inner)

    def test_a_write_is_not_reachable(self) -> None:
        for name in ("set_electrode_status", "finalise_qc_decisions",
                     "save_sem_edx", "undo_last_action"):
            with self.subTest(method=name):
                with self.assertRaises(AttributeError):
                    getattr(self.backend, name)
        self.assertEqual(self.inner.saved, [])

    def test_the_refusal_says_what_is_allowed(self) -> None:
        with self.assertRaises(AttributeError) as caught:
            self.backend.set_electrode_status
        self.assertIn("read only", str(caught.exception))
        self.assertIn("get_metadata", str(caught.exception))

    def test_nothing_can_be_assigned_onto_it(self) -> None:
        with self.assertRaises(AttributeError):
            self.backend.get_metadata = lambda: "tampered"
        self.assertEqual(self.backend.get_metadata(), "electrodes")

    def test_the_wrapped_backend_is_not_an_ordinary_attribute(self) -> None:
        """Not a security boundary, and not claimed to be. It is here so an
        accidental write is impossible and a deliberate one has to be written
        on purpose."""
        with self.assertRaises(AttributeError):
            self.backend._backend


if __name__ == "__main__":
    unittest.main()
