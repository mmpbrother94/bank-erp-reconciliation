"""Bank <-> ERP reconciliation runner.

Usage
-----
    python run_reco.py
    python run_reco.py --erp "ERP Payment Import as on 25-08-2026.xlsx" --banks . --out output
    python run_reco.py --date-tolerance 2 --amount-tolerance 0.5

Drop next month's ERP export and the new bank statements in a folder and run it
again - the parsers detect each bank's layout, so no code change is needed.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import webbrowser
from datetime import datetime

from reco_system.dashboard import build_dashboard
from reco_system.orchestrator import run
from reco_system.report import write_all_bank_workbooks, write_workbook


def _guess_erp(folder):
    hits = [f for f in glob.glob(os.path.join(folder, "*.xls*"))
            if "erp" in os.path.basename(f).lower()]
    if not hits:
        raise SystemExit("Could not find an ERP file (name must contain 'ERP'). "
                         "Pass it with --erp.")
    return max(hits, key=os.path.getmtime)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bank statement vs ERP payment reconciliation")
    ap.add_argument("--erp", help="ERP Payment Import file (.xlsx)")
    ap.add_argument("--banks", default=".", help="folder holding the bank statements")
    ap.add_argument("--out", default="output", help="output folder")
    ap.add_argument("--amount-tolerance", type=float, default=0.0,
                    help="rupee tolerance on the amount; 0 = strict, exact to the paisa")
    ap.add_argument("--date-tolerance", type=int, default=3)
    ap.add_argument("--desc-threshold", type=float, default=0.34)
    ap.add_argument("--no-open", action="store_true", help="do not open the dashboard")
    a = ap.parse_args(argv)

    erp = a.erp or _guess_erp(a.banks)
    os.makedirs(a.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"ERP file        : {erp}")
    print(f"Statements from : {os.path.abspath(a.banks)}")
    res = run(erp, a.banks, cfg={
        "amount_tolerance": a.amount_tolerance,
        "date_tolerance_days": a.date_tolerance,
        "desc_threshold": a.desc_threshold,
    })

    xls = os.path.join(a.out, f"Bank_ERP_Reconciliation_{stamp}.xlsx")
    write_workbook(res, xls)
    per_bank_dir = os.path.join(a.out, "per_bank")
    links = write_all_bank_workbooks(res, per_bank_dir)
    links = {k: "per_bank/" + v for k, v in links.items()}
    html = os.path.join(a.out, f"Reconciliation_Dashboard_{stamp}.html")
    build_dashboard(res, html, excel_name=os.path.basename(xls), bank_links=links)

    s = res["summary"]
    e = res["erp_result"]
    print("\n--- result ---")
    print(f"bank lines read   : {int(s['bank_txns'].sum())}  "
          f"(rejects ignored: {int(s['bank_rejects'].sum())})")
    print(f"ERP lines in scope: {len(e)}")
    for st in ("MATCHED", "EXCEPTION", "UNMATCHED", "NOT COMPARED"):
        print(f"  {st:<14}: {int((e['status'] == st).sum())}")
    print(f"bank lines open   : {int(s['bank_unmatched'].sum())}")
    print(f"\nworkbook : {os.path.abspath(xls)}")
    print(f"dashboard: {os.path.abspath(html)}")
    if res["parse_errors"]:
        print("\nfiles that could not be read:")
        for fn, err in res["parse_errors"]:
            print(f"  {fn}: {err}")
    if not a.no_open:
        webbrowser.open("file:///" + os.path.abspath(html).replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
