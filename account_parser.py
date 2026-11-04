"""Extract account holder details from the statement header using regex rules.

Banks label fields differently ("A/C No", "Account Number", "Acct No."), so each field
has a list of label variants. The first match wins.
"""
import re

from .utils import ANY_DATE_RE, clean_text, parse_amount, parse_date

# value = rest of the line after the label (stops at 2+ spaces, which usually separate columns)
_VALUE = r"[\s:.\-]*([^\n]+?)(?:\s{2,}|\n|$)"

FIELD_PATTERNS = {
    "account_holder": [
        r"(?:account\s*holder(?:'s)?\s*name|account\s*name|customer\s*name|name\s*of\s*(?:the\s*)?customer)",
        r"(?:^|\n)\s*name",
    ],
    "account_number": [
        r"(?:account\s*(?:number|no\.?)|a/?c\.?\s*(?:number|no\.?)|acct\.?\s*(?:number|no\.?))",
    ],
    "ifsc": [r"ifsc(?:\s*code)?"],
    "micr": [r"micr(?:\s*code)?"],
    "customer_id": [r"(?:customer\s*id|cust\s*id|cif\s*(?:no\.?|number|id)?)"],
    "branch": [r"(?:branch\s*name|branch)"],
    "account_type": [r"(?:account\s*type|a/c\s*type|product)"],
    "currency": [r"currency"],
}

IFSC_RE = re.compile(r"\b([A-Z]{4}[0O][A-Z0-9]{6})\b")  # 5th char is always 0 (OCR may read "O")
ACCOUNT_NO_RE = re.compile(r"\b(\d[\d\s-]{7,20}\d)\b")
PERIOD_LINE_RE = re.compile(r"^.*(?:period|from).*$", re.IGNORECASE | re.MULTILINE)
OPENING_BAL_RE = re.compile(r"opening\s*balance[^\d\n(-]*([\d,().-]+\d)", re.IGNORECASE)
CLOSING_BAL_RE = re.compile(r"closing\s*balance[^\d\n(-]*([\d,().-]+\d)", re.IGNORECASE)
BANK_NAME_RE = re.compile(r"^\s*([A-Z][A-Za-z&.\s-]{2,40}?\bBank(?:\s+(?:Ltd|Limited|of\s+[A-Z][a-z]+))?\.?)", re.MULTILINE)


# Two fields often share a line ("Name : X   Account No : Y"), so a value is cut where
# the next known label starts.
NEXT_LABEL_RE = re.compile(
    "|".join(p for key, pats in FIELD_PATTERNS.items() for p in pats if not p.startswith("(?:^"))
    + r"|\b(?:name|address|phone|mobile|email|statement|period|date|opening\s*balance|closing\s*balance)\s*:",
    re.IGNORECASE,
)


def _find_field(text, label_patterns):
    for label in label_patterns:
        for m in re.finditer(label + _VALUE, text, re.IGNORECASE):
            value = NEXT_LABEL_RE.split(m.group(1), maxsplit=1)[0]
            value = clean_text(value).strip(" :,-")
            if value:
                return value
    return None


def extract_account_details(text):
    """Return a dict of account details found in the statement text (missing ones are None)."""
    # Only look at the header part (before the transaction table) to avoid false matches.
    header = text[:4000]
    details = {key: _find_field(header, pats) for key, pats in FIELD_PATTERNS.items()}

    # --- clean / validate individual fields ---
    if details["account_number"]:
        m = ACCOUNT_NO_RE.search(details["account_number"])
        details["account_number"] = re.sub(r"[\s-]", "", m.group(1)) if m else None

    ifsc = IFSC_RE.search(details["ifsc"] or "") or IFSC_RE.search(header)
    details["ifsc"] = (ifsc.group(1)[:4] + "0" + ifsc.group(1)[5:]) if ifsc else None

    bank = BANK_NAME_RE.search(header)
    details["bank_name"] = clean_text(bank.group(1)) if bank else None

    # statement period: first line mentioning "period"/"from" that has two dates on it
    details["statement_from"] = details["statement_to"] = None
    for line in PERIOD_LINE_RE.findall(header):
        dates = [parse_date(d) for d in ANY_DATE_RE.findall(line)]
        dates = [d for d in dates if d]
        if len(dates) >= 2:
            details["statement_from"], details["statement_to"] = dates[0], dates[1]
            break

    ob = OPENING_BAL_RE.search(text)
    cb = CLOSING_BAL_RE.search(text)
    details["opening_balance"] = parse_amount(ob.group(1)) if ob else None
    details["closing_balance"] = parse_amount(cb.group(1)) if cb else None
    return details
