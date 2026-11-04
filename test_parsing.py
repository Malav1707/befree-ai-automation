from datetime import date

from src.account_parser import extract_account_details
from src.transaction_parser import parse_transactions
from src.utils import parse_amount, parse_date


# ---------------------------------------------------------------- utils
def test_parse_amount_formats():
    assert parse_amount("1,23,456.78") == 123456.78
    assert parse_amount("1,234.50") == 1234.5
    assert parse_amount("(500.00)") == -500.0
    assert parse_amount("-75.25") == -75.25
    assert parse_amount("-") is None
    assert parse_amount("") is None


def test_parse_date_formats():
    assert parse_date("05/01/2024") == date(2024, 1, 5)
    assert parse_date("05-01-24") == date(2024, 1, 5)
    assert parse_date("2024-01-05") == date(2024, 1, 5)
    assert parse_date("05 Jan 2024") == date(2024, 1, 5)
    assert parse_date("05-Jan-24") == date(2024, 1, 5)
    assert parse_date("5 January 2024") == date(2024, 1, 5)
    assert parse_date("31/02/2024") is None  # invalid date


# ---------------------------------------------------------------- transactions
def test_debit_credit_from_running_balance():
    text = """Date Narration Withdrawal Deposit Balance
01/01/2024 Opening Balance 1,000.00
02/01/2024 UPI/SWIGGY/order 200.00 800.00
03/01/2024 NEFT SALARY ACME 5,000.00 5,800.00
"""
    tx, opening, _ = parse_transactions(text)
    assert opening == 1000.0
    assert len(tx) == 2
    assert tx[0]["debit"] == 200.0 and tx[0]["credit"] is None
    assert tx[1]["credit"] == 5000.0 and tx[1]["debit"] is None
    assert all(t["remarks"] == "" for t in tx)


def test_dr_cr_markers():
    text = """01-02-2024 ATM WDL 500.00 Dr 9,500.00 Cr
02-02-2024 INTEREST CREDIT 20.00 Cr 9,520.00 Cr
"""
    tx, _, _ = parse_transactions(text)
    assert tx[0]["debit"] == 500.0 and tx[0]["balance"] == 9500.0
    assert tx[1]["credit"] == 20.0


def test_separate_columns_with_zero_and_credit_first_layout():
    text = """Date Particulars Credit Debit Balance
01/03/2024 POS AMAZON 0.00 450.00 9,550.00
02/03/2024 REFUND AMAZON 450.00 0.00 10,000.00
"""
    tx, _, _ = parse_transactions(text, opening_balance=10000.0)
    assert tx[0]["debit"] == 450.0 and tx[0]["credit"] is None
    assert tx[1]["credit"] == 450.0 and tx[1]["debit"] is None


def test_multiline_description_and_page_noise():
    text = """01/01/24 NEFT/ACME TECHNOLOGIES PVT LTD/SALARY 85,000.00 1,10,000.00
FOR JAN 2024
Page 1 of 2   This is a computer generated statement
Date Narration Withdrawal Amt. Deposit Amt. Closing Balance
02/01/24 POS AMAZON 550.00 1,09,450.00
"""
    tx, _, _ = parse_transactions(text, opening_balance=25000.0)
    assert tx[0]["description"] == "NEFT/ACME TECHNOLOGIES PVT LTD/SALARY FOR JAN 2024"
    assert tx[0]["credit"] == 85000.0
    assert tx[1]["description"] == "POS AMAZON"  # footer/header not glued on
    assert tx[1]["debit"] == 550.0


def test_newest_first_statement_is_reordered():
    text = """2024-01-03 UPI ZOMATO 100.00 Dr 700.00 Cr
2024-01-02 UPI SWIGGY 200.00 Dr 800.00 Cr
"""
    tx, _, _ = parse_transactions(text, opening_balance=1000.0)
    assert [t["date"] for t in tx] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert all(t["remarks"] == "" for t in tx)


def test_balance_mismatch_is_flagged():
    text = """01/01/2024 UPI SWIGGY 200.00 0.00 800.00
02/01/2024 UPI ZOMATO 100.00 0.00 650.00
"""
    tx, _, _ = parse_transactions(text, opening_balance=1000.0)
    assert tx[0]["remarks"] == ""
    assert "Balance mismatch" in tx[1]["remarks"]


def test_reference_numbers_are_separated():
    text = "05/01/24 POS DMART 900123 05/01/24 300.00 700.00\n"
    tx, _, _ = parse_transactions(text, opening_balance=1000.0)
    assert tx[0]["description"] == "POS DMART"
    assert tx[0]["reference"] == "900123"


# ---------------------------------------------------------------- account details
def test_account_details_two_fields_per_line():
    text = """Sunrise Bank Ltd
Account Name : RAVI KUMAR MEHTA Account No : 50100234567891
Address : 12, Lake View Road IFSC : SUNR0001234
Customer ID : 88776655 Branch : Pune Camp
Statement Period : 01/01/2024 to 28/02/2024
Opening Balance : 25,000.00 Closing Balance : 64,764.40
"""
    d = extract_account_details(text)
    assert d["account_holder"] == "RAVI KUMAR MEHTA"
    assert d["account_number"] == "50100234567891"
    assert d["ifsc"] == "SUNR0001234"
    assert d["customer_id"] == "88776655"
    assert d["branch"] == "Pune Camp"
    assert d["bank_name"] == "Sunrise Bank Ltd"
    assert d["statement_from"] == date(2024, 1, 1) and d["statement_to"] == date(2024, 2, 28)
    assert d["opening_balance"] == 25000.0 and d["closing_balance"] == 64764.40


def test_account_number_with_spaces():
    d = extract_account_details("Customer Name: Ms. Ananya Iyer A/C No.: 0012 3456 7890\nIFSC Code: MTRO0000456")
    assert d["account_holder"] == "Ms. Ananya Iyer"
    assert d["account_number"] == "001234567890"
