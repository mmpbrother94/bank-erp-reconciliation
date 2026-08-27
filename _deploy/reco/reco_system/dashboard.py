"""Standalone HTML dashboard for the reconciliation result."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd

from .engine import RULE_TEXT

ROW_COLS = ["side", "bank", "file", "acct", "status", "rule", "mode", "drcr",
            "edate", "eamt", "eref", "payee", "edesc", "bdate", "bamt", "bdrcr",
            "bdesc", "bref", "ddiff", "adiff", "assign", "tfor"]


def _d(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return "" if pd.isna(v) else v.strftime("%d-%m-%Y")
    return str(v)


def _n(v):
    try:
        f = float(v)
        return 0.0 if pd.isna(f) else round(f, 2)
    except Exception:
        return 0.0


def _rows(res):
    out = []
    e = res["erp_result"]
    for r in e.itertuples():
        out.append([
            "ERP", _d(getattr(r, "bank", "")), _d(getattr(r, "statement_file", "")),
            _d(getattr(r, "account_no", "")), _d(r.status), _d(r.rule_code),
            _d(getattr(r, "mode", "")), _d(getattr(r, "dr_cr", "")),
            _d(r.txn_date), _n(r.amount), _d(getattr(r, "ref_no", "")),
            _d(getattr(r, "payee", ""))[:70], _d(getattr(r, "description", ""))[:110],
            _d(getattr(r, "bank_date", "")), _n(getattr(r, "bank_amount", 0)),
            _d(getattr(r, "bank_dr_cr", "")), _d(getattr(r, "bank_description", ""))[:110],
            _d(getattr(r, "bank_ref_no", "")), _d(getattr(r, "date_diff_days", "")),
            _d(getattr(r, "amount_diff", "")), _d(getattr(r, "is_assign", "")),
            _d(getattr(r, "transac_for", "")),
        ])
    b = res["bank_result"]
    b = b[b["status"] != "MATCHED"]
    for r in b.itertuples():
        out.append([
            "BANK", _d(getattr(r, "bank", "")), _d(r.bank_file), _d(r.account_no),
            _d(r.status), _d(r.rule_code), "", _d(r.dr_cr), "", 0.0, "", "", "",
            _d(r.txn_date), _n(r.amount), _d(r.dr_cr), _d(r.description)[:110],
            _d(r.ref_no), "", "", "", "",
        ])
    excl = res["erp_excluded"]
    for r in excl.itertuples():
        out.append([
            "ERP", _d(getattr(r, "bank", "")), "", _d(r.account_no), "EXCLUDED",
            _d(r.rule_code), _d(getattr(r, "mode", "")), _d(getattr(r, "dr_cr", "")),
            _d(r.txn_date), _n(r.amount), _d(getattr(r, "ref_no", "")),
            _d(getattr(r, "payee", ""))[:70], _d(getattr(r, "description", ""))[:110],
            "", 0.0, "", "", "", "", "", _d(getattr(r, "is_assign", "")),
            _d(getattr(r, "transac_for", "")),
        ])
    return out


def _summary(res):
    s = res["summary"].copy()
    for c in ("period_from", "period_to"):
        s[c] = s[c].map(_d)
    for c in s.columns:
        if s[c].dtype.kind in "fi":
            s[c] = s[c].map(_n)
    return s.fillna("").to_dict("records")


def build_dashboard(res: dict, path: str, excel_name: str = "",
                    bank_links: dict | None = None, title: str = ""):
    payload = {
        "bank_links": bank_links or {},
        "title": title,
        "generated": datetime.now().strftime("%d-%m-%Y %H:%M"),
        "erp_file": os.path.basename(res["erp_path"]),
        "excel": excel_name,
        "cfg": res["cfg"],
        "cols": ROW_COLS,
        "rows": _rows(res),
        "summary": _summary(res),
        "rules": {k: list(v) for k, v in RULE_TEXT.items()},
        "errors": res["parse_errors"],
    }
    html = _TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bank vs ERP Reconciliation Dashboard</title>
<style>
:root{
  --bg:#f4f6fa; --panel:#ffffff; --ink:#0b2545; --muted:#5a6b7b; --line:#e2e8f0;
  --ok:#0f9d58; --warn:#e8a33d; --bad:#d64545; --idle:#8895a7; --accent:#1b4f9c;
}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 "Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{background:linear-gradient(100deg,#0b2545,#1b4f9c);color:#fff;padding:18px 24px}
header h1{margin:0;font-size:20px;letter-spacing:.3px}
header .sub{opacity:.85;font-size:12px;margin-top:4px}
.wrap{padding:18px 24px 60px;max-width:1800px;margin:0 auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
     box-shadow:0 1px 2px rgba(11,37,69,.05)}
.kpi .lab{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
.kpi .val{font-size:22px;font-weight:700;margin-top:4px}
.kpi .sub{font-size:11px;color:var(--muted);margin-top:2px}
.kpi.ok .val{color:var(--ok)} .kpi.bad .val{color:var(--bad)} .kpi.warn .val{color:var(--warn)}
.grid2{display:grid;grid-template-columns:1.35fr .9fr;gap:16px;margin-bottom:18px}
@media(max-width:1150px){.grid2{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card h2{margin:0 0 10px;font-size:14px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#eef3f8;position:sticky;top:0;font-size:11px;text-transform:uppercase;
   letter-spacing:.4px;color:var(--muted);z-index:2}
tbody tr:hover{background:#f7fbff}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.bankrow{cursor:pointer}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600;white-space:nowrap}
.p-MATCHED{background:#e4f6ec;color:#0f7a45}
.p-UNMATCHED{background:#fdeaea;color:#b32e2e}
.p-EXCEPTION{background:#fdf3e2;color:#9a6a12}
.p-NOTCOMPARED{background:#eef1f5;color:#5a6b7b}
.p-EXCLUDED{background:#f2effa;color:#5b4b9b}
.bar{height:8px;border-radius:5px;background:#eef1f5;overflow:hidden}
.bar>span{display:block;height:100%;background:var(--ok)}
.reason{display:grid;grid-template-columns:34px 1fr 74px;gap:8px;align-items:center;margin-bottom:7px;font-size:12px}
.reason .code{font-weight:700;color:var(--accent)}
.reason .tr{background:#eef1f5;border-radius:4px;height:16px;position:relative;overflow:hidden}
.reason .tr>i{position:absolute;left:0;top:0;bottom:0;background:var(--accent);opacity:.75}
.reason .tr>b{position:absolute;left:6px;top:0;line-height:16px;font-size:11px;color:#0b2545;font-weight:600}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
select,input[type=text],input[type=date]{padding:6px 8px;border:1px solid var(--line);border-radius:6px;
  background:#fff;font-size:12.5px;color:var(--ink)}
button{padding:7px 13px;border:1px solid var(--accent);background:var(--accent);color:#fff;
  border-radius:6px;font-size:12.5px;cursor:pointer}
button.ghost{background:#fff;color:var(--accent)}
button:hover{filter:brightness(1.08)}
.tablewrap{max-height:620px;overflow:auto;border:1px solid var(--line);border-radius:8px}
.det{background:#fbfdff;font-size:12px}
.det table{width:100%}
.det td{border:none;padding:3px 6px}
.small{font-size:11.5px;color:var(--muted)}
.rules dl{margin:0}
.rules dt{font-weight:700;margin-top:10px;color:var(--accent)}
.rules dd{margin:2px 0 0 0}
.legend{font-size:11.5px;color:var(--muted);margin-top:8px}
.tag{display:inline-block;background:#eef3f8;border:1px solid var(--line);border-radius:5px;
  padding:1px 6px;margin:0 4px 4px 0;font-size:11px}
.footnote{margin-top:24px;font-size:12px;color:var(--muted)}
header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.hleft{min-width:0}
.rulesbtn{flex:none;margin-top:2px;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.45);
  color:#fff;padding:9px 15px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.rulesbtn:hover{background:rgba(255,255,255,.26)}
.modal{display:none;position:fixed;inset:0;background:rgba(11,37,69,.55);z-index:50;
  padding:34px 18px;overflow:auto}
.modal.on{display:block}
.sheet{background:var(--panel);border-radius:12px;padding:22px 26px;max-width:1180px;margin:0 auto;
  position:relative;box-shadow:0 18px 50px rgba(11,37,69,.3)}
.sheet .x{position:absolute;top:12px;right:14px;background:transparent;border:0;color:var(--muted);
  font-size:26px;line-height:1;padding:2px 8px;cursor:pointer}
.sheet .x:hover{color:var(--bad)}
@media(max-width:700px){header{flex-direction:column}.rulesbtn{align-self:flex-start}}
</style></head><body>
<header>
  <div class="hleft">
    <h1>Bank Statement &nbsp;&#8596;&nbsp; ERP Payment Reconciliation</h1>
    <div class="sub" id="hsub"></div>
  </div>
  <button class="rulesbtn" onclick="openRules()" title="Parameters used and what every result code means">
    &#9432;&nbsp; Rules &amp; Parameters</button>
</header>
<div class="wrap">
  <div class="kpis" id="kpis"></div>

  <div class="grid2">
    <div class="card">
      <h2>Bank-wise reconciliation</h2>
      <div class="tablewrap" style="max-height:340px">
        <table id="banktbl"></table>
      </div>
      <div class="legend">Click a row to filter the transaction register below; use
        <b>3-sheet Excel</b> to download that bank on its own (bank statement + ERP statement of that
        account + reconciliation).
        Scope <b>ACCOUNT</b> = statement account number found in the ERP,
        <b>BANK</b> = matched at bank level (statement header had no / an unknown account number).</div>
    </div>
    <div class="card">
      <h2>Why each transaction landed where it did</h2>
      <div id="reasons"></div>
      <div class="legend">Codes are explained in the "Parameters" section at the bottom of this page
        and on the <i>Rules &amp; Parameters</i> sheet of the workbook.</div>
    </div>
  </div>

  <div class="card">
    <h2>Transaction register &mdash; every line with its reason</h2>
    <div class="controls">
      <select id="fBank"></select>
      <select id="fFile"></select>
      <select id="fStatus"></select>
      <select id="fRule"></select>
      <select id="fMode"></select>
      <select id="fDrCr"></select>
      <select id="fSide"></select>
      <input type="text" id="fText" placeholder="search ref / payee / narration" style="min-width:220px">
      <input type="date" id="fFrom"><input type="date" id="fTo">
      <button class="ghost" onclick="resetF()">Reset</button>
      <button onclick="exportCsv()">Download filtered CSV</button>
      <button id="xlsbtn" onclick="openXls()">Download full Excel workbook</button>
      <span class="small" id="cnt"></span>
    </div>
    <div class="tablewrap"><table id="tbl"></table></div>
    <div class="controls" style="margin-top:10px">
      <button class="ghost" onclick="page(-1)">&laquo; Prev</button>
      <span class="small" id="pg"></span>
      <button class="ghost" onclick="page(1)">Next &raquo;</button>
      <select id="pageSize"><option>100</option><option>250</option><option>500</option><option>1000</option></select>
      <span class="small">rows per page</span>
    </div>
  </div>

  <div class="modal" id="rulesModal" onclick="if(event.target===this)closeRules()">
   <div class="sheet rules">
    <button class="x" onclick="closeRules()" title="Close">&times;</button>
    <h2>Parameters considered &mdash; Online vs Offline</h2>
    <div class="grid2">
      <div>
        <dl>
          <dt>ONLINE transactions (API payments) &mdash; Rules 2, 3 &amp; 6</dt>
          <dd>1. <b>Transaction date</b> (ERP date vs bank transaction date <i>and</i> value date)</dd>
          <dd>2. <b>Amount</b> (exact match including decimals)</dd>
          <dd>3. <b>PMT / Ref number</b> looked up inside the bank reference, cheque number and narration</dd>
          <dd>4. <b>If the date varies, the reference number decides</b> the match (Rule 3)</dd>
          <dd>5. <b>Dr/Cr</b>: an ERP "online" transac type is treated as a bank <b>DEBIT</b> (Rule 6)</dd>
        </dl>
        <dl>
          <dt>OFFLINE transactions (PI to PI / Tally) &mdash; Rule 4</dt>
          <dd>1. <b>Transaction date</b> (ERP date vs bank transaction / value date)</dd>
          <dd>2. <b>Amount</b> (exact match including decimals)</dd>
          <dd>3. <b>Amount type</b> &mdash; the <b>bank statement is the base</b>: ERP Debit must face a bank
              Debit and ERP Credit a bank Credit; the opposite is raised as a Dr/Cr exception</dd>
          <dd>4. <b>Description / payee</b> token overlap against the bank narration</dd>
          <dd>5. <b>Cheque / reference number</b> in the ERP reference or description vs the bank cheque &amp; reference</dd>
        </dl>
      </div>
      <div>
        <dl>
          <dt>Population</dt>
          <dd>Rule 1 &mdash; assigned <i>and</i> unassigned rows are read; <b>Reject</b> rows are ignored on both sides.</dd>
          <dd>Rule 5 &mdash; only <b>Fully Assigned</b> and <b>Auto Assiged</b> take part; everything else is EXCLUDED.</dd>
          <dd>Rule 7 &mdash; ERP transaction date and bank transaction date are treated as the same date.</dd>
          <dd>Statements are paired to ERP rows on the <b>bank account number</b>; ERP rows outside the
              uploaded statement period are reported as <i>not compared</i>, never as a break.</dd>
          <dt>Tolerances in force</dt>
          <dd id="cfgline"></dd>
          <dt>Scoring</dt>
          <dd>Reference hit 120 &middot; same date 45 &middot; near date up to 30 &middot; narration up to 40.
              Pairs are locked in best-score-first and one-to-one, so a bank line can never satisfy two ERP lines.</dd>
        </dl>
      </div>
    </div>
    <h2 style="margin-top:16px">Result codes</h2>
    <table id="ruletbl"></table>
    </div></div>

  <div class="footnote" id="foot"></div>
</div>
<script>
const D = __PAYLOAD__;
const C = {}; D.cols.forEach((c,i)=>C[c]=i);
const ROWS = D.rows;
const fmt = n => (n||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtI = n => (n||0).toLocaleString('en-IN');
const cls = s => 'p-'+String(s).replace(/\s+/g,'');
const parseD = s => { if(!s) return null; const p=s.split('-'); return new Date(+p[2],+p[1]-1,+p[0]); };

if(D.title) document.querySelector('header h1').textContent = D.title;
document.getElementById('hsub').textContent =
  'ERP source: '+D.erp_file+'   |   generated '+D.generated+
  '   |   '+D.summary.length+' statement/bank groups   |   '+fmtI(ROWS.length)+' transaction lines';
document.getElementById('cfgline').textContent =
  (D.cfg.amount_tolerance<=0 ? 'amount: EXACT (equal to the paisa)' :
   'amount ± Rs.'+D.cfg.amount_tolerance.toFixed(2))+'  ·  date ± '+D.cfg.date_tolerance_days+
  ' days  ·  narration overlap ≥ '+D.cfg.desc_threshold+'  ·  reference token ≥ '+
  D.cfg.ref_min_len+' chars (full-UTR substring ≥ '+D.cfg.ref_substr_min_len+' digits)';

/* ---------------- KPIs ---------------- */
function kpis(){
  const erp = ROWS.filter(r=>r[C.side]==='ERP');
  const st = s => erp.filter(r=>r[C.status]===s);
  const bank = ROWS.filter(r=>r[C.side]==='BANK');
  const sum = a => a.reduce((t,r)=>t+(r[C.eamt]||r[C.bamt]||0),0);
  const m=st('MATCHED'), x=st('EXCEPTION'), u=st('UNMATCHED'), nc=st('NOT COMPARED'), ex=st('EXCLUDED');
  const comparable = m.length+x.length+u.length;
  const rate = comparable? (100*m.length/comparable):0;
  const bankTot = D.summary.reduce((t,s)=>t+s.bank_txns,0);
  const bankUn = D.summary.reduce((t,s)=>t+s.bank_unmatched,0);
  const dr = D.summary.reduce((t,s)=>t+s.bank_debit,0), cr = D.summary.reduce((t,s)=>t+s.bank_credit,0);
  const cmp = m.concat(x,u);                       // ERP lines actually compared
  const side=(a,d)=>a.filter(r=>r[C.drcr]===d).reduce((t,r)=>t+r[C.eamt],0);
  const edr=side(cmp,'DEBIT'), ecr=side(cmp,'CREDIT');
  const mdr=side(m,'DEBIT'), mcr=side(m,'CREDIT');
  const K=[
   ['Banks / statements', D.summary.filter(s=>s.bank_txns>0).length, 'files parsed'],
   ['Bank lines', fmtI(bankTot), 'Dr '+fmt(dr)+' | Cr '+fmt(cr)],
   ['ERP lines compared', fmtI(comparable), 'Dr '+fmt(edr)+' | Cr '+fmt(ecr)],
   ['Matched', fmtI(m.length), 'Dr '+fmt(mdr)+' | Cr '+fmt(mcr), 'ok'],
   ['Match rate', rate.toFixed(1)+'%', 'of compared ERP lines', rate>=90?'ok':(rate>=60?'warn':'bad')],
   ['Exceptions', fmtI(x.length), 'Dr/Cr &amp; amount breaks', x.length?'warn':'ok'],
   ['Unmatched ERP', fmtI(u.length), fmt(sum(u)), u.length?'bad':'ok'],
   ['Unmatched bank', fmtI(bankUn), 'in bank, not in ERP', bankUn?'bad':'ok'],
   ['Not compared', fmtI(nc.length), 'period / statement gap'],
   ['Excluded', fmtI(ex.length), 'reject &amp; not fully assigned'],
  ];
  document.getElementById('kpis').innerHTML = K.map(k=>
    `<div class="kpi ${k[3]||''}"><div class="lab">${k[0]}</div><div class="val">${k[1]}</div>
     <div class="sub">${k[2]}</div></div>`).join('');
}

/* ---------------- bank table ---------------- */
function bankTable(){
  const h = ['Bank','Statement file','A/c','Scope','Period','Bank lines','Bank debit','Bank credit',
             'ERP lines','ERP debit','ERP credit','Matched','Matched amount','Exc.','Unm. ERP',
             'Unm. bank','Open bank Dr','Open bank Cr','Open ERP amount','Not comp.',
             'Match rate','Sheet'];
  let html = '<thead><tr>'+h.map((x,i)=>`<th class="${i>4?'num':''}">${x}</th>`).join('')+'</tr></thead><tbody>';
  D.summary.forEach(s=>{
    const r = s.match_rate;
    html += `<tr class="bankrow" onclick="filterBank('${s.statement_file.replace(/'/g,"")}')">
      <td>${s.bank||'-'}</td><td>${s.statement_file}</td><td>${s.account_no||'-'}</td>
      <td><span class="small">${s.scope_level}</span></td>
      <td class="small">${s.period_from? s.period_from+' &rarr; '+s.period_to : '-'}</td>
      <td class="num">${fmtI(s.bank_txns)}</td>
      <td class="num">${fmt(s.bank_debit)}</td><td class="num">${fmt(s.bank_credit)}</td>
      <td class="num">${fmtI(s.erp_txns)}</td>
      <td class="num">${fmt(s.erp_debit)}</td><td class="num">${fmt(s.erp_credit)}</td>
      <td class="num">${fmtI(s.erp_matched)}</td><td class="num">${fmt(s.matched_amount)}</td>
      <td class="num">${fmtI(s.erp_exception)}</td>
      <td class="num">${fmtI(s.erp_unmatched)}</td><td class="num">${fmtI(s.bank_unmatched)}</td>
      <td class="num">${fmt(s.bank_unmatched_debit)}</td>
      <td class="num">${fmt(s.bank_unmatched_credit)}</td>
      <td class="num">${fmt(s.unmatched_erp_amount)}</td>
      <td class="num">${fmtI(s.erp_not_compared)}</td>
      <td class="num" style="min-width:110px">${r.toFixed(1)}%<div class="bar"><span style="width:${r}%;
        background:${r>=90?'var(--ok)':(r>=60?'var(--warn)':'var(--bad)')}"></span></div></td>
      <td>${D.bank_links[s.statement_file]?
        `<button class="ghost" onclick="event.stopPropagation();dl('${s.statement_file.replace(/'/g,"")}')"
          title="Excel with 3 sheets: bank statement, ERP statement of this account, reconciliation">
          &#11015; 3-sheet Excel</button>`:'<span class="small">-</span>'}</td></tr>`;
  });
  document.getElementById('banktbl').innerHTML = html+'</tbody>';
}

/* ---------------- reason breakdown ---------------- */
function reasons(){
  const cnt={};
  ROWS.forEach(r=>{const k=r[C.rule]||'--'; cnt[k]=(cnt[k]||0)+1;});
  const keys=Object.keys(cnt).sort((a,b)=>cnt[b]-cnt[a]);
  const max=Math.max(...keys.map(k=>cnt[k]));
  document.getElementById('reasons').innerHTML = keys.map(k=>{
    const lbl = (D.rules[k]||[k,''])[0];
    return `<div class="reason" title="${((D.rules[k]||['',''])[1]).replace(/"/g,'')}">
      <div class="code">${k}</div>
      <div class="tr"><i style="width:${100*cnt[k]/max}%"></i><b>${lbl}</b></div>
      <div class="num" style="text-align:right">${fmtI(cnt[k])}</div></div>`;
  }).join('');
}

/* ---------------- register ---------------- */
let cur=1, view=[];
function uniq(i){return [...new Set(ROWS.map(r=>r[i]).filter(v=>v!==''))].sort();}
function fillSel(id,label,idx){
  const s=document.getElementById(id);
  s.innerHTML = `<option value="">${label}: all</option>`+uniq(idx).map(v=>`<option>${v}</option>`).join('');
  s.onchange=()=>{cur=1;render();};
}
function filters(){
  fillSel('fBank','Bank',C.bank); fillSel('fFile','File',C.file);
  fillSel('fStatus','Status',C.status); fillSel('fRule','Rule',C.rule);
  fillSel('fMode','Mode',C.mode); fillSel('fDrCr','Dr/Cr',C.drcr); fillSel('fSide','Source',C.side);
  ['fText','fFrom','fTo'].forEach(id=>document.getElementById(id).oninput=()=>{cur=1;render();});
  document.getElementById('pageSize').onchange=()=>{cur=1;render();};
  if(!D.excel) document.getElementById('xlsbtn').style.display='none';
}
function resetF(){['fBank','fFile','fStatus','fRule','fMode','fDrCr','fSide','fText','fFrom','fTo']
  .forEach(id=>document.getElementById(id).value='');cur=1;render();}
function filterBank(f){resetF();document.getElementById('fFile').value=f;cur=1;render();
  document.getElementById('tbl').scrollIntoView({behavior:'smooth',block:'center'});}

function apply(){
  const g=id=>document.getElementById(id).value;
  const b=g('fBank'),f=g('fFile'),s=g('fStatus'),ru=g('fRule'),mo=g('fMode'),dc=g('fDrCr'),sd=g('fSide');
  const t=g('fText').toLowerCase(), from=g('fFrom')?new Date(g('fFrom')):null, to=g('fTo')?new Date(g('fTo')):null;
  return ROWS.filter(r=>{
    if(b&&r[C.bank]!==b)return false; if(f&&r[C.file]!==f)return false;
    if(s&&r[C.status]!==s)return false; if(ru&&r[C.rule]!==ru)return false;
    if(mo&&r[C.mode]!==mo)return false; if(dc&&r[C.drcr]!==dc)return false;
    if(sd&&r[C.side]!==sd)return false;
    if(t){const blob=(r[C.eref]+' '+r[C.payee]+' '+r[C.edesc]+' '+r[C.bdesc]+' '+r[C.bref]).toLowerCase();
      if(blob.indexOf(t)<0)return false;}
    if(from||to){const d=parseD(r[C.edate])||parseD(r[C.bdate]); if(!d)return false;
      if(from&&d<from)return false; if(to&&d>to)return false;}
    return true;
  });
}
function render(){
  view=apply();
  const ps=+document.getElementById('pageSize').value||100;
  const pages=Math.max(1,Math.ceil(view.length/ps)); if(cur>pages)cur=pages;
  const slice=view.slice((cur-1)*ps,cur*ps);
  const h=['Src','Bank','Date','Dr/Cr','Mode','Amount','ERP Ref','Payee / Narration','Status','Rule','Bank date','Bank amount','Bank narration'];
  let html='<thead><tr>'+h.map((x,i)=>`<th class="${[5,11].includes(i)?'num':''}">${x}</th>`).join('')+'</tr></thead><tbody>';
  slice.forEach((r,i)=>{
    const amt = r[C.side]==='BANK'? r[C.bamt] : r[C.eamt];
    html+=`<tr class="bankrow" onclick="tog(${i})">
      <td>${r[C.side]}</td><td class="small">${r[C.bank]||'-'}</td>
      <td>${r[C.edate]||r[C.bdate]||'-'}</td><td>${r[C.drcr]||'-'}</td><td>${r[C.mode]||'-'}</td>
      <td class="num">${fmt(amt)}</td><td class="small">${r[C.eref]||'-'}</td>
      <td class="small">${(r[C.payee]||r[C.bdesc]||'-')}</td>
      <td><span class="pill ${cls(r[C.status])}">${r[C.status]}</span></td>
      <td title="${((D.rules[r[C.rule]]||['',''])[1]).replace(/"/g,'')}">${r[C.rule]||''}</td>
      <td>${r[C.bdate]||'-'}</td><td class="num">${r[C.bamt]?fmt(r[C.bamt]):'-'}</td>
      <td class="small">${r[C.bdesc]||'-'}</td></tr>
      <tr class="det" id="d${i}" style="display:none"><td colspan="13">${detail(r)}</td></tr>`;
  });
  document.getElementById('tbl').innerHTML=html+'</tbody>';
  let vdr=0,vcr=0;
  view.forEach(r=>{const a=r[C.side]==='BANK'?r[C.bamt]:r[C.eamt];
    if(r[C.drcr]==='DEBIT')vdr+=a; else if(r[C.drcr]==='CREDIT')vcr+=a;});
  document.getElementById('cnt').innerHTML=fmtI(view.length)+' of '+fmtI(ROWS.length)+
    ' lines &nbsp;|&nbsp; Debit <b>'+fmt(vdr)+'</b> &nbsp; Credit <b>'+fmt(vcr)+'</b>';
  document.getElementById('pg').textContent='page '+cur+' / '+pages;
}
function detail(r){
  const R=D.rules[r[C.rule]]||['',''];
  return `<table><tr><td style="width:50%">
    <b>ERP side</b><br>Date: ${r[C.edate]||'-'} &nbsp; Amount: <b>${fmt(r[C.eamt])}</b> ${r[C.drcr]}<br>
    Mode: ${r[C.mode]||'-'} &nbsp; Is Assign: ${r[C.assign]||'-'} &nbsp; Transac For: ${r[C.tfor]||'-'}<br>
    PMT/Ref: ${r[C.eref]||'-'}<br>Payee: ${r[C.payee]||'-'}<br>Description: ${r[C.edesc]||'-'}<br>
    A/c: ${r[C.acct]||'-'}</td><td>
    <b>Bank side</b><br>Date: ${r[C.bdate]||'-'} &nbsp; Amount: <b>${r[C.bamt]?fmt(r[C.bamt]):'-'}</b> ${r[C.bdrcr]}<br>
    Ref: ${r[C.bref]||'-'}<br>Narration: ${r[C.bdesc]||'-'}<br>File: ${r[C.file]||'-'}</td></tr>
    <tr><td colspan="2" style="padding-top:8px"><b>${r[C.rule]} &mdash; ${R[0]}</b><br>${R[1]}
    ${r[C.ddiff]!==''?'<br>Date difference: '+r[C.ddiff]+' day(s)':''}
    ${r[C.adiff]!==''&&r[C.adiff]!=='0.0'?'<br>Amount difference: '+r[C.adiff]:''}</td></tr></table>`;
}
function tog(i){const e=document.getElementById('d'+i);e.style.display=e.style.display==='none'?'':'none';}
function page(d){cur=Math.max(1,cur+d);render();}
function exportCsv(){
  const head=['Source','Bank','Statement file','A/c','Status','Rule','Rule meaning','Mode','Dr/Cr',
    'ERP date','ERP amount','ERP ref','Payee','ERP description','Bank date','Bank amount','Bank Dr/Cr',
    'Bank narration','Bank ref','Date diff','Amount diff','Is Assign','Transac For'];
  const q=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"';
  const lines=[head.map(q).join(',')];
  view.forEach(r=>{
    const R=D.rules[r[C.rule]]||['',''];
    lines.push([r[C.side],r[C.bank],r[C.file],r[C.acct],r[C.status],r[C.rule],R[0],r[C.mode],r[C.drcr],
      r[C.edate],r[C.eamt],r[C.eref],r[C.payee],r[C.edesc],r[C.bdate],r[C.bamt],r[C.bdrcr],r[C.bdesc],
      r[C.bref],r[C.ddiff],r[C.adiff],r[C.assign],r[C.tfor]].map(q).join(','));
  });
  const blob=new Blob(['﻿'+lines.join('\n')],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='reconciliation_filtered.csv';a.click();
}
function openXls(){ window.location.href = encodeURI(D.excel); }
function dl(f){ const u=D.bank_links[f]; if(u) window.location.href = encodeURI(u); }
function openRules(){document.getElementById('rulesModal').classList.add('on');
  document.body.style.overflow='hidden';}
function closeRules(){document.getElementById('rulesModal').classList.remove('on');
  document.body.style.overflow='';}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeRules();});
function ruleTable(){
  let h='<thead><tr><th>Code</th><th>Meaning</th><th>Definition</th></tr></thead><tbody>';
  Object.keys(D.rules).forEach(k=>{h+=`<tr><td><b>${k}</b></td><td>${D.rules[k][0]}</td><td>${D.rules[k][1]}</td></tr>`;});
  document.getElementById('ruletbl').innerHTML=h+'</tbody>';
}
document.getElementById('foot').innerHTML =
  'Workbook: <b>'+(D.excel||'-')+'</b> &middot; the same figures, plus the ERP statement, the bank statement, '+
  'the reconciliation sheet and the parameter sheet.'+
  (D.errors.length? '<br><b style="color:var(--bad)">Files that could not be read:</b> '+
    D.errors.map(e=>e[0]+' ('+e[1]+')').join('; ') : '');
kpis(); bankTable(); reasons(); filters(); ruleTable(); render();
</script></body></html>
"""
