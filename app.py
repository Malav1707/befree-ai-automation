"""Streamlit web UI.   Run:  streamlit run app.py"""
import hashlib
import logging
import os

import altair as alt
import pandas as pd
import streamlit as st

from src.config import BASE_DIR, ENABLE_LEARNING, MAX_PAGES
from src.exporter import LOW_CONFIDENCE, category_summary, monthly_summary, to_csv_bytes, to_excel_bytes
from src.pdf_reader import PDFReadError
from src.pipeline import get_classifier, process_statement
from src.utils import mask_account_number

log = logging.getLogger("app")
SAMPLES_DIR = os.path.join(BASE_DIR, "samples")

st.set_page_config(page_title="Bank Statement Processor", layout="wide")


@st.cache_resource
def classifier():
    return get_classifier()


class SampleFile:
    """Looks like an uploaded file, but reads one of the bundled sample PDFs."""

    def __init__(self, name):
        self.name = name
        with open(os.path.join(SAMPLES_DIR, name), "rb") as f:
            self._data = f.read()

    def getvalue(self):
        return self._data


def process(file, password, force_ocr):
    """Process each file once per session; re-runs (e.g. editing a category) are instant."""
    data = file.getvalue()
    key = hashlib.sha256(data + f"|{password}|{force_ocr}".encode()).hexdigest()
    cache = st.session_state.setdefault("results", {})
    if key not in cache:
        with st.spinner(f"Reading {file.name} ... (scanned PDFs take a few seconds per page)"):
            cache[key] = process_statement(data, filename=file.name, password=password,
                                           force_ocr=force_ocr, classifier=classifier())
    return key, cache[key]


def money(x):
    """85000.0 -> '85,000.00'; empty for missing values (instead of 'None')."""
    return "" if x is None or pd.isna(x) else f"{x:,.2f}"


FIELD_LABELS = {"ifsc": "IFSC", "micr": "MICR", "customer_id": "Customer ID"}
RIGHT = "right"


# ================================================================== sidebar
with st.sidebar:
    st.header("Options")
    password = st.text_input("PDF password", type="password", help="Only for password-protected statements")
    force_ocr = st.checkbox("Force OCR on all pages", help="Use if a text PDF gives garbled output")
    mask = st.checkbox("Mask account numbers", value=True)
    st.divider()
    samples = sorted(f for f in os.listdir(SAMPLES_DIR) if f.endswith(".pdf")) if os.path.isdir(SAMPLES_DIR) else []
    sample = st.selectbox("Or try a sample statement", ["—"] + samples,
                          help="Fictional banks with fake data. Password for the protected one: 1234")
    st.divider()
    st.caption("**How categories are assigned (no LLM):** keyword rules first, then a "
               "TF-IDF + Logistic Regression model (scikit-learn). Scanned pages are read with Tesseract OCR.")

# ================================================================== header + upload
st.title("Bank Statement Processor")
st.caption("Upload PDF bank statements (text-based or scanned) → extract account details and transactions → "
           "auto-categorise → export to Excel / CSV.")

uploads = st.file_uploader(f"Upload bank statement PDF(s) — up to {MAX_PAGES} pages each",
                           type=["pdf"], accept_multiple_files=True)
files = list(uploads or [])
if sample != "—":
    files.append(SampleFile(sample))

if not files:
    c1, c2, c3 = st.columns(3)
    c1.info("**1. Upload**\n\nOne or more PDF statements from any bank. Text or scanned.")
    c2.info("**2. Review**\n\nCheck the extracted rows; fix any category with one click.")
    c3.info("**3. Export**\n\nDownload a formatted Excel workbook or a CSV file.")
    st.stop()

# ================================================================== process
results, used_keys = [], set()
for f in files:
    try:
        key, res = process(f, password or None, force_ocr)
    except PDFReadError as e:
        st.error(f"**{f.name}**: {e}")
        continue
    except Exception:  # never show a stack trace to the user
        log.exception("Unexpected error while processing a file")
        st.error(f"**{f.name}**: something went wrong while reading this file. Please check it is a valid "
                 "bank statement PDF.")
        continue
    results.append({**res, "key": key})
    used_keys.add(key)

# keep memory bounded: forget results of files that were removed
st.session_state["results"] = {k: v for k, v in st.session_state.get("results", {}).items() if k in used_keys}

if not results:
    st.stop()

# stable row id = file content hash + row number (survives filtering and re-runs)
frames = [r["transactions"].assign(row_id=[f"{r['key'][:16]}#{i}" for i in range(len(r["transactions"]))])
          for r in results if not r["transactions"].empty]
if not frames:
    for r in results:
        for w in r["warnings"]:
            st.warning(f"**{r['account']['source_file']}**: {w}")
    st.error("No transactions could be extracted from the uploaded file(s).")
    st.stop()

all_tx = pd.concat(frames, ignore_index=True)
all_tx["auto_category"] = all_tx["category"]

# apply the user's manual category edits (stored by stable row id, so filters don't mix them up)
overrides = {rid: c for rid, c in st.session_state.get("overrides", {}).items() if rid[:16] in
             {k[:16] for k in used_keys}}
st.session_state["overrides"] = overrides
all_tx["category"] = [overrides.get(rid, cat) for rid, cat in zip(all_tx["row_id"], all_tx["auto_category"])]
all_tx["review"] = (all_tx["confidence"] < LOW_CONFIDENCE) | (all_tx["remarks"].fillna("") != "")

# ================================================================== KPI cards
n_warn = sum(len(r["warnings"]) for r in results)
k = st.columns(5)
k[0].metric("Statements", len(results))
k[1].metric("Transactions", len(all_tx))
k[2].metric("Money out", money(all_tx["debit"].sum()))
k[3].metric("Money in", money(all_tx["credit"].sum()))
k[4].metric("Rows to review", int(all_tx["review"].sum()))
if n_warn:
    st.warning(f"{n_warn} warning(s) found — see the **Statement details** tab.")

tab_tx, tab_sum, tab_acc, tab_exp = st.tabs(["Transactions", "Summary", "Statement details", "Export"])

# ================================================================== transactions
with tab_tx:
    f1, f2, f3, f4 = st.columns([2, 2, 1, 1])
    search = f1.text_input("Search description", placeholder="e.g. amazon")
    cats = f2.multiselect("Category", sorted(all_tx["category"].unique()))
    txn_type = f3.selectbox("Type", ["All", "Debit", "Credit"])
    only_review = f4.checkbox("Only rows to review")

    view = all_tx
    if search:
        view = view[view["description"].str.contains(search, case=False, regex=False)]
    if cats:
        view = view[view["category"].isin(cats)]
    if txn_type != "All":
        view = view[view["type"] == txn_type]
    if only_review:
        view = view[view["review"]]

    st.caption("Double-click a **category** cell to change it. Rows marked **Review** have low confidence or a remark — "
               "worth a quick check.")
    more = st.toggle("Show reference / method columns")
    table = pd.DataFrame({
        "flag": view["review"].map({True: "Yes", False: ""}),
        "date": view["date"],
        "description": view["description"],
        "debit": view["debit"].map(money),
        "credit": view["credit"].map(money),
        "balance": view["balance"].map(money),
        "category": view["category"],
        "confidence": view["confidence"],
        "remarks": view["remarks"],
    })
    if more:
        table["reference"], table["method"] = view["reference"], view["method"]
    if len(results) > 1:
        table["file"] = view["source_file"]

    editor_key = "editor-" + hashlib.md5(f"{search}|{cats}|{txn_type}|{only_review}|{more}|{sorted(used_keys)}"
                                         .encode()).hexdigest()
    edited = st.data_editor(
        table,
        key=editor_key,
        width="stretch",
        height=520,
        hide_index=True,
        disabled=[c for c in table.columns if c != "category"],
        column_config={
            "date": st.column_config.DateColumn("Date", format="DD-MM-YYYY", width="small", pinned=True),
            "description": st.column_config.TextColumn("Description", width="large", pinned=True),
            "debit": st.column_config.TextColumn("Debit (out)", alignment=RIGHT),
            "credit": st.column_config.TextColumn("Credit (in)", alignment=RIGHT),
            "balance": st.column_config.TextColumn("Balance", alignment=RIGHT),
            "category": st.column_config.SelectboxColumn("Category (editable)", options=classifier().categories,
                                                         required=True, width="medium"),
            "confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1,
                                                          format="%.2f", width="small"),
            "flag": st.column_config.TextColumn("Review", width=60, help="Needs review", pinned=True),
            "remarks": st.column_config.TextColumn("Remarks", width="medium"),
            "reference": st.column_config.TextColumn("Reference"),
            "method": st.column_config.TextColumn("Method", help="rule:<keyword>, ml, fallback, manual"),
            "file": st.column_config.TextColumn("File"),
        },
    )
    # remember edits by row id
    for rid, new_cat in zip(view["row_id"], edited["category"]):
        overrides[rid] = new_cat
    all_tx["category"] = [overrides.get(rid, cat) for rid, cat in zip(all_tx["row_id"], all_tx["auto_category"])]

    changed = all_tx["category"] != all_tx["auto_category"]
    all_tx.loc[changed, ["method", "confidence"]] = ["manual", 1.0]
    st.caption(f"Showing {len(view)} of {len(all_tx)} transactions · {int(changed.sum())} edited")

    if ENABLE_LEARNING and changed.any():
        if st.button(f"Teach the model my {int(changed.sum())} correction(s)"):
            n = classifier().add_corrections(list(zip(all_tx.loc[changed, "description"],
                                                      all_tx.loc[changed, "category"])))
            st.success(f"Saved {n} correction(s) and retrained the model — used for future statements.")

# ================================================================== summary
with tab_sum:
    cat = category_summary(all_tx)
    left, right = st.columns(2)
    left.markdown("**Spending by category**")
    spend = cat[cat["total_debit"] > 0]
    left.altair_chart(
        alt.Chart(spend).mark_bar(color="#305496").encode(
            x=alt.X("total_debit:Q", title="Money out"),
            y=alt.Y("category:N", sort="-x", title=None),
            tooltip=[alt.Tooltip("category", title="Category"),
                     alt.Tooltip("total_debit", title="Money out", format=",.2f"),
                     alt.Tooltip("transactions", title="Transactions")],
        ).properties(height=420),
        width="stretch",
    )
    right.markdown("**Category totals**")
    right.dataframe(
        pd.DataFrame({"Category": cat["category"], "Transactions": cat["transactions"],
                      "Money out": cat["total_debit"].map(money), "Money in": cat["total_credit"].map(money)}),
        hide_index=True, width="stretch", height=420,
        column_config={c: st.column_config.TextColumn(alignment=RIGHT) for c in ("Money out", "Money in")},
    )

    st.markdown("**Month by month**")
    month = monthly_summary(all_tx)
    long = month.melt(id_vars="month", value_vars=["total_credit", "total_debit"], var_name="flow",
                      value_name="amount").replace({"total_credit": "Money in", "total_debit": "Money out"})
    left, right = st.columns(2)
    left.altair_chart(
        alt.Chart(long).mark_bar().encode(
            x=alt.X("month:N", title=None, axis=alt.Axis(labelAngle=0)),
            xOffset="flow:N",
            y=alt.Y("amount:Q", title=None),
            color=alt.Color("flow:N", title=None, legend=alt.Legend(orient="top"),
                            scale=alt.Scale(domain=["Money in", "Money out"], range=["#2e7d32", "#c62828"])),
            tooltip=[alt.Tooltip("month", title="Month"), alt.Tooltip("flow", title=" "),
                     alt.Tooltip("amount", title="Amount", format=",.2f")],
        ).properties(height=300),
        width="stretch",
    )
    right.dataframe(
        pd.DataFrame({"Month": month["month"], "Money in": month["total_credit"].map(money),
                      "Money out": month["total_debit"].map(money), "Net": month["net"].map(money)}),
        hide_index=True, width="stretch",
        column_config={c: st.column_config.TextColumn(alignment=RIGHT) for c in ("Money in", "Money out", "Net")},
    )

# ================================================================== statement details
with tab_acc:
    for res in results:
        acc = res["account"]
        st.markdown(f"#### {acc['source_file']}")
        st.caption(f"{res['pdf_type'].upper()} PDF · {len(res['pages'])} page(s) · "
                   f"{len(res['transactions'])} transactions · pages: "
                   + ", ".join(f"{p['page']}={p['type']}" for p in res["pages"]))
        shown = dict(acc, account_number=mask_account_number(acc.get("account_number")) if mask
                     else acc.get("account_number"))
        def show(key, v):
            if v is None:
                return "—"
            if key.endswith("_balance"):
                return money(v)
            if key.startswith("statement_"):
                return v.strftime("%d-%m-%Y")
            return str(v)

        items = [(FIELD_LABELS.get(k, k.replace("_", " ").title()), show(k, v)) for k, v in shown.items()
                 if k not in ("source_file", "pdf_type")]
        half = (len(items) + 1) // 2
        c1, c2 = st.columns(2)
        c1.dataframe(pd.DataFrame(items[:half], columns=["Field", "Value"]), hide_index=True, width="stretch")
        c2.dataframe(pd.DataFrame(items[half:], columns=["Field", "Value"]), hide_index=True, width="stretch")
        if res["warnings"]:
            for w in res["warnings"]:
                st.warning(w)
        else:
            st.success("All checks passed: running balance and totals reconcile.")
        with st.expander("Show extracted raw text"):
            st.text("\n\n".join(p["text"] for p in res["pages"])[:20000])
        st.divider()

# ================================================================== export
with tab_exp:
    export = all_tx.drop(columns=["review", "row_id", "auto_category"])
    accounts = [dict(r["account"], account_number=mask_account_number(r["account"].get("account_number")))
                if mask else r["account"] for r in results]
    base = "bank_statements" if len(results) > 1 else os.path.splitext(results[0]["account"]["source_file"])[0]
    st.write("Exports include your category edits. Excel has 4 sheets: Transactions, Account Details, "
             "Category Summary, Monthly Summary (rows to review are highlighted).")
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button("Download Excel (.xlsx)", to_excel_bytes(export, accounts), file_name=f"{base}_processed.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary", width="stretch")
    d2.download_button("Download CSV (.csv)", to_csv_bytes(export), file_name=f"{base}_processed.csv",
                       mime="text/csv", width="stretch")
