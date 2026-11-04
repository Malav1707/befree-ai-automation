# Bank Statement Processor (no LLM)

Upload a PDF bank statement (text-based **or** scanned). The app extracts account details and
transactions, assigns each transaction a category using **keyword rules + a small ML model**
(no LLM), and exports everything to **Excel / CSV**.

## Which model is used?
**No LLM.** Classification uses two traditional techniques:

| Step | Technique | Library |
|---|---|---|
| 1 | **Keyword rules** (~270 keywords in `data/category_rules.json`) | plain Python regex |
| 2 | **TF-IDF (character 2–4-grams) + Logistic Regression**, trained on `data/training_data.csv` at start-up (< 1 s) | scikit-learn |
| OCR | **Tesseract 5** (open-source OCR engine) for scanned pages | pytesseract |

## Run locally

Requirements: **Python 3.11 or newer** (tested on 3.12), plus **Tesseract OCR**. Tesseract is
only needed for scanned PDFs; text PDFs work without it.

### macOS
```bash
brew install tesseract
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py                 # opens http://localhost:8501
```

### Linux (Ubuntu / Debian)
```bash
sudo apt install tesseract-ocr python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

### Windows
1. **Install Python 3.11+** from https://www.python.org/downloads/. In the installer, tick
   **"Add python.exe to PATH"**.
2. **Install Tesseract** (only needed for scanned PDFs). Either download the installer from
   https://github.com/UB-Mannheim/tesseract/wiki and keep the default folder
   `C:\Program Files\Tesseract-OCR`, or run `winget install UB-Mannheim.TesseractOCR` in a terminal.
   The app finds it in that folder automatically, so you don't need to change PATH.
3. Open **Command Prompt** or **PowerShell** in the project folder and run:
   ```bat
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   streamlit run app.py
   ```
   If PowerShell says *"running scripts is disabled"* on the `activate` line, run
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use Command Prompt instead.
   If Tesseract is installed in a different folder, tell the app where it is before starting:
   `set TESSERACT_CMD=D:\Tools\Tesseract-OCR\tesseract.exe` (Command Prompt) or
   `$env:TESSERACT_CMD="D:\Tools\Tesseract-OCR\tesseract.exe"` (PowerShell).

### Using the app
Sample statements (fictional banks, fake data) are already in `samples/`. Pick one from the
sidebar under **"Try a sample statement"** (the protected one's password is `1234`), or upload
your own PDF. To regenerate the samples, run `python samples/generate_samples.py`.

Command line (same commands on Windows):
```bash
python cli.py samples/sunrise_bank_text.pdf                 # -> output/sunrise_bank_text.xlsx
python cli.py "samples/*.pdf" --combine --password 1234     # one Excel for all files
python cli.py samples/coastal_bank_text.pdf --format csv
pytest -q                                                   # 34 tests
```

## Settings
Defaults live in `src/config.py`, e.g. a maximum of 50 pages per PDF and 300 DPI for OCR, plus
`TESSERACT_CMD` for a custom Tesseract location. The upload limit (20 MB) is in
`.streamlit/config.toml`.

## Privacy
Uploaded PDFs are processed **in memory** and never written to disk. Results live only in the
browser session. The only thing ever saved is category corrections (description text and
category) when the user clicks the *Teach the model* button.

## How it works

```
PDF ──► pdf_reader ──► text per page ──► account_parser ──► account details
          │  (text page? pdfplumber                │
          │   scanned page? render + Tesseract)    └──► transaction_parser ──► rows
          │                                                      │
          └ detects: text / image / mixed                        ▼
                                                   classifier (rules → ML → fallback)
                                                                 │
                                         validation checks ◄─────┤
                                                                 ▼
                                                    exporter (Excel / CSV)
```

| File | What it does |
|---|---|
| `src/pdf_reader.py` | Checks **each page**: if it has text, uses pdfplumber; otherwise renders it to an image and OCRs it (Tesseract). Handles password-protected, corrupt and non-PDF files. |
| `src/account_parser.py` | Regex rules with several label variants per field (`A/C No`, `Account Number`, `Acct No.`...). Gets holder name, account no., IFSC, MICR, customer ID, branch, period, opening/closing balance. |
| `src/transaction_parser.py` | Line-based parser (layout independent): a line starting with a date = new transaction, decimal numbers = amounts, last amount = balance, lines without a date = wrapped description. |
| `src/classifier.py` | Hybrid classifier — see below. |
| `src/pipeline.py` | Runs everything and performs validation checks. |
| `src/exporter.py` | CSV + formatted Excel (Transactions, Account Details, Category Summary, Monthly Summary). |
| `src/config.py` | All settings (overridable with environment variables). |
| `app.py` / `cli.py` | Streamlit web UI / command line. |
| `data/category_rules.json` | Editable keyword rules. |
| `data/training_data.csv` | Labelled examples for the ML model (~215 rows). |

### Debit vs credit detection
Banks show debit/credit in different ways, so the parser tries these in order:
1. Explicit **Dr / Cr** marker next to the amount (`500.00 Dr`)
2. **Separate debit / credit columns** (`0.00  450.00  9,550.00`). Column order is read from the table header.
3. **Running balance**: if `previous balance − amount = balance`, it is a debit; if `+ amount`, a credit
4. Keywords in the description (`salary`, `refund`, `interest credit`...) as a last resort. These rows are flagged.

### Classification (no LLM)
1. **Priority rules**: refund, bank charges, tax, salary, interest. These override merchant names, so `REFUND AMAZON` becomes *Refund*, not *Shopping*.
2. **Keyword rules**: about 270 merchant keywords. The longest match wins (`uber eats` beats `uber`).
3. **ML model**: `TfidfVectorizer(char n-grams) + LogisticRegression` handles merchants not in the rules (e.g. `WESTSIDE TRENT` → Shopping) and OCR typos. It is only used when its confidence is ≥ 0.35.
4. **Fallback**: payment-channel words (`UPI`, `NEFT`, `IMPS`...) → *Transfer*; an unknown credit → *Other Income*; anything else → *Others*.
5. **Direction check**: a credit can't be an expense category, so it becomes *Refund* or *Transfer* instead.

Every row gets a `confidence` and a `method` (e.g. `rule:swiggy`, `ml`, `fallback:upi`), so results can be explained.
**Learning from feedback:** in the UI you can fix categories and click *Learn from my corrections*.
The corrections are saved to `data/user_corrections.csv` and the model retrains straight away.

## Real-world cases handled
- Text, scanned and **mixed** PDFs (detected per page), plus a *Force OCR* option for garbled text layers
- **Password-protected** PDFs, wrong password, corrupt or non-PDF files (clear error messages)
- Different layouts: separate debit/credit columns, single amount + Dr/Cr column, `0.00` or blank fillers, credit-before-debit columns
- Many date formats: `05/01/2024`, `05-01-24`, `2024-01-05`, `05 Jan 2024`, `05-Jan-24`
- Indian digit grouping `1,23,456.78`, `(500.00)` negatives, `Cr`/`Dr` balances (overdraft)
- **Multi-line descriptions**, multi-page statements, repeated page headers and footers
- **Newest-first** statements (reordered automatically)
- Value-date and reference-number columns (references moved to their own column)
- Opening / closing balance rows (used for validation, not counted as transactions)
- OCR clean-up (`UP1/` → `UPI/`, `O` vs `0` in IFSC, stray symbols)
- **Validation**: running-balance mismatch per row, opening + credits − debits = closing, header closing balance vs last row, possible duplicates, out-of-order dates
- Multiple files at once, with a combined export and a `source_file` column
- Account-number masking (privacy) and highlighting of low-confidence rows in the UI and Excel

## Limitations / next steps
- The parser expects each transaction row to start with a date (true for most Indian and international statements). A bank-specific template could be added for unusual layouts.
- OCR quality depends on the scan. Very low-resolution or skewed photos may need pre-processing (deskew, thresholding).
- The ML model is trained on a small synthetic dataset. More real, labelled data (e.g. from the user-corrections loop) will improve it.
