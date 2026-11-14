#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nrd_feed.py - Newly Registered Domains feed for Lead Studio.

Downloads the daily list of newly registered domains from several free
sources, merges and de-duplicates them, keeps a rolling "already seen"
index so you can tell what is genuinely new, and cuts the raw firehose
(~350-400k domains a day) into small, usable slices: Slovak/Czech
domains, keyword matches, per-TLD lists.

Stdlib only. No pip install. Python 3.8+.

Commands
    daily                 normal run: today + any missed closed days, merge, slice
    backfill N            fetch the last N closed days (default 7)
    day YYYY-MM-DD        fetch one specific day
    slice [YYYY-MM-DD]    re-run the filters over a day already on disk (no network)
    status                what is on disk, index size, last run
    sources               show configured sources and whether they are usable
    schedule [HH:MM]      register a Windows daily scheduled task (default 07:30)
    unschedule            remove that scheduled task

Outputs land in <this folder>/NRD/ :
    config.json           sources, slices, retention - edit this
    state.json            ETags, last run, per-day counts
    index.sqlite          rolling seen-domain index (first_seen per domain)
    raw/                  files exactly as downloaded (.txt.gz)
    daily/<date>.txt      merged, de-duplicated union for that UTC day
    out/<date>/           the slices you actually work from + summary

Data sources and attribution
    smet.cz        CC BY 4.0 - requires visible credit to smet.cz, linked to
                   https://smet.cz/ wherever you surface this data. See
                   NRD/ATTRIBUTION.txt, which this script writes and keeps.
    domainkits.com free account API key (dk_...), domains_only, 3-day history
                   on the free plan. Set the key in config.json or the
                   DOMAINKITS_API_KEY environment variable.
    whoisextractor.com
                   account API key, pid=10 Daily Registered Domains,
                   ~240k/day over 1601 TLDs, 4 days of history. Set the key in
                   config.json or WHOISEXTRACTOR_API_KEY. Their dates run on
                   India time, so a day can sit a few hours out of step.
"""

import base64
import gzip
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
import datetime as dt
import urllib.error
import urllib.parse
import urllib.request

APP = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.environ.get("NRD_DIR") or os.path.join(APP, "NRD"))
RAW = os.path.join(BASE, "raw")
DAILY = os.path.join(BASE, "daily")
OUT = os.path.join(BASE, "out")
CONFIG = os.path.join(BASE, "config.json")
STATE = os.path.join(BASE, "state.json")
INDEX = os.path.join(BASE, "index.sqlite")
ATTRIB = os.path.join(BASE, "ATTRIBUTION.txt")

UA = "LeadStudio-NRD/1.0 (daily newly-registered-domain feed; lead research)"
TASK_NAME = "LeadStudio NRD Daily"

ATTRIBUTION_TEXT = """\
Data sources used by nrd_feed.py
================================

smet.cz - Newly registered domains feed
  https://smet.cz/
  Licence: CC BY 4.0. Free for commercial and non-commercial use, with
  VISIBLE attribution to smet.cz, linked to https://smet.cz/ where the
  medium allows. Redistribution is permitted with that attribution
  carried along. Credit smet.cz in your product UI, documentation or
  README. Do not put smet.cz in User-Agent strings.

domainkits.com - Newly registered domains download
  https://domainkits.com/
  Free daily list / API, requires an account API key (dk_...).
  Check their terms before redistributing.

If any domain in these lists belongs to someone who wants it removed,
both providers honour removal requests.
"""

DEFAULT_CONFIG = {
    "retention_days": 90,
    "keep_raw_days": 14,
    "sources": {
        "smet": {
            "enabled": True,
            "note": "No key, no signup. 90 days of history, ~390k domains/day, 1647 TLDs."
        },
        "domainkits": {
            "enabled": False,
            "api_key": "",
            "tld": "",
            "note": "Free account key (dk_...). Set api_key here or DOMAINKITS_API_KEY env var, then set enabled=true. Free plan: domains_only, 3 days of history, each file downloadable 10x."
        },
        "whoisextractor": {
            "enabled": False,
            "api_key": "",
            "pid": 10,
            "note": "Key from whoisextractor.com (or WHOISEXTRACTOR_API_KEY env var). pid=10 is Daily Registered Domains, ~240k/day over 1601 TLDs, no WHOIS fields. Only 4 days of history, and their dates run on India time (GMT+5:30), so a day can be a few hours out of step with the others. Only one API key is active at a time."
        }
    },
    "slices": [
        {"name": "sk_cz", "tlds": ["sk", "cz"],
         "note": "Brand new Slovak/Czech domains - freshest possible Modulario prospects."},
        {"name": "neighbours", "tlds": ["at", "de", "pl", "hu"]},
        {"name": "eu_generic", "tlds": ["eu", "com", "shop", "store", "online", "biz"],
         "keywords": ["sk", "cz", "slovak", "czech", "praha", "brno", "bratislava", "kosice", "nitra", "zilina"],
         "note": "Generic TLDs whose name hints at a SK/CZ business."},
        {"name": "manufacturing", "keywords": [
            "kov", "metal", "stal", "steel", "zelez", "iron", "stav", "bau", "constr",
            "vyroba", "vyrob", "manufact", "fabrik", "industr", "prumysl", "priemysl",
            "cnc", "zvar", "weld", "obrab", "machin", "sklad", "logistic", "doprav"],
         "note": "Modulario ICP: manufacturing / construction / logistics SMBs."},
        {"name": "gates_fences", "keywords": [
            "brana", "brany", "vejar", "plot", "ploty", "gate", "fence", "zabradl",
            "oplot", "zamecn", "kovovyroba", "garaz", "garage", "pergol", "zahrad"],
         "note": "Steel gate / fence business: new competitors and new-build homeowners."},
        {"name": "erp_saas", "keywords": [
            "erp", "mes", "wms", "crm", "saas", "cloud", "automat", "workflow",
            "digital", "softw", "saas", "ai-", "-ai", "agent"],
         "note": "Software/automation buyers and competitors."}
    ],
    "exclude_keywords": [],
    "min_sld_len": 3,
    "schedule_time": "07:30"
}

TWO_LEVEL = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk", "sch.uk",
    "com.au", "net.au", "org.au", "com.br", "net.br", "org.br",
    "co.jp", "or.jp", "ne.jp", "co.nz", "net.nz", "org.nz",
    "co.za", "org.za", "com.mx", "com.ar", "com.tr", "com.cn",
    "com.pl", "net.pl", "org.pl", "com.ua", "co.in", "net.in",
    "org.in", "com.sg", "com.hk", "co.kr", "com.tw", "com.my",
    "co.il", "com.es", "com.pt", "com.ro", "com.hr", "com.gr",
}


# ---------------------------------------------------------------- helpers

def say(*a):
    msg = " ".join(str(x) for x in a)
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def ensure_dirs():
    for d in (BASE, RAW, DAILY, OUT):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(ATTRIB):
        with open(ATTRIB, "w", encoding="utf-8") as f:
            f.write(ATTRIBUTION_TEXT)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
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
        return json.loads(json.dumps(DEFAULT_CONFIG))
    cfg = load_json(CONFIG, DEFAULT_CONFIG)
    # fill in anything a newer version of this script expects
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    for name, src in DEFAULT_CONFIG["sources"].items():
        cfg.setdefault("sources", {}).setdefault(name, src)
    return cfg


def utc_today():
    return dt.datetime.now(dt.timezone.utc).date()


def fetch(url, headers=None, timeout=180, retries=3, state_key=None, state=None):
    """GET a URL. Returns (bytes, final_headers) or (None, None) on 304/failure."""
    hdrs = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if headers:
        hdrs.update(headers)
    if state_key and state is not None:
        prev = state.get("etags", {}).get(state_key) or {}
        if prev.get("etag"):
            hdrs["If-None-Match"] = prev["etag"]
        elif prev.get("last_modified"):
            hdrs["If-Modified-Since"] = prev["last_modified"]

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding", "").lower() == "gzip" and not url.endswith(".gz"):
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                if state_key and state is not None:
                    state.setdefault("etags", {})[state_key] = {
                        "etag": r.headers.get("ETag"),
                        "last_modified": r.headers.get("Last-Modified"),
                        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    }
                return data, dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                say("    not changed since last run (304)")
                return None, None
            last_err = "HTTP %s %s" % (e.code, e.reason)
            if e.code in (400, 401, 403, 404, 429):
                break
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
        if attempt < retries:
            time.sleep(2 * attempt)
    say("    failed:", last_err)
    return None, None


def to_text(data, url=""):
    """Decompress whatever came back into decoded text."""
    if data is None:
        return ""
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except Exception:
            pass
    elif data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = [n for n in z.namelist() if not n.endswith("/")]
                data = b"\n".join(z.read(n) for n in names)
        except Exception:
            pass
    return data.decode("utf-8", "replace")


DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,252}\.[a-z0-9\-]{2,63}$")


def parse_domains(text):
    """Pull clean lowercase domains out of txt / csv / json content."""
    t = text.lstrip()
    if t[:1] in "{[":
        try:
            obj = json.loads(t)
            if isinstance(obj, dict):
                obj = obj.get("domains") or obj.get("data") or []
            text = "\n".join(str(x) for x in obj)
        except Exception:
            pass
    out = set()
    for line in text.splitlines():
        line = line.strip().strip('",').lower()
        if not line or line.startswith("#"):
            continue
        if "," in line or ";" in line or "\t" in line:
            line = re.split(r"[,;\t]", line)[0].strip().strip('"').lower()
        if line in ("domain", "domain_name", "domainname", "name"):
            continue
        if line.startswith("http://") or line.startswith("https://"):
            line = line.split("://", 1)[1].split("/")[0]
        line = line.rstrip(".")
        if DOMAIN_RE.match(line):
            out.add(line)
    return out


def split_domain(d):
    """Return (sld, tld) using a small two-level suffix list."""
    parts = d.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LEVEL:
        return parts[-3], ".".join(parts[-2:])
    return parts[-2] if len(parts) >= 2 else d, parts[-1]


# ---------------------------------------------------------------- sources

def src_smet(date, is_today, state):
    """smet.cz - no key needed. Returns a set of domains."""
    if is_today:
        url = "https://smet.cz/nrd/data/today.txt.gz"
        key = "smet:today"
    else:
        url = "https://smet.cz/nrd/data/daily/%s.txt.gz" % date.isoformat()
        key = "smet:%s" % date.isoformat()
    say("  smet.cz ->", url)
    data, _ = fetch(url, state_key=key, state=state)
    if data is None:
        return set(), None
    raw_path = os.path.join(RAW, "smet_%s.txt.gz" % date.isoformat())
    try:
        with open(raw_path, "wb") as f:
            f.write(data if data[:2] == b"\x1f\x8b" else gzip.compress(data))
    except Exception as e:
        say("    could not keep the raw file:", e)
    doms = parse_domains(to_text(data, url))
    say("    %d domains" % len(doms))
    return doms, raw_path


def src_domainkits(date, is_today, state, cfg):
    """domainkits.com (the ABTdomain feed). Needs a free API key."""
    sc = cfg["sources"]["domainkits"]
    key = (sc.get("api_key") or os.environ.get("DOMAINKITS_API_KEY") or "").strip()
    if not key:
        say("  domainkits: no API key set - skipped")
        return set(), None
    url = "https://premium-api.domainkits.com/api/v1/nrds/download?type=domains_only"
    if sc.get("tld"):
        url += "&tld=" + str(sc["tld"]).lstrip(".")
    if not is_today:
        url += "&date=" + date.isoformat()
    say("  domainkits ->", url)
    data, _ = fetch(url, headers={"Authorization": "Bearer " + key})
    if data is None:
        return set(), None
    raw_path = os.path.join(RAW, "domainkits_%s.txt.gz" % date.isoformat())
    try:
        with open(raw_path, "wb") as f:
            f.write(data if data[:2] == b"\x1f\x8b" else gzip.compress(data))
    except Exception as e:
        say("    could not keep the raw file:", e)
    doms = parse_domains(to_text(data, url))
    say("    %d domains" % len(doms))
    return doms, raw_path


def src_whoisextractor(date, is_today, state, cfg):
    """whoisextractor.com - Daily Registered Domains (pid=10). Needs an account key."""
    sc = cfg["sources"].get("whoisextractor", {})
    key = (sc.get("api_key") or os.environ.get("WHOISEXTRACTOR_API_KEY") or "").strip()
    if not key:
        say("  whoisextractor: no API key set - skipped")
        return set(), None
    pid = str(sc.get("pid") or 10)
    url = "https://get.whoisextractor.com/?api=%s&pid=%s" % (urllib.parse.quote(key, safe=""), pid)
    if not is_today:
        url += "&date=" + date.isoformat()
    # never log the url itself - the key is in it
    say("  whoisextractor -> get.whoisextractor.com pid=%s%s" % (
        pid, "" if is_today else " date=" + date.isoformat()))
    data, _ = fetch(url, headers={"X-API-Key": key})
    if data is None:
        return set(), None
    if data[:1] in (b"{", b"["):
        say("    the service sent an error instead of a file:", to_text(data)[:300])
        return set(), None
    raw_path = os.path.join(RAW, "whoisextractor_%s.txt.gz" % date.isoformat())
    doms = parse_domains(to_text(data, url))
    if not doms:
        say("    the file held no usable domains")
        return set(), None
    try:
        with open(raw_path, "wb") as f:
            f.write(gzip.compress("\n".join(sorted(doms)).encode("utf-8")))
    except Exception as e:
        say("    could not keep the raw file:", e)
    say("    %d domains" % len(doms))
    return doms, raw_path


# ---------------------------------------------------------------- index

def open_index():
    ensure_dirs()
    con = sqlite3.connect(INDEX, timeout=60)
    # WAL is faster, but some network/virtual drives cannot do it - fall back quietly.
    for mode in ("WAL", "DELETE"):
        try:
            con.execute("PRAGMA journal_mode=%s" % mode)
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("CREATE TABLE IF NOT EXISTS seen (domain TEXT PRIMARY KEY, first_seen TEXT NOT NULL)")
            con.execute("CREATE INDEX IF NOT EXISTS seen_day ON seen(first_seen)")
            con.commit()
            return con
        except sqlite3.OperationalError as e:
            last = e
            continue
    raise SystemExit(
        "The domain index could not be opened at %s (%s).\n"
        "If this folder sits on a network or synced drive, set NRD_DIR to a local path,\n"
        "for example:  set NRD_DIR=C:\\NRD   then run this script again." % (INDEX, last))


def index_add(con, domains, date):
    """Insert domains, return only the ones that were genuinely new."""
    day = date.isoformat()
    cur = con.cursor()
    fresh = []
    batch = []
    for d in domains:
        batch.append((d, day))
        if len(batch) >= 20000:
            fresh += _flush(cur, batch)
            batch = []
    if batch:
        fresh += _flush(cur, batch)
    con.commit()
    return fresh


def _flush(cur, batch):
    existing = set()
    qs = ",".join("?" * len(batch))
    cur.execute("SELECT domain FROM seen WHERE domain IN (%s)" % qs, [b[0] for b in batch])
    for (d,) in cur.fetchall():
        existing.add(d)
    new = [b for b in batch if b[0] not in existing]
    cur.executemany("INSERT OR IGNORE INTO seen (domain, first_seen) VALUES (?,?)", new)
    return [b[0] for b in new]


def index_prune(con, retention_days):
    cutoff = (utc_today() - dt.timedelta(days=int(retention_days))).isoformat()
    cur = con.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,))
    con.commit()
    if cur.rowcount and cur.rowcount > 0:
        say("index: dropped %d entries older than %s" % (cur.rowcount, cutoff))


# ---------------------------------------------------------------- slicing

def apply_slices(date, domains, cfg, fresh_set):
    """Write the usable cuts for one day. Returns a summary dict."""
    day = date.isoformat()
    outdir = os.path.join(OUT, day)
    os.makedirs(outdir, exist_ok=True)

    excl = [k.lower() for k in cfg.get("exclude_keywords") or []]
    min_len = int(cfg.get("min_sld_len") or 0)

    rows = []
    tld_counts = {}
    for d in domains:
        sld, tld = split_domain(d)
        tld_counts[tld] = tld_counts.get(tld, 0) + 1
        rows.append((d, sld, tld))

    summary = {"date": day, "total": len(domains), "new_to_index": len(fresh_set), "slices": {}}

    for spec in cfg.get("slices") or []:
        name = spec.get("name") or "slice"
        tlds = set(t.lower().lstrip(".") for t in (spec.get("tlds") or []))
        kws = [k.lower() for k in (spec.get("keywords") or [])]
        hits = []
        for d, sld, tld in rows:
            if tlds and tld not in tlds:
                continue
            matched = ""
            if kws:
                for k in kws:
                    if k in sld:
                        matched = k
                        break
                if not matched:
                    continue
            if min_len and len(sld) < min_len:
                continue
            if excl and any(x in d for x in excl):
                continue
            hits.append((d, sld, tld, matched))

        hits.sort()
        txt = os.path.join(outdir, name + ".txt")
        csvf = os.path.join(outdir, name + ".csv")
        with open(txt, "w", encoding="utf-8") as f:
            for d, _s, _t, _m in hits:
                f.write(d + "\n")
        with open(csvf, "w", encoding="utf-8", newline="") as f:
            f.write("domain,sld,tld,matched_keyword,first_seen,is_new,sld_len,hyphens,has_digits\n")
            for d, s, t, m in hits:
                f.write("%s,%s,%s,%s,%s,%s,%d,%d,%s\n" % (
                    d, s, t, m, day, "1" if d in fresh_set else "0",
                    len(s), s.count("-"), "1" if any(c.isdigit() for c in s) else "0"))
        summary["slices"][name] = len(hits)
        say("  slice %-14s %7d  -> %s" % (name, len(hits), os.path.basename(csvf)))

    # new-to-index list and top TLDs
    with open(os.path.join(outdir, "new_today.txt"), "w", encoding="utf-8") as f:
        for d in sorted(fresh_set):
            f.write(d + "\n")
    top = sorted(tld_counts.items(), key=lambda kv: -kv[1])[:40]
    summary["top_tlds"] = top
    with open(os.path.join(outdir, "tld_counts.csv"), "w", encoding="utf-8") as f:
        f.write("tld,count\n")
        for t, c in sorted(tld_counts.items(), key=lambda kv: -kv[1]):
            f.write("%s,%d\n" % (t, c))

    save_json(os.path.join(outdir, "summary.json"), summary)
    with open(os.path.join(outdir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("Newly registered domains - %s (UTC)\n" % day)
        f.write("=" * 46 + "\n")
        f.write("total merged      %d\n" % summary["total"])
        f.write("new to the index  %d\n\n" % summary["new_to_index"])
        f.write("slices\n")
        for k, v in summary["slices"].items():
            f.write("  %-16s %d\n" % (k, v))
        f.write("\ntop TLDs\n")
        for t, c in top[:15]:
            f.write("  .%-12s %d\n" % (t, c))
        f.write("\nData: smet.cz (CC BY 4.0, https://smet.cz/) and domainkits.com.\n")
        f.write("See NRD/ATTRIBUTION.txt - credit smet.cz visibly if you surface this data.\n")
    return summary


# ---------------------------------------------------------------- run

def run_day(date, cfg, state, con, is_today=None):
    if is_today is None:
        is_today = (date == utc_today())
    say("\n=== %s%s ===" % (date.isoformat(), " (today, still collecting)" if is_today else ""))

    merged = set()
    got_any = False
    if cfg["sources"].get("smet", {}).get("enabled", True):
        doms, _ = src_smet(date, is_today, state)
        if doms:
            merged |= doms
            got_any = True
    if cfg["sources"].get("domainkits", {}).get("enabled", False):
        doms, _ = src_domainkits(date, is_today, state, cfg)
        if doms:
            merged |= doms
            got_any = True
    if cfg["sources"].get("whoisextractor", {}).get("enabled", False):
        doms, _ = src_whoisextractor(date, is_today, state, cfg)
        if doms:
            merged |= doms
            got_any = True

    if not merged:
        if got_any:
            say("  nothing new to merge for this day")
        else:
            say("  no source returned data for this day")
        return None

    say("  merged union: %d unique domains" % len(merged))
    daily_path = os.path.join(DAILY, "%s.txt" % date.isoformat())
    with open(daily_path, "w", encoding="utf-8") as f:
        for d in sorted(merged):
            f.write(d + "\n")

    fresh = set(index_add(con, merged, date))
    say("  new to the index: %d" % len(fresh))

    summary = apply_slices(date, merged, cfg, fresh)
    state.setdefault("days", {})[date.isoformat()] = {
        "total": len(merged), "new": len(fresh),
        "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "closed": not is_today,
    }
    return summary


def cmd_daily(cfg, state, con):
    today = utc_today()
    done = state.get("days", {})
    todo = []
    # catch up on closed days we never captured, up to 7 back
    for back in range(7, 0, -1):
        d = today - dt.timedelta(days=back)
        rec = done.get(d.isoformat())
        if not rec or not rec.get("closed"):
            todo.append((d, False))
    todo.append((today, True))

    results = []
    for d, is_today in todo:
        r = run_day(d, cfg, state, con, is_today=is_today)
        if r:
            results.append(r)
        save_json(STATE, state)
        time.sleep(2)

    index_prune(con, cfg.get("retention_days", 90))
    prune_raw(cfg.get("keep_raw_days", 14))
    state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    save_json(STATE, state)

    if results:
        last = results[-1]
        say("\n--- done ---")
        say("latest day %s: %d domains, %d new to index" % (last["date"], last["total"], last["new_to_index"]))
        for k, v in last["slices"].items():
            say("  %-16s %d" % (k, v))
        say("\nfiles: %s" % os.path.join(OUT, last["date"]))
    else:
        say("\nnothing was downloaded. Run 'sources' to check your setup.")


def prune_raw(keep_days):
    cutoff = time.time() - int(keep_days) * 86400
    n = 0
    for name in os.listdir(RAW) if os.path.isdir(RAW) else []:
        p = os.path.join(RAW, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
                n += 1
        except Exception:
            pass
    if n:
        say("raw: removed %d file(s) older than %s days" % (n, keep_days))


def cmd_status(cfg, state, con):
    say("folder:        ", BASE)
    say("last run:      ", state.get("last_run") or "never")
    try:
        n = con.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        lo, hi = con.execute("SELECT MIN(first_seen), MAX(first_seen) FROM seen").fetchone()
        say("index:          %d domains, %s .. %s" % (n, lo, hi))
    except Exception as e:
        say("index:          unreadable (%s)" % e)
    days = sorted((state.get("days") or {}).items(), reverse=True)[:14]
    if days:
        say("\nrecent days")
        for d, rec in days:
            say("  %s  total %-9s new %-9s %s" % (
                d, rec.get("total"), rec.get("new"), "closed" if rec.get("closed") else "today"))
    say("\nretention: %s days   raw kept: %s days" % (
        cfg.get("retention_days"), cfg.get("keep_raw_days")))


def cmd_sources(cfg):
    say("configured sources")
    for name, sc in (cfg.get("sources") or {}).items():
        on = "on " if sc.get("enabled") else "off"
        extra = ""
        if name in ("domainkits", "whoisextractor"):
            env = {"domainkits": "DOMAINKITS_API_KEY",
                   "whoisextractor": "WHOISEXTRACTOR_API_KEY"}[name]
            key = (sc.get("api_key") or os.environ.get(env) or "").strip()
            extra = "  key: %s" % (("set, ends ..." + key[-4:]) if key
                                   else "MISSING - add it to config.json")
        say("  [%s] %-12s%s" % (on, name, extra))
        if sc.get("note"):
            say("        %s" % sc["note"])
    say("\nedit: %s" % CONFIG)


def cmd_schedule(cfg, when=None):
    when = when or cfg.get("schedule_time") or "07:30"
    if os.name != "nt":
        say("Scheduling is Windows-only here. On this machine, add a cron entry instead:")
        say("  30 7 * * *  %s %s daily" % (sys.executable, os.path.abspath(__file__)))
        return
    pyw = sys.executable
    cand = os.path.join(os.path.dirname(pyw), "pythonw.exe")
    if os.path.exists(cand):
        pyw = cand
    tr = '"%s" "%s" daily' % (pyw, os.path.abspath(__file__))
    cmd = ['schtasks', '/Create', '/TN', TASK_NAME, '/TR', tr,
           '/SC', 'DAILY', '/ST', when, '/F']
    say("registering scheduled task '%s' at %s daily" % (TASK_NAME, when))
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True)
    say((r.stdout or "").strip() or (r.stderr or "").strip())
    if r.returncode == 0:
        say("done. Check it with:  schtasks /Query /TN \"%s\"" % TASK_NAME)
    else:
        say("that failed (exit %s). Try running this from an Administrator prompt." % r.returncode)


def cmd_unschedule():
    if os.name != "nt":
        say("Nothing to remove (not Windows).")
        return
    import subprocess
    r = subprocess.run(['schtasks', '/Delete', '/TN', TASK_NAME, '/F'],
                       capture_output=True, text=True)
    say((r.stdout or "").strip() or (r.stderr or "").strip() or "removed")


def main(argv):
    cfg = load_config()
    state = load_json(STATE, {})
    cmd = (argv[0] if argv else "daily").lower()

    if cmd in ("schedule",):
        return cmd_schedule(cfg, argv[1] if len(argv) > 1 else None)
    if cmd in ("unschedule",):
        return cmd_unschedule()
    if cmd in ("sources",):
        return cmd_sources(cfg)

    con = open_index()
    try:
        if cmd == "daily":
            cmd_daily(cfg, state, con)
        elif cmd == "status":
            cmd_status(cfg, state, con)
        elif cmd == "backfill":
            n = int(argv[1]) if len(argv) > 1 else 7
            today = utc_today()
            for back in range(n, 0, -1):
                run_day(today - dt.timedelta(days=back), cfg, state, con, is_today=False)
                save_json(STATE, state)
                time.sleep(2)
            index_prune(con, cfg.get("retention_days", 90))
            save_json(STATE, state)
        elif cmd == "day":
            if len(argv) < 2:
                say("usage: nrd_feed.py day YYYY-MM-DD")
                return
            d = dt.date.fromisoformat(argv[1])
            run_day(d, cfg, state, con, is_today=(d == utc_today()))
            save_json(STATE, state)
        elif cmd == "slice":
            day = argv[1] if len(argv) > 1 else None
            if not day:
                have = sorted(f[:-4] for f in os.listdir(DAILY) if f.endswith(".txt"))
                if not have:
                    say("no daily files on disk yet - run 'daily' first")
                    return
                day = have[-1]
            p = os.path.join(DAILY, day + ".txt")
            if not os.path.exists(p):
                say("not on disk:", p)
                return
            with open(p, encoding="utf-8") as f:
                doms = set(x.strip() for x in f if x.strip())
            say("re-slicing %s (%d domains)" % (day, len(doms)))
            rows = con.execute("SELECT domain FROM seen WHERE first_seen=?", (day,)).fetchall()
            apply_slices(dt.date.fromisoformat(day), doms, cfg, set(r[0] for r in rows))
        else:
            say(__doc__)
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        say("\nstopped")
