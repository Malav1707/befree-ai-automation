"""End-to-end pipeline: PDF -> text -> account details + transactions -> categories -> checks."""
import logging
import time

import pandas as pd

from .account_parser import extract_account_details
from .classifier import TransactionClassifier
from .pdf_reader import read_pdf
from .transaction_parser import parse_transactions

log = logging.getLogger(__name__)
_classifier = None


def get_classifier():
    """Build the classifier once and reuse it (training takes ~1 second)."""
    global _classifier
    if _classifier is None:
        _classifier = TransactionClassifier()
    return _classifier


def process_statement(file, filename="statement.pdf", password=None, force_ocr=False, classifier=None):
    """
    Returns dict with keys:
      pdf_type, pages, account (dict), transactions (DataFrame), warnings (list[str])
    Raises pdf_reader.PDFReadError for unreadable files.
    """
    classifier = classifier or get_classifier()
    started = time.perf_counter()
    pdf = read_pdf(file, password=password, force_ocr=force_ocr)
    full_text = "\n".join(p["text"] for p in pdf["pages"])
    warnings = []

    # ---- account details
    account = extract_account_details(full_text)
    account["source_file"] = filename
    account["pdf_type"] = pdf["pdf_type"]

    # ---- transactions
    txns, found_open, found_close = parse_transactions(full_text, opening_balance=account["opening_balance"])
    if account["opening_balance"] is None:
        account["opening_balance"] = found_open
    if account["closing_balance"] is None:
        account["closing_balance"] = found_close

    df = pd.DataFrame(txns, columns=["date", "description", "reference", "debit", "credit", "balance", "type", "remarks"])

    if df.empty:
        warnings.append("No transactions found. The layout may be unsupported or the scan quality too low "
                        "(try 'Force OCR').")
    else:
        # ---- classification
        preds = classifier.classify_many(df["description"].tolist(), df["type"].tolist())
        df["category"] = [p["category"] for p in preds]
        df["confidence"] = [p["confidence"] for p in preds]
        df["method"] = [p["method"] for p in preds]
        df["source_file"] = filename
        warnings += _validate(df, account)

    if pdf["pdf_type"] != "text":
        warnings.append(f"PDF is {pdf['pdf_type']} (scanned pages were read with OCR) - please review values.")
    missing = [k for k in ("account_holder", "account_number", "ifsc") if not account.get(k)]
    if missing:
        warnings.append("Could not find: " + ", ".join(missing))

    # no personal data in logs - only counts and timing
    log.info("Processed %s: %d transactions, %d warning(s) in %.1fs",
             pdf["pdf_type"], len(df), len(warnings), time.perf_counter() - started)
    return {"pdf_type": pdf["pdf_type"], "pages": pdf["pages"], "account": account,
            "transactions": df, "warnings": warnings}


def _validate(df, account):
    """Sanity checks that catch extraction errors."""
    warnings = []
    mism = df["remarks"].str.contains("Balance mismatch", na=False).sum()
    if mism:
        warnings.append(f"{mism} row(s) have a running-balance mismatch - see 'remarks'.")

    guessed = df["remarks"].str.contains("guessed", na=False).sum()
    if guessed:
        warnings.append(f"{guessed} row(s): debit/credit direction guessed from description.")

    dup = df.duplicated(subset=["date", "description", "debit", "credit", "balance"], keep="first")
    if dup.any():
        df.loc[dup, "remarks"] = (df.loc[dup, "remarks"] + "; Possible duplicate").str.strip("; ")
        warnings.append(f"{int(dup.sum())} possible duplicate row(s) flagged.")

    # closing balance in the header should equal the last running balance
    last_balance = df["balance"].dropna().iloc[-1] if df["balance"].notna().any() else None
    if account.get("closing_balance") is not None and last_balance is not None:
        if abs(account["closing_balance"] - last_balance) > 0.01:
            warnings.append(f"Closing balance {account['closing_balance']:,.2f} does not match last "
                            f"transaction balance {last_balance:,.2f} - some rows may be missing.")

    # opening + credits - debits == closing
    if account.get("opening_balance") is not None and last_balance is not None:
        computed = account["opening_balance"] + df["credit"].fillna(0).sum() - df["debit"].fillna(0).sum()
        if abs(computed - last_balance) > 0.01:
            warnings.append(f"Totals do not reconcile: opening + credits - debits = {computed:,.2f}, "
                            f"statement shows {last_balance:,.2f}.")

    if not df["date"].is_monotonic_increasing:
        warnings.append("Dates are not in order - check for mis-read dates.")
    return warnings
