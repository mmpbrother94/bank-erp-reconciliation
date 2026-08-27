"""Format-agnostic bank statement reader.

Every Indian bank exports a different layout, so instead of one parser per bank
we auto-detect the header row and map the columns onto a canonical schema:

    bank_file, bank_name, account_no, row_no, txn_date, value_date,
    description, ref_no, cheque_no, debit, credit, amount, dr_cr, balance

The detection is driven by keyword dictionaries, so a new bank / a new export
template usually works with no code change.
"""
from __future__ import annotations

import csv
import io
import os
import re

import pandas as pd

from .common import (acct_key, clean_text, ref_tokens, to_amount, to_date)

# --------------------------------------------------------------- dictionaries

COLMAP = {
    "txn_date": [
        "transaction date & time", "transaction date", "txn date", "tran date",
        "trans date", "date of transaction", "posting date", "post date",
        "transaction posted date", "payment date", "date",
    ],
    "value_date": ["value date", "valuedate", "val date", "value dt"],
    "description": [
        "payment narration", "transaction remarks", "transaction details",
        "narration", "narrative", "particulars", "description", "remarks",
        "transaction description", "details",
    ],
    "ref_no": [
        "cheque. no./ref. no.", "ref no./cheque no.", "chq /ref no.",
        "customer reference no", "bank reference", "utr number", "reference no",
        "reference number", "ref no", "cheque id", "instr. id", "tran id",
        "transaction id", "reference",
    ],
    "cheque_no": ["cheque no", "cheque no.", "chq. no.", "cheque number", "chq no"],
    "debit": [
        "withdrawal amt (inr)", "withdrawl amt", "withdrawal amt", "withdrawals",
        "withdrawal", "debit amount", "debit amt", "debit", "dr amount", "paid out",
    ],
    "credit": [
        "deposit amt (inr)", "deposit amt", "deposits", "deposit",
        "credit amount", "credit amt", "credit", "cr amount", "paid in",
    ],
    "amount": ["amount(inr)", "amount (inr)", "transaction amount", "amount"],
    "dr_cr": ["dr / cr", "dr/cr", "debit/credit", "type", "tran type", "cr/dr"],
    "balance": ["balance (inr)", "balance(inr)", "running balance", "available balance",
                "closing balance", "balance"],
}

# A row qualifies as the header row when it contains a date-ish column plus at
# least one narration column plus at least one money column.
_DATEISH = set(COLMAP["txn_date"] + COLMAP["value_date"])
_DESCISH = set(COLMAP["description"])
_MONEYISH = set(COLMAP["debit"] + COLMAP["credit"] + COLMAP["amount"])

# Rows that must never be treated as transactions.
NOISE_PATTERNS = [
    r"^\s*$", r"opening balance", r"closing balance", r"balance b/?f",
    r"^total\b", r"page total", r"^grand\b", r"page \d+ of \d+",
    r"end of statement", r"statement downloaded", r"important note",
    r"^\s*brought forward", r"carried forward",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.I)

REJECT_RE = re.compile(r"\bREJECT(ED)?\b|\bRETURN(ED)?\s*(UNPAID|CHQ)|\bREVERSAL OF REJECT", re.I)

ACCT_RE = re.compile(
    r"(?:a/?c\s*(?:no|number)?|account\s*(?:no|number)?|statement of account(?:\s*no)?)"
    r"[^0-9]{0,20}([0-9]{6,20})", re.I)


# ------------------------------------------------------------------ raw read

def _read_raw(path: str) -> pd.DataFrame:
    """Read any statement file into a header-less string DataFrame."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt"):
        return _read_delimited(path)
    try:
        return pd.read_excel(path, header=None, dtype=str)
    except Exception:
        # Some banks ship a tab/CSV file with an .xls extension.
        return _read_delimited(path)


def _read_delimited(path: str) -> pd.DataFrame:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    sample = text[:8000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows, dtype=str)


# ------------------------------------------------------------ header finding

def _match_col(label: str):
    """Map a raw header label onto a canonical field name."""
    lab = clean_text(label).lower().strip(" :*#")
    if not lab:
        return None
    best, best_len = None, 0
    for field, keys in COLMAP.items():
        for k in keys:
            if lab == k or lab.startswith(k) or k in lab:
                if len(k) > best_len:
                    best, best_len = field, len(k)
    return best


def _find_header(raw: pd.DataFrame, scan: int = 80):
    """Return (row_index, {col_index: field}) for the transaction header row."""
    best = None
    for i in range(min(scan, len(raw))):
        labels = [clean_text(v).lower() for v in raw.iloc[i].tolist()]
        mapping, seen = {}, set()
        for j, lab in enumerate(labels):
            f = _match_col(lab)
            if not f:
                continue
            # first occurrence wins for duplicated labels
            if f in seen and f not in ("txn_date", "value_date"):
                continue
            if f in seen and f in ("txn_date", "value_date"):
                continue
            mapping[j] = f
            seen.add(f)
        has_date = any(l in _DATEISH or _match_col(l) in ("txn_date", "value_date") for l in labels if l)
        has_desc = any(_match_col(l) == "description" for l in labels if l)
        has_money = any(_match_col(l) in ("debit", "credit", "amount") for l in labels if l)
        score = len(mapping) + (3 if has_date else 0) + (3 if has_desc else 0) + (3 if has_money else 0)
        if has_date and has_money and len(mapping) >= 3:
            if best is None or score > best[2]:
                best = (i, mapping, score)
    if best is None:
        raise ValueError("Could not locate a transaction header row")
    return best[0], best[1]


def _find_account(raw: pd.DataFrame, header_row: int) -> str:
    """Pull the account number out of the statement header block."""
    blob_rows = []
    for i in range(0, min(header_row + 1, len(raw))):
        blob_rows.append(" ".join(clean_text(v) for v in raw.iloc[i].tolist()))
    # also look a couple of rows below the header (RBL puts it in a title row)
    blob = " \n ".join(blob_rows)
    hits = ACCT_RE.findall(blob)
    if hits:
        # prefer the longest hit (account numbers beat customer ids)
        return max(hits, key=len)
    return ""


# ------------------------------------------------------------------- parsing

def parse_statement(path: str, bank_name: str = "", account_no: str = "") -> pd.DataFrame:
    """Parse one bank statement file into the canonical schema."""
    raw = _read_raw(path)
    hdr, mapping = _find_header(raw)
    acct = account_no or _find_account(raw, hdr)
    fname = os.path.basename(path)
    bank = bank_name or os.path.splitext(fname)[0].upper()

    inv = {}
    for col, field in mapping.items():
        inv.setdefault(field, col)

    recs = []
    for i in range(hdr + 1, len(raw)):
        row = raw.iloc[i].tolist()
        joined = " ".join(clean_text(v) for v in row)
        if NOISE_RE.search(joined) and not re.search(r"\d{2}[-/][\d\w]{2,3}[-/]\d{2,4}", joined):
            continue
        if not joined.strip():
            continue

        def g(field):
            j = inv.get(field)
            return row[j] if j is not None and j < len(row) else None

        txn_date = to_date(g("txn_date"))
        val_date = to_date(g("value_date"))
        if pd.isna(txn_date) and pd.isna(val_date):
            continue  # not a transaction row
        if pd.isna(txn_date):
            txn_date = val_date
        if pd.isna(val_date):
            val_date = txn_date

        debit = to_amount(g("debit"))
        credit = to_amount(g("credit"))
        amount = to_amount(g("amount"))
        drcr_raw = clean_text(g("dr_cr")).upper()

        if debit or credit:
            dr_cr = "DEBIT" if debit >= credit and debit > 0 else "CREDIT"
            amt = debit if dr_cr == "DEBIT" else credit
        elif amount:
            if drcr_raw.startswith("D") or "WITHDRAW" in drcr_raw:
                dr_cr, debit, credit, amt = "DEBIT", amount, 0.0, amount
            elif drcr_raw.startswith("C") or "DEPOSIT" in drcr_raw:
                dr_cr, debit, credit, amt = "CREDIT", 0.0, amount, amount
            else:
                dr_cr, amt = "", amount
        else:
            continue  # zero / non-money row

        if amt == 0:
            continue

        desc = clean_text(g("description"))
        ref = clean_text(g("ref_no"))
        chq = clean_text(g("cheque_no"))
        is_reject = bool(REJECT_RE.search(desc) or REJECT_RE.search(ref))

        recs.append({
            "bank_file": fname,
            "bank_name": bank,
            "account_no": acct,
            "bank_row": i + 1,
            "txn_date": txn_date,
            "value_date": val_date,
            "description": desc,
            "ref_no": ref,
            "cheque_no": chq,
            "debit": round(debit, 2),
            "credit": round(credit, 2),
            "amount": round(amt, 2),
            "dr_cr": dr_cr,
            "balance": to_amount(g("balance")),
            "is_reject": is_reject,
        })

    df = pd.DataFrame(recs)
    if df.empty:
        df = pd.DataFrame(columns=[
            "bank_file", "bank_name", "account_no", "bank_row", "txn_date",
            "value_date", "description", "ref_no", "cheque_no", "debit",
            "credit", "amount", "dr_cr", "balance", "is_reject"])
        df["bank_file"] = pd.Series(dtype=str)
    else:
        df["bank_ref_tokens"] = [
            ref_tokens(r.description, r.ref_no, r.cheque_no) for r in df.itertuples()
        ]
        df["acct_key"] = df["account_no"].map(acct_key)
        df["bank_id"] = [f"{fname}#{r}" for r in df["bank_row"]]
    return df


# project files that live next to the statements and are not statements
SKIP_NAMES = {"requirements.txt", "readme.txt", "notes.txt"}


def parse_folder(folder: str, exclude=()) -> pd.DataFrame:
    """Parse every statement file found in a folder."""
    frames, errors = [], []
    for fn in sorted(os.listdir(folder)):
        if fn in exclude or fn.startswith("~$") or fn.lower() in SKIP_NAMES:
            continue
        if os.path.splitext(fn)[1].lower() not in (".xls", ".xlsx", ".csv", ".txt"):
            continue
        path = os.path.join(folder, fn)
        try:
            df = parse_statement(path)
            frames.append(df)
        except Exception as exc:  # keep going; report at the end
            errors.append((fn, f"{type(exc).__name__}: {exc}"))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return out, errors
