#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_pipeline.py - the whole chain as one function.

    smet.cz + WhoisExtractor          Watchlist Internet
    ~390k new domains/day             Phishing.Database
            |                                  |
       nrd_feed.py                      brand_protect.py
            |                                  |
       NRD/daily/<date>.txt  -------->    fraud.sqlite
       NRD/out/<date>/sk_cz.csv                |
            |                           redpoints_cases.py
            |                                  |  + the 560,827-brand
            v                                  v    enriched dataset
       Modulario outreach         BRAND/out/<date>/redpoints_cases.csv
                                               |
                                               v
                                   Manyreach / contact forms

One run executes the stages in order, keeps going when a stage fails, and
ends with a report that says exactly what landed where - including the two
handoff files: sk_cz.csv for Modulario outreach and redpoints_cases.csv for
Manyreach. Each stage is still its own script with its own tools, so nothing
about running them individually changes.

Commands
    run                 run the pipeline (default)
    run --only nrd,cases     run just those stages
    run --with-jobs     also run the hiring-signal match
    report              re-print the latest report
    stages              list the stages and whether each is on
    schedule [HH:MM]    ONE Windows task for the whole chain (default 07:15);
                        replaces the three separate morning tasks
    unschedule

Output: <this folder>/PIPELINE/report_<date>.txt
"""

import csv
import glob
import json
import os
import subprocess
import sys
import time
import datetime as dt

APP = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(APP, "PIPELINE")
CONFIG = os.path.join(BASE, "config.json")
TASK_NAME = "LeadStudio Daily Pipeline"

csv.field_size_limit(min(2 ** 31 - 1, sys.maxsize))

DEFAULT_CONFIG = {
    "stages": [
        {"id": "nrd", "title": "New domains (smet.cz + WhoisExtractor)",
         "script": "nrd_feed.py", "args": ["daily"], "enabled": True, "timeout_s": 2400},
        {"id": "fraud", "title": "Fraud listings (Watchlist + Phishing.Database)",
         "script": "brand_protect.py", "args": ["daily"], "enabled": True, "timeout_s": 2400},
        {"id": "radar", "title": "Scam radar: new domains x 561k brands x fake-shop patterns",
         "script": "scam_radar.py", "args": ["daily"], "enabled": True, "timeout_s": 2400},
        {"id": "cases", "title": "Red Points cases (corpus x 561k brands)",
         "script": "redpoints_cases.py", "args": ["build"], "enabled": True, "timeout_s": 2400},
        {"id": "cross", "title": "Cross-check: new domains vs fraud lists",
         "script": "brand_protect.py", "args": ["cross-nrd", "7"], "enabled": True, "timeout_s": 900},
        {"id": "jobs", "title": "Hiring signals (affiliate offers)",
         "script": "job_signals.py", "args": ["daily"], "enabled": False, "timeout_s": 2400,
         "note": "Off by default because it is a different workstream; turn it on here or run it with --with-jobs."},
    ],
    "stop_on_first_failure": False,
    "schedule_time": "07:15"
}


def say(*a):
    sys.stdout.write(" ".join(str(x) for x in a) + "\n")
    sys.stdout.flush()


def load_config():
    os.makedirs(BASE, exist_ok=True)
    if not os.path.exists(CONFIG):
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=1)
        say("wrote a starter config:", CONFIG)
    try:
        cfg = json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    have = {s.get("id") for s in cfg.get("stages", [])}
    for s in DEFAULT_CONFIG["stages"]:
        if s["id"] not in have:
            cfg.setdefault("stages", []).append(s)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


# ---------------------------------------------------------------- running

def run_stage(stage):
    """Run one script as a subprocess, streaming its output with a prefix."""
    script = os.path.join(APP, stage["script"])
    if not os.path.exists(script):
        return "missing", 0.0, ["%s is not in the LEAD_STUDIO folder" % stage["script"]]
    cmd = [sys.executable, "-u", script] + list(stage.get("args") or [])
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    t0 = time.time()
    deadline = t0 + float(stage.get("timeout_s") or 2400)
    tail = []
    try:
        p = subprocess.Popen(cmd, cwd=APP, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
    except Exception as e:
        return "error", 0.0, ["could not start: %s" % e]
    prefix = "[%s] " % stage["id"]
    try:
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            say(prefix + line)
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            if time.time() > deadline:
                p.kill()
                tail.append("stopped after %ds timeout" % int(stage.get("timeout_s") or 2400))
                return "timeout", time.time() - t0, tail
        p.wait()
    except KeyboardInterrupt:
        p.kill()
        raise
    took = time.time() - t0
    return ("ok" if p.returncode == 0 else "failed(exit %s)" % p.returncode), took, tail


# ---------------------------------------------------------------- harvest

def _read_json(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _csv_rows(p):
    try:
        with open(p, encoding="utf-8-sig", errors="replace", newline="") as f:
            return max(0, sum(1 for _ in f) - 1)
    except Exception:
        return 0


def harvest(today):
    """Pull the day's numbers out of each stage's own files."""
    h = {}
    nrd_state = _read_json(os.path.join(APP, "NRD", "state.json"))
    day = (nrd_state.get("days") or {}).get(today) or {}
    nrd_sum = _read_json(os.path.join(APP, "NRD", "out", today, "summary.json"))
    skcz = os.path.join(APP, "NRD", "out", today, "sk_cz.csv")
    h["nrd"] = {"total": day.get("total"), "new": day.get("new"),
                "slices": nrd_sum.get("slices") or {},
                "sk_cz_file": skcz if os.path.exists(skcz) else "",
                "sk_cz_rows": _csv_rows(skcz) if os.path.exists(skcz) else 0}

    b_state = _read_json(os.path.join(APP, "BRAND", "state.json"))
    bday = (b_state.get("days") or {}).get(today) or {}
    h["fraud"] = {"new": bday.get("new"), "hits": bday.get("hits"),
                  "watchlist_total": b_state.get("watchlist_total")}

    cases = os.path.join(APP, "BRAND", "out", today, "redpoints_cases.csv")
    top = []
    if os.path.exists(cases):
        try:
            with open(cases, encoding="utf-8-sig", errors="replace", newline="") as f:
                for i, r in enumerate(csv.DictReader(f)):
                    if i >= 5:
                        break
                    top.append("%s - %s (%s)" % (r.get("brand"), r.get("fake_shop"),
                                                 r.get("fit_tier")))
        except Exception:
            pass
    h["cases"] = {"file": cases if os.path.exists(cases) else "",
                  "rows": _csv_rows(cases) if os.path.exists(cases) else 0, "top": top}

    rad = os.path.join(APP, "RADAR", "out", today, "scam_candidates.csv")
    rtop = []
    if os.path.exists(rad):
        try:
            with open(rad, encoding="utf-8-sig", errors="replace", newline="") as f:
                for i, r in enumerate(csv.DictReader(f)):
                    if i >= 5:
                        break
                    rtop.append("%s -> %s (score %s)" % (
                        r.get("candidate_domain"), r.get("brand_display"), r.get("score")))
        except Exception:
            pass
    h["radar"] = {"file": rad if os.path.exists(rad) else "",
                  "rows": _csv_rows(rad) if os.path.exists(rad) else 0, "top": rtop}

    cross = os.path.join(APP, "BRAND", "out", today, "nrd_cross_check.csv")
    h["cross"] = {"file": cross if os.path.exists(cross) else "",
                  "rows": _csv_rows(cross) if os.path.exists(cross) else 0}

    jobs_sum = _read_json(os.path.join(APP, "JOBS", "out", today, "summary.json"))
    h["jobs"] = {"total": jobs_sum.get("total"), "offers": jobs_sum.get("offers") or {}}
    return h


def write_report(today, results, h):
    os.makedirs(BASE, exist_ok=True)
    p = os.path.join(BASE, "report_%s.txt" % today)
    lines = []
    w = lines.append
    w("Lead Studio daily pipeline - %s" % today)
    w("=" * 44)
    w("")
    w("stages")
    for st, status, took in results:
        w("  %-7s %-22s %s" % (st["id"], status, "%.0fs" % took))
    w("")
    n = h["nrd"]
    if n.get("total"):
        w("new domains      %s pulled, %s new to the index" % (n["total"], n["new"]))
        for k, v in sorted((n.get("slices") or {}).items(), key=lambda kv: -kv[1])[:6]:
            w("  slice %-16s %s" % (k, v))
    f = h["fraud"]
    if f.get("new") is not None:
        w("fraud listings   %s newly listed, %s brand hits" % (f.get("new"), f.get("hits")))
    rd = h["radar"]
    if rd["rows"]:
        w("scam radar       %d possible new scam sites, on no list yet" % rd["rows"])
        for t in rd["top"]:
            w("  " + t)
    c = h["cases"]
    if c["rows"]:
        w("red points       %d pitch-ready cases" % c["rows"])
        for t in c["top"]:
            w("  " + t)
    x = h["cross"]
    if x["rows"]:
        w("cross-check      %d newly registered domains overlap a fraud list" % x["rows"])
    j = h["jobs"]
    if j.get("total"):
        w("hiring signals   %s matched postings" % j["total"])
    w("")
    w("handoffs")
    w("  Modulario outreach   %s  (%d rows)" % (
        n["sk_cz_file"] or "- not produced today -", n["sk_cz_rows"]))
    w("  Manyreach / forms    %s  (%d rows)" % (
        c["file"] or "- not produced today -", c["rows"]))
    w("  early warning        %s  (%d rows)" % (
        rd["file"] or "- not produced today -", rd["rows"]))
    w("")
    w("Every fraud entry is an allegation by the listing body, not a finding of")
    w("fact. Check messages (redpoints_cases.py sample) before anything is sent.")
    with open(p, "w", encoding="utf-8") as fo:
        fo.write("\n".join(lines) + "\n")
    return p, lines


def cmd_run(cfg, only=None, with_jobs=False):
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    stages = []
    for s in cfg.get("stages", []):
        if only is not None:
            if s["id"] in only:
                stages.append(s)
            continue
        if s.get("enabled") or (with_jobs and s["id"] == "jobs"):
            stages.append(s)
    if not stages:
        say("no stages selected. 'stages' lists what exists.")
        return

    say("pipeline: %s" % " -> ".join(s["id"] for s in stages))
    say("")
    results = []
    for s in stages:
        say("=" * 60)
        say("%s: %s" % (s["id"], s.get("title") or s["script"]))
        say("=" * 60)
        status, took, _tail = run_stage(s)
        results.append((s, status, took))
        say("")
        if status != "ok":
            say("[%s] %s - carrying on with the rest" % (s["id"], status))
            say("")
            if cfg.get("stop_on_first_failure"):
                break

    h = harvest(today)
    p, lines = write_report(today, results, h)
    say("=" * 60)
    for ln in lines:
        say(ln)
    say("report: %s" % p)


def cmd_report():
    reps = sorted(glob.glob(os.path.join(BASE, "report_*.txt")))
    if not reps:
        say("no report yet - run the pipeline first")
        return
    say(open(reps[-1], encoding="utf-8").read())
    say("(%s)" % reps[-1])


def cmd_stages(cfg):
    say("stage order")
    for s in cfg.get("stages", []):
        on = "on " if s.get("enabled") else "off"
        exists = os.path.exists(os.path.join(APP, s["script"]))
        say("  [%s] %-7s %-46s %s%s" % (on, s["id"], s.get("title") or "",
                                        s["script"], "" if exists else "  <- MISSING"))
        if s.get("note"):
            say("          %s" % s["note"])
    say("\nedit: %s" % CONFIG)


def cmd_schedule(cfg, when=None):
    when = when or cfg.get("schedule_time") or "07:15"
    if os.name != "nt":
        say("Windows only. cron equivalent:")
        say("  15 7 * * *  %s %s run" % (sys.executable, os.path.abspath(__file__)))
        return
    pyw = sys.executable
    c = os.path.join(os.path.dirname(pyw), "pythonw.exe")
    if os.path.exists(c):
        pyw = c
    r = subprocess.run(['schtasks', '/Create', '/TN', TASK_NAME, '/TR',
                        '"%s" "%s" run' % (pyw, os.path.abspath(__file__)),
                        '/SC', 'DAILY', '/ST', when, '/F'],
                       capture_output=True, text=True)
    say((r.stdout or "").strip() or (r.stderr or "").strip())
    if r.returncode == 0:
        say("\nOne task now runs the whole chain at %s. If you registered the" % when)
        say("separate 07:30 / 07:50 / 08:15 tasks earlier, remove them so nothing")
        say("runs twice:")
        say('  schtasks /Delete /TN "LeadStudio NRD Daily" /F')
        say('  schtasks /Delete /TN "LeadStudio Brand Protect Daily" /F')
        say('  schtasks /Delete /TN "LeadStudio Job Signals Daily" /F')
    else:
        say("failed (exit %s) - try an Administrator prompt." % r.returncode)


def main(argv):
    cfg = load_config()
    cmd = (argv[0] if argv else "run").lower()
    rest = argv[1:]
    if cmd == "run":
        only = None
        if "--only" in rest:
            i = rest.index("--only")
            only = set(x.strip() for x in (rest[i + 1] if i + 1 < len(rest) else "").split(",") if x.strip())
        cmd_run(cfg, only=only, with_jobs=("--with-jobs" in rest))
    elif cmd == "report":
        cmd_report()
    elif cmd == "stages":
        cmd_stages(cfg)
    elif cmd == "schedule":
        cmd_schedule(cfg, rest[0] if rest else None)
    elif cmd == "unschedule":
        if os.name != "nt":
            say("nothing to remove")
            return
        r = subprocess.run(['schtasks', '/Delete', '/TN', TASK_NAME, '/F'],
                           capture_output=True, text=True)
        say((r.stdout or "").strip() or (r.stderr or "").strip() or "removed")
    else:
        say(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        say("\nstopped")
