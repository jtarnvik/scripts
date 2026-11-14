"""
paperless_names.py

Title and document date from an archive filename — decision 11 of the Paperless plan in paperless-plan.md,
extended in step 8. Shared by the daily ingest (paperless_ingest.py) and the backfill (paperless_backfill.py).

Rules, in order:
  1. a full date anywhere in the name, 2026-03-01 or 20260301           -> that date
  2. a six-digit date: " - YYMMDD" as a part of its own                 -> that date
     (Nordea - Betalning - 181108), or YY-MM-DD (FH 17-06-03)
  3. a name ending " - YYMM", month 01-12                               -> the first of that month
  4. a name ending " - YYYY"                                            -> 31 December of that year
  5. anything else                                                      -> no date; Paperless reads it from the content

The reference year is where the file belongs in time: its year folder (backfill), a year at the start of its
name when it is in no year folder ("2017 - Bergvärme"), or the current year (daily ingest). A two-digit year
(rules 2 and 3) must be at most one year from it — this keeps "... - 2012" in a 2012 folder from being read as
December 2020. Without a reference year, a trailing " - NNNN" that is a plausible year is read as one, and
two-digit years only need to lie between 2000 and next year.

The title is the filename without its extension and without a trailing " (1)"-style suffix, which Hazel adds on a
name clash. Names are normalised to NFC: macOS file systems may hand out "å" as "a" plus a combining ring.
"""

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Paperless refuses longer titles
MAX_TITLE_LENGTH = 128

# Years outside this range in a name are not dates (part numbers, colour codes ...)
EARLIEST_YEAR = 1970

DASHED_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
COMPACT_DATE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
SIX_DIGIT_DATE = re.compile(r"(?:^| - )(\d{2})(\d{2})(\d{2})(?= - |$)")
SHORT_DASHED_DATE = re.compile(r"(?<![\d-])(\d{2})-(\d{2})-(\d{2})(?![\d-])")
TRAILING_FOUR_DIGITS = re.compile(r" - (\d{4})$")
LEADING_YEAR = re.compile(r"^((?:19|20)\d\d)(?!\d)")
HAZEL_SUFFIX = re.compile(r" \(\d+\)$")


@dataclass(frozen=True)
class ParsedName:
    title: str
    created: Optional[date]
    # Which rule set the date: "full-date", "yymmdd", "yymm", "year" or "none" — for logs and the dry run
    rule: str


def parse_name(filename: str, reference_year: Optional[int]) -> ParsedName:
    stem = unicodedata.normalize("NFC", os.path.splitext(filename)[0])
    stem = HAZEL_SUFFIX.sub("", stem).strip()
    title = stem[:MAX_TITLE_LENGTH]
    latest_year = (reference_year if reference_year is not None else date.today().year) + 1

    found = _find_full_date(stem, latest_year)
    if found is not None:
        return ParsedName(title, found, "full-date")

    found = _find_six_digit_date(stem, reference_year, latest_year)
    if found is not None:
        return ParsedName(title, found, "yymmdd")

    match = TRAILING_FOUR_DIGITS.search(stem)
    if match is not None:
        digits = match.group(1)
        year = int(digits)
        plausible_year = EARLIEST_YEAR <= year <= latest_year

        # Without a reference year, "- 2012" is more likely the year 2012 than December 2020
        if reference_year is None and plausible_year:
            return ParsedName(title, date(year, 12, 31), "year")

        year_month = _as_year_month(digits, reference_year, latest_year)
        if year_month is not None:
            return ParsedName(title, year_month, "yymm")

        if plausible_year:
            return ParsedName(title, date(year, 12, 31), "year")

    return ParsedName(title, None, "none")


def leading_year(filename: str) -> Optional[int]:
    """The year a name starts with, as in "2017 - Bergvärme.pdf", or None."""
    match = LEADING_YEAR.match(unicodedata.normalize("NFC", filename))
    return int(match.group(1)) if match else None


def _find_full_date(stem: str, latest_year: int) -> Optional[date]:
    for pattern in (DASHED_DATE, COMPACT_DATE):
        for match in pattern.finditer(stem):
            year = int(match.group(1))
            if EARLIEST_YEAR <= year <= latest_year:
                found = _date_or_none(year, int(match.group(2)), int(match.group(3)))
                if found is not None:
                    return found
    return None


def _find_six_digit_date(stem: str, reference_year: Optional[int], latest_year: int) -> Optional[date]:
    for pattern in (SIX_DIGIT_DATE, SHORT_DASHED_DATE):
        for match in pattern.finditer(stem):
            year = 2000 + int(match.group(1))
            if _two_digit_year_fits(year, reference_year, latest_year):
                found = _date_or_none(year, int(match.group(2)), int(match.group(3)))
                if found is not None:
                    return found
    return None


def _as_year_month(digits: str, reference_year: Optional[int], latest_year: int) -> Optional[date]:
    year = 2000 + int(digits[:2])
    month = int(digits[2:])
    if month < 1 or month > 12:
        return None
    if not _two_digit_year_fits(year, reference_year, latest_year):
        return None
    return date(year, month, 1)


def _two_digit_year_fits(year: int, reference_year: Optional[int], latest_year: int) -> bool:
    if reference_year is not None:
        return abs(year - reference_year) <= 1
    return year <= latest_year


def _date_or_none(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None
