"""Turn statement text into a list of transactions.

Works on plain text lines, so the same logic handles text PDFs and OCR output and
does not depend on one bank's exact table layout.

Idea:
  1. A line that STARTS with a date begins a new transaction.
  2. Numbers with decimals on that line are amounts; the LAST one is usually the balance.
  3. Lines without a date are continuation of the previous description (wrapped text).
  4. Debit vs credit is decided by (in order of trust):
       a. explicit "Dr"/"Cr" marker next to the amount
       b. separate debit/credit columns (3 numbers on the line, e.g. "0.00 500.00 1,200.00")
       c. change in running balance (prev_balance - amount == balance  -> debit)
       d. keywords in the description ("salary", "refund"... -> credit)
"""
import re

from .utils import AMOUNT_RE, ANY_DATE_RE, LINE_START_DATE_RE, clean_text, parse_amount, parse_date

# Lines that are never part of a transaction description (headers, footers, totals...)
NOISE_PATTERNS = re.compile(
    r"(page\s*\d+(\s*of\s*\d+)?|statement\s+of\s+account|^\s*(txn|tran|transaction|value)?\s*date\b.*"
    r"(narration|description|particulars|details)|computer\s+generated|does\s+not\s+require\s+signature|"
    r"^\s*total\b|end\s+of\s+statement|^\s*closing\s+balance|^\s*opening\s+balance|carried\s+forward|"
    r"brought\s+forward|registered\s+office|customer\s+care|toll\s*free|www\.|https?://|"
    r"^\s*(withdrawal|deposit|debit|credit)s?\b.*\bbalance\b)",
    re.IGNORECASE,
)
OPENING_ROW_RE = re.compile(r"(opening\s+balance|balance\s+(b/?f|brought\s+forward)|^\s*b/?f\b)", re.IGNORECASE)
CLOSING_ROW_RE = re.compile(r"(closing\s+balance|balance\s+c/?f|carried\s+forward)", re.IGNORECASE)

CREDIT_WORDS = re.compile(
    r"\b(salary|sal\b|refund|reversal|rev\b|cashback|interest\s*(credit|cr|paid)|int\.?\s*cr|deposit|"
    r"received|recd|by\s+transfer|by\s+clg|credited|dividend|cr\b)",
    re.IGNORECASE,
)

MAX_CONTINUATION_LINES = 3
TOLERANCE = 0.01


def _detect_column_order(lines):
    """Return 'debit_first' or 'credit_first' based on the table header (default: debit first)."""
    for line in lines:
        low = line.lower()
        d = re.search(r"\b(debit|withdrawal|withdrawals|dr)\b", low)
        c = re.search(r"\b(credit|deposit|deposits|cr)\b", low)
        if d and c and "balance" in low:
            return "debit_first" if d.start() < c.start() else "credit_first"
    return "debit_first"


REFERENCE_RE = re.compile(r"(?<!\S)\d{6,20}(?!\S)")  # standalone cheque / ref numbers


def _split_description_and_amounts(rest):
    """Return (description, [(value, marker), ...], [ref numbers]) for the text after the date."""
    matches = list(AMOUNT_RE.finditer(rest))
    amounts = [(parse_amount(m.group(1)), (m.group(2) or "").upper()) for m in matches]
    description = rest[: matches[0].start()] if matches else rest
    # Drop a second "value date" column and stray separators from the description
    description = ANY_DATE_RE.sub(" ", description)
    description = re.sub(r"\s[-|]+\s", " ", description)
    refs = REFERENCE_RE.findall(description)
    description = REFERENCE_RE.sub(" ", description)
    # OCR clean-up: stray symbols at the start ("~ ", ". ", "— ") and common misreads
    description = re.sub(r"^[^A-Za-z0-9(]+", "", clean_text(description))
    description = re.sub(r"\bUP[1l|]\s?/", "UPI/", description)
    return description, amounts, refs


def extract_raw_rows(text):
    """Step 1: group text lines into raw transaction rows (date + description + amount tokens)."""
    lines = text.splitlines()
    rows, current, cont_lines = [], None, 0

    for line in lines:
        if not line.strip():
            continue
        m = LINE_START_DATE_RE.match(line)
        date = parse_date(m.group(1)) if m else None

        if date:
            description, amounts, refs = _split_description_and_amounts(line[m.end():])
            current = {"date": date, "description": description, "amounts": amounts, "refs": refs,
                       "raw": line.strip()}
            rows.append(current)
            cont_lines = 0
            continue

        if NOISE_PATTERNS.search(line):
            current = None  # header/footer: stop attaching text to the previous transaction
            continue

        if current is not None and cont_lines < MAX_CONTINUATION_LINES:
            desc, amounts, refs = _split_description_and_amounts(line)
            current["refs"] += refs
            if amounts and not current["amounts"]:
                current["amounts"] = amounts  # amounts landed on the wrapped line (common in OCR)
            if desc:
                current["description"] = clean_text(current["description"] + " " + desc)
            current["raw"] += " | " + line.strip()
            cont_lines += 1
    return rows


def _resolve_row(row, prev_balance, column_order):
    """Step 2: work out debit / credit / balance for one raw row."""
    amounts = [(v, mk) for v, mk in row["amounts"] if v is not None]
    debit = credit = balance = None
    notes = []

    if not amounts:
        return None

    # --- balance is the last amount when there are 2+ numbers ---
    if len(amounts) >= 2:
        bal_val, bal_marker = amounts[-1]
        balance = -abs(bal_val) if bal_marker == "DR" else bal_val
        txn_amounts = amounts[:-1]
    else:
        txn_amounts = amounts

    # --- a. explicit Dr / Cr marker on the transaction amount ---
    for value, marker in txn_amounts:
        if marker == "DR":
            debit = abs(value)
        elif marker == "CR":
            credit = abs(value)
    if debit is not None or credit is not None:
        return debit, credit, balance, notes

    non_zero = [abs(v) for v, _ in txn_amounts if abs(v) > 0]

    # --- c. separate debit and credit columns (one of them 0.00) ---
    if len(txn_amounts) >= 2 and len(non_zero) == 1:
        first, second = abs(txn_amounts[0][0]), abs(txn_amounts[1][0])
        d_val, c_val = (first, second) if column_order == "debit_first" else (second, first)
        debit, credit = (d_val or None), (c_val or None)
        return debit, credit, balance, notes

    amount = non_zero[0] if non_zero else 0.0
    if len(non_zero) > 1:
        notes.append("Multiple amounts on line - check")

    # --- b. use running balance change ---
    if balance is not None and prev_balance is not None:
        if abs(prev_balance - amount - balance) <= TOLERANCE:
            return amount, None, balance, notes
        if abs(prev_balance + amount - balance) <= TOLERANCE:
            return None, amount, balance, notes

    # --- d. keyword fallback ---
    if CREDIT_WORDS.search(row["description"]):
        credit = amount
    else:
        debit = amount
    notes.append("Debit/Credit guessed from description")
    return debit, credit, balance, notes


def parse_transactions(text, opening_balance=None):
    """Return (transactions: list[dict], opening_balance_found, closing_balance_found)."""
    column_order = _detect_column_order(text.splitlines())
    # rows without any amount are things like "01/01/2024 to 31/01/2024" - not transactions
    raw_rows = [r for r in extract_raw_rows(text) if r["amounts"]]

    # Some banks list newest first - process oldest first so running-balance logic works
    if len(raw_rows) > 1 and raw_rows[0]["date"] > raw_rows[-1]["date"]:
        raw_rows.reverse()

    transactions = []
    prev_balance = opening_balance
    found_opening = found_closing = None

    for row in raw_rows:
        desc = row["description"]
        # Opening / closing balance rows are not transactions, but they help validation
        if OPENING_ROW_RE.search(desc) and row["amounts"]:
            found_opening = row["amounts"][-1][0]
            prev_balance = found_opening if prev_balance is None else prev_balance
            continue
        if CLOSING_ROW_RE.search(desc):
            if row["amounts"]:
                found_closing = row["amounts"][-1][0]
            continue

        resolved = _resolve_row(row, prev_balance, column_order)
        if resolved is None:
            continue  # date line with no amounts (e.g. "01/01/2024 to 31/01/2024")
        debit, credit, balance, notes = resolved

        # balance check: does prev - debit + credit == balance ?
        if prev_balance is not None and balance is not None:
            expected = round(prev_balance - (debit or 0) + (credit or 0), 2)
            if abs(expected - balance) > TOLERANCE:
                notes.append(f"Balance mismatch (expected {expected:,.2f})")

        transactions.append({
            "date": row["date"],
            "description": desc or "(no description)",
            "reference": " ".join(row["refs"]),
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "type": "Credit" if credit else "Debit",
            "remarks": "; ".join(notes),
        })
        if balance is not None:
            prev_balance = balance
        elif prev_balance is not None:
            prev_balance = round(prev_balance - (debit or 0) + (credit or 0), 2)

    return transactions, found_opening, found_closing
