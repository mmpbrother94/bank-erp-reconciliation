# Bank Statement ↔ ERP Payment Reconciliation

Reconciles the ERP *Payment Import* export against any number of bank statements
(one file per bank / account), produces **one Excel workbook** (ERP statement +
bank statement + reconciliation + exceptions + parameters) and **one HTML
dashboard** where every single line carries the reason it landed where it did.

## Two ways to run it

### 1. Live portal (for the whole team)

```bash
python webapp.py --prod
```

Prints a **shareable link** like `http://192.168.1.23:5000` — give that to
anyone on the same office network / Wi-Fi and they can use it from their own
machine while your PC stays on. First time only, allow the port through Windows
Firewall (admin prompt):

```bash
netsh advfirewall firewall add rule name="Reco Portal 5000" dir=in action=allow protocol=TCP localport=5000
```

Users sign in with the password from `RECO_PASSWORD`, upload the ERP export
plus the bank statements, watch the progress page, and land on the dashboard.
From there they can download:

* **one three-sheet Excel per bank** — sheet 1 the bank statement, sheet 2 the
  ERP statement of *that account number only*, sheet 3 the reconciliation of
  that bank with the reasons and the parameters used;
* the full workbook (all banks, all sheets);
* the filtered register as CSV.

Every run is kept under `jobs/<timestamp>/` and listed on the home page, so
people can reopen an earlier reconciliation without uploading again.

For a real deployment put it behind a WSGI server, e.g.

```bash
waitress-serve --port=8080 webapp:app
```

### 2. Command line (single machine)

```bash
python run_reco.py
```

That auto-detects the ERP file (any `*.xls*` whose name contains "ERP") in the
current folder, reads every other `.xls / .xlsx / .csv` as a bank statement and
writes both outputs into `output/`.

Explicit form:

```bash
python run_reco.py --erp "ERP Payment Import as on 25-08-2026.xlsx" --banks . --out output
```

Tuning (all optional):

| flag | default | meaning |
|---|---|---|
| `--amount-tolerance` | `0.00` | **strict — amounts must be equal to the paisa**; raise only if you want slack |
| `--date-tolerance` | `3` | days of drift allowed between ERP date and bank date/value date |
| `--desc-threshold` | `0.34` | narration overlap needed to corroborate an offline match |
| `--no-open` | – | don't open the dashboard automatically |

Requirements: `pandas`, `openpyxl`, `xlrd`, `xlsxwriter` (all already installed here).

## Rules implemented

| # | Rule | Where |
|---|---|---|
| 1 | Assigned + unassigned read; **Reject ignored** on both sides (ERP `Is Assign = Reject`, bank narration containing `REJECT`) | `erp_loader.py`, `bank_parser.py` |
| 2 | Two ERP worlds: **Online** (API) and **Offline** (PI-to-PI / Tally) | `erp_loader._mode` |
| 3 | Online match on **date + amount + PMT/Ref number**; if the date varies the **reference decides** | `engine._score` |
| 4 | Offline match on **date + amount + Dr/Cr + description + cheque/ref**, with the **bank statement as the base for Dr/Cr** | `engine._score`, `engine._explain_unmatched` |
| 5 | Only **Fully Assigned** and **Auto Assiged** are reconciled | `erp_loader.VALID_ASSIGN` |
| 6 | Transac type **`online` = DEBIT** | `erp_loader._dr_cr` |
| 7 | ERP transaction date and bank transaction date treated as the same date | `engine._score` |

### How a match is decided

Every plausible ERP/bank pair is scored, then pairs are locked in **best score
first, one-to-one**, so one bank line can never satisfy two ERP lines.

```
reference hit ....... +120     same date ........... +45
narration overlap ... +0..40   1-3 days apart ...... +30 .. +10
```

A candidate is only considered when the **amount** agrees exactly, decimals included and the
**Dr/Cr agrees with the bank** — the opposite Dr/Cr is never auto-matched, it is
raised as an exception.

### Result codes

| code | meaning |
|---|---|
| `M1` | reference + date + amount + type |
| `M2` | reference + amount, date differs |
| `M3` | date + amount + type + narration |
| `M4` | date + amount + type, unique candidate |
| `M5` | amount + type inside the date tolerance |
| `M6` | best fit among several candidates — review |
| `X1` | Dr/Cr mismatch (bank is the base) |
| `X2` | same reference, different amount |
| `X3` | duplicate / ambiguous ERP posting |
| `U1` | in ERP, no bank line in the statement period |
| `U2` | in bank, no ERP entry |
| `U3` | ERP date outside the uploaded statement's period — not compared |
| `U4` | no statement uploaded for that ERP bank account |
| `E1` `E2` `E3` | excluded: ERP reject / not fully assigned / bank reject |

## Adding next month's files

Drop the new ERP export and the new statements in the folder and run again. The
bank reader detects the header row and maps the columns by keyword, so a new
bank or a changed export template normally needs no code change. If a bank ever
uses wording the reader doesn't know, add it to `COLMAP` in
`reco_system/bank_parser.py`.

Statements are paired to ERP rows on the **bank account number** taken from the
statement header (digits only, leading zeros ignored). When the header carries
no account number the whole bank's ERP rows are used and the scope column shows
`BANK (a/c not in stmt header)`.

## Layout

```
run_reco.py              CLI entry point
reco_system/
  common.py              text / amount / date / reference normalisation
  bank_parser.py         format-agnostic bank statement reader
  erp_loader.py          ERP export loader + rule 1/2/5/6 classification
  engine.py              scoring, one-to-one assignment, reason codes
  orchestrator.py        statement <-> ERP account pairing, period handling
  report.py              the Excel workbook
  dashboard.py           the standalone HTML dashboard
webapp.py                Flask portal (upload -> dashboard -> per-bank download)
output/                  generated workbook + dashboard + per_bank/
jobs/                    one folder per portal run (uploads + outputs)
```

## The per-bank workbook (3 sheets)

`reco_system/report.write_bank_workbook(res, statement_file, path)` builds it,
and `write_all_bank_workbooks(res, folder)` builds one for every uploaded
statement. Sheets, in order:

| sheet | contents |
|---|---|
| **Bank Statement** | that statement as parsed, with each line's status (matched / open / reject-ignored) |
| **ERP Statement** | only the ERP rows whose `Bank Account Number` belongs to that statement — in-scope *and* excluded rows, each with the scope decision |
| **Reconciliation** | that bank's reconciliation: ERP line beside the bank line it matched, rule code, plain-English reason, date and amount differences, and the parameter block at the bottom |

Nothing from any other bank appears in any of the three sheets. Each sheet ends
with the closing figures:

```
TOTALS                                                    DEBIT      CREDIT
Total amount in the BANK STATEMENT
Total amount in the ERP (this bank account, all dates)
Total amount in the ERP within the statement period
DIFFERENCE  (Bank - ERP within the statement period)
Difference against the full ERP (Bank - ERP all dates)

OPEN / UNMATCHED                                          DEBIT      CREDIT
Amount in BANK but NOT in ERP
Amount in ERP but NOT in BANK
ERP amount not compared (outside the statement period)
```

## Hosting on an existing domain (cPanel / WHM)

No new domain needed - the portal mounts on a route of the one you already have.
Upload the project, then in cPanel -> **Setup Python App**:

| field | value |
|---|---|
| Application root | `reco` |
| Application URL | `yourdomain.com/reco` |
| Startup file | `passenger_wsgi.py` |
| Entry point | `application` |

Run *Pip Install* against `requirements.txt` and restart. `ProxyFix` +
`X-Forwarded-Prefix` keep every generated link under `/reco`; if a link ever
comes out wrong, set the environment variable `RECO_URL_PREFIX=/reco`.

---

## Author

Built by **Manohar Kumar Sah** ([@mmpbrother94](https://github.com/mmpbrother94)).
