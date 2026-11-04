"""Small helpers for parsing amounts and dates found in bank statements."""
import re
from datetime import datetime

# Amount like 1,23,456.78 / 123456.78 / 1,234.5 / (1,234.00) / -500.00, optionally followed by Cr/Dr.
# We require 2 decimal places (or 1) so that reference numbers / years are not mistaken for amounts.
AMOUNT_RE = re.compile(
    r"(?<![\w/.-])"                     # not glued to another token (avoids dates / ref numbers)
    r"(\(?-?\d{1,3}(?:,\d{2,3})*\.\d{1,2}\)?|\(?-?\d+\.\d{1,2}\)?)(?!\d)"
    r"(?:\s?(CR|DR|Cr|Dr|cr|dr)\b)?"
)

DATE_PATTERNS = [
    # (regex, list of strptime formats to try)
    (r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", ["%d/%m/%Y", "%d-%m-%Y"]),
    (r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", ["%Y-%m-%d", "%Y/%m/%d"]),
    (r"\d{1,2}[/-]\d{1,2}[/-]\d{2}", ["%d/%m/%y", "%d-%m-%y"]),
    (r"\d{1,2}[\s-][A-Za-z]{3,9}[\s,-]+\d{4}", ["%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%d-%B-%Y"]),
    (r"\d{1,2}[\s-][A-Za-z]{3}[\s-]\d{2}", ["%d %b %y", "%d-%b-%y"]),
]

# A date at the very start of a line (this is how we spot a transaction row)
LINE_START_DATE_RE = re.compile(r"^\s*(" + "|".join(p for p, _ in DATE_PATTERNS) + r")\b")
ANY_DATE_RE = re.compile(r"(" + "|".join(p for p, _ in DATE_PATTERNS) + r")")


def parse_amount(text):
    """'1,23,456.78' -> 123456.78, '(500.00)' -> -500.0. Returns None if not a number."""
    if text is None:
        return None
    s = str(text).strip()
    if not s or s in {"-", "--"}:
        return None
    negative = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r"[^\d.]", "", s)
    if not s or s.count(".") > 1:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def parse_date(text):
    """Try all known date formats. Returns a datetime.date or None."""
    if not text:
        return None
    s = re.sub(r"\s+", " ", str(text).replace(",", " ")).strip()
    for _, formats in DATE_PATTERNS:
        for fmt in formats:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None


def clean_text(text):
    """Collapse whitespace."""
    return re.sub(r"\s+", " ", text or "").strip()


def mask_account_number(acc):
    """XXXXXXXX1234 style masking for privacy."""
    if not acc:
        return acc
    acc = str(acc)
    return "X" * max(len(acc) - 4, 0) + acc[-4:]
