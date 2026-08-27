"""Ties everything together: load -> pair statements with ERP accounts -> match."""
from __future__ import annotations

import os

import pandas as pd

from .bank_parser import parse_folder, parse_statement
from .common import desc_tokens, ref_key, clean_text
from .engine import DEFAULTS, RULE_TEXT, canonical_bank, reconcile_account
from .erp_loader import load_erp


def _prep_bank(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["bank_canon"] = df["bank_name"].map(canonical_bank)
    df["bank_desc_tokens"] = [
        desc_tokens(f"{r.description} {r.ref_no} {r.cheque_no}") for r in df.itertuples()
    ]
    df["bank_key_blob"] = [
        ref_key(f"{r.description}{r.ref_no}{r.cheque_no}") for r in df.itertuples()
    ]
    df["bank_digit_blob"] = df["bank_key_blob"].map(lambda s: "".join(c for c in s if c.isdigit()))
    return df


def run(erp_path: str, bank_folder: str, cfg=None):
    """Run the full reconciliation. Returns a dict of result frames + meta."""
    cfg = {**DEFAULTS, **(cfg or {})}
    erp = load_erp(erp_path)
    banks, parse_errors = parse_folder(bank_folder, exclude={os.path.basename(erp_path)})
    banks = _prep_bank(banks)

    tol = pd.Timedelta(days=cfg["date_tolerance_days"])
    erp_scope = erp[erp["in_scope"]].copy()

    # ---- pair each statement file with the ERP rows of the same bank account
    statements = []
    for (fname, acct), grp in banks.groupby(["bank_file", "acct_key"], dropna=False):
        statements.append({
            "file": fname,
            "acct_key": acct or "",
            "bank_canon": grp["bank_canon"].iloc[0],
            "account_no": grp["account_no"].iloc[0],
            "rows": grp,
            "from": grp["txn_date"].min(),
            "to": grp["txn_date"].max(),
        })
    # statements that produced zero transactions still deserve a line in the report
    for fn in sorted(os.listdir(bank_folder)):
        if fn == os.path.basename(erp_path) or fn.startswith("~$"):
            continue
        if os.path.splitext(fn)[1].lower() not in (".xls", ".xlsx", ".csv", ".txt"):
            continue
        if not any(s["file"] == fn for s in statements):
            statements.append({
                "file": fn, "acct_key": "", "bank_canon": canonical_bank(fn),
                "account_no": "", "rows": banks.iloc[0:0], "from": pd.NaT, "to": pd.NaT,
            })

    erp_accounts = set(erp_scope["acct_key"])
    claimed = set()
    for s in statements:
        s["scope_level"] = None
        if s["acct_key"] and s["acct_key"] in erp_accounts:
            s["scope_level"] = "ACCOUNT"
            s["erp_accounts"] = {s["acct_key"]}
            claimed.add(s["acct_key"])
            # the file name is a poor source for the bank's name; once the
            # account is known, take the name the ERP uses for it
            erp_name = erp_scope.loc[erp_scope["acct_key"] == s["acct_key"],
                                     "bank_name"].iloc[0]
            if clean_text(erp_name):
                s["bank_canon"] = canonical_bank(erp_name)

    for s in statements:
        if s["scope_level"]:
            continue
        same_bank = {a for a in erp_accounts
                     if canonical_bank(erp_scope.loc[erp_scope["acct_key"] == a, "bank_name"].iloc[0]) == s["bank_canon"]}
        s["erp_accounts"] = same_bank - claimed
        if not s["erp_accounts"]:
            s["scope_level"] = "NONE"
        elif s["acct_key"]:
            # statement carries an account number that the ERP does not know
            s["scope_level"] = "BANK (stmt a/c not in ERP)"
        else:
            s["scope_level"] = "BANK (a/c not in stmt header)"
        claimed |= s["erp_accounts"]

    # ---- reconcile statement by statement
    erp_out, bank_out, summary = [], [], []
    for s in statements:
        pool = erp_scope[erp_scope["acct_key"].isin(s["erp_accounts"])].copy()
        brows = s["rows"]
        rej = brows["is_reject"].astype(bool) if len(brows) else brows
        live = brows[~rej] if len(brows) else brows
        rejected = brows[rej] if len(brows) else brows

        if len(live) and not pool.empty:
            lo, hi = s["from"] - tol, s["to"] + tol
            inwin = pool[(pool["txn_date"] >= lo) & (pool["txn_date"] <= hi)]
            outwin = pool[~pool.index.isin(inwin.index)]
            e_res, b_res = reconcile_account(inwin, live, cfg)
            # ERP rows that only entered the pool through the date tolerance and
            # then failed to match are boundary noise, not true breaks.
            if len(e_res):
                edge = (e_res["status"] == "UNMATCHED") & (
                    (e_res["txn_date"] < s["from"]) | (e_res["txn_date"] > s["to"]))
                e_res.loc[edge, "status"] = "NOT COMPARED"
                e_res.loc[edge, "rule_code"] = "U3"
        else:
            inwin = pool.iloc[0:0]
            outwin = pool
            e_res = pd.DataFrame()
            b_res = live.assign(status="UNMATCHED", rule_code="U2") if len(live) else live

        if not outwin.empty:
            ow = outwin.copy()
            ow["status"] = "NOT COMPARED"
            ow["rule_code"] = "U3"
            for c in ("bank_id", "bank_description", "bank_ref_no", "bank_cheque_no", "bank_dr_cr"):
                ow[c] = ""
            for c in ("bank_debit", "bank_credit", "bank_amount", "match_score", "desc_score"):
                ow[c] = 0.0
            ow["bank_date"] = pd.NaT
            ow["bank_value_date"] = pd.NaT
            ow["bank_row"] = ""
            ow["date_diff_days"] = ""
            ow["ref_matched"] = False
            ow["candidates"] = 0
            ow["amount_diff"] = ""
            e_res = pd.concat([e_res, ow], ignore_index=True)

        if len(rejected):
            rj = rejected.assign(status="EXCLUDED", rule_code="E3")
            b_res = pd.concat([b_res, rj], ignore_index=True) if len(b_res) else rj

        for df in (e_res, b_res):
            if len(df):
                df["statement_file"] = s["file"]
                df["statement_account"] = s["account_no"]
                df["bank"] = s["bank_canon"]
                df["scope_level"] = s["scope_level"]

        if len(e_res):
            erp_out.append(e_res)
        if len(b_res):
            bank_out.append(b_res)

        summary.append(_summarise(s, e_res, b_res, live, rejected))

    # ---- ERP accounts with no statement at all
    orphan = erp_scope[~erp_scope["acct_key"].isin(claimed)].copy()
    if not orphan.empty:
        orphan["status"] = "NOT COMPARED"
        orphan["rule_code"] = "U4"
        orphan["statement_file"] = ""
        orphan["statement_account"] = ""
        orphan["bank"] = orphan["bank_name"].map(canonical_bank)
        orphan["scope_level"] = "NONE"
        for c in ("bank_id", "bank_description", "bank_ref_no", "bank_cheque_no", "bank_dr_cr"):
            orphan[c] = ""
        for c in ("bank_debit", "bank_credit", "bank_amount", "match_score", "desc_score"):
            orphan[c] = 0.0
        orphan["bank_date"] = pd.NaT
        orphan["bank_value_date"] = pd.NaT
        orphan["bank_row"] = ""
        orphan["date_diff_days"] = ""
        orphan["ref_matched"] = False
        orphan["candidates"] = 0
        orphan["amount_diff"] = ""
        erp_out.append(orphan)
        for bnk, g in orphan.groupby("bank"):
            summary.append({
                "statement_file": "(no statement uploaded)", "bank": bnk,
                "account_no": ", ".join(sorted(set(g["account_no"]))[:4]),
                "scope_level": "NONE", "period_from": pd.NaT, "period_to": pd.NaT,
                "bank_txns": 0, "bank_debit": 0.0, "bank_credit": 0.0,
                "bank_rejects": 0, "bank_unmatched": 0,
                "bank_unmatched_debit": 0.0, "bank_unmatched_credit": 0.0,
                "erp_txns": len(g), "erp_debit": float(g.loc[g["dr_cr"] == "DEBIT", "amount"].sum()),
                "erp_credit": float(g.loc[g["dr_cr"] == "CREDIT", "amount"].sum()),
                "erp_matched": 0, "erp_exception": 0, "erp_unmatched": 0,
                "erp_not_compared": len(g),
                "matched_amount": 0.0, "unmatched_erp_amount": float(g["amount"].sum()),
                "match_rate": 0.0,
            })

    erp_res = pd.concat(erp_out, ignore_index=True) if erp_out else pd.DataFrame()
    bank_res = pd.concat(bank_out, ignore_index=True) if bank_out else pd.DataFrame()

    # excluded ERP rows (rejects / not fully assigned) carried for completeness
    excl = erp[~erp["in_scope"]].copy()
    excl["status"] = "EXCLUDED"
    excl["rule_code"] = excl["is_assign_n"].map(lambda v: "E1" if v.startswith("reject") else "E2")
    excl["bank"] = excl["bank_name"].map(canonical_bank)

    for df in (erp_res, bank_res, excl):
        if len(df):
            df["rule_label"] = df["rule_code"].map(lambda c: RULE_TEXT.get(c, ("", ""))[0])
            df["reason"] = df["rule_code"].map(lambda c: RULE_TEXT.get(c, ("", ""))[1])

    return {
        "statements": [{
            "file": st["file"],
            "bank": st["bank_canon"],
            "account_no": st["account_no"],
            "acct_key": st["acct_key"],
            "scope_level": st["scope_level"],
            "erp_accounts": sorted(st["erp_accounts"]),
            "period_from": st["from"],
            "period_to": st["to"],
        } for st in statements],
        "erp_raw": erp,
        "erp_result": erp_res,
        "erp_excluded": excl,
        "bank_raw": banks,
        "bank_result": bank_res,
        "summary": pd.DataFrame(summary),
        "parse_errors": parse_errors,
        "cfg": cfg,
        "erp_path": erp_path,
        "bank_folder": bank_folder,
    }


def _summarise(s, e_res, b_res, live, rejected):
    def amt(df, col, val):
        if not len(df) or col not in df:
            return 0.0
        return float(df.loc[df[col] == val, "amount"].sum())

    e_matched = int((e_res["status"] == "MATCHED").sum()) if len(e_res) else 0
    e_exc = int((e_res["status"] == "EXCEPTION").sum()) if len(e_res) else 0
    e_unm = int((e_res["status"] == "UNMATCHED").sum()) if len(e_res) else 0
    e_nc = int((e_res["status"] == "NOT COMPARED").sum()) if len(e_res) else 0
    b_unm = b_res[(b_res["status"] == "UNMATCHED")] if len(b_res) else b_res
    comparable = e_matched + e_exc + e_unm
    return {
        "statement_file": s["file"],
        "bank": s["bank_canon"],
        "account_no": s["account_no"],
        "scope_level": s["scope_level"],
        "period_from": s["from"],
        "period_to": s["to"],
        "bank_txns": int(len(live)),
        "bank_debit": float(live["debit"].sum()) if len(live) else 0.0,
        "bank_credit": float(live["credit"].sum()) if len(live) else 0.0,
        "bank_rejects": int(len(rejected)),
        "bank_unmatched": int(len(b_unm)),
        "bank_unmatched_debit": float(b_unm["debit"].sum()) if len(b_unm) else 0.0,
        "bank_unmatched_credit": float(b_unm["credit"].sum()) if len(b_unm) else 0.0,
        "erp_txns": int(len(e_res)),
        "erp_debit": amt(e_res, "dr_cr", "DEBIT"),
        "erp_credit": amt(e_res, "dr_cr", "CREDIT"),
        "erp_matched": e_matched,
        "erp_exception": e_exc,
        "erp_unmatched": e_unm,
        "erp_not_compared": e_nc,
        "matched_amount": amt(e_res, "status", "MATCHED"),
        "unmatched_erp_amount": (amt(e_res, "status", "UNMATCHED") + amt(e_res, "status", "EXCEPTION")),
        "match_rate": round(100.0 * e_matched / comparable, 1) if comparable else 0.0,
    }
