"""ERP Payment Import loader + normaliser."""
from __future__ import annotations

import os
import re

import pandas as pd

from .common import (acct_key, clean_text, desc_tokens, ref_digits, ref_key,
                     ref_tokens, strong_refs, to_amount, to_date)

# Rule 5 - only these assignment states take part in the reconciliation.
VALID_ASSIGN = {"fully assigned", "auto assiged", "auto assigned"}
# Rule 1 - rejects have no impact and are dropped outright.
REJECT_ASSIGN = {"reject", "rejected"}

ERP_COLS = {
    "slno": ["slno", "sl no", "s.no"],
    "payment_mode": ["payment mode"],
    "transac_type": ["transac type", "transaction type"],
    "txn_date": ["transaction date"],
    "ref_no": ["pmt/ref number", "pmt ref number", "ref number"],
    "assign_req": ["assign request number"],
    "import_req": ["import request number"],
    "bank_acct_name": ["bank account name"],
    "account_no": ["bank account number"],
    "transac_for": ["transac for"],
    "amount": ["transac amount", "transaction amount"],
    "payee": ["payee name"],
    "description": ["description"],
    "reference": ["reference"],
    "is_assign": ["is assign"],
    "organization": ["organization"],
    "bank_name": ["bank name"],
    "alias": ["alias name"],
    "entry_id": ["entry id"],
}


def _resolve(cols):
    out = {}
    low = {str(c).strip().lower(): c for c in cols}
    for field, keys in ERP_COLS.items():
        for k in keys:
            if k in low:
                out[field] = low[k]
                break
    return out


REQUIRED = ("txn_date", "amount", "payment_mode", "transac_type",
            "is_assign", "account_no")


def _find_erp_header(path: str, scan: int = 40):
    """Locate the ERP header row - exports often carry title / total rows above it."""
    probe = pd.read_excel(path, sheet_name=0, header=None, nrows=scan, dtype=str)
    best, best_hit = None, 0
    for i in range(len(probe)):
        labels = [str(v) for v in probe.iloc[i].tolist()]
        hit = len(_resolve(labels))
        if hit > best_hit:
            best, best_hit = i, hit
        if best_hit >= len(ERP_COLS) - 3:      # a full header row, stop early
            break
    if best is None or best_hit < 5:
        raise ValueError(
            "Could not find the ERP header row. The sheet must contain columns like "
            "'Transaction Date', 'Transac Amount', 'Payment Mode', 'Transac Type', "
            "'Is Assign' and 'Bank Account Number'.")
    return best


def load_erp(path: str, header_row: int | None = None) -> pd.DataFrame:
    """Read the ERP export and normalise it to the reconciliation schema."""
    if header_row is None:
        header_row = _find_erp_header(path)
    raw = pd.read_excel(path, sheet_name=0, header=header_row)
    raw = raw.dropna(how="all")
    m = _resolve(raw.columns)
    missing = [f for f in REQUIRED if f not in m]
    if missing:
        want = {"txn_date": "Transaction Date", "amount": "Transac Amount",
                "payment_mode": "Payment Mode", "transac_type": "Transac Type",
                "is_assign": "Is Assign", "account_no": "Bank Account Number"}
        raise ValueError(
            "ERP file is missing required columns: "
            + ", ".join(f"'{want[f]}'" for f in missing)
            + f". Header row used: {header_row + 1}. Columns found: "
            + ", ".join(str(c) for c in list(raw.columns)[:25]))

    df = pd.DataFrame(index=raw.index)
    for field, col in m.items():
        df[field] = raw[col]

    df["erp_row"] = raw.index + header_row + 2   # -> real excel row number
    df["erp_id"] = [f"ERP#{r}" for r in df["erp_row"]]
    df["txn_date"] = df["txn_date"].map(to_date)
    df["amount"] = df["amount"].map(to_amount).round(2)
    df["account_no"] = df["account_no"].map(clean_text)
    df["acct_key"] = df["account_no"].map(acct_key)
    df["payment_mode_n"] = df["payment_mode"].map(lambda v: clean_text(v).lower())
    df["transac_type_n"] = df["transac_type"].map(lambda v: clean_text(v).lower())
    df["is_assign_n"] = df["is_assign"].map(lambda v: clean_text(v).lower())
    for c in ("description", "payee", "reference", "ref_no", "bank_name", "alias",
              "bank_acct_name", "transac_for", "organization"):
        if c in df:
            df[c] = df[c].map(clean_text)
        else:
            df[c] = ""

    # Rule 2/3/6 - the ERP has two worlds: online (API) and offline (PI-to-PI Tally).
    df["mode"] = df.apply(_mode, axis=1)
    # Rule 6 - an "online" transac type is a DEBIT for reconciliation purposes.
    df["dr_cr"] = df.apply(_dr_cr, axis=1)

    df["ref_digits"] = df["ref_no"].map(ref_digits)
    df["ref_key"] = df["ref_no"].map(ref_key)
    # PRIMARY = the ERP PMT / Ref number itself (rule 3 & 4 key).
    # SECONDARY = UTR-like numbers that appear inside the ERP narration.
    df["erp_ref_primary"] = [strong_refs(ref_tokens(r.ref_no)) for r in df.itertuples()]
    df["erp_ref_secondary"] = [
        strong_refs(ref_tokens(r.reference, r.description)) for r in df.itertuples()
    ]
    df["erp_ref_tokens"] = [
        a | b for a, b in zip(df["erp_ref_primary"], df["erp_ref_secondary"])
    ]
    df["erp_desc_tokens"] = [
        desc_tokens(f"{r.payee} {r.description} {r.reference}") for r in df.itertuples()
    ]

    # Rules 1 + 5 - scope of the reconciliation population.
    def scope(v):
        if v in REJECT_ASSIGN:
            return "EXCLUDED - Reject (Rule 1)"
        if v in VALID_ASSIGN:
            return "IN SCOPE"
        return f"EXCLUDED - '{v or 'blank'}' not Fully/Auto Assigned (Rule 5)"

    df["scope"] = df["is_assign_n"].map(scope)
    df["in_scope"] = df["scope"].eq("IN SCOPE")
    df["source_file"] = os.path.basename(path)
    return df


def _mode(r) -> str:
    pm, tt = r["payment_mode_n"], r["transac_type_n"]
    if pm == "online" or tt == "online":
        return "ONLINE"
    if pm == "offline":
        return "OFFLINE"
    return "OFFLINE"


def _dr_cr(r) -> str:
    tt = r["transac_type_n"]
    if tt == "online":          # Rule 6: online == debit
        return "DEBIT"
    if tt.startswith("debit"):
        return "DEBIT"
    if tt.startswith("credit"):
        return "CREDIT"
    # fall back on the payment mode for blank transac types
    return "DEBIT" if r["payment_mode_n"] == "online" else ""
