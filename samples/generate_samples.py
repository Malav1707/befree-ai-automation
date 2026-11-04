"""Generate sample bank statements (fictional banks, fake data) for testing.

Creates in samples/:
  sunrise_bank_text.pdf      - layout A: separate Withdrawal/Deposit columns, dd/mm/yy, wrapped narrations, 2 pages
  metro_bank_text.pdf        - layout B: "01 Jan 2024" dates, Debit/Credit with 0.00 fillers
  coastal_bank_text.pdf      - layout C: single Amount + Dr/Cr column, "Cr" balances, NEWEST FIRST
  sunrise_bank_scanned.pdf   - layout A rendered to images (image-based PDF -> needs OCR)
  metro_bank_protected.pdf   - layout B with password "1234"

Run:  python samples/generate_samples.py
"""
import io
import os
import random
from datetime import date, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = os.path.dirname(os.path.abspath(__file__))
W, H = A4

DESCRIPTIONS = [
    ("UPI/412345678901/SWIGGY/swiggy@icici/Food order", "D", 250, 900),
    ("UPI/412345678902/ZOMATO LTD/zomato@hdfc", "D", 200, 800),
    ("POS 4521XXXXXX1234 DMART AVENUE SUPERMARTS", "D", 800, 4000),
    ("UPI/BIGBASKET/Order 99812", "D", 500, 3000),
    ("POS AMAZON PAY INDIA PVT LTD", "D", 300, 6000),
    ("UPI/FLIPKART INTERNET/Payment", "D", 400, 5000),
    ("UPI/UBER INDIA SYSTEMS/trip", "D", 150, 700),
    ("POS HPCL PETROL PUMP MG ROAD", "D", 1000, 3000),
    ("BILLPAY BESCOM ELECTRICITY BILL", "D", 900, 2500),
    ("UPI/AIRTEL POSTPAID/9876500000", "D", 499, 999),
    ("ATM WDL SBI ATM KORAMANGALA", "D", 2000, 10000),
    ("NACH/BAJAJ FINANCE LTD/EMI 22", "D", 4500, 4500),
    ("ACH/ZERODHA BROKING/SIP", "D", 5000, 5000),
    ("UPI/NETFLIX COM/subscription", "D", 649, 649),
    ("UPI/APOLLO PHARMACY/medicines", "D", 200, 1500),
    ("UPI/THIRD WAVE COFFEE ROASTERS", "D", 250, 600),       # not in rules -> ML
    ("UPI/RAHUL SHARMA/rahul@okaxis/Dinner split", "D", 300, 2000),
    ("NEFT/LIC OF INDIA/PREMIUM", "D", 3000, 3000),
    ("UPI/MAKEMYTRIP/Flight booking BLR-DEL", "D", 3000, 9000),
    ("SMS ALERT CHARGES QTR", "D", 15, 25),
    ("POS WESTSIDE TRENT LTD", "D", 1200, 3500),              # not in rules -> ML
    ("IMPS/P2P/PRIYA PATEL/Shared rent", "C", 5000, 9000),
    ("REFUND AMAZON ORDER 403-1234", "C", 300, 2000),
    ("SB INTEREST CREDIT", "C", 100, 400),
]
SALARY = "NEFT/ACME TECHNOLOGIES PVT LTD/SALARY FOR {month}"
RENT = "IMPS/HOUSE RENT/LANDLORD MR K RAO"


def make_transactions(seed=7, start=date(2024, 1, 1), days=59, opening=25000.0):
    rnd = random.Random(seed)
    txns, bal = [], opening
    d = start
    while d < start + timedelta(days=days):
        if d.day == 1:
            items = [(SALARY.format(month=d.strftime("%b %Y").upper()), "C", 85000, 85000), (RENT, "D", 22000, 22000)]
        else:
            items = [rnd.choice(DESCRIPTIONS)] if rnd.random() < 0.7 else []
        for desc, kind, lo, hi in items:
            amt = round(rnd.uniform(lo, hi), 2) if lo != hi else float(lo)
            bal = round(bal - amt if kind == "D" else bal + amt, 2)
            txns.append({"date": d, "desc": desc, "debit": amt if kind == "D" else None,
                         "credit": amt if kind == "C" else None, "balance": bal})
        d += timedelta(days=1)
    return opening, txns


def fmt(x):
    """Indian-style digit grouping: 1,23,456.78"""
    s = f"{x:.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{whole}.{frac}"


def wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + [cur] if cur else lines


class Statement:
    """Tiny helper around reportlab canvas with automatic page breaks + repeated table header."""

    def __init__(self, buf, header_fn, columns, encrypt=None):
        self.c = canvas.Canvas(buf, pagesize=A4, encrypt=encrypt)
        self.header_fn, self.columns = header_fn, columns
        self.page = 1
        self.y = self.header_fn(self.c)
        self._table_header()

    def _table_header(self):
        self.c.setFont("Helvetica-Bold", 8)
        for title, x, align in self.columns:
            (self.c.drawRightString if align == "r" else self.c.drawString)(x, self.y, title)
        self.y -= 14
        self.c.setFont("Helvetica", 8)

    def row(self, values, extra_lines=()):
        needed = 12 + 10 * len(extra_lines)
        if self.y - needed < 60:
            self.new_page()
        for (_, x, align), v in zip(self.columns, values):
            if v:
                (self.c.drawRightString if align == "r" else self.c.drawString)(x, self.y, v)
        for line in extra_lines:
            self.y -= 10
            self.c.drawString(self.columns[1][1], self.y, line)
        self.y -= 13

    def text(self, s, x=40, bold=False, size=8):
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.c.drawString(x, self.y, s)
        self.y -= 13
        self.c.setFont("Helvetica", 8)

    def new_page(self):
        self._footer()
        self.c.showPage()
        self.page += 1
        self.y = H - 50
        self._table_header()

    def _footer(self):
        self.c.setFont("Helvetica", 7)
        self.c.drawString(40, 30, f"Page {self.page}   This is a computer generated statement and does not require signature.")

    def save(self):
        self._footer()
        self.c.save()


def header_block(bank, lines):
    def draw(c):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, H - 50, bank)
        c.setFont("Helvetica", 9)
        y = H - 75
        for left, right in lines:
            c.drawString(40, y, left)
            if right:
                c.drawString(320, y, right)
            y -= 14
        return y - 16
    return draw


# ---------------------------------------------------------------- layout A
def build_sunrise(opening, txns, encrypt=None):
    buf = io.BytesIO()
    hdr = header_block("Sunrise Bank Ltd", [
        ("Account Name : RAVI KUMAR MEHTA", "Account No : 50100234567891"),
        ("Address : 12, Lake View Road, Pune 411001", "IFSC : SUNR0001234"),
        ("Customer ID : 88776655", "Branch : Pune Camp"),
        ("Statement Period : 01/01/2024 to 28/02/2024", "Currency : INR"),
        (f"Opening Balance : {fmt(opening)}", f"Closing Balance : {fmt(txns[-1]['balance'])}"),
    ])
    cols = [("Date", 40, "l"), ("Narration", 85, "l"), ("Ref No.", 290, "l"), ("Value Dt", 332, "l"),
            ("Withdrawal Amt.", 445, "r"), ("Deposit Amt.", 495, "r"), ("Closing Balance", 560, "r")]
    st = Statement(buf, hdr, cols, encrypt)
    for i, t in enumerate(txns):
        lines = wrap(t["desc"], 40)
        d = t["date"].strftime("%d/%m/%y")
        st.row([d, lines[0], f"{900000 + i:06d}", d,
                fmt(t["debit"]) if t["debit"] else "", fmt(t["credit"]) if t["credit"] else "",
                fmt(t["balance"])], lines[1:])
    st.save()
    return buf.getvalue()


# ---------------------------------------------------------------- layout B
def build_metro(opening, txns, encrypt=None):
    buf = io.BytesIO()
    hdr = header_block("Metro Co-operative Bank", [
        ("Customer Name: Ms. Ananya Iyer", "A/C No.: 0012 3456 7890"),
        ("IFSC Code: MTRO0000456", "MICR Code: 400123456"),
        ("Account Type: Savings", "Branch Name: Andheri West"),
        ("Statement from 01 Jan 2024 to 28 Feb 2024", ""),
    ])
    cols = [("Txn Date", 40, "l"), ("Description", 100, "l"), ("Debit", 420, "r"),
            ("Credit", 490, "r"), ("Balance", 560, "r")]
    st = Statement(buf, hdr, cols, encrypt)
    st.row([txns[0]["date"].strftime("%d %b %Y"), "Opening Balance", "", "", fmt(opening)])
    for t in txns:
        st.row([t["date"].strftime("%d %b %Y"), t["desc"][:58], fmt(t["debit"] or 0), fmt(t["credit"] or 0),
                fmt(t["balance"])])
    st.text(f"Closing Balance {fmt(txns[-1]['balance'])}", bold=True)
    st.save()
    return buf.getvalue()


# ---------------------------------------------------------------- layout C
def build_coastal(opening, txns):
    buf = io.BytesIO()
    hdr = header_block("Coastal State Bank", [
        ("Name: JOHN D SOUZA", "Account Number: 33445566778"),
        ("IFSC: CSTB0009876", "Branch: Panaji Main"),
        ("Period: 2024-01-01 to 2024-02-28", ""),
    ])
    cols = [("Date", 40, "l"), ("Particulars", 110, "l"), ("Amount", 430, "r"), ("Dr/Cr", 445, "l"),
            ("Balance", 560, "r")]
    st = Statement(buf, hdr, cols)
    for t in reversed(txns):  # newest first
        amt, mark = (t["debit"], "Dr") if t["debit"] else (t["credit"], "Cr")
        st.row([t["date"].strftime("%Y-%m-%d"), t["desc"][:55], fmt(amt), mark, f"{fmt(t['balance'])} Cr"])
    st.save()
    return buf.getvalue()


def to_scanned(pdf_bytes, dpi=200, encrypt=None):
    """Turn every page into a picture -> image-based PDF without a text layer (like a scan).

    Pages are stored as JPEG (like real scanners do), which keeps the file small.
    """
    import pdfplumber
    from PIL import ImageFilter
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    out = canvas.Canvas(buf, encrypt=encrypt)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            img = page.to_image(resolution=dpi).original.convert("L").rotate(0.3, fillcolor=255)
            img = img.filter(ImageFilter.GaussianBlur(0.4))  # a bit of scanner blur
            jpg = io.BytesIO()
            img.save(jpg, format="JPEG", quality=70)
            jpg.seek(0)
            out.setPageSize((page.width, page.height))
            out.drawImage(ImageReader(jpg), 0, 0, width=page.width, height=page.height)
            out.showPage()
    out.save()
    return buf.getvalue()


def main():
    opening, txns = make_transactions()
    files = {
        "sunrise_bank_text.pdf": build_sunrise(opening, txns),
        "metro_bank_text.pdf": build_metro(opening, txns),
        "coastal_bank_text.pdf": build_coastal(opening, txns),
        "metro_bank_protected.pdf": build_metro(opening, txns, encrypt="1234"),
    }
    files["sunrise_bank_scanned.pdf"] = to_scanned(files["sunrise_bank_text.pdf"])
    for name, data in files.items():
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(data)
        print("wrote", name)
    print(f"{len(txns)} transactions, opening {opening:,.2f}, closing {txns[-1]['balance']:,.2f}")


if __name__ == "__main__":
    main()
