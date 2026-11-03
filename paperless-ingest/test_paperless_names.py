"""
Unit tests for paperless_names.py. Run from this folder:

    python3 -m unittest test_paperless_names
"""

import unicodedata
import unittest
from datetime import date

from paperless_names import parse_name


class FullDateTest(unittest.TestCase):

    def test_dashed_date_anywhere_in_the_name(self):
        parsed = parse_name("Kvitto 2026-03-14 Clas Ohlson.pdf", 2026)
        self.assertEqual(date(2026, 3, 14), parsed.created)
        self.assertEqual("full-date", parsed.rule)

    def test_compact_date(self):
        self.assertEqual(date(2026, 3, 1), parse_name("Faktura 20260301.pdf", 2026).created)

    def test_full_date_wins_over_a_trailing_year_month(self):
        self.assertEqual(date(2025, 12, 30), parse_name("Hyra 2025-12-30 - 2601.pdf", 2026).created)

    def test_impossible_date_is_ignored(self):
        self.assertEqual("none", parse_name("Order 20261399.pdf", 2026).rule)

    def test_longer_digit_run_is_not_a_date(self):
        self.assertEqual("none", parse_name("Ordernummer 1202603011.pdf", 2026).rule)

    def test_date_far_in_the_future_is_ignored(self):
        self.assertEqual("none", parse_name("Garanti till 2031-05-01.pdf", 2026).rule)


class YearMonthTest(unittest.TestCase):

    def test_trailing_year_month_is_the_first_of_the_month(self):
        parsed = parse_name("SBAB Lön - 2603.pdf", 2026)
        self.assertEqual(date(2026, 3, 1), parsed.created)
        self.assertEqual("yymm", parsed.rule)

    def test_year_month_one_year_from_the_reference_year(self):
        # A December document filed in January
        self.assertEqual(date(2025, 12, 1), parse_name("Nordea - Gold - 2512.pdf", 2026).created)
        self.assertEqual(date(2027, 1, 1), parse_name("Nordea - Gold - 2701.pdf", 2026).created)

    def test_year_month_too_far_from_the_reference_year_is_a_year(self):
        # "2012" in a 2012 folder is the year 2012, not December 2020
        parsed = parse_name("Årsbesked Skandia - 2012.pdf", 2012)
        self.assertEqual(date(2012, 12, 31), parsed.created)
        self.assertEqual("year", parsed.rule)

    def test_month_out_of_range_is_not_a_year_month(self):
        self.assertEqual("year", parse_name("Årsbesked - 2025.pdf", 2026).rule)

    def test_hazel_suffix_is_removed_before_matching(self):
        parsed = parse_name("SBAB Lön - 2603 (1).pdf", 2026)
        self.assertEqual(date(2026, 3, 1), parsed.created)
        self.assertEqual("SBAB Lön - 2603", parsed.title)


class YearTest(unittest.TestCase):

    def test_trailing_year_is_the_last_day_of_that_year(self):
        parsed = parse_name("Årsbesked Nordea - 2025.pdf", 2026)
        self.assertEqual(date(2025, 12, 31), parsed.created)
        self.assertEqual("year", parsed.rule)

    def test_implausible_trailing_number_is_no_date(self):
        self.assertEqual("none", parse_name("Ritning - 4711.pdf", 2026).rule)

    def test_number_without_the_separator_is_no_date(self):
        self.assertEqual("none", parse_name("Telefonnummerflytt 2603.pdf", 2026).rule)


class NoDateTest(unittest.TestCase):

    def test_no_date_in_the_name(self):
        parsed = parse_name("Spec Lampor kök.pdf", 2026)
        self.assertIsNone(parsed.created)
        self.assertEqual("none", parsed.rule)

    def test_leading_year_is_not_covered(self):
        # Names that start with a year are not part of decision 11 (see step 3 of the plan)
        self.assertEqual("none", parse_name("2018 - Färgkod grund - NCS S 2002-Y.docx", 2018).rule)


class TitleTest(unittest.TestCase):

    def test_title_is_the_name_without_extension(self):
        self.assertEqual("Spec Lampor kök", parse_name("Spec Lampor kök.pdf", 2026).title)

    def test_title_is_normalised_to_nfc(self):
        decomposed = unicodedata.normalize("NFD", "Försäkringsbrev.pdf")
        self.assertEqual("Försäkringsbrev", parse_name(decomposed, 2026).title)
        self.assertEqual(unicodedata.normalize("NFC", "Försäkringsbrev"), parse_name(decomposed, 2026).title)

    def test_only_the_last_extension_is_removed(self):
        self.assertEqual("Kontrakt v1.2", parse_name("Kontrakt v1.2.pdf", 2026).title)

    def test_long_title_is_cut(self):
        self.assertEqual(128, len(parse_name("x" * 200 + ".pdf", 2026).title))


if __name__ == "__main__":
    unittest.main()
