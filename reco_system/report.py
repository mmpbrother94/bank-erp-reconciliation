"""Single-workbook Excel output: ERP + Bank + Reconciliation + Rules."""
from __future__ import annotations

import pandas as pd

from .engine import DEFAULTS, RULE_TEXT

# ------------------------------------------------------------------- layouts

RECO_COLS = [
    ("statement_file", "Bank Statement File"),
    ("bank", "Bank"),
    ("statement_account", "Statement A/c"),
    ("scope_level", "Matching Scope"),
    ("erp_row", "ERP Row"),
    ("txn_date", "ERP Date"),
    ("mode", "ERP Mode"),
    ("payment_mode", "Payment Mode"),
    ("transac_type", "Transac Type"),
    ("dr_cr", "ERP Dr/Cr"),
    ("amount", "ERP Amount"),
    ("ref_no", "ERP PMT/Ref No"),
    ("payee", "Payee Name"),
    ("description", "ERP Description"),
    ("reference", "ERP Reference"),
    ("account_no", "ERP Bank A/c"),
    ("alias", "ERP Alias"),
    ("transac_for", "Transac For"),
    ("is_assign", "Is Assign"),
    ("status", "Status"),
    ("rule_code", "Rule"),
    ("rule_label", "Rule Applied"),
    ("reason", "Reason"),
    ("bank_row", "Bank Row"),
    ("bank_date", "Bank Date"),
    ("bank_value_date", "Bank Value Date"),
    ("bank_dr_cr", "Bank Dr/Cr"),
    ("bank_debit", "Bank Debit"),
    ("bank_credit", "Bank Credit"),
    ("bank_description", "Bank Narration"),
    ("bank_ref_no", "Bank Ref No"),
    ("bank_cheque_no", "Bank Cheque No"),
    ("amount_diff", "Amount Diff"),
    ("date_diff_days", "Date Diff (days)"),
    ("ref_matched", "Ref Matched?"),
    ("desc_score", "Narration Score"),
    ("match_score", "Match Score"),
]

BANK_COLS = [
    ("bank_file", "Bank Statement File"),
    ("bank", "Bank"),
    ("account_no", "Statement A/c"),
    ("bank_row", "Row"),
    ("txn_date", "Transaction Date"),
    ("value_date", "Value Date"),
    ("description", "Narration"),
    ("ref_no", "Reference No"),
    ("cheque_no", "Cheque No"),
    ("debit", "Debit"),
    ("credit", "Credit"),
    ("dr_cr", "Dr/Cr"),
    ("balance", "Balance"),
    ("is_reject", "Reject?"),
    ("status", "Status"),
    ("rule_code", "Rule"),
    ("rule_label", "Rule Applied"),
    ("reason", "Reason"),
]

ERP_COLS_OUT = [
    ("erp_row", "ERP Row"),
    ("txn_date", "Transaction Date"),
    ("payment_mode", "Payment Mode"),
    ("transac_type", "Transac Type"),
    ("mode", "Mode (derived)"),
    ("dr_cr", "Dr/Cr (derived)"),
    ("amount", "Transac Amount"),
    ("ref_no", "PMT/Ref Number"),
    ("payee", "Payee Name"),
    ("description", "Description"),
    ("reference", "Reference"),
    ("bank_name", "Bank Name"),
    ("account_no", "Bank Account Number"),
    ("alias", "Alias Name"),
    ("transac_for", "Transac For"),
    ("organization", "Organization"),
    ("is_assign", "Is Assign"),
    ("scope", "Scope Decision"),
]

SUMMARY_COLS = [
    ("statement_file", "Bank Statement File"),
    ("bank", "Bank"),
    ("account_no", "Statement A/c No"),
    ("scope_level", "Matching Scope"),
    ("period_from", "Period From"),
    ("period_to", "Period To"),
    ("bank_txns", "Bank Txns"),
    ("bank_debit", "Bank Debit"),
    ("bank_credit", "Bank Credit"),
    ("bank_rejects", "Bank Rejects (ignored)"),
    ("erp_txns", "ERP Txns (in scope)"),
    ("erp_debit", "ERP Debit"),
    ("erp_credit", "ERP Credit"),
    ("erp_matched", "Matched"),
    ("erp_exception", "Exceptions"),
    ("erp_unmatched", "Unmatched (ERP)"),
    ("bank_unmatched", "Unmatched (Bank)"),
    ("erp_not_compared", "Not Compared"),
    ("matched_amount", "Matched Amount"),
    ("unmatched_erp_amount", "Open ERP Amount"),
    ("match_rate", "Match Rate %"),
]


def _frame(df: pd.DataFrame, cols):
    out = pd.DataFrame()
    for src, label in cols:
        out[label] = df[src] if src in df.columns else ""
    return out


def write_workbook(res: dict, path: str):
    """Write the one-file deliverable: ERP + Bank + Reco + Rules."""
    erp_res = res["erp_result"]
    bank_res = res["bank_result"]
    erp_all = pd.concat(
        [erp_res, res["erp_excluded"]], ignore_index=True, sort=False)

    with pd.ExcelWriter(path, engine="xlsxwriter",
                        datetime_format="dd-mm-yyyy", date_format="dd-mm-yyyy") as xw:
        wb = xw.book
        fmt = _formats(wb)

        _sheet_summary(xw, fmt, res)
        grand = totals_rows(bank_res, erp_res)
        grand[0] = ("head", "GRAND TOTALS - ALL BANKS", "DEBIT", "CREDIT")
        _write(xw, fmt, "Reconciliation", _frame(erp_res.sort_values(
            ["bank", "txn_date", "amount"], na_position="last"), RECO_COLS),
            money=["ERP Amount", "Bank Debit", "Bank Credit", "Amount Diff"],
            totals=grand, footer=PARAM_FOOTER)
        exc = erp_res[erp_res["status"].isin(["EXCEPTION", "UNMATCHED"])]
        bexc = bank_res[bank_res["status"] == "UNMATCHED"]
        _write(xw, fmt, "Exceptions (ERP side)", _frame(exc, RECO_COLS),
               money=["ERP Amount", "Bank Debit", "Bank Credit", "Amount Diff"],
               footer=PARAM_FOOTER)
        _write(xw, fmt, "Exceptions (Bank side)", _frame(bexc, BANK_COLS),
               money=["Debit", "Credit", "Balance"])
        _write(xw, fmt, "Bank Statement", _frame(bank_res, BANK_COLS),
               money=["Debit", "Credit", "Balance"])
        _write(xw, fmt, "ERP Statement", _frame(erp_all, ERP_COLS_OUT),
               money=["Transac Amount"])
        _sheet_rules(xw, fmt, res)
    return path


def safe_name(text: str) -> str:
    """Filesystem + URL safe base name (no spaces - the web download route
    passes names through werkzeug's secure_filename)."""
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in str(text).strip()]
    out = "".join(keep)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")[:80] or "statement"


def bank_slices(res: dict, statement_file: str):
    """The three slices that belong to ONE bank statement file."""
    st = next((x for x in res["statements"] if x["file"] == statement_file), None)
    if st is None:
        raise KeyError(f"no such statement file in the result: {statement_file}")

    bank = res["bank_result"]
    bank = bank[bank["bank_file"] == statement_file] if len(bank) else bank

    reco = res["erp_result"]
    reco = reco[reco["statement_file"] == statement_file] if len(reco) else reco

    # every ERP row (in scope AND excluded) belonging to this statement's accounts
    accounts = set(st["erp_accounts"]) or {st["acct_key"]}
    erp_all = pd.concat([res["erp_result"], res["erp_excluded"]],
                        ignore_index=True, sort=False)
    erp = erp_all[erp_all["acct_key"].isin(accounts)] if accounts else erp_all.iloc[0:0]
    return st, bank, erp, reco


def _sum(df, col, mask=None):
    if not len(df) or col not in df:
        return 0.0
    d = df if mask is None else df[mask]
    return float(pd.to_numeric(d[col], errors="coerce").fillna(0).sum())


def totals_rows(bank, erp_reco):
    """The closing figures asked for: bank Dr/Cr, ERP Dr/Cr, and what is open
    on each side, split Debit / Credit.  `bank` = parsed statement lines,
    `erp_reco` = the reconciliation rows of the same bank."""
    live = bank[~bank["is_reject"].astype(bool)] if len(bank) else bank
    b_dr = _sum(live, "debit")
    b_cr = _sum(live, "credit")

    # every in-scope ERP line of this bank account (compared AND not compared),
    # so the figure ties back to the ERP export itself
    scope = erp_reco[erp_reco["status"] != "EXCLUDED"] if len(erp_reco) else erp_reco
    e_dr = _sum(scope, "amount", scope["dr_cr"] == "DEBIT") if len(scope) else 0.0
    e_cr = _sum(scope, "amount", scope["dr_cr"] == "CREDIT") if len(scope) else 0.0

    open_b = live[live["status"] == "UNMATCHED"] if len(live) and "status" in live else live.iloc[0:0]
    ob_dr, ob_cr = _sum(open_b, "debit"), _sum(open_b, "credit")

    open_e = erp_reco[erp_reco["status"].isin(["UNMATCHED", "EXCEPTION"])]         if len(erp_reco) else erp_reco
    oe_dr = _sum(open_e, "amount", open_e["dr_cr"] == "DEBIT") if len(open_e) else 0.0
    oe_cr = _sum(open_e, "amount", open_e["dr_cr"] == "CREDIT") if len(open_e) else 0.0

    nc = erp_reco[erp_reco["status"] == "NOT COMPARED"] if len(erp_reco) else erp_reco
    nc_dr = _sum(nc, "amount", nc["dr_cr"] == "DEBIT") if len(nc) else 0.0
    nc_cr = _sum(nc, "amount", nc["dr_cr"] == "CREDIT") if len(nc) else 0.0

    # ERP restricted to the period the statement actually covers - this is the
    # figure that should tie back to the bank statement
    p_dr, p_cr = e_dr - nc_dr, e_cr - nc_cr

    return [
        ("head", "TOTALS", "DEBIT", "CREDIT"),
        ("row", "Total amount in the BANK STATEMENT", b_dr, b_cr),
        ("row", "Total amount in the ERP (this bank account, all dates)", e_dr, e_cr),
        ("row", "Total amount in the ERP within the statement period", p_dr, p_cr),
        ("bold", "DIFFERENCE  (Bank - ERP within the statement period)", b_dr - p_dr, b_cr - p_cr),
        ("row", "Difference against the full ERP (Bank - ERP all dates)", b_dr - e_dr, b_cr - e_cr),
        ("gap", "", "", ""),
        ("head", "OPEN / UNMATCHED", "DEBIT", "CREDIT"),
        ("row", "Amount in BANK but NOT in ERP", ob_dr, ob_cr),
        ("row", "Amount in ERP but NOT in BANK", oe_dr, oe_cr),
        ("row", "ERP amount not compared (outside the statement period)", nc_dr, nc_cr),
    ]


def write_bank_workbook(res: dict, statement_file: str, path: str):
    """One bank = one file with exactly three sheets:

    1. Bank Statement    - the uploaded statement, as parsed, with its status
    2. ERP Statement     - only the ERP rows of that bank account number
    3. Reconciliation    - the match / break of that bank, with the reasons
    """
    st, bank, erp, reco = bank_slices(res, statement_file)
    tot = totals_rows(bank, reco)
    with pd.ExcelWriter(path, engine="xlsxwriter",
                        datetime_format="dd-mm-yyyy", date_format="dd-mm-yyyy") as xw:
        fmt = _formats(xw.book)
        head = [
            f"{st['bank']}   |   statement file: {st['file']}",
            f"A/c {st['account_no'] or '(not printed in the statement header)'}   |   "
            f"period {_dt(st['period_from'])} to {_dt(st['period_to'])}   |   "
            f"matching scope: {st['scope_level']}",
        ]
        _write(xw, fmt, "Bank Statement", _frame(bank, BANK_COLS),
               money=["Debit", "Credit", "Balance"], title=head, totals=tot)
        _write(xw, fmt, "ERP Statement", _frame(erp, ERP_COLS_OUT),
               money=["Transac Amount"], title=head + [
                   "ERP rows of this bank account only (in-scope and excluded rows both shown)."],
               totals=tot)
        _write(xw, fmt, "Reconciliation", _frame(reco.sort_values(
                   ["status", "txn_date", "amount"], na_position="last"), RECO_COLS),
               money=["ERP Amount", "Bank Debit", "Bank Credit", "Amount Diff"],
               title=head, totals=tot, footer=PARAM_FOOTER)
    return path


def write_all_bank_workbooks(res: dict, folder: str):
    """One three-sheet workbook per uploaded statement. Returns {file: path}."""
    import os
    os.makedirs(folder, exist_ok=True)
    out = {}
    for st in res["statements"]:
        name = f"Reco_{safe_name(st['bank'] or st['file'])}_{safe_name(st['account_no'] or 'NA')}.xlsx"
        path = os.path.join(folder, name)
        write_bank_workbook(res, st["file"], path)
        out[st["file"]] = name
    return out


def _dt(v):
    return "-" if v is None or pd.isna(v) else pd.Timestamp(v).strftime("%d-%m-%Y")


def _formats(wb):
    return {
        "title": wb.add_format({"bold": True, "font_size": 15, "font_color": "#0B2545"}),
        "sub": wb.add_format({"font_size": 9, "font_color": "#5A6B7B", "italic": True}),
        "hdr": wb.add_format({"bold": True, "bg_color": "#0B2545", "font_color": "white",
                              "border": 1, "text_wrap": True, "valign": "vcenter"}),
        "cell": wb.add_format({"border": 1, "valign": "top"}),
        "money": wb.add_format({"border": 1, "num_format": "#,##0.00"}),
        "kmoney": wb.add_format({"border": 1, "num_format": "#,##0.00", "bold": True}),
        "date": wb.add_format({"border": 1, "num_format": "dd-mm-yyyy"}),
        "sec": wb.add_format({"bold": True, "font_size": 12, "font_color": "#0B2545",
                              "bottom": 2, "border_color": "#0B2545"}),
        "wrap": wb.add_format({"text_wrap": True, "valign": "top", "border": 1}),
        "key": wb.add_format({"bold": True, "valign": "top", "border": 1,
                              "bg_color": "#EEF3F8"}),
        "tot": wb.add_format({"bold": True, "bg_color": "#EEF3F8", "border": 1,
                              "num_format": "#,##0.00"}),
        "totc": wb.add_format({"bold": True, "bg_color": "#EEF3F8", "border": 1}),
    }


PARAM_FOOTER = [
    "PARAMETERS CONSIDERED FOR THIS RECONCILIATION",
    "ONLINE (API) transactions  [Rules 2, 3, 6]  ->  1) Transaction date  2) Amount  "
    "3) PMT/Ref number searched in the bank reference, cheque number and narration  "
    "4) if the date varies, the reference number decides the match  "
    "5) ERP 'online' transac type is treated as a bank DEBIT.",
    "OFFLINE (PI to PI / Tally) transactions  [Rule 4]  ->  1) Transaction date  2) Amount  "
    "3) Amount type Dr/Cr with the BANK STATEMENT as the base (ERP Dr must face bank Dr, "
    "ERP Cr must face bank Cr)  4) Description / payee narration match  "
    "5) Cheque / reference number match.",
    "POPULATION  ->  Rule 1: assigned + unassigned included, Reject ignored on both sides.  "
    "Rule 5: only 'Fully Assigned' and 'Auto Assiged' are reconciled.  "
    "Rule 7: ERP transaction date and bank transaction date are treated as the same date.",
    "Full detail, tolerances and result-code definitions: see the 'Rules & Parameters' sheet.",
]


def _write(xw, fmt, name, df, money=(), start=0, footer=None, title=None,
           totals=None):
    df = df.copy()
    ws = xw.book.add_worksheet(name[:31])
    xw.sheets[name[:31]] = ws
    if title:
        ws.write(start, 0, title[0], fmt["title"])
        for k, line in enumerate(title[1:], start=1):
            ws.write(start + k, 0, line, fmt["sub"])
        start += len(title) + 1
    for j, col in enumerate(df.columns):
        ws.write(start, j, col, fmt["hdr"])
        longest = df[col].astype(str).str.len().max()
        longest = 10 if pd.isna(longest) else int(longest)
        width = max(10, min(46, longest + 2))
        ws.set_column(j, j, max(width, len(col) + 2))
    for i, row in enumerate(df.itertuples(index=False), start=start + 1):
        for j, (col, v) in enumerate(zip(df.columns, row)):
            if pd.isna(v) if not isinstance(v, (list, set, dict)) else False:
                ws.write_blank(i, j, None, fmt["cell"])
            elif isinstance(v, pd.Timestamp):
                ws.write_datetime(i, j, v.to_pydatetime(), fmt["date"])
            elif col in money and isinstance(v, (int, float)):
                ws.write_number(i, j, float(v), fmt["money"])
            elif isinstance(v, (int, float)):
                ws.write_number(i, j, float(v), fmt["cell"])
            else:
                ws.write(i, j, str(v), fmt["cell"])
    ws.freeze_panes(start + 1, 0)
    if len(df):
        ws.autofilter(start, 0, start + len(df), len(df.columns) - 1)
    r = start + len(df) + 2
    if totals:
        for kind, label, dr, cr in totals:
            if kind == "gap":
                r += 1
                continue
            f_lab = fmt["hdr"] if kind == "head" else (fmt["totc"] if kind == "bold" else fmt["key"])
            f_val = fmt["hdr"] if kind == "head" else (fmt["tot"] if kind == "bold" else fmt["money"])
            ws.write(r, 0, label, f_lab)
            for j, v in ((1, dr), (2, cr)):
                if isinstance(v, (int, float)):
                    ws.write_number(r, j, float(v), f_val)
                else:
                    ws.write(r, j, str(v), f_val)
            r += 1
        r += 2
    if footer:
        ws.write(r, 0, footer[0], fmt["sec"])
        for line in footer[1:]:
            r += 1
            ws.write(r, 0, line, fmt["sub"])
    return ws


def _sheet_summary(xw, fmt, res):
    s = res["summary"].copy()
    df = _frame(s, SUMMARY_COLS)
    ws = xw.book.add_worksheet("Summary")
    xw.sheets["Summary"] = ws
    ws.write(0, 0, "BANK  <->  ERP  RECONCILIATION SUMMARY", fmt["title"])
    ws.write(1, 0, f"ERP source: {res['erp_path']}   |   Statement folder: {res['bank_folder']}", fmt["sub"])
    ws.write(2, 0, "Only 'Fully Assigned' / 'Auto Assiged' ERP rows are reconciled; "
                   "Reject rows (ERP and bank) are ignored - see the 'Rules & Parameters' sheet.", fmt["sub"])

    start = 4
    for j, col in enumerate(df.columns):
        ws.write(start, j, col, fmt["hdr"])
        ws.set_column(j, j, max(12, min(30, len(col) + 4)))
    money_cols = {"Bank Debit", "Bank Credit", "ERP Debit", "ERP Credit",
                  "Matched Amount", "Open ERP Amount"}
    for i, row in enumerate(df.itertuples(index=False), start=start + 1):
        for j, (col, v) in enumerate(zip(df.columns, row)):
            if isinstance(v, pd.Timestamp) and not pd.isna(v):
                ws.write_datetime(i, j, v.to_pydatetime(), fmt["date"])
            elif v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "NaT":
                ws.write_blank(i, j, None, fmt["cell"])
            elif col in money_cols:
                ws.write_number(i, j, float(v), fmt["money"])
            elif isinstance(v, (int, float)):
                ws.write_number(i, j, float(v), fmt["cell"])
            else:
                ws.write(i, j, str(v), fmt["cell"])

    tot = start + len(df) + 1
    ws.write(tot, 0, "GRAND TOTAL", fmt["totc"])
    for j, col in enumerate(df.columns):
        if j == 0:
            continue
        if col in money_cols or col in (
                "Bank Txns", "Bank Rejects (ignored)", "ERP Txns (in scope)", "Matched",
                "Exceptions", "Unmatched (ERP)", "Unmatched (Bank)", "Not Compared"):
            ws.write_number(tot, j, float(pd.to_numeric(df[col], errors="coerce").sum()),
                            fmt["tot"])
        else:
            ws.write_blank(tot, j, None, fmt["totc"])
    ws.freeze_panes(start + 1, 1)


def _amt_text(res):
    t = res["cfg"]["amount_tolerance"]
    return "EXACT - equal to the paisa" if t <= 0 else f"equal within Rs. {t:.2f}"


def _sheet_rules(xw, fmt, res):
    """The parameter documentation the business asked to see at the end."""
    ws = xw.book.add_worksheet("Rules & Parameters")
    xw.sheets["Rules & Parameters"] = ws
    ws.set_column(0, 0, 22)
    ws.set_column(1, 1, 30)
    ws.set_column(2, 2, 96)
    r = 0
    ws.write(r, 0, "PARAMETERS USED FOR THIS RECONCILIATION", fmt["title"]); r += 2

    def section(t):
        nonlocal r
        ws.write(r, 0, t, fmt["sec"]); r += 1

    def kv(k, v, d=""):
        nonlocal r
        ws.write(r, 0, k, fmt["key"])
        ws.write(r, 1, v, fmt["wrap"])
        ws.write(r, 2, d, fmt["wrap"])
        r += 1

    section("A. POPULATION / SCOPE")
    kv("Rule 1", "Assigned + unassigned included, Reject excluded",
       "ERP rows with Is Assign = 'Reject' and bank lines whose narration contains REJECT are dropped - they carry no financial impact.")
    kv("Rule 5", "Only 'Fully Assigned' and 'Auto Assiged'",
       "Any other Is Assign value (Initial Assign Completed, Not Found, blank) is reported as EXCLUDED and is not reconciled.")
    kv("Account pairing", "ERP 'Bank Account Number' = statement account number",
       "Digits only, leading zeros ignored. If the statement header carries no account number, the whole bank's ERP rows are used and the scope is flagged 'BANK'.")
    kv("Period", "Statement period (min/max transaction date)",
       "ERP rows outside the uploaded statement's period are reported as 'Not compared - outside statement period', never as a break.")

    section("B. ONLINE TRANSACTIONS (API payments)   [Rule 2, 3, 6]")
    kv("Base fields", "Date + Amount + PMT/Ref number + Dr/Cr",
       "Rule 6: Transac Type 'online' is treated as a DEBIT on the bank side.")
    kv("1. Reference", "ERP PMT/Ref number vs bank reference / cheque / narration",
       "Exact token match (e.g. NEFTTESTTXNS1905 inside NEFT/AXISCN1425455918/NEFTTESTTXNS1905/...). A full UTR (12+ digits) may also match as a substring.")
    kv("2. Amount", _amt_text(res),
       "Decimals are compared strictly - 1,234.50 never matches 1,234.00 or 1,235.00.")
    kv("3. Date", f"equal, else within +/- {res['cfg']['date_tolerance_days']} days",
       "Rule 3: if the date varies, the reference number decides the match. Bank transaction date AND value date are both tested.")
    kv("4. Dr/Cr", "ERP online = bank DEBIT",
       "A candidate with the opposite Dr/Cr is never auto-matched; it is raised as a Dr/Cr exception.")

    section("C. OFFLINE TRANSACTIONS (PI to PI / Tally)   [Rule 4]")
    kv("Base fields", "Date + Amount + Dr/Cr + Description + Cheque / Ref number",
       "Rule 4: the BANK statement is the base for Dr/Cr. ERP Credit must sit against a bank Credit, ERP Debit against a bank Debit.")
    kv("1. Amount", _amt_text(res), "Decimals are compared strictly.")
    kv("2. Dr/Cr", "ERP Transac Type vs bank Debit/Credit column",
       "Mismatch = flagged, never silently matched.")
    kv("3. Date", f"equal, else within +/- {res['cfg']['date_tolerance_days']} days",
       "Rule 7: ERP transaction date and bank transaction date are treated as the same date.")
    kv("4. Narration", f"payee + description token overlap >= {res['cfg']['desc_threshold']:.2f}",
       "Payee name / description words are compared with the bank narration after removing NEFT/RTGS/IMPS/UPI style noise words.")
    kv("5. Cheque / Ref", "ERP reference & description UTR vs bank cheque / reference",
       "Used both to confirm a match and to detect 'same reference, different amount'.")

    section("D. MATCHING MECHANICS")
    kv("Assignment", "One-to-one, highest score first",
       "Every candidate pair is scored (reference 120 pts, same date 45 pts, near date up to 30, narration up to 40). Pairs are locked in from the best score downwards, so one bank line can never satisfy two ERP lines.")
    kv("Duplicates", "Flagged, not silently consumed",
       "Where several ERP lines compete for one bank line the extra lines stay open as exceptions.")
    kv("Tolerances", f"amount: {_amt_text(res)}, date +/- {res['cfg']['date_tolerance_days']} d, "
                     f"narration >= {res['cfg']['desc_threshold']:.2f}, reference >= {res['cfg']['ref_min_len']} chars",
       "Configurable in reco_system/engine.py -> DEFAULTS.")

    r += 1
    section("E. RESULT CODES USED IN THE SHEETS")
    ws.write(r, 0, "Code", fmt["hdr"]); ws.write(r, 1, "Meaning", fmt["hdr"])
    ws.write(r, 2, "Definition", fmt["hdr"]); r += 1
    for code, (label, desc) in RULE_TEXT.items():
        ws.write(r, 0, code, fmt["key"])
        ws.write(r, 1, label, fmt["wrap"])
        ws.write(r, 2, desc, fmt["wrap"])
        r += 1

    if res["parse_errors"]:
        r += 1
        section("F. FILES THAT COULD NOT BE READ")
        for fn, err in res["parse_errors"]:
            kv(fn, err, "")
