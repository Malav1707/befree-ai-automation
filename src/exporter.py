"""Export processed transactions to CSV / Excel."""
import io

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

EXPORT_COLUMNS = ["date", "description", "reference", "debit", "credit", "balance", "type",
                  "category", "confidence", "method", "remarks", "source_file"]
REVIEW_FILL = PatternFill("solid", fgColor="FFF2CC")  # light yellow = needs review
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="305496")
LOW_CONFIDENCE = 0.6


def _ordered(df):
    cols = [c for c in EXPORT_COLUMNS if c in df.columns]
    return df[cols]


def category_summary(df):
    if df.empty:
        return pd.DataFrame(columns=["category", "transactions", "total_debit", "total_credit"])
    return (df.groupby("category")
              .agg(transactions=("description", "count"),
                   total_debit=("debit", "sum"),
                   total_credit=("credit", "sum"))
              .round(2)
              .reset_index()
              .sort_values("total_debit", ascending=False))


def monthly_summary(df):
    if df.empty:
        return pd.DataFrame(columns=["month", "total_debit", "total_credit", "net"])
    m = df.assign(month=pd.to_datetime(df["date"]).dt.strftime("%Y-%m"))
    out = m.groupby("month").agg(total_debit=("debit", "sum"), total_credit=("credit", "sum")).reset_index()
    out["net"] = out["total_credit"] - out["total_debit"]
    return out.round(2)


def to_csv_bytes(df):
    return _ordered(df).to_csv(index=False).encode("utf-8-sig")  # BOM so Excel opens UTF-8 correctly


def _style_sheet(ws, money_cols=()):
    for cell in ws[1]:
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    headers = [c.value for c in ws[1]]
    for idx, name in enumerate(headers, start=1):
        letter = get_column_letter(idx)
        width = max([len(str(name))] + [len(str(c.value)) for c in ws[letter][1:200] if c.value is not None])
        ws.column_dimensions[letter].width = min(width + 2, 60)
        if name in money_cols:
            for c in ws[letter][1:]:
                c.number_format = "#,##0.00"


def to_excel_bytes(df, account_details=None):
    """Workbook with: Transactions, Account Details, Category Summary, Monthly Summary."""
    buf = io.BytesIO()
    tx = _ordered(df)
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        tx.to_excel(writer, sheet_name="Transactions", index=False)
        if account_details:
            rows = account_details if isinstance(account_details, list) else [account_details]
            pd.DataFrame(rows).to_excel(writer, sheet_name="Account Details", index=False)
        category_summary(df).to_excel(writer, sheet_name="Category Summary", index=False)
        monthly_summary(df).to_excel(writer, sheet_name="Monthly Summary", index=False)

        money = ("debit", "credit", "balance", "total_debit", "total_credit", "net",
                 "opening_balance", "closing_balance")
        for ws in writer.book.worksheets:
            _style_sheet(ws, money)

        # highlight rows the user should double-check
        ws = writer.book["Transactions"]
        for i, (_, r) in enumerate(tx.iterrows(), start=2):
            if (r.get("confidence", 1) or 0) < LOW_CONFIDENCE or r.get("remarks"):
                for cell in ws[i]:
                    cell.fill = REVIEW_FILL
    return buf.getvalue()
