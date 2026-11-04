"""End-to-end tests on generated sample PDFs (3 layouts, password-protected, scanned)."""
import io

import openpyxl
import pytest

from samples.generate_samples import build_coastal, build_metro, build_sunrise, make_transactions, to_scanned
from src.exporter import to_csv_bytes, to_excel_bytes
from src.pdf_reader import PDFReadError, find_tesseract
from src.pipeline import process_statement

OPENING, TXNS = make_transactions()
CLOSING = TXNS[-1]["balance"]
HAS_TESSERACT = find_tesseract() is not None


def check(res):
    df = res["transactions"]
    assert len(df) == len(TXNS)
    assert round(df["debit"].sum(), 2) == round(sum(t["debit"] or 0 for t in TXNS), 2)
    assert round(df["credit"].sum(), 2) == round(sum(t["credit"] or 0 for t in TXNS), 2)
    assert df["balance"].iloc[-1] == CLOSING
    assert df["category"].notna().all()


@pytest.mark.parametrize("builder", [build_sunrise, build_metro, build_coastal])
def test_text_pdfs(builder):
    res = process_statement(builder(OPENING, TXNS))
    assert res["pdf_type"] == "text"
    check(res)
    assert not any("mismatch" in w or "reconcile" in w for w in res["warnings"])
    assert res["account"]["account_number"] and res["account"]["ifsc"]


def test_password_protected():
    pdf = build_metro(OPENING, TXNS, encrypt="1234")
    with pytest.raises(PDFReadError, match="password"):
        process_statement(pdf)
    with pytest.raises(PDFReadError, match="Wrong password"):
        process_statement(pdf, password="0000")
    check(process_statement(pdf, password="1234"))


def test_not_a_pdf():
    with pytest.raises(PDFReadError, match="not a valid PDF"):
        process_statement(b"hello world")


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed")
def test_scanned_pdf_uses_ocr():
    res = process_statement(to_scanned(build_sunrise(OPENING, TXNS)))
    assert res["pdf_type"] == "image"
    df = res["transactions"]
    # OCR is not perfect - require that nearly all rows and the final balance are recovered
    assert len(df) >= len(TXNS) - 2
    assert df["balance"].iloc[-1] == CLOSING
    assert res["account"]["ifsc"] == "SUNR0001234"


def test_exports():
    res = process_statement(build_sunrise(OPENING, TXNS))
    csv = to_csv_bytes(res["transactions"]).decode("utf-8-sig")
    assert csv.splitlines()[0].startswith("date,description,reference,debit,credit,balance,type,category")
    wb = openpyxl.load_workbook(io.BytesIO(to_excel_bytes(res["transactions"], res["account"])))
    assert wb.sheetnames == ["Transactions", "Account Details", "Category Summary", "Monthly Summary"]
    assert wb["Transactions"].max_row == len(TXNS) + 1


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed")
def test_mixed_pdf_page1_text_page2_scanned():
    import pypdfium2 as pdfium  # installed together with pdfplumber
    text_pdf = build_sunrise(OPENING, TXNS)
    mixed = pdfium.PdfDocument.new()
    mixed.import_pages(pdfium.PdfDocument(text_pdf), [0])                # page 1: text
    mixed.import_pages(pdfium.PdfDocument(to_scanned(text_pdf)), [1])    # page 2: scanned
    buf = io.BytesIO()
    mixed.save(buf)
    res = process_statement(buf.getvalue())
    assert res["pdf_type"] == "mixed"
    assert [p["type"] for p in res["pages"]] == ["text", "image"]
    check(res)


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed")
def test_password_protected_scanned_pdf():
    locked = to_scanned(build_sunrise(OPENING, TXNS), encrypt="1234")
    res = process_statement(locked, password="1234")
    assert res["pdf_type"] == "image"
    check(res)


def test_page_limit(monkeypatch):
    import src.pdf_reader as reader
    monkeypatch.setattr(reader, "MAX_PAGES", 1)
    with pytest.raises(PDFReadError, match="limit is 1"):
        process_statement(build_sunrise(OPENING, TXNS))  # 2 pages


def test_find_tesseract_windows_default_folder(monkeypatch, tmp_path):
    """On Windows Tesseract is often not on PATH; the default install folder must be found."""
    import src.pdf_reader as reader
    fake_exe = tmp_path / "tesseract.exe"
    fake_exe.write_text("")
    monkeypatch.setattr(reader, "TESSERACT_CMD", "")
    monkeypatch.setattr(reader.shutil, "which", lambda name: None)       # not on PATH
    monkeypatch.setattr(reader, "_WINDOWS_TESSERACT_PATHS", [str(tmp_path / "missing.exe"), str(fake_exe)])
    assert reader.find_tesseract() == str(fake_exe)
    monkeypatch.setattr(reader, "TESSERACT_CMD", str(fake_exe))        # explicit setting wins
    assert reader.find_tesseract() == str(fake_exe)
