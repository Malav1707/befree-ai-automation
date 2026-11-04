"""Command line usage.

    python cli.py samples/sunrise_bank_text.pdf                     -> output/sunrise_bank_text.xlsx
    python cli.py a.pdf b.pdf --format csv --out-dir results        -> one CSV per file
    python cli.py *.pdf --combine                                   -> one combined Excel file
    python cli.py locked.pdf --password 1234 --force-ocr
"""
import argparse
import glob
import os
import sys

import pandas as pd

from src.exporter import to_csv_bytes, to_excel_bytes
from src.pdf_reader import PDFReadError
from src.pipeline import process_statement


def main():
    ap = argparse.ArgumentParser(description="Extract & classify bank statement transactions (no LLM).")
    ap.add_argument("files", nargs="+", help="PDF bank statement(s)")
    ap.add_argument("--format", choices=["xlsx", "csv"], default="xlsx")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--password", help="password for protected PDFs")
    ap.add_argument("--force-ocr", action="store_true", help="OCR every page even if it has text")
    ap.add_argument("--combine", action="store_true", help="write all statements into one file")
    args = ap.parse_args()

    # Windows terminals don't expand "*.pdf" themselves, so do it here
    files = [f for pattern in args.files for f in (sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern])]
    os.makedirs(args.out_dir, exist_ok=True)
    all_tx, all_acc, failed = [], [], 0

    for path in files:
        name = os.path.basename(path)
        try:
            res = process_statement(path, filename=name, password=args.password, force_ocr=args.force_ocr)
        except (PDFReadError, FileNotFoundError) as e:
            print(f"[FAILED] {name}: {e}")
            failed += 1
            continue

        df, acc = res["transactions"], res["account"]
        print(f"[OK] {name}: type={res['pdf_type']}, holder={acc.get('account_holder')}, "
              f"account={acc.get('account_number')}, transactions={len(df)}")
        for w in res["warnings"]:
            print(f"     ! {w}")

        if args.combine:
            all_tx.append(df)
            all_acc.append(acc)
            continue
        write(df, acc, os.path.join(args.out_dir, os.path.splitext(name)[0]), args.format)

    if args.combine and all_tx:
        write(pd.concat(all_tx, ignore_index=True), all_acc, os.path.join(args.out_dir, "combined"), args.format)
    return 1 if failed else 0


def write(df, account, base, fmt):
    path = f"{base}.{fmt}"
    data = to_csv_bytes(df) if fmt == "csv" else to_excel_bytes(df, account)
    with open(path, "wb") as f:
        f.write(data)
    print(f"     -> {path}")


if __name__ == "__main__":
    sys.exit(main())
