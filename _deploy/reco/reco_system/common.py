"""Shared normalisation helpers for the Bank <-> ERP reconciliation system."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------- text utils

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")


def clean_text(v) -> str:
    """Collapse whitespace / strip weird excel artefacts."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v)
    if s.lower() in ("nan", "nat", "none"):
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.replace('="', " ").replace('"', " ").replace("\n", " ").replace("\t", " ")
    return _WS.sub(" ", s).strip()


def norm_desc(v) -> str:
    """Upper-cased alphanumeric form of a narration, used for token/fuzzy work."""
    s = clean_text(v).upper()
    return _WS.sub(" ", _NON_ALNUM.sub(" ", s)).strip()


STOPWORDS = {
    "UPI", "NEFT", "RTGS", "IMPS", "CMS", "TO", "BY", "TRANSFER", "TRF", "FROM",
    "PAYMENT", "PAY", "ONLINE", "INB", "P2A", "P2M", "NA", "N", "A", "AC", "ACC",
    "LTD", "LIMITED", "PVT", "PRIVATE", "THE", "AND", "OF", "CHQ", "CHEQUE",
    "CR", "DR", "CREDIT", "DEBIT", "BANK", "INR", "REF", "NO", "MR", "MRS",
}


def desc_tokens(v) -> set:
    """Meaningful alphabetic tokens (>=3 chars) from a narration."""
    out = set()
    for t in norm_desc(v).split():
        if len(t) >= 3 and not t.isdigit() and t not in STOPWORDS:
            out.add(t)
    return out


def token_score(a: set, b: set) -> float:
    """Overlap coefficient between two token sets (0..1)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# ------------------------------------------------------------- number / date

_NUM = re.compile(r"-?[\d,]*\.?\d+")


def to_amount(v):
    """Parse an Indian-format money string to float. Returns 0.0 when absent."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = clean_text(v).replace("₹", "").replace("Rs.", "").replace("INR", "")
    s = s.replace("Cr.", "").replace("Dr.", "").replace("CR", "").replace("DR", "")
    s = s.replace(",", "").strip()
    if not s or s in ("-", "."):
        return 0.0
    m = _NUM.search(s)
    if not m:
        return 0.0
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return 0.0


_DATE_FORMATS = [
    "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d/%b/%Y",
    "%d-%b-%y", "%d %b %y", "%d/%b/%y", "%d-%m-%y", "%d/%m/%y", "%Y/%m/%d",
    "%b %d, %Y", "%d.%m.%Y",
]


def to_date(v):
    """Parse a bank/ERP date cell to a normalised pandas Timestamp (date only)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return pd.NaT
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).normalize()
    s = clean_text(v)
    if not s:
        return pd.NaT
    # keep only the date portion when a time component is glued on
    s = s.split(" ")[0] if re.match(r"^[\d]{1,4}[-/.][\w]{1,4}[-/.][\d]{2,4}", s) else s
    for fmt in _DATE_FORMATS:
        try:
            return pd.Timestamp(datetime.strptime(s, fmt)).normalize()
        except ValueError:
            continue
    try:
        return pd.Timestamp(pd.to_datetime(s, dayfirst=True)).normalize()
    except Exception:
        return pd.NaT


# --------------------------------------------------------------- references

# Long numeric runs (UTR / PMT / RRN) and alphanumeric UTR style tokens.
_REF_NUM = re.compile(r"\d{6,}")
_REF_ALNUM = re.compile(r"\b[A-Z]{2,6}[A-Z0-9]{6,}\b")


def ref_tokens(*parts) -> set:
    """Every reference-looking token found in the given free-text fields."""
    blob = " ".join(clean_text(p) for p in parts).upper()
    out = set(_REF_NUM.findall(blob))
    out |= set(_REF_ALNUM.findall(blob))
    # a reference must be at least 6 chars AND carry a digit - this keeps
    # ordinary narration words ("REINVESTMENT", "ELECTRONICS") out of the set
    return {t for t in out if len(t) >= 6 and any(c.isdigit() for c in t)}


def strong_refs(tokens) -> set:
    """Keep only references reliable enough to assert a match on their own.

    * alphanumeric refs (NEFTTESTTXNS1234, SBIN526213647135) need >= 8 chars
    * pure numbers (UTR / RRN) need >= 10 digits, otherwise short ERP sequence
      numbers collide with cheque numbers and random digits in narrations
    """
    out = set()
    for t in tokens:
        if t.isdigit():
            if len(t) >= 10:
                out.add(t)
        elif len(t) >= 8:
            out.add(t)
    return out


def ref_digits(v) -> str:
    """Digits-only form of a single reference value (ERP PMT/Ref number)."""
    return re.sub(r"\D", "", clean_text(v))


def ref_key(v) -> str:
    """Comparable reference key: alphanumeric, upper-cased."""
    return re.sub(r"[^A-Z0-9]", "", clean_text(v).upper())


def acct_key(v) -> str:
    """Comparable bank-account key (digits only, leading zeros trimmed)."""
    d = re.sub(r"\D", "", clean_text(v))
    return d.lstrip("0")
