"""The reconciliation engine: ERP payment rows <-> bank statement rows.

Matching is score based and one-to-one.  For every in-scope ERP row we build
the set of plausible bank rows, score each candidate on the parameters the
business defined (date / amount / Dr-Cr / reference / narration), then assign
greedily from the highest scoring pair downwards so that a bank line can only
ever be consumed once.  The winning candidate's feature mix is translated back
into a human readable rule code + reason, which is what the dashboard shows.
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from .common import acct_key, clean_text, desc_tokens, ref_key, token_score

# ------------------------------------------------------------------ settings

DEFAULTS = {
    "amount_tolerance": 0.00,      # STRICT: amounts must agree to the paisa
    "date_tolerance_days": 3,      # value-date vs posting-date drift
    "desc_threshold": 0.34,        # narration overlap needed to corroborate
    "ref_min_len": 6,              # shortest token treated as a reference
    "ref_substr_min_len": 12,      # digits-in-narration hits need a full UTR
}

# Rule codes -> (label, description) shown on the dashboard and in the workbook.
RULE_TEXT = {
    "M1": ("Matched - Reference + Date + Amount + Type",
           "Reference number found in the bank narration/reference, same date, same amount, same Dr/Cr."),
    "M2": ("Matched - Reference + Amount (date differs)",
           "Reference number and amount agree but the ERP date differs from the bank date (Rule 3 fallback)."),
    "M3": ("Matched - Date + Amount + Type + Narration",
           "Same date, amount and Dr/Cr, and the payee/description also matches the bank narration (Rule 4)."),
    "M4": ("Matched - Date + Amount + Type (unique)",
           "Same date, amount and Dr/Cr, single possible bank line, no reference/narration corroboration."),
    "M5": ("Matched - Amount + Type within date tolerance",
           "Amount and Dr/Cr agree and the dates are within the tolerance window (posting vs value date)."),
    "M6": ("Matched - Best fit (review)",
           "More than one bank line could fit; the closest one on date/narration was taken. Please review."),
    "X1": ("Exception - Dr/Cr mismatch",
           "Date + amount (and often the reference) agree but the ERP Dr/Cr is opposite to the bank. Bank is the base (Rule 4)."),
    "X2": ("Exception - Amount mismatch",
           "The reference number was found in the bank statement but the amount does not agree."),
    "X3": ("Exception - Duplicate / ambiguous",
           "Several identical ERP lines compete for the same bank line - a possible duplicate posting."),
    "U1": ("Unmatched - Not in bank statement",
           "No bank line with this amount and Dr/Cr inside the statement period."),
    "U2": ("Unmatched - In bank, not in ERP",
           "Bank line with no corresponding ERP payment entry."),
    "U3": ("Not compared - Outside statement period",
           "ERP date falls outside the period covered by the uploaded bank statement."),
    "U4": ("Not compared - No statement uploaded",
           "No bank statement was uploaded for this ERP bank account."),
    "E1": ("Excluded - Reject", "Rejected ERP entries are ignored (Rule 1)."),
    "E2": ("Excluded - Not Fully/Auto Assigned",
           "Only 'Fully Assigned' and 'Auto Assiged' entries are reconciled (Rule 5)."),
    "E3": ("Excluded - Bank side reject",
           "Bank narration marked REJECT - no financial impact, ignored (Rule 1)."),
}

BANK_ALIASES = [
    ("BANK OF BARODA", ["BANK OF BARODA", "BOB", "BARODA"]),
    ("CANARA BANK", ["CANARA"]),
    ("ICICI BANK LIMITED", ["ICICI"]),
    ("INDIAN BANK", ["INDIAN BANK"]),
    ("INDUSIND BANK LIMITED", ["INDUSIND", "INDUS IND"]),
    ("KOTAK MAHINDRA BANK LIMITED", ["KOTAK"]),
    ("PUNJAB NATIONAL BANK", ["PUNJAB NATIONAL", "PNB"]),
    ("RBL BANK LIMITED", ["RBL"]),
    ("STATE BANK OF INDIA", ["SBI", "STATE BANK"]),
    ("UNION BANK OF INDIA", ["UNION BANK"]),
    ("AXIS BANK LIMITED", ["AXIS", "AXIX"]),
]


def canonical_bank(name: str) -> str:
    up = clean_text(name).upper()
    for canon, keys in BANK_ALIASES:
        if any(k in up for k in keys):
            return canon
    return up


# --------------------------------------------------------------- scoring core

def amounts_agree(a, b, cfg) -> bool:
    """Strict paise-level comparison (0.004 only guards binary float noise)."""
    return abs(round(a, 2) - round(b, 2)) <= max(cfg["amount_tolerance"], 0.004)


def _score(erp, bank, cfg):
    """Score one ERP/bank pair. Returns (score, features) or None if impossible."""
    if not amounts_agree(erp["amount"], bank["amount"], cfg):
        return None
    same_type = bool(erp["dr_cr"]) and erp["dr_cr"] == bank["dr_cr"]
    if not same_type:
        return None

    d_erp, d_bank = erp["txn_date"], bank["txn_date"]
    dv_bank = bank["value_date"]
    if pd.isna(d_erp) or pd.isna(d_bank):
        return None
    dd = min(abs((d_erp - d_bank).days),
             abs((d_erp - dv_bank).days) if not pd.isna(dv_bank) else 999)
    if dd > cfg["date_tolerance_days"]:
        return None

    ref_hit = _ref_hit(erp, bank, cfg)
    ds = token_score(erp["erp_desc_tokens"], bank["bank_desc_tokens"])

    score = 100.0
    if ref_hit:
        score += 120
    if dd == 0:
        score += 45
    else:
        score += max(0, 30 - dd * 10)
    score += ds * 40
    feats = {"ref_hit": ref_hit, "date_diff": dd, "desc_score": round(ds, 3)}
    return score, feats


def _ref_hit(erp, bank, cfg, primary_only: bool = False) -> bool:
    """Does the ERP reference appear anywhere in the bank line?

    PRIMARY  = the ERP PMT/Ref number (or its digits) found in the bank
               reference / cheque / narration.
    SECONDARY = a UTR style number carried in the ERP narration that also
               appears in the bank narration.
    """
    n = cfg["ref_min_len"]
    b_tokens = bank["bank_ref_tokens"]

    if erp["erp_ref_primary"] & b_tokens:
        return True
    # a raw substring hit is only trusted for a full length UTR, otherwise
    # NEFTTESTTXNS544 would "match" NEFTTESTTXNS5444
    m = cfg["ref_substr_min_len"]
    rd = erp["ref_digits"]
    if len(rd) >= m and rd in bank["bank_digit_blob"]:
        return True
    if primary_only:
        return False
    if erp["erp_ref_secondary"] & b_tokens:
        return True
    return False


def _rule_of(erp, feats, ambiguous: bool) -> str:
    if ambiguous:
        return "M6"
    if feats["ref_hit"] and feats["date_diff"] == 0:
        return "M1"
    if feats["ref_hit"]:
        return "M2"
    if feats["date_diff"] == 0 and feats["desc_score"] >= 0.34:
        return "M3"
    if feats["date_diff"] == 0:
        return "M4"
    return "M5"


# ------------------------------------------------------------------ main pass

def reconcile_account(erp_rows: pd.DataFrame, bank_rows: pd.DataFrame, cfg=None):
    """Reconcile one account (or one statement). Returns (erp_result, bank_result)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    erp_recs = erp_rows.to_dict("records")
    bank_recs = bank_rows.to_dict("records")

    # bucket bank rows by (dr_cr, rounded amount) so scoring stays O(n)
    buckets = defaultdict(list)
    for b in bank_recs:
        buckets[(b["dr_cr"], round(b["amount"], 0))].append(b)

    pairs = []
    cand_count = defaultdict(int)
    for e in erp_recs:
        best = []
        for delta in (-1, 0, 1):  # covers the rupee level amount tolerance
            for b in buckets.get((e["dr_cr"], round(e["amount"], 0) + delta), []):
                sc = _score(e, b, cfg)
                if sc:
                    best.append((sc[0], sc[1], b))
        cand_count[e["erp_id"]] = len(best)
        for sc, feats, b in best:
            pairs.append((sc, e["erp_id"], b["bank_id"], feats))

    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_e, used_b, assign = set(), set(), {}
    for sc, eid, bid, feats in pairs:
        if eid in used_e or bid in used_b:
            continue
        used_e.add(eid)
        used_b.add(bid)
        assign[eid] = (bid, sc, feats)

    bank_by_id = {b["bank_id"]: b for b in bank_recs}
    out_e = []
    for e in erp_recs:
        rec = dict(e)
        got = assign.get(e["erp_id"])
        if got:
            bid, sc, feats = got
            b = bank_by_id[bid]
            ambiguous = cand_count[e["erp_id"]] > 1 and not feats["ref_hit"] and feats["desc_score"] < 0.34
            rule = _rule_of(e, feats, ambiguous)
            rec.update({
                "status": "MATCHED",
                "rule_code": rule,
                "match_score": round(sc, 1),
                "date_diff_days": feats["date_diff"],
                "ref_matched": feats["ref_hit"],
                "desc_score": feats["desc_score"],
                "candidates": cand_count[e["erp_id"]],
                "bank_id": bid,
                "bank_row": b["bank_row"],
                "bank_date": b["txn_date"],
                "bank_value_date": b["value_date"],
                "bank_description": b["description"],
                "bank_ref_no": b["ref_no"],
                "bank_cheque_no": b["cheque_no"],
                "bank_debit": b["debit"],
                "bank_credit": b["credit"],
                "bank_dr_cr": b["dr_cr"],
                "bank_amount": b["amount"],
                "amount_diff": round(e["amount"] - b["amount"], 2),
            })
        else:
            rec.update(_explain_unmatched(e, bank_recs, cfg))
        out_e.append(rec)

    out_b = []
    for b in bank_recs:
        rec = dict(b)
        if b["bank_id"] in used_b:
            rec["status"] = "MATCHED"
            rec["rule_code"] = ""
        else:
            rec["status"] = "UNMATCHED"
            rec["rule_code"] = "U2"
        out_b.append(rec)

    return pd.DataFrame(out_e), pd.DataFrame(out_b)


def _explain_unmatched(e, bank_recs, cfg):
    """Work out *why* an ERP line could not be matched - this drives the dashboard."""
    same_amt_other_type = []
    ref_hits = []
    for b in bank_recs:
        # only the ERP PMT/Ref number itself is strong enough to assert
        # "same transaction, wrong amount"
        if _ref_hit(e, b, cfg, primary_only=True):
            ref_hits.append(b)
        if amounts_agree(e["amount"], b["amount"], cfg) and b["dr_cr"] != e["dr_cr"]:
            same_amt_other_type.append(b)

    base = {
        "status": "EXCEPTION",
        "match_score": 0.0,
        "date_diff_days": "",
        "ref_matched": bool(ref_hits),
        "desc_score": 0.0,
        "candidates": 0,
        "bank_id": "", "bank_row": "", "bank_date": pd.NaT, "bank_value_date": pd.NaT,
        "bank_description": "", "bank_ref_no": "", "bank_cheque_no": "",
        "bank_debit": 0.0, "bank_credit": 0.0, "bank_dr_cr": "", "bank_amount": 0.0,
        "amount_diff": "",
    }

    if ref_hits:
        b = ref_hits[0]
        base.update({
            "bank_id": b["bank_id"], "bank_row": b["bank_row"], "bank_date": b["txn_date"],
            "bank_value_date": b["value_date"], "bank_description": b["description"],
            "bank_ref_no": b["ref_no"], "bank_cheque_no": b["cheque_no"],
            "bank_debit": b["debit"], "bank_credit": b["credit"],
            "bank_dr_cr": b["dr_cr"], "bank_amount": b["amount"],
            "amount_diff": round(e["amount"] - b["amount"], 2),
        })
        if not amounts_agree(e["amount"], b["amount"], cfg):
            base["rule_code"] = "X2"
            return base
        base["rule_code"] = "X1"
        return base

    # Rule 4: the bank is the base for Dr/Cr. Only call it a Dr/Cr mismatch when
    # the narration also lines up - otherwise it is just a coincidental amount.
    same_amt_other_type = [
        b for b in same_amt_other_type
        if abs((e["txn_date"] - b["txn_date"]).days) <= cfg["date_tolerance_days"]
        and token_score(e["erp_desc_tokens"], b["bank_desc_tokens"]) >= cfg["desc_threshold"]
    ]
    if same_amt_other_type:
        b = same_amt_other_type[0]
        base.update({
            "rule_code": "X1", "bank_id": b["bank_id"], "bank_row": b["bank_row"],
            "bank_date": b["txn_date"], "bank_value_date": b["value_date"],
            "bank_description": b["description"], "bank_ref_no": b["ref_no"],
            "bank_cheque_no": b["cheque_no"], "bank_debit": b["debit"],
            "bank_credit": b["credit"], "bank_dr_cr": b["dr_cr"],
            "bank_amount": b["amount"], "amount_diff": 0.0,
        })
        return base

    base["status"] = "UNMATCHED"
    base["rule_code"] = "U1"
    return base
