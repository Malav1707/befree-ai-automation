"""Read a PDF and return its text, page by page.

Each page is checked separately:
  * if pdfplumber finds real text on it  -> "text" page
  * otherwise (scanned / photographed)   -> "image" page, rendered to a picture and read
                                            with Tesseract OCR

So a PDF can be "text", "image" or "mixed" (some pages scanned, some not).
"""
import io
import logging
import os
import shutil

import pdfplumber

from .config import MAX_PAGES, OCR_DPI, OCR_LANG, TESSERACT_CMD

log = logging.getLogger(__name__)
MIN_CHARS_FOR_TEXT_PAGE = 50  # less than this = treat page as scanned


class PDFReadError(Exception):
    """Raised for files we cannot open (corrupt, wrong password, not a PDF, too large...)."""


# Windows installers put Tesseract here but often do not add it to PATH
_WINDOWS_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
]


def find_tesseract():
    """Return the path of the Tesseract program, or None if it is not installed."""
    if TESSERACT_CMD:
        return TESSERACT_CMD if os.path.exists(TESSERACT_CMD) else None
    found = shutil.which("tesseract")
    if found:
        return found
    return next((p for p in _WINDOWS_TESSERACT_PATHS if os.path.exists(p)), None)


def _ocr_page(page, page_number):
    """Render one pdfplumber page to an image and read it with Tesseract OCR."""
    import pytesseract
    from PIL import ImageOps

    try:
        # pdfplumber renders pages with pypdfium2 (installed together with pdfplumber)
        img = page.to_image(resolution=OCR_DPI).original
    except Exception as e:
        raise PDFReadError(f"Could not render page {page_number} for OCR: {e}") from e

    img = ImageOps.autocontrast(ImageOps.grayscale(img))  # helps on faded scans
    tesseract = find_tesseract()
    if tesseract:
        pytesseract.pytesseract.tesseract_cmd = tesseract
    try:
        # psm 6 = "single uniform block of text" -> keeps table rows on one line
        return pytesseract.image_to_string(img, lang=OCR_LANG, config="--psm 6")
    except pytesseract.TesseractNotFoundError as e:
        raise PDFReadError(
            "This PDF is scanned and needs OCR, but Tesseract is not installed. "
            "Install it with `brew install tesseract` (macOS), `sudo apt install tesseract-ocr` (Linux) "
            "or the Windows installer from https://github.com/UB-Mannheim/tesseract/wiki (see README)."
        ) from e


def _to_bytes(file):
    if isinstance(file, (bytes, bytearray)):
        return bytes(file)
    if hasattr(file, "read"):
        return file.read()
    with open(file, "rb") as f:
        return f.read()


def read_pdf(file, password=None, force_ocr=False):
    """
    file: path, bytes or file-like object.
    Returns {"pdf_type": "text|image|mixed", "pages": [{"page": 1, "type": "text", "text": "..."}]}
    """
    pdf_bytes = _to_bytes(file)
    if b"%PDF" not in pdf_bytes[:1024]:  # some PDFs have a few junk bytes before the header
        raise PDFReadError("The file is not a valid PDF.")

    pages = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
            if not pdf.pages:
                raise PDFReadError("The PDF has no pages.")
            if len(pdf.pages) > MAX_PAGES:
                raise PDFReadError(f"The PDF has {len(pdf.pages)} pages; the limit is {MAX_PAGES}. "
                                   "Please split it into smaller files.")
            for i, page in enumerate(pdf.pages):
                text = "" if force_ocr else (page.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
                if len(text.strip()) >= MIN_CHARS_FOR_TEXT_PAGE:
                    pages.append({"page": i + 1, "type": "text", "text": text})
                else:
                    pages.append({"page": i + 1, "type": "image", "text": _ocr_page(page, i + 1)})
    except PDFReadError:
        raise
    except Exception as e:
        if "password" in repr(e).lower():
            hint = "Wrong password." if password else "The PDF is password protected."
            raise PDFReadError(f"{hint} Please provide the correct password.") from e
        log.warning("Could not read PDF: %r", e)
        raise PDFReadError(f"Could not read PDF: {str(e) or repr(e)}") from e

    types = {p["type"] for p in pages}
    pdf_type = types.pop() if len(types) == 1 else "mixed"
    log.info("Read PDF: %d page(s), type=%s", len(pages), pdf_type)
    return {"pdf_type": pdf_type, "pages": pages}
