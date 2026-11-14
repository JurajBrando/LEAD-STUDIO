#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job_signals.py - Hiring signals -> affiliate offer matches, for Lead Studio.

A company that is hiring tells you what it is short of. A firm advertising for
a Performance Marketing Manager is about to spend more on ads and will hit an
attribution wall. One hiring three Technical Recruiters has a hiring problem.
One hiring a Production Planner is running production on spreadsheets.

This script pulls fresh job postings from public HuggingFace datasets, matches
each posting against your offers with an editable rule table, enriches the
company with how hard it is hiring overall, and writes one ready-to-send CSV
per offer.

Stdlib only. No pip install.

Commands
    daily                  today's new postings -> matched -> per-offer CSVs
    match [YYYY-MM-DD]     re-match what is already on disk (no network)
    search "<text>"        full-text search across the datasets
                             --country SK --limit 50 --source cleanfeeds
    company "<name>"       what one company is hiring, and how hard
    companies              refresh the company hiring-intensity table
    demand [CC]            most in-demand job titles in a country
    offers                 show the offer rules and what each one catches
    sources                which datasets are on, and their shape
    status                 what is on disk, index size, last run
    schedule [HH:MM]       register a Windows daily task (default 08:15)
    unschedule

Everything lands in <this folder>/JOBS/.

Data sources, all public HuggingFace datasets
    cleanfeeds/tech-jobs-daily              CC BY 4.0  daily CSV, ~10k rows/day
    Aramente/eu-tech-jobs                   CC BY 4.0  daily EU diff, JSONL
    Carmelon23/career-site-jobs-nov-2026    CC BY 4.0  company intensity table
    edwarddgao/open-apply-jobs              MIT        searchable, has descriptions
    scholarweave/Jobs-Dataset-v2            Apache-2.0 searchable, has skills
    feder-cr/jobs                           -          searchable, has descriptions
    Jobs-database/job-titles                CC BY 4.0  title demand by country
Credit these datasets if you surface their data in anything public.
"""

import csv
import gzip
import io
import json
import os
import re
import sqlite3
import sys
import time
import datetime as dt
import urllib.error
import urllib.parse
import urllib.request

APP = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.environ.get("JOBS_DIR") or os.path.join(APP, "JOBS"))
RAW = os.path.join(BASE, "raw")
OUT = os.path.join(BASE, "out")
CONFIG = os.path.join(BASE, "config.json")
RULES = os.path.join(BASE, "offer_rules.json")
STATE = os.path.join(BASE, "state.json")
INDEX = os.path.join(BASE, "seen_jobs.sqlite")
COMPANIES = os.path.join(BASE, "companies.csv")
ATTRIB = os.path.join(BASE, "ATTRIBUTION.txt")

UA = "LeadStudio-JobSignals/1.0"
HF = "https://huggingface.co"
DSS = "https://datasets-server.huggingface.co"
TASK_NAME = "LeadStudio Job Signals Daily"

csv.field_size_limit(min(2 ** 31 - 1, sys.maxsize))

ATTRIBUTION_TEXT = """\
Datasets used by job_signals.py
===============================

cleanfeeds/tech-jobs-daily            CC BY 4.0
Aramente/eu-tech-jobs                 CC BY 4.0
Carmelon23/career-site-jobs-nov-2026  CC BY 4.0
Jobs-database/job-titles              CC BY 4.0
edwarddgao/open-apply-jobs            MIT
scholarweave/Jobs-Dataset-v2          Apache-2.0
feder-cr/jobs                         see the dataset card

All are public datasets on huggingface.co. The CC BY 4.0 ones require
attribution wherever you surface their data publicly. Job postings are
business contact information, not personal data, but the descriptions
can name individuals - do not republish them wholesale.

Nothing here is a substitute for checking each dataset card before you
put its data in front of a client.
"""

DEFAULT_CONFIG = {
    "hf_token": "",
    "_hf_token_note": "Optional. A free HuggingFace token raises the rate limits on search. Or set HF_TOKEN.",
    "countries": [],
    "_countries_note": "Empty = keep every country. Example: [\"SK\",\"CZ\",\"AT\",\"DE\"] to keep only those.",
    "min_score": 3,
    "days_back": 2,
    "retention_days": 60,
    "sources": {
        "cleanfeeds": {"enabled": True, "dataset": "cleanfeeds/tech-jobs-daily",
                       "note": "Daily CSV of ATS postings, ~10k rows/day, 7 days kept upstream. Main daily feed."},
        "aramente": {"enabled": True, "dataset": "Aramente/eu-tech-jobs",
                     "exclude_feeds": ["remoteok", "weworkremotely", "wwr"],
                     "note": "Daily EU diff of new postings (JSONL). Real European employers via workday/wttj/greenhouse. exclude_feeds drops the remote-board aggregators, which only repost other firms' ATS jobs that cleanfeeds already covers, with worse metadata and country 'XX'."},
        "carmelon": {"enabled": True, "dataset": "Carmelon23/career-site-jobs-nov-2026",
                     "note": "Company-level hiring intensity: open_jobs, new_jobs_7d, seniority mix. Used for enrichment and scoring."}
    },
    "search_datasets": [
        {"name": "cleanfeeds", "dataset": "cleanfeeds/tech-jobs-daily", "config": "default", "split": "train"},
        {"name": "feder", "dataset": "feder-cr/jobs", "config": "default", "split": "train"},
        {"name": "openapply", "dataset": "edwarddgao/open-apply-jobs", "config": "default", "split": "train"},
        {"name": "scholarweave", "dataset": "scholarweave/Jobs-Dataset-v2",
         "config": "minimal-active-current", "split": "active"}
    ],
    "schedule_time": "08:15"
}

# Each offer: which job titles betray the need your offer fills.
# patterns are matched against the job title (and department), neg kills a match.
DEFAULT_RULES = {
    "_how_to_read_this": "patterns: substrings matched lowercase against title+department. neg: if any of these appear, skip. weight: added to the score on a title hit. pitch: the line that explains why this company needs this offer - use it as your cold email angle.",
    "offers": [
        {"offer": "HYROS", "link": "",
         "pitch": "They are hiring to scale paid acquisition. The moment ad spend grows, attribution breaks - which is exactly the hole HYROS fills.",
         "weight": 4,
         "patterns": ["performance marketing", "paid media", "media buyer", "paid social",
                      "paid search", "ppc", "sem specialist", "google ads", "meta ads",
                      "facebook ads", "user acquisition", "growth marketing", "demand generation",
                      "demand gen", "acquisition manager", "marketing analytics", "attribution",
                      "marketing operations", "marketing ops", "growth lead", "head of growth"],
         "neg": ["intern", "internship", "student", "praktik"]},

        {"offer": "HYROS AIR", "link": "",
         "pitch": "High-ticket phone sales with no call-level tracking. They are hiring closers and setters but cannot yet tell which ad produced which booked call.",
         "weight": 4,
         "patterns": ["appointment setter", "high ticket", "high-ticket", "closer",
                      "inside sales representative", "call center manager", "sales floor",
                      "outbound caller", "phone sales"],
         "neg": ["intern", "student"]},

        {"offer": "PLAI", "link": "",
         "pitch": "They are putting a person on social instead of a system. PLAI runs the paid social side without an agency retainer.",
         "weight": 3,
         "patterns": ["social media manager", "social media specialist", "social media coordinator",
                      "content creator", "community manager", "influencer", "tiktok", "instagram",
                      "brand ambassador", "content marketing"],
         "neg": ["intern", "internship", "student", "volunteer"]},

        {"offer": "GoHighLevel", "link": "",
         "pitch": "Hiring humans to do what automation should. An SDR or lead-gen hire means outbound is manual - CRM, pipelines and follow-up are the gap.",
         "weight": 4,
         "patterns": ["sdr", "bdr", "sales development", "business development representative",
                      "lead generation", "lead gen", "cold caller", "telemarket",
                      "crm manager", "marketing automation", "revenue operations", "revops",
                      "sales operations", "sales enablement", "outbound sales"],
         "neg": ["intern", "student"]},

        {"offer": "micro1 - engineers", "link": "",
         "pitch": "Hiring recruiters to hire engineers. micro1 skips a layer: AI-vetted engineers, no sourcing pipeline to staff.",
         "weight": 4,
         "patterns": ["technical recruiter", "talent acquisition", "recruiting manager",
                      "sourcer", "head of talent", "recruitment specialist", "recruiting coordinator",
                      "talent partner", "head of recruiting"],
         "neg": ["intern", "student"]},

        {"offer": "micro1 - data partnership", "link": "",
         "pitch": "They run their own data-labelling or model-evaluation work in house. That is exactly the company micro1 pays a referral on as a data partner.",
         "weight": 6,
         "patterns": ["data annotat", "annotation specialist", "data label", "ai trainer",
                      "ai tutor", "rlhf", "model evaluation", "llm evaluat", "prompt engineer",
                      "red team", "data operations specialist", "human data", "training data"],
         "neg": []},

        {"offer": "QuoteIQ", "link": "",
         "pitch": "Field work quoted by hand. An estimator or dispatcher hire is the symptom - quoting and scheduling software is the fix.",
         "weight": 3,
         "patterns": ["estimator", "field service", "dispatcher", "service technician",
                      "installer", "field operations", "service coordinator", "scheduling coordinator",
                      "project coordinator", "crew lead"],
         "neg": ["intern", "student", "software", "engineer ii", "sre"]},

        {"offer": "Modulario", "link": "",
         "pitch": "Production, stock and planning are being run by people, not a system. This is the ERP/MES conversation, and they are staffing around the gap right now.",
         "weight": 5,
         "patterns": ["production planner", "production manager", "manufacturing engineer",
                      "process engineer", "plant manager", "supply chain planner",
                      "warehouse manager", "logistics coordinator", "quality manager",
                      "erp", "mes", "production supervisor", "materials planner",
                      "inventory manager", "operations planner"],
         "neg": ["intern", "student"]},

        {"offer": "Dermis", "link": "",
         "pitch": "A clinic or spa adding treatment staff - the point where client flow and skin-service tooling starts to matter.",
         "weight": 3,
         "patterns": ["esthetician", "aesthetician", "dermatolog", "medical spa", "medspa",
                      "skincare", "aesthetic nurse", "beauty therapist", "cosmetolog"],
         "neg": []},

        {"offer": "Ataraxis", "link": "",
         "pitch": "",
         "weight": 3,
         "patterns": [],
         "_note": "FILL THIS IN - add the job titles that signal a need for Ataraxis, plus a pitch line, and it joins the daily run."},

        {"offer": "A.I. Scale (own)", "link": "",
         "pitch": "They are hiring a person to hold a process together. That is the high-ticket automation conversation.",
         "weight": 3,
         "patterns": ["automation engineer", "business operations", "operations manager",
                      "chief of staff", "rpa", "workflow", "integration specialist",
                      "no-code", "nocode", "ai engineer", "solutions engineer",
                      "process improvement", "operations analyst"],
         "neg": ["intern", "student"]}
    ]
}


# ---------------------------------------------------------------- plumbing

def say(*a):
    sys.stdout.write(" ".join(str(x) for x in a) + "\n")
    sys.stdout.flush()


def ensure_dirs():
    for d in (BASE, RAW, OUT):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(ATTRIB):
        with open(ATTRIB, "w", encoding="utf-8") as f:
            f.write(ATTRIBUTION_TEXT)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(default))


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_config():
    ensure_dirs()
    if not os.path.exists(CONFIG):
        save_json(CONFIG, DEFAULT_CONFIG)
        say("wrote a starter config:", CONFIG)
    cfg = load_json(CONFIG, DEFAULT_CONFIG)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    if not os.path.exists(RULES):
        save_json(RULES, DEFAULT_RULES)
        say("wrote the offer rules:", RULES)
    return cfg


def load_rules():
    r = load_json(RULES, DEFAULT_RULES)
    return [o for o in r.get("offers", []) if o.get("patterns")]


def token(cfg):
    return (cfg.get("hf_token") or os.environ.get("HF_TOKEN") or "").strip()


def http(url, cfg=None, timeout=120, tries=5, quiet=False):
    """GET with backoff. HuggingFace throws transient 500s constantly."""
    hdrs = {"User-Agent": UA}
    if cfg and token(cfg):
        hdrs["Authorization"] = "Bearer " + token(cfg)
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
                data = r.read()
                if data[:2] == b"\x1f\x8b":
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                return data
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:160]
            except Exception:
                pass
            last = "HTTP %s %s" % (e.code, body)
            if e.code in (500, 502, 503, 504, 429):
                time.sleep(2 + 3 * i)
                continue
            break
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(2 + 2 * i)
    if not quiet:
        say("    failed:", last)
    return None


def hf_file(dataset, path, cfg):
    return http("%s/datasets/%s/resolve/main/%s" % (HF, dataset, urllib.parse.quote(path)), cfg)


def dss(endpoint, params, cfg, tries=5):
    url = DSS + "/" + endpoint + "?" + urllib.parse.urlencode(params)
    d = http(url, cfg, tries=tries, quiet=True)
    if not d:
        return None
    try:
        return json.loads(d)
    except Exception:
        return None


# ---------------------------------------------------------------- normalise

def clean(s):
    if s is None:
        return ""
    s = str(s).replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", s)


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return clean(s)


COUNTRY_HINTS = {
    "slovakia": "SK", "slovak": "SK", "bratislava": "SK", "kosice": "SK", "nitra": "SK",
    "zilina": "SK", "presov": "SK", "trnava": "SK",
    "czech": "CZ", "czechia": "CZ", "prague": "CZ", "praha": "CZ", "brno": "CZ", "ostrava": "CZ",
    "austria": "AT", "vienna": "AT", "wien": "AT",
    "germany": "DE", "berlin": "DE", "munich": "DE", "hamburg": "DE",
    "poland": "PL", "warsaw": "PL", "krakow": "PL", "wroclaw": "PL",
    "hungary": "HU", "budapest": "HU",
    "united kingdom": "GB", "london": "GB", "england": "GB",
    "united states": "US", "usa": "US", "remote": "",
    "canada": "CA", "toronto": "CA", "vancouver": "CA", "montreal": "CA", "ottawa": "CA",
    "netherlands": "NL", "amsterdam": "NL", "ireland": "IE", "dublin": "IE",
    "spain": "ES", "barcelona": "ES", "madrid": "ES", "france": "FR", "paris": "FR",
    "switzerland": "CH", "zurich": "CH", "romania": "RO", "bucharest": "RO",
}


US_STATES = set("""AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI
MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
PR GU VI AS MP""".split())


def guess_country(location, explicit=""):
    if explicit and len(str(explicit)) == 2:
        return str(explicit).upper()
    lo = (location or "").lower()
    for k, v in COUNTRY_HINTS.items():
        if k in lo:
            return v
    m = re.search(r",\s*([A-Z]{2})\s*$", location or "")
    if not m:
        return ""
    cc = m.group(1)
    # "Austin, TX" is not country TX - US state codes would wreck a country filter
    return "US" if cc in US_STATES else cc


def row(company, title, location, url, source, posted_at, department="", country="", desc=""):
    return {
        "company": clean(company), "title": clean(title), "location": clean(location),
        "url": clean(url), "source": source, "posted_at": clean(posted_at),
        "department": clean(department), "country": country or guess_country(location),
        "desc": strip_html(desc)[:600], "feed": "",
    }


# ---------------------------------------------------------------- sources

def pull_cleanfeeds(date, cfg):
    ds = cfg["sources"]["cleanfeeds"]["dataset"]
    path = "data/jobs-%s.csv" % date.isoformat()
    say("  cleanfeeds ->", path)
    data = hf_file(ds, path, cfg)
    if not data:
        say("    not available for that day (upstream keeps about a week)")
        return []
    try:
        with open(os.path.join(RAW, "cleanfeeds_%s.csv.gz" % date.isoformat()), "wb") as f:
            f.write(gzip.compress(data))
    except Exception:
        pass
    out = []
    for r in csv.DictReader(io.StringIO(data.decode("utf-8", "replace"))):
        out.append(row(r.get("company"), r.get("title"), r.get("location"), r.get("url"),
                       "cleanfeeds", r.get("posted_at"), r.get("department")))
    say("    %d postings" % len(out))
    return out


def pull_aramente(date, cfg):
    ds = cfg["sources"]["aramente"]["dataset"]
    path = "diffs/%s-diff.jsonl" % date.isoformat()
    say("  aramente (EU) ->", path)
    data = hf_file(ds, path, cfg)
    if not data:
        say("    no diff published for that day")
        return []
    try:
        with open(os.path.join(RAW, "aramente_%s.jsonl.gz" % date.isoformat()), "wb") as f:
            f.write(gzip.compress(data))
    except Exception:
        pass
    excl = set(x.lower() for x in (cfg["sources"]["aramente"].get("exclude_feeds") or []))
    out = []
    counts = {"new": 0, "other_event": 0, "dropped_feed": 0}
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        # the diff also carries 'removed' and 'changed' events, which hold only
        # an id and no company or title - they are not postings
        if (o.get("event") or "new") != "new":
            counts["other_event"] += 1
            continue
        feed = (o.get("source") or "").lower()
        if feed in excl:
            counts["dropped_feed"] += 1
            continue
        comp = (o.get("company_slug") or "").replace("-", " ").strip()
        comp = re.sub(r"^(via|wttj)\s+", "", comp).strip()
        if not comp or not o.get("title"):
            continue
        r = row(comp, o.get("title"), o.get("location"), o.get("url"),
                "aramente", o.get("posted_at"))
        r["feed"] = feed
        out.append(r)
        counts["new"] += 1
    say("    %d new postings (%d close/edit events skipped%s)" % (
        counts["new"], counts["other_event"],
        ", %d aggregator reposts dropped" % counts["dropped_feed"] if counts["dropped_feed"] else ""))
    return out


def refresh_companies(cfg, force=False):
    """Company-level hiring intensity, cached on disk."""
    if os.path.exists(COMPANIES) and not force:
        age = (time.time() - os.path.getmtime(COMPANIES)) / 86400.0
        if age < 7:
            return read_companies()
    ds = cfg["sources"]["carmelon"]["dataset"]
    say("  refreshing the company table from", ds)
    data = hf_file(ds, "companies.csv", cfg)
    if not data:
        say("    could not fetch it; carrying on without company enrichment")
        return read_companies()
    with open(COMPANIES, "wb") as f:
        f.write(data)
    say("    saved", COMPANIES)
    return read_companies()


def read_companies():
    if not os.path.exists(COMPANIES):
        return {}
    out = {}
    try:
        with open(COMPANIES, encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                k = clean(r.get("company")).lower()
                if k:
                    out[k] = r
    except Exception as e:
        say("    company table unreadable:", e)
    return out


# ---------------------------------------------------------------- matching

_PAT_CACHE = {}


def compile_pattern(p):
    """Match on word starts, not bare substrings.

    Plain 'in' matching is a trap: 'erp' hides inside 'Enterprise' and
    'mes' inside 'Outcomes'. Patterns anchor to a word start. Very short
    ones (<=4 chars) also anchor at the end, so 'erp' will not fire on
    'Messaging' or 'Serpentine'. Longer ones stay open-ended so
    'data annotat' still catches 'Data Annotation'.
    """
    r = _PAT_CACHE.get(p)
    if r is None:
        core = re.escape(p.strip())
        r = re.compile(r"\b" + core + (r"\b" if len(p.strip()) <= 4 else ""))
        _PAT_CACHE[p] = r
    return r


def match_row(r, rules):
    hay = (r["title"] + " " + r["department"]).lower()
    hits = []
    for o in rules:
        if any(n and compile_pattern(n).search(hay) for n in (o.get("neg") or [])):
            continue
        for p in o.get("patterns") or []:
            if p and compile_pattern(p).search(hay):
                hits.append((o, p))
                break
    return hits


def intensity(comp_row):
    """Score how hard this company is hiring - budget and pain proxy."""
    if not comp_row:
        return 0, ""
    try:
        n7 = int(float(comp_row.get("new_jobs_7d") or 0))
        openj = int(float(comp_row.get("open_jobs") or 0))
    except Exception:
        return 0, ""
    s = 0
    notes = []
    if n7 >= 50:
        s += 3; notes.append("%d new roles in 7 days" % n7)
    elif n7 >= 10:
        s += 2; notes.append("%d new roles in 7 days" % n7)
    elif n7 >= 3:
        s += 1; notes.append("%d new roles in 7 days" % n7)
    if openj >= 200:
        s += 2; notes.append("%d open roles" % openj)
    elif openj >= 30:
        s += 1; notes.append("%d open roles" % openj)
    return s, "; ".join(notes)


def run_match(rows, cfg, date, con):
    rules = load_rules()
    if not rules:
        say("no usable offer rules - edit", RULES)
        return {}
    comps = refresh_companies(cfg)
    countries = set(c.upper() for c in (cfg.get("countries") or []))
    min_score = int(cfg.get("min_score") or 0)

    per_offer = {}
    seen_new = 0
    for r in rows:
        if countries and r["country"] and r["country"] not in countries:
            continue
        hits = match_row(r, rules)
        if not hits:
            continue
        # the same role often appears several times (re-posts, multi-location),
        # so key on company+title as well as the url
        ck = "ct:" + re.sub(r"[^a-z0-9]+", "", (r["company"] + r["title"]).lower())
        key = r["url"] or ck
        if already_seen(con, key) or already_seen(con, ck):
            continue
        mark_seen(con, key, date)
        mark_seen(con, ck, date)
        seen_new += 1
        comp = comps.get(r["company"].lower())
        ins, inote = intensity(comp)
        for o, pat in hits:
            score = int(o.get("weight") or 3) + ins
            if score < min_score:
                continue
            rec = dict(r)
            rec.update({
                "offer": o["offer"], "matched_on": pat, "score": score,
                "why": o.get("pitch") or "",
                "hiring_signal": inote,
                "careers_url": clean((comp or {}).get("careers_url")),
                "open_jobs": (comp or {}).get("open_jobs") or "",
                "new_jobs_7d": (comp or {}).get("new_jobs_7d") or "",
                "ats": clean((comp or {}).get("ats")),
                "top_countries": clean((comp or {}).get("top_countries"))[:60],
            })
            per_offer.setdefault(o["offer"], []).append(rec)
    con.commit()
    say("  %d postings not pitched before" % seen_new)
    return per_offer


COLS = ["company", "title", "department", "location", "country", "offer", "score",
        "matched_on", "why", "hiring_signal", "open_jobs", "new_jobs_7d", "ats",
        "careers_url", "url", "source", "feed", "posted_at"]


def write_out(per_offer, date):
    day = date.isoformat()
    d = os.path.join(OUT, day)
    os.makedirs(d, exist_ok=True)
    allrows = []
    summary = {}
    for offer, rows in sorted(per_offer.items()):
        rows.sort(key=lambda r: -r["score"])
        slug = re.sub(r"[^a-z0-9]+", "_", offer.lower()).strip("_")
        p = os.path.join(d, slug + ".csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        summary[offer] = len(rows)
        allrows += rows
        say("  %-26s %5d -> %s" % (offer, len(rows), os.path.basename(p)))
    if allrows:
        allrows.sort(key=lambda r: -r["score"])
        with open(os.path.join(d, "all_signals.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(allrows)
    with open(os.path.join(d, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("Hiring signals - %s\n%s\n" % (day, "=" * 34))
        f.write("matched postings: %d\n\n" % len(allrows))
        for k, v in sorted(summary.items(), key=lambda kv: -kv[1]):
            f.write("  %-26s %d\n" % (k, v))
        f.write("\ntop 15 by score\n")
        for r in allrows[:15]:
            f.write("  [%2d] %-22s %-40s %s\n" % (
                r["score"], r["company"][:22], r["title"][:40], r["offer"]))
        f.write("\nData: public HuggingFace datasets - see JOBS/ATTRIBUTION.txt\n")
    save_json(os.path.join(d, "summary.json"), {"date": day, "total": len(allrows), "offers": summary})
    return summary, len(allrows)


# ---------------------------------------------------------------- seen index

def open_index():
    ensure_dirs()
    con = sqlite3.connect(INDEX, timeout=60)
    for mode in ("WAL", "DELETE"):
        try:
            con.execute("PRAGMA journal_mode=%s" % mode)
            con.execute("CREATE TABLE IF NOT EXISTS seen (k TEXT PRIMARY KEY, d TEXT NOT NULL)")
            con.execute("CREATE INDEX IF NOT EXISTS seen_d ON seen(d)")
            con.commit()
            return con
        except sqlite3.OperationalError as e:
            last = e
    raise SystemExit("Could not open %s (%s). If this folder is on a synced drive, "
                     "set JOBS_DIR to a local path." % (INDEX, last))


def already_seen(con, k):
    return con.execute("SELECT 1 FROM seen WHERE k=?", (k,)).fetchone() is not None


def mark_seen(con, k, date):
    con.execute("INSERT OR IGNORE INTO seen (k,d) VALUES (?,?)", (k, date.isoformat()))


def prune(con, days):
    cut = (dt.date.today() - dt.timedelta(days=int(days))).isoformat()
    c = con.execute("DELETE FROM seen WHERE d < ?", (cut,))
    con.commit()
    if c.rowcount and c.rowcount > 0:
        say("index: dropped %d entries older than %s" % (c.rowcount, cut))


# ---------------------------------------------------------------- commands

def cmd_daily(cfg, state, con):
    today = dt.datetime.now(dt.timezone.utc).date()
    days = int(cfg.get("days_back") or 2)
    rows = []
    for back in range(days, -1, -1):
        d = today - dt.timedelta(days=back)
        say("\n=== %s ===" % d.isoformat())
        if cfg["sources"]["cleanfeeds"].get("enabled", True):
            rows += pull_cleanfeeds(d, cfg)
        if cfg["sources"]["aramente"].get("enabled", True):
            rows += pull_aramente(d, cfg)
    if not rows:
        say("\nnothing came back from any source.")
        return
    say("\n%d postings pulled in total" % len(rows))
    say("\nmatching against your offers")
    per_offer = run_match(rows, cfg, today, con)
    if not per_offer:
        say("  no matches above the score floor (min_score=%s)" % cfg.get("min_score"))
        return
    say("")
    summary, total = write_out(per_offer, today)
    prune(con, cfg.get("retention_days", 60))
    state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state.setdefault("days", {})[today.isoformat()] = {"pulled": len(rows), "matched": total}
    save_json(STATE, state)
    say("\n--- done --- %d matched postings" % total)
    say("files: %s" % os.path.join(OUT, today.isoformat()))


def cmd_search(cfg, args):
    if not args:
        say('usage: job_signals.py search "performance marketing" [--country SK] [--limit 50] [--source cleanfeeds]')
        return
    query = args[0]
    opts = {}
    i = 1
    while i < len(args) - 1:
        if args[i].startswith("--"):
            opts[args[i][2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    limit = int(opts.get("limit") or 30)
    want_cc = (opts.get("country") or "").upper()
    only = (opts.get("source") or "").lower()

    rules = load_rules()
    comps = read_companies()
    found = []
    for sd in cfg.get("search_datasets") or []:
        if only and sd["name"] != only:
            continue
        say("searching %-14s %s" % (sd["name"], sd["dataset"]))
        got = 0
        for offset in (0, 100, 200):
            if got >= limit:
                break
            d = dss("search", {"dataset": sd["dataset"], "config": sd["config"],
                               "split": sd["split"], "query": query,
                               "offset": offset, "length": 100}, cfg, tries=4)
            if not d or not d.get("rows"):
                if offset == 0:
                    say("    no answer from the search index (it may still be loading) - skipped")
                break
            if offset == 0:
                say("    %s total hits" % d.get("num_rows_total"))
            for rr in d["rows"]:
                x = rr.get("row") or {}
                r = row(x.get("company") or x.get("source_slug") or x.get("name") or "",
                        x.get("title"),
                        x.get("location") or x.get("location_city") or
                        (", ".join(x["locations"]) if isinstance(x.get("locations"), list) else ""),
                        x.get("url") or x.get("apply_url") or x.get("job_url") or x.get("career_url"),
                        sd["name"], x.get("posted_at"), x.get("department"),
                        x.get("country_code") or x.get("location_country_code") or x.get("country") or "",
                        x.get("description") or x.get("description_plain") or x.get("description_html") or "")
                if want_cc and r["country"] != want_cc:
                    continue
                hits = match_row(r, rules)
                r["offer"] = hits[0][0]["offer"] if hits else ""
                r["matched_on"] = hits[0][1] if hits else ""
                c = comps.get(r["company"].lower())
                ins, inote = intensity(c)
                r["score"] = (int(hits[0][0].get("weight") or 3) if hits else 0) + ins
                r["hiring_signal"] = inote
                r["careers_url"] = clean((c or {}).get("careers_url"))
                r["open_jobs"] = (c or {}).get("open_jobs") or ""
                r["new_jobs_7d"] = (c or {}).get("new_jobs_7d") or ""
                r["ats"] = clean((c or {}).get("ats"))
                r["why"] = hits[0][0].get("pitch") if hits else ""
                found.append(r)
                got += 1
            time.sleep(1)

    if not found:
        say("\nnothing matched. Try fewer words - the index does not like long phrases.")
        return
    # de-dupe on url
    uniq, seen = [], set()
    for r in found:
        k = r["url"] or (r["company"] + r["title"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    uniq.sort(key=lambda r: -r["score"])
    uniq = uniq[:limit]

    say("\n%d results\n" % len(uniq))
    say("%-22s %-44s %-16s %-6s %s" % ("COMPANY", "TITLE", "LOCATION", "SCORE", "OFFER"))
    for r in uniq:
        say("%-22s %-44s %-16s %-6s %s" % (
            r["company"][:22], r["title"][:44], r["location"][:16], r["score"], r["offer"]))

    os.makedirs(OUT, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")[:40]
    p = os.path.join(OUT, "search_%s_%s.csv" % (slug, dt.date.today().isoformat()))
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(uniq)
    say("\nsaved: %s" % p)


def cmd_company(cfg, args):
    if not args:
        say('usage: job_signals.py company "Acme"')
        return
    name = args[0]
    comps = refresh_companies(cfg)
    key = name.lower()
    hit = comps.get(key)
    if not hit:
        near = [k for k in comps if key in k or k in key][:8]
        if near:
            say("no exact match. Close names: " + ", ".join(near))
        else:
            say("'%s' is not in the company table (it covers ~2k firms). Try: search \"%s\"" % (name, name))
        if not near:
            return
        hit = comps[near[0]]
        say("\nshowing: %s\n" % hit.get("company"))
    say("company        %s" % clean(hit.get("company")))
    say("ats            %s" % clean(hit.get("ats")))
    say("careers        %s" % clean(hit.get("careers_url")))
    say("open roles     %s" % hit.get("open_jobs"))
    say("new in 7d      %s" % hit.get("new_jobs_7d"))
    say("new in 30d     %s" % hit.get("new_jobs_30d"))
    say("remote roles   %s" % hit.get("remote_jobs"))
    say("departments    %s" % clean(hit.get("top_departments"))[:110])
    say("countries      %s" % clean(hit.get("top_countries"))[:110])
    say("seniority      %s" % clean(hit.get("seniority_mix"))[:110])
    say("median sal max %s USD" % (hit.get("median_salary_max_usd") or "-"))
    say("newest role    %s (%s)" % (clean(hit.get("newest_job_title")), clean(hit.get("newest_job_published_at"))[:10]))
    ins, note = intensity(hit)
    say("\nhiring signal  +%d  %s" % (ins, note or "quiet"))
    rules = load_rules()
    titles = clean(hit.get("latest_titles"))
    offers = {}
    for t in titles.split("|"):
        for o, p in match_row(row("", t, "", "", "", ""), rules):
            offers.setdefault(o["offer"], []).append((clean(t)[:50], p))
    if offers:
        say("\noffers their open roles point at")
        for off, ts in sorted(offers.items(), key=lambda kv: -len(kv[1])):
            say("  %-26s %d role(s)  e.g. %s" % (off, len(ts), ts[0][0]))
    else:
        say("\nnothing in their latest titles maps to an offer.")


def cmd_demand(cfg, args):
    cc = (args[0].upper() if args else "SK")
    say("most-posted job titles in %s\n" % cc)
    d = dss("filter", {"dataset": "Jobs-database/job-titles", "config": "default",
                       "split": "train", "where": '"country_iso2"=\'%s\'' % cc,
                       "orderby": '"postings" DESC', "length": 40}, cfg, tries=6)
    if not d or not d.get("rows"):
        say("no answer for %s. The dataset's country coverage is patchy - try US, DE, GB." % cc)
        return
    say("%-56s %8s %8s %s" % ("TITLE", "POSTINGS", "FIRMS", "MONTH"))
    rules = load_rules()
    agg = {}
    for rr in d["rows"]:
        x = rr["row"]
        t = clean(x.get("title"))
        say("%-56s %8s %8s %s" % (t[:56], x.get("postings"), x.get("companies"), x.get("month")))
        for o, p in match_row(row("", t, "", "", "", ""), rules):
            agg[o["offer"]] = agg.get(o["offer"], 0) + int(x.get("postings") or 0)
    say("\ntotal postings in %s that point at an offer" % cc)
    if agg:
        for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
            say("  %-26s %d" % (k, v))
    else:
        say("  none of these titles map to an offer")
    say("\nNote: this dataset counts only what it sampled; treat it as direction, not market size.")


def cmd_offers():
    r = load_json(RULES, DEFAULT_RULES)
    for o in r.get("offers", []):
        n = len(o.get("patterns") or [])
        flag = "" if n else "   <- no patterns yet, skipped in the daily run"
        say("\n%s  (weight %s, %d patterns)%s" % (o.get("offer"), o.get("weight"), n, flag))
        if o.get("pitch"):
            say("  angle: %s" % o["pitch"])
        if n:
            say("  catches: %s" % ", ".join((o["patterns"])[:10]) + (" ..." if n > 10 else ""))
        if o.get("_note"):
            say("  TODO: %s" % o["_note"])
    say("\nedit: %s" % RULES)


def cmd_sources(cfg):
    say("bulk sources used by 'daily'")
    for k, v in (cfg.get("sources") or {}).items():
        say("  [%s] %-11s %s" % ("on " if v.get("enabled") else "off", k, v.get("dataset")))
        if v.get("note"):
            say("        %s" % v["note"])
    say("\ndatasets used by 'search'")
    for sd in cfg.get("search_datasets") or []:
        say("  %-13s %s  (%s/%s)" % (sd["name"], sd["dataset"], sd["config"], sd["split"]))
    say("\nHF token: %s" % ("set" if token(cfg) else "not set (search gets tighter rate limits)"))
    say("country filter: %s" % (cfg.get("countries") or "all countries"))
    say("min score: %s" % cfg.get("min_score"))


def cmd_status(cfg, state, con):
    say("folder:   ", BASE)
    say("last run: ", state.get("last_run") or "never")
    try:
        n = con.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        say("pitched index: %d postings" % n)
    except Exception as e:
        say("pitched index unreadable:", e)
    if os.path.exists(COMPANIES):
        age = (time.time() - os.path.getmtime(COMPANIES)) / 86400.0
        say("company table: %s (%.1f days old)" % (len(read_companies()), age))
    else:
        say("company table: not downloaded yet")
    days = sorted((state.get("days") or {}).items(), reverse=True)[:10]
    if days:
        say("\nrecent runs")
        for d, rec in days:
            say("  %s  pulled %-8s matched %s" % (d, rec.get("pulled"), rec.get("matched")))


def cmd_schedule(cfg, when=None):
    when = when or cfg.get("schedule_time") or "08:15"
    if os.name != "nt":
        say("Windows only. On this machine use cron:")
        say("  15 8 * * *  %s %s daily" % (sys.executable, os.path.abspath(__file__)))
        return
    pyw = sys.executable
    c = os.path.join(os.path.dirname(pyw), "pythonw.exe")
    if os.path.exists(c):
        pyw = c
    import subprocess
    r = subprocess.run(['schtasks', '/Create', '/TN', TASK_NAME, '/TR',
                        '"%s" "%s" daily' % (pyw, os.path.abspath(__file__)),
                        '/SC', 'DAILY', '/ST', when, '/F'], capture_output=True, text=True)
    say((r.stdout or "").strip() or (r.stderr or "").strip())
    if r.returncode:
        say("failed (exit %s) - try an Administrator prompt." % r.returncode)


def cmd_unschedule():
    if os.name != "nt":
        say("nothing to remove")
        return
    import subprocess
    r = subprocess.run(['schtasks', '/Delete', '/TN', TASK_NAME, '/F'],
                       capture_output=True, text=True)
    say((r.stdout or "").strip() or (r.stderr or "").strip() or "removed")


def main(argv):
    cfg = load_config()
    state = load_json(STATE, {})
    cmd = (argv[0] if argv else "daily").lower()
    rest = argv[1:]

    if cmd == "offers":
        return cmd_offers()
    if cmd == "sources":
        return cmd_sources(cfg)
    if cmd == "schedule":
        return cmd_schedule(cfg, rest[0] if rest else None)
    if cmd == "unschedule":
        return cmd_unschedule()
    if cmd == "search":
        return cmd_search(cfg, rest)
    if cmd == "company":
        return cmd_company(cfg, rest)
    if cmd == "demand":
        return cmd_demand(cfg, rest)
    if cmd == "companies":
        refresh_companies(cfg, force=True)
        return

    con = open_index()
    try:
        if cmd == "daily":
            cmd_daily(cfg, state, con)
        elif cmd == "status":
            cmd_status(cfg, state, con)
        elif cmd == "match":
            day = rest[0] if rest else dt.datetime.now(dt.timezone.utc).date().isoformat()
            date = dt.date.fromisoformat(day)
            rows = []
            p1 = os.path.join(RAW, "cleanfeeds_%s.csv.gz" % day)
            if os.path.exists(p1):
                with gzip.open(p1, "rt", encoding="utf-8", errors="replace") as f:
                    for r in csv.DictReader(f):
                        rows.append(row(r.get("company"), r.get("title"), r.get("location"),
                                        r.get("url"), "cleanfeeds", r.get("posted_at"),
                                        r.get("department")))
            p2 = os.path.join(RAW, "aramente_%s.jsonl.gz" % day)
            if os.path.exists(p2):
                with gzip.open(p2, "rt", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            o = json.loads(line)
                        except Exception:
                            continue
                        if (o.get("event") or "new") != "new" or not o.get("title"):
                            continue
                        rr = row((o.get("company_slug") or "").replace("-", " "),
                                 o.get("title"), o.get("location"), o.get("url"),
                                 "aramente", o.get("posted_at"))
                        rr["feed"] = (o.get("source") or "").lower()
                        rows.append(rr)
            if not rows:
                say("nothing cached for %s - run 'daily' first" % day)
                return
            say("re-matching %d cached postings for %s" % (len(rows), day))
            con.execute("DELETE FROM seen WHERE d=?", (day,))
            con.commit()
            per = run_match(rows, cfg, date, con)
            if per:
                write_out(per, date)
        else:
            say(__doc__)
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        say("\nstopped")
