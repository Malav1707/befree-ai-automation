import shutil

import pytest

from src.classifier import TRAINING_FILE, TransactionClassifier, normalize


@pytest.fixture(scope="module")
def clf(tmp_path_factory):
    # use a temp corrections file so tests never touch the real one
    tmp = tmp_path_factory.mktemp("clf")
    return TransactionClassifier(corrections_file=str(tmp / "corrections.csv"))


def test_normalize():
    assert normalize("UPI/412345678901/SWIGGY*Order@icici") == "upi swiggy order icici"


@pytest.mark.parametrize("desc,expected", [
    ("UPI/412345678901/SWIGGY/swiggy@icici", "Food & Dining"),
    ("ATM WDL SBI ATM KORAMANGALA", "Cash Withdrawal"),
    ("NACH/BAJAJ FINANCE LTD/EMI 22", "EMI & Loans"),
    ("BILLPAY BESCOM ELECTRICITY BILL", "Utilities & Bills"),
    ("UPI/UBER INDIA SYSTEMS/trip", "Transport"),
    ("UPI/UBER EATS/order", "Food & Dining"),     # longest keyword wins over "uber"
])
def test_rules(clf, desc, expected):
    r = clf.classify(desc, "Debit")
    assert r["category"] == expected
    assert r["method"].startswith("rule")


def test_priority_rules_override_merchant(clf):
    assert clf.classify("REFUND AMAZON ORDER", "Credit")["category"] == "Refund & Cashback"
    assert clf.classify("TDS ON INTEREST", "Debit")["category"] == "Taxes"
    assert clf.classify("GST ON CHARGES", "Debit")["category"] == "Bank Charges"


def test_ml_handles_unknown_merchant(clf):
    r = clf.classify("POS WESTSIDE TRENT LTD", "Debit")  # not in any keyword list
    assert r["method"] == "ml"
    assert r["category"] == "Shopping"


def test_credit_cannot_be_expense(clf):
    r = clf.classify("IMPS/P2P/PRIYA PATEL/Shared rent", "Credit")
    assert r["category"] == "Transfer"


def test_unknown_credit_is_income(clf):
    r = clf.classify("ZXQW 77", "Credit")
    assert r["category"] in {"Other Income", "Transfer"} or r["method"] == "ml"


def test_learning_from_corrections(tmp_path):
    shutil.copy(TRAINING_FILE, tmp_path / "train.csv")
    c = TransactionClassifier(training_file=str(tmp_path / "train.csv"),
                              corrections_file=str(tmp_path / "corr.csv"))
    pairs = [("POS KRISHNA STATIONERS", "Education")] * 5
    assert c.add_corrections(pairs) == 5
    assert c.classify("POS KRISHNA STATIONERS", "Debit")["category"] == "Education"
