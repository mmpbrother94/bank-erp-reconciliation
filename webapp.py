"""Live reconciliation portal.

    python webapp.py                 -> http://127.0.0.1:5000
    python webapp.py --host 0.0.0.0  -> reachable from the LAN / a server

Anyone with the link can upload one ERP Payment Import export plus any number of
bank statements, watch it reconcile, browse the dashboard and download either
the full workbook or a **three sheet workbook per bank** (bank statement +
the ERP statement of that account only + the reconciliation of that bank).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import hashlib
import hmac
import queue as _queue
import secrets
import threading
import traceback
from datetime import datetime
from urllib.parse import quote

from flask import (Flask, Response, abort, redirect, render_template_string,
                   request, send_file, session, url_for)
from werkzeug.utils import secure_filename

from reco_system.dashboard import build_dashboard
from reco_system.orchestrator import run
from reco_system.report import write_all_bank_workbooks, write_workbook

BASE = os.path.dirname(os.path.abspath(__file__))
JOBS = os.path.join(BASE, "jobs")
ALLOWED = {".xls", ".xlsx", ".csv", ".txt", ".xlsm"}
MAX_MB = 80

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

# ------------------------------------------------------------------- security
#
# The portal handles bank statements, so it never runs open to the world.
# Set the shared password (and, on a public host, a fixed secret key) as
# environment variables:
#
#     RECO_PASSWORD   = the password everyone types to get in   (required)
#     RECO_SECRET_KEY = any long random string, keeps logins alive over restarts
#
# With no RECO_PASSWORD set, a random one is generated and printed at startup -
# handy locally, useless on a server, so always set it there.

PASSWORD = os.environ.get("RECO_PASSWORD", "")
GENERATED_PASSWORD = ""
if not PASSWORD:
    GENERATED_PASSWORD = PASSWORD = secrets.token_urlsafe(9)

app.secret_key = os.environ.get("RECO_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=bool(os.environ.get("RECO_HTTPS_ONLY")))

_PW_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()
OPEN_ENDPOINTS = {"login", "static"}


@app.before_request
def _require_login():
    if request.endpoint in OPEN_ENDPOINTS:
        return None
    if session.get("ok") == _PW_HASH:
        return None
    if request.endpoint == "state_json":
        return {"status": "locked"}, 401
    # request.full_path drops the mount point, so on a sub-path install
    # ("/bankreco") the post-login redirect would land on the domain root.
    target = (request.script_root or "") + request.full_path
    if target.endswith("?"):
        target = target[:-1]
    return redirect(url_for("login", next=target))


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        given = request.form.get("password", "")
        if hmac.compare_digest(hashlib.sha256(given.encode()).hexdigest(), _PW_HASH):
            session.permanent = True
            session["ok"] = _PW_HASH
            nxt = request.form.get("next") or ""
            # only same-site absolute paths; "//host" would leave the site
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("home")
            return redirect(nxt)
        err = "Wrong password."
    return render_template_string(LOGIN, err=err,
                                  next=request.args.get("next", "")), (401 if err else 200)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# Mounted behind Apache/nginx on a sub-path of an existing domain
# (e.g. https://yourdomain.com/reco) the proxy sends X-Forwarded-* and
# X-Forwarded-Prefix; honouring them keeps every generated link correct.
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
except Exception:  # pragma: no cover
    pass

_FORCED_PREFIX = os.environ.get("RECO_URL_PREFIX", "").rstrip("/")
if _FORCED_PREFIX:
    class _Prefix:
        def __init__(self, wsgi, prefix):
            self.wsgi, self.prefix = wsgi, prefix

        def __call__(self, environ, start_response):
            environ["SCRIPT_NAME"] = self.prefix
            path = environ.get("PATH_INFO", "")
            if path.startswith(self.prefix):
                environ["PATH_INFO"] = path[len(self.prefix):] or "/"
            return self.wsgi(environ, start_response)

    app.wsgi_app = _Prefix(app.wsgi_app, _FORCED_PREFIX)

_lock = threading.Lock()

# --------------------------------------------------------------- the job queue
#
# Reconciling a 14,000 row ERP export costs roughly 160 MB and a minute of CPU.
# Letting everyone run at once would exhaust the account's memory and kill every
# run in flight, so submissions go into a strict first-in-first-out queue and a
# small pool of workers drains it.  Anyone may submit at any time; the person
# who pressed the button first finishes first, everyone else simply waits.

WORKERS = max(1, int(os.environ.get("RECO_WORKERS", "2")))
_jobq = _queue.Queue()
_pool_started = False
_pool_lock = threading.Lock()
_running = set()          # job ids currently being processed
_running_lock = threading.Lock()


def _worker():
    while True:
        args = _jobq.get()
        job_id = args[0]
        with _running_lock:
            _running.add(job_id)
        try:
            do_run(*args)
        except Exception:                      # never let a worker die
            traceback.print_exc()
        finally:
            with _running_lock:
                _running.discard(job_id)
            _jobq.task_done()


def _ensure_pool():
    global _pool_started
    with _pool_lock:
        if _pool_started:
            return
        for i in range(WORKERS):
            threading.Thread(target=_worker, daemon=True,
                             name=f"reco-worker-{i + 1}").start()
        _pool_started = True


def submit(job_id, d, erp_path, cfg, label, prefix):
    _ensure_pool()
    _jobq.put((job_id, d, erp_path, cfg, label, prefix))


def queue_info(job_id):
    """(place in the waiting line, how many are waiting, how many are running)."""
    waiting = []
    for name in sorted(os.listdir(JOBS)) if os.path.isdir(JOBS) else []:
        st = read_state(name)
        if st and st.get("status") == "queued":
            waiting.append(name)
    with _running_lock:
        running = len(_running)
    place = waiting.index(job_id) + 1 if job_id in waiting else 0
    return place, len(waiting), running


# ------------------------------------------------------------------ job state

def job_dir(job_id):
    d = os.path.join(JOBS, secure_filename(job_id))
    if not os.path.isdir(d):
        abort(404)
    return d


def read_state(job_id):
    try:
        with open(os.path.join(JOBS, secure_filename(job_id), "state.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def write_state(d, state):
    with _lock:
        with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as fh:
            json.dump(state, fh, default=str)


def list_jobs(limit=25):
    if not os.path.isdir(JOBS):
        return []
    out = []
    for name in sorted(os.listdir(JOBS), reverse=True):
        st = read_state(name)
        if st:
            out.append(st)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------- the work

def do_run(job_id, d, erp_path, cfg, label, prefix=""):
    state = read_state(job_id) or {}
    try:
        state.update(status="running", step="reading files")
        write_state(d, state)

        res = run(erp_path, os.path.join(d, "uploads"), cfg=cfg)

        state.update(step="writing workbook")
        write_state(d, state)
        out = os.path.join(d, "output")
        os.makedirs(out, exist_ok=True)
        xls_name = f"Bank_ERP_Reconciliation_{job_id}.xlsx"
        write_workbook(res, os.path.join(out, xls_name))

        state.update(step="writing per-bank sheets")
        write_state(d, state)
        links = write_all_bank_workbooks(res, os.path.join(out, "per_bank"))

        state.update(step="building dashboard")
        write_state(d, state)
        # built by hand: this runs in a worker thread, outside the request
        # context. `prefix` is the mount point (empty at the domain root,
        # "/reco" when the app is mounted on a sub-path of an existing domain).
        url_links = {k: f"{prefix}/job/{job_id}/bank/{quote(v)}" for k, v in links.items()}
        build_dashboard(res, os.path.join(out, "dashboard.html"),
                        excel_name=f"{prefix}/job/{job_id}/full.xlsx",
                        bank_links=url_links,
                        title=label or "Bank Statement ↔ ERP Payment Reconciliation")

        s = res["summary"]
        e = res["erp_result"]
        state.update(
            status="done", step="", finished=datetime.now().strftime("%d-%m-%Y %H:%M"),
            excel=xls_name, per_bank=links,
            stats={
                "bank_lines": int(s["bank_txns"].sum()),
                "erp_lines": int(len(e)),
                "matched": int((e["status"] == "MATCHED").sum()),
                "exception": int((e["status"] == "EXCEPTION").sum()),
                "unmatched": int((e["status"] == "UNMATCHED").sum()),
                "not_compared": int((e["status"] == "NOT COMPARED").sum()),
                "bank_open": int(s["bank_unmatched"].sum()),
                "banks": int((s["bank_txns"] > 0).sum()),
            },
            errors=[f"{f}: {m}" for f, m in res["parse_errors"]])
    except Exception as exc:
        state.update(status="error", step="",
                     message=f"{type(exc).__name__}: {exc}",
                     trace=traceback.format_exc()[-3000:])
    write_state(d, state)


# --------------------------------------------------------------------- routes

@app.route("/", methods=["GET"])
def home():
    return render_template_string(HOME, jobs=list_jobs(), maxmb=MAX_MB)


@app.route("/run", methods=["POST"])
def start():
    erp = request.files.get("erp")
    banks = [f for f in request.files.getlist("banks") if f and f.filename]
    if not erp or not erp.filename:
        return render_template_string(HOME, jobs=list_jobs(), maxmb=MAX_MB,
                                      err="Please choose the ERP Payment Import file."), 400
    if not banks:
        return render_template_string(HOME, jobs=list_jobs(), maxmb=MAX_MB,
                                      err="Please choose at least one bank statement."), 400

    # two people can submit inside the same second - keep the ids unique so
    # one run can never overwrite another
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with _lock:
        job_id = stamp
        n = 1
        while os.path.exists(os.path.join(JOBS, job_id)):
            n += 1
            job_id = f"{stamp}-{n}"
        os.makedirs(os.path.join(JOBS, job_id), exist_ok=True)
    d = os.path.join(JOBS, job_id)
    up = os.path.join(d, "uploads")
    os.makedirs(up, exist_ok=True)

    erp_name = secure_filename(erp.filename)
    if os.path.splitext(erp_name)[1].lower() not in ALLOWED:
        shutil.rmtree(d, ignore_errors=True)
        return render_template_string(HOME, jobs=list_jobs(), maxmb=MAX_MB,
                                      err=f"Unsupported ERP file type: {erp_name}"), 400
    erp_path = os.path.join(d, erp_name)
    erp.save(erp_path)

    saved = []
    for f in banks:
        name = secure_filename(f.filename)
        if os.path.splitext(name)[1].lower() not in ALLOWED:
            continue
        f.save(os.path.join(up, name))
        saved.append(name)
    if not saved:
        shutil.rmtree(d, ignore_errors=True)
        return render_template_string(HOME, jobs=list_jobs(), maxmb=MAX_MB,
                                      err="None of the bank files had a supported type "
                                          "(.xls .xlsx .csv .txt)."), 400

    cfg = {
        "amount_tolerance": float(request.form.get("amt", 0) or 0),
        "date_tolerance_days": int(request.form.get("days", 3) or 3),
        "desc_threshold": float(request.form.get("desc", 0.34) or 0.34),
    }
    label = (request.form.get("label") or "").strip()[:80]
    state = {"job": job_id, "status": "queued", "step": "queued",
             "started": datetime.now().strftime("%d-%m-%Y %H:%M"), "label": label,
             "erp": erp_name, "banks": saved, "cfg": cfg,
             "prefix": (request.script_root or "").rstrip("/")}
    write_state(d, state)

    prefix = (request.script_root or "").rstrip("/")
    submit(job_id, d, erp_path, cfg, label, prefix)
    return redirect(url_for("status", job_id=job_id))


@app.route("/job/<job_id>")
def status(job_id):
    st = read_state(job_id)
    if not st:
        abort(404)
    if st.get("status") == "done":
        return redirect(url_for("dashboard", job_id=job_id))
    place, waiting, running = queue_info(job_id)
    return render_template_string(STATUS, s=st, place=place,
                                  waiting=waiting, running=running,
                                  workers=WORKERS)


@app.route("/job/<job_id>/dashboard")
def dashboard(job_id):
    d = job_dir(job_id)
    p = os.path.join(d, "output", "dashboard.html")
    if not os.path.exists(p):
        return redirect(url_for("status", job_id=job_id))
    with open(p, encoding="utf-8") as fh:
        return Response(fh.read(), mimetype="text/html")


@app.route("/job/<job_id>/full.xlsx")
def dl_full(job_id):
    d = job_dir(job_id)
    st = read_state(job_id) or {}
    p = os.path.join(d, "output", st.get("excel", ""))
    if not os.path.exists(p):
        abort(404)
    return send_file(p, as_attachment=True)


@app.route("/job/<job_id>/bank/<path:name>")
def dl_bank(job_id, name):
    d = job_dir(job_id)
    p = os.path.join(d, "output", "per_bank", secure_filename(name))
    if not os.path.exists(p):
        abort(404)
    return send_file(p, as_attachment=True)


@app.route("/job/<job_id>/state")
def state_json(job_id):
    st = read_state(job_id)
    if not st:
        abort(404)
    place, waiting, running = queue_info(job_id)
    st = dict(st, queue_place=place, queue_waiting=waiting, queue_running=running)
    return st


# ------------------------------------------------------------------ templates

CSS = """
:root{--bg:#f4f6fa;--panel:#fff;--ink:#0b2545;--muted:#5a6b7b;--line:#e2e8f0;--accent:#1b4f9c;
--ok:#0f9d58;--bad:#d64545}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 "Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{background:linear-gradient(100deg,#0b2545,#1b4f9c);color:#fff;padding:20px 26px}
header h1{margin:0;font-size:20px} header .s{opacity:.85;font-size:12.5px;margin-top:4px}
.wrap{max-width:1000px;margin:0 auto;padding:22px 24px 60px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
label{display:block;font-weight:600;margin:12px 0 4px;font-size:13px}
input[type=file],input[type=text],input[type=number]{width:100%;padding:9px;border:1px solid var(--line);
 border-radius:7px;background:#fbfdff;font-size:13px}
.row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
button{margin-top:16px;padding:11px 22px;border:0;background:var(--accent);color:#fff;border-radius:7px;
 font-size:14px;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.1)}
.hint{font-size:12px;color:var(--muted);margin-top:4px}
.err{background:#fdeaea;color:#b32e2e;border:1px solid #f3c9c9;padding:10px 12px;border-radius:7px;
 margin-bottom:14px;font-size:13px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left}
th{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);background:#eef3f8}
a{color:var(--accent)}
.badge{display:inline-block;padding:1px 9px;border-radius:20px;font-size:11px;font-weight:600}
.b-done{background:#e4f6ec;color:#0f7a45}.b-running{background:#fdf3e2;color:#9a6a12}
.b-error{background:#fdeaea;color:#b32e2e}.b-queued{background:#eef1f5;color:#5a6b7b}
.spin{width:34px;height:34px;border:4px solid #dfe7f1;border-top-color:var(--accent);border-radius:50%;
 animation:s 1s linear infinite;margin:8px 0 14px}
@keyframes s{to{transform:rotate(360deg)}}
ol{margin:6px 0 0 18px;padding:0} ol li{margin:3px 0}
.qbig{font-size:46px;font-weight:700;color:var(--accent);line-height:1;margin:2px 0 6px}
"""

HOME = """<!doctype html><meta charset="utf-8"><title>Reconciliation Portal</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>""" + CSS + """</style>
<header><h1>Bank &#8596; ERP Reconciliation Portal</h1>
<div class="s">Upload the ERP Payment Import export and the bank statements &mdash; get the dashboard
and a three-sheet Excel for every bank.</div></header>
<div class="wrap">
{% if err %}<div class="err">{{err}}</div>{% endif %}
<form class="card" method="post" action="{{url_for('start')}}" enctype="multipart/form-data">
  <h2>New reconciliation</h2>
  <label>ERP Payment Import file (.xlsx)</label>
  <input type="file" name="erp" accept=".xls,.xlsx,.xlsm,.csv" required>
  <label>Bank statements &mdash; select all of them together</label>
  <input type="file" name="banks" multiple accept=".xls,.xlsx,.xlsm,.csv,.txt" required>
  <div class="hint">Any bank, any layout: .xls, .xlsx, .csv or .txt. Max {{maxmb}} MB per upload.</div>
  <label>Label for this run (optional)</label>
  <input type="text" name="label" placeholder="e.g. August 2026 reconciliation">
  <div class="row">
    <div><label>Amount tolerance (Rs.)</label><input type="number" name="amt" value="0" step="0.01" min="0">
      <div class="hint">0 = exact, to the paisa</div></div>
    <div><label>Date tolerance (days)</label><input type="number" name="days" value="3" min="0" max="15">
      <div class="hint">ERP date vs bank date</div></div>
    <div><label>Narration match</label><input type="number" name="desc" value="0.34" step="0.01" min="0" max="1">
      <div class="hint">share of words that must agree</div></div>
  </div>
  <button type="submit">Run reconciliation</button>
</form>

<div class="card"><h2>What you get</h2>
<ol>
<li><b>Dashboard</b> &mdash; every line with the reason it matched or broke, filters by bank, status, rule, online/offline, Dr/Cr, date and free text.</li>
<li><b>Per-bank Excel (3 sheets)</b> &mdash; sheet 1 the bank statement, sheet 2 the ERP statement of that account number only, sheet 3 the reconciliation with reasons and the parameters used.</li>
<li><b>Full workbook</b> &mdash; summary, reconciliation, both exception lists, both statements and the rule sheet.</li>
</ol></div>

<div class="card" style="display:flex;justify-content:space-between;align-items:center">
  <span class="hint">Signed in.</span><a href="{{url_for('logout')}}">Sign out</a></div>

{% if jobs %}<div class="card"><h2>Previous runs</h2><table>
<tr><th>Run</th><th>Label</th><th>Started</th><th>Status</th><th>Result</th><th></th></tr>
{% for j in jobs %}<tr>
<td>{{j.job}}</td><td>{{j.label or '-'}}</td><td>{{j.started}}</td>
<td><span class="badge b-{{j.status}}">{{j.status}}</span></td>
<td>{% if j.stats %}{{j.stats.matched}} matched / {{j.stats.unmatched}} open ERP /
  {{j.stats.bank_open}} open bank{% else %}{{j.step or '-'}}{% endif %}</td>
<td><a href="{{url_for('status',job_id=j.job)}}">open</a></td></tr>{% endfor %}
</table></div>{% endif %}
</div>"""

STATUS = """<!doctype html><meta charset="utf-8"><title>Reconciling…</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
{% if s.status != 'error' %}<meta http-equiv="refresh" content="4">{% endif %}
<style>""" + CSS + """</style>
<header><h1>Bank &#8596; ERP Reconciliation</h1><div class="s">Run {{s.job}}{% if s.label %} &mdash; {{s.label}}{% endif %}</div></header>
<div class="wrap"><div class="card">
{% if s.status == 'error' %}
  <h2>Something went wrong</h2>
  <div class="err">{{s.message}}</div>
  <pre style="font-size:11.5px;overflow:auto;background:#fbfdff;padding:10px;border:1px solid #e2e8f0;
   border-radius:7px">{{s.trace}}</pre>
  <a href="{{url_for('home')}}">&laquo; back</a>
{% elif s.status == 'queued' %}
  <h2>Waiting in the queue</h2>
  <div class="spin"></div>
  <div class="qbig">{{ place if place else '-' }}</div>
  <div class="hint" style="font-size:14px">
    {% if place == 1 %}You are <b>next</b> &mdash; your run starts as soon as a slot frees up.
    {% elif place %}There {{ 'is' if place-1 == 1 else 'are' }} <b>{{place-1}}</b>
      run{{ '' if place-1 == 1 else 's' }} ahead of you.
    {% else %}Starting&hellip;{% endif %}
    <br>{{running}} running now &middot; {{waiting}} waiting &middot; {{workers}} run
    {{ 'slot' if workers == 1 else 'slots' }} available.
  </div>
  <div class="hint">Runs finish strictly in the order they were submitted.
  Leave this page open &mdash; it refreshes itself and will move on automatically.
  <br><br>ERP file: {{s.erp}}<br>Bank statements: {{s.banks|length}} file(s)</div>
{% else %}
  <h2>Working &mdash; {{s.step or s.status}}</h2>
  <div class="spin"></div>
  <div class="hint">ERP file: {{s.erp}}<br>Bank statements: {{s.banks|length}} file(s)<br>
  {% if waiting %}{{waiting}} other run{{ '' if waiting == 1 else 's' }} waiting behind you.<br>{% endif %}
  This page refreshes itself; a large ERP export takes a few minutes.</div>
{% endif %}
</div></div>"""


def lan_addresses():
    """Every IPv4 address of this machine that a colleague could reach."""
    import socket
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in out:
                out.append(ip)
    except Exception:
        pass
    if not out:                      # fallback: ask the OS which route it would use
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.connect(("8.8.8.8", 80))
            out.append(sk.getsockname()[0])
            sk.close()
        except Exception:
            pass
    return out


def recover_jobs():
    """After an app restart, nothing is queued or running any more in memory.
    Re-queue what was still waiting and flag what was cut off mid-run."""
    if not os.path.isdir(JOBS):
        return
    for name in sorted(os.listdir(JOBS)):
        st = read_state(name)
        if not st:
            continue
        d = os.path.join(JOBS, name)
        if st.get("status") == "running":
            st.update(status="error", step="",
                      message="The server restarted while this run was in progress. "
                              "Please submit it again.")
            write_state(d, st)
        elif st.get("status") == "queued":
            erp = os.path.join(d, st.get("erp", ""))
            if os.path.exists(erp):
                submit(name, d, erp, st.get("cfg") or {}, st.get("label", ""),
                       st.get("prefix", ""))
            else:
                st.update(status="error", step="", message="Uploaded files are gone.")
                write_state(d, st)


LOGIN = """<!doctype html><meta charset="utf-8"><title>Sign in - Reconciliation Portal</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>""" + CSS + """
.box{max-width:390px;margin:9vh auto}
</style>
<header><h1>Bank &#8596; ERP Reconciliation</h1><div class="s">Internal use - please sign in.</div></header>
<div class="wrap"><form class="card box" method="post">
  <h2>Sign in</h2>
  {% if err %}<div class="err">{{err}}</div>{% endif %}
  <input type="hidden" name="next" value="{{next}}">
  <label>Password</label>
  <input type="password" name="password" autofocus required autocomplete="current-password">
  <button type="submit" style="width:100%">Sign in</button>
</form></div>"""


# Passenger / any WSGI server imports this module rather than running it, so
# recovery has to happen at import time, not only under __main__.
try:
    recover_jobs()
except Exception:                      # never block startup
    traceback.print_exc()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0",
                    help="0.0.0.0 shares the portal on your network (default); "
                         "127.0.0.1 keeps it to this machine only")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--prod", action="store_true",
                    help="serve with waitress instead of the Flask dev server")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    os.makedirs(JOBS, exist_ok=True)

    print("\n  Bank <-> ERP Reconciliation portal\n")
    print(f"  on this machine : http://127.0.0.1:{a.port}")
    if a.host == "0.0.0.0":
        for ip in lan_addresses():
            print(f"  share this link : http://{ip}:{a.port}")
        print("\n  Colleagues must be on the same office network / Wi-Fi.")
        print("  If the link does not open for them, allow the port once (run as admin):")
        print(f'    netsh advfirewall firewall add rule name="Reco Portal {a.port}" '
              f"dir=in action=allow protocol=TCP localport={a.port}")
    if GENERATED_PASSWORD:
        print(f"\n  PASSWORD for this session : {GENERATED_PASSWORD}")
        print("  (set RECO_PASSWORD to pick your own and keep it across restarts)")
    else:
        print("\n  Password : the value of RECO_PASSWORD")
    print("\n  Ctrl+C to stop.\n")

    if a.prod:
        from waitress import serve
        serve(app, host=a.host, port=a.port, threads=8)
    else:
        app.run(host=a.host, port=a.port, debug=a.debug, threaded=True)
