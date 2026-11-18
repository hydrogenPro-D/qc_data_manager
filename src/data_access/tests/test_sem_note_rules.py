"""Reading the old SEM notes as results.

Every note quoted here was typed into the production database by somebody, so
these are not invented cases: they are what the transformation has to cope with
once and will never see again.
"""

import unittest

from src.data_access.sem_edx.sem_note_rules import (
    CATEGORIES,
    CRACKED,
    FEW_MICRO_CRACKS,
    FINE,
    LESS_CRACKED,
    MICROCRACKS,
    classify,
    result_of,
)


class TestTheFive(unittest.TestCase):

    def test_they_are_in_order_of_how_bad_the_plate_is(self):
        self.assertEqual(CATEGORIES, (FINE, FEW_MICRO_CRACKS, MICROCRACKS,
                                      LESS_CRACKED, CRACKED))


class TestPlainNotes(unittest.TestCase):
    """The five as they are usually written, 587 of the 777 samples."""

    def test_the_obvious_ones(self):
        for note, expected in (("Fine", FINE),
                               ("Cracked", CRACKED),
                               ("Few microcracks", FEW_MICRO_CRACKS),
                               ("Less cracked", LESS_CRACKED),
                               ("Microcracked", MICROCRACKS)):
            self.assertEqual(result_of(note), expected, note)

    def test_case_never_matters(self):
        for note in ("CRACKED", "cracked", "Cracked"):
            self.assertEqual(result_of(note), CRACKED, note)


class TestSpelling(unittest.TestCase):
    """163 spellings of five opinions, which is what started all this."""

    def test_the_ways_microcracks_has_been_spelled(self):
        for note in ("Microcracks", "Microcracked", "Micro cracks",
                     "micro-cracks", "Microcracking", "Microcraks",
                     "Mickrocracked", "Mikrocracks"):
            self.assertEqual(result_of(note), MICROCRACKS, note)

    def test_the_ways_few_microcracks_has_been_spelled(self):
        for note in ("Few microcracks", "Few Microcracks", "few microcracks",
                     "Few micro cracks", "Few microcrackes", "Few mickrocrackes",
                     "Few microcacks", "Very few microcracks",
                     "Few small/micro cracks", "Few small cacks"):
            self.assertEqual(result_of(note), FEW_MICRO_CRACKS, note)


class TestTheScale(unittest.TestCase):
    """Where one result's words appear inside another's."""

    def test_few_beats_microcracks(self):
        """'Few microcracks' is the milder result, not the microcracked one."""
        self.assertEqual(result_of("Few microcracks"), FEW_MICRO_CRACKS)

    def test_less_cracked_is_not_read_as_cracked(self):
        self.assertEqual(result_of("Less cracking"), LESS_CRACKED)

    def test_slightly_cracked_is_the_mild_half_of_cracked(self):
        self.assertEqual(result_of("Slightly cracked"), LESS_CRACKED)

    def test_no_cracks_is_fine_however_it_is_written(self):
        for note in ("No cracks", "Vat 29 No Cracks", "no crack"):
            self.assertEqual(result_of(note), FINE, note)

    def test_small_cracks_are_microcracks(self):
        for note in ("Small cracks", "Microcracks/small cracks",
                     "Small cracks/microcracks"):
            self.assertEqual(result_of(note), MICROCRACKS, note)

    def test_a_crack_with_nothing_qualifying_it_is_cracked(self):
        for note in ("Cracks, should do post mortem", "White spots on sample, cracks",
                     "Large cracks Batch did not receive full plating recipe."):
            self.assertEqual(result_of(note), CRACKED, note)


class TestReadByHand(unittest.TestCase):
    """The three notes that are a pasted number, which QC called Fine."""

    def test_a_pasted_number_reads_as_what_qc_decided(self):
        for note in ("0.055353410012392204", "0.09385018171583047",
                     "0.5276924615662871"):
            self.assertEqual(result_of(note), FINE, note)

    def test_the_number_itself_is_still_kept(self):
        note = "0.5276924615662871"
        self.assertEqual(classify(note), (FINE, note))

    def test_any_other_number_is_still_left_for_a_human(self):
        self.assertEqual(classify("0.42"), ("", "0.42"))


class TestTheComment(unittest.TestCase):

    def test_a_note_that_is_only_its_result_leaves_no_comment(self):
        for note in ("Fine", "Few microcracks", "Sample fine", "Rigid and Fine"):
            self.assertEqual(classify(note), (result_of(note), ""), note)

    def test_a_note_that_says_more_keeps_all_of_it(self):
        """Word for word. Cutting the result out of somebody's sentence and
        keeping the rest produces text nobody wrote."""
        note = "Few microcracks, sample seems a bit dirty"
        self.assertEqual(classify(note), (FEW_MICRO_CRACKS, note))

    def test_the_vat_is_not_thrown_away(self):
        note = "Cracked Vat 30"
        self.assertEqual(classify(note), (CRACKED, note))

    def test_a_note_with_no_result_in_it_keeps_its_text_and_waits(self):
        """Nothing is invented. A human sets the result on these 14 samples."""
        note = "Measured in weight% -unvalid"
        self.assertEqual(classify(note), ("", note))

    def test_nothing_at_all(self):
        for note in ("", "   ", None):
            self.assertEqual(classify(note), ("", ""))


class TestRealProductionNotes(unittest.TestCase):
    """A walk through the awkward end of the 163, as they were actually typed."""

    CASES = (
        ("1st run in new vat.28 First dummy electrode plated in 3,3 pH. Cracked",
         CRACKED),
        ("31/7-2026. 2nd run in new vat 28 Testing dummy electrode plated in "
         "3,5 ph. Few Microcracks", FEW_MICRO_CRACKS),
        ("extra coupon; it wasn't marked, so sample was cut in a random corner "
         "few microcracks", FEW_MICRO_CRACKS),
        ("vat 29; Fine. Notes from production: Burn on the electrode on the "
         "clamp side.", FINE),
        ("less cracked; Lacking 2 EDS - AZtecOne not working", LESS_CRACKED),
        ("Microcracks (given as anode but tested as a cathode)", MICROCRACKS),
        ("White percipitate on sample, few microcracks", FEW_MICRO_CRACKS),
        ("Small cracks/microcracks (kind of on the edge, but a little worse "
         "than B14159)", MICROCRACKS),
        ("Fine, First electrode after chemical adjustment, Vat 30", FINE),
    )

    def test_each_one(self):
        for note, expected in self.CASES:
            with self.subTest(note=note):
                result, comment = classify(note)
                self.assertEqual(result, expected)
                self.assertEqual(comment, note)


if __name__ == "__main__":
    unittest.main()
