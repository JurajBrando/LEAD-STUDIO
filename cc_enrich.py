"""
cc_enrich.py - the domains in your lead database that have nobody to write to,
and a way to find their contacts in Common Crawl without crawling the web yourself.

  python cc_enrich.py gaps [--limit N] [--tld sk,cz] [--rebuild]
        Reads LEAD_INDEX/leads.parquet, keeps the domains where no row has an email
        or a phone, and writes CC_ENRICH/gaps.csv. Then checks how many of them
        Common Crawl already holds a page for.

  python cc_enrich.py run [--par 40] [--limit N] [--source hf|s3|https]
        Downloads just those domains' home and contact pages out of the Common Crawl
        archives (a byte range per page, not a crawl), reads the addresses and phone
        numbers off them, and writes CC_ENRICH/enriched_<date>.csv.
        Resumable: a domain already done is never fetched twice.

  python cc_enrich.py status      where it stands
  python cc_enrich.py export      one merged CC_ENRICH/contacts_found.csv, newest first

The heavy lifting - the page index, the fetcher, the parser - is your own
cc_bulk_scrape.py. This only aims it at the domains you are missing.
"""
import os, re, sys, csv, glob, json, time, asyncio, argparse, datetime

csv.field_size_limit(10 ** 9)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(HERE))
OUT = os.path.join(ROOT, "CC_ENRICH")
GAPS = os.path.join(OUT, "gaps.csv")
WORK = os.path.join(OUT, "work.parquet")
LEDGER = os.path.join(OUT, "_done_domains.txt")
SETTINGS = os.path.join(HERE, "lead_studio_settings.json")
LEADS = os.path.join(ROOT, "LEAD_INDEX", "leads.parquet")
STRIP = re.compile(r"^\s*(?:https?://)?(?:www\.)?", re.I)


def say(o):
    print(json.dumps(o, ensure_ascii=False, indent=1), flush=True)
    sys.exit(0 if o.get("ok", True) else 1)


def settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def cc_dir():
    """Where cc_bulk_scrape.py and its cc_pages index live."""
    s = str(settings().get("cc_dir") or "").strip()
    if s and os.path.exists(os.path.join(s, "cc_bulk_scrape.py")):
        return s
    for c in sorted(glob.glob(os.path.join(ROOT, "_CLEAN_*")), reverse=True) + [ROOT]:
        if os.path.exists(os.path.join(c, "cc_bulk_scrape.py")):
            return c
    return ""


def load_cb():
    d = cc_dir()
    if not d:
        say({"ok": False, "error": "I cannot find cc_bulk_scrape.py. Put the folder's path in "
                                   "lead_studio_settings.json as cc_dir."})
    sys.path.insert(0, d)
    try:
        import cc_bulk_scrape as cb
    except Exception as e:
        say({"ok": False, "error": "cc_bulk_scrape.py would not load: %s" % e, "folder": d})
    return cb, d


def duck():
    try:
        import duckdb
    except ImportError:
        say({"ok": False, "error": "duckdb is not installed. Run: pip install duckdb"})
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")
    return con


def pages_glob(d):
    """The Common Crawl page index: domain, page_type, warc file, offset, length.
    A scan written by `scan` into CC_ENRICH\cc_pages is used first, then the pre-built one."""
    for p in (os.path.join(OUT, "cc_pages", "*.parquet"),
              os.path.join(d, "cc_pages", "*.parquet"), os.path.join(d, "cc_pages", "**", "*.parquet")):
        if glob.glob(p, recursive=True):
            return p.replace("\\", "/")
    return ""


# ------------------------------------------------------------------ scan the Common Crawl columnar index
IDXOUT = os.path.join(OUT, "cc_pages")
SCANDB = os.path.join(OUT, "scan.duckdb")
CONTACT_RX = ("(contact|contacts|contact-us|kontakt|kontakty|kontaktai|impressum|imprint|about|about-us|"
              "o-nas|onas|o-firme|ueber-uns|uber-uns|team|support|customer-service|help|legal|privacy)")
PATHS_TRIES = ("cc-index-table.paths/cc-index-table.paths", "cc-index-table.paths",
               "../cc-index-table.paths/cc-index-table.paths")


def index_dir_setting():
    return str(settings().get("cc_index_dir") or "").strip()


def index_files_for(source, idx):
    """The 300 columnar-index files to read: local copies, or their URLs at data.commoncrawl.org."""
    if source == "local":
        d = (idx or index_dir_setting()).strip().strip('"')
        if not d or not os.path.isdir(d):
            say({"ok": False, "error": "No local index folder. Give the folder that holds the "
                                       "part-00000-....c000.zstd.parquet files (for example M:\\index), "
                                       "or save it in lead_studio_settings.json as cc_index_dir."})
        f = sorted(glob.glob(os.path.join(d, "*.parquet")))
        if not f:
            say({"ok": False, "error": "No .parquet files in %s" % d})
        return f, d
    base = "https://data.commoncrawl.org/"
    for root in (cc_dir() or ROOT, ROOT, os.path.dirname(ROOT)):
        for t in PATHS_TRIES:
            pf = os.path.normpath(os.path.join(root, t))
            if os.path.isfile(pf):
                with open(pf, encoding="utf-8") as fh:
                    rel = [x.strip() for x in fh if "/subset=warc/" in x]
                if rel:
                    return [base + r for r in rel], pf
    say({"ok": False, "error": "For the live index I need the crawl's cc-index-table.paths file "
                               "(the list of the 300 index parts). Download it from "
                               "https://data.commoncrawl.org/crawl-data/CC-MAIN-<crawl>/cc-index-table.paths.gz, "
                               "unpack it next to the workspace, then run this again."})


def step_scan(source="local", idx="", limit=0, fresh=False):
    """Run the gap domains straight against the Common Crawl columnar index - the local copy of the
    300 index parts, or the same files live over HTTPS - and write a cc_pages index for them."""
    if not os.path.exists(GAPS):
        say({"ok": False, "error": "No gap list yet - press Build the list first."})
    files, where_from = index_files_for(source, idx)
    os.makedirs(IDXOUT, exist_ok=True)
    if fresh:
        for f in (SCANDB, SCANDB + ".wal"):
            if os.path.exists(f):
                os.remove(f)
    try:
        import duckdb as _d
    except ImportError:
        say({"ok": False, "error": "duckdb is not installed. Run: pip install duckdb"})
    con = _d.connect(SCANDB)
    con.execute("PRAGMA memory_limit='6GB'")
    os.makedirs(os.path.join(OUT, "_tmp"), exist_ok=True)
    con.execute("PRAGMA temp_directory='%s'" % os.path.join(OUT, "_tmp").replace("\\", "/"))
    con.execute("PRAGMA preserve_insertion_order=false")
    if source != "local":
        try:
            con.execute("INSTALL httpfs"); con.execute("LOAD httpfs")
        except Exception as e:
            say({"ok": False, "error": "The live index needs DuckDB's httpfs extension: %s" % e})
    lim = " LIMIT %d" % int(limit) if limit else ""
    con.execute("CREATE OR REPLACE TABLE gaps AS SELECT DISTINCT lower(domain) AS domain FROM "
                "(SELECT domain FROM read_csv('%s', header=true, all_varchar=true)%s)"
                % (GAPS.replace("\\", "/"), lim))
    ngaps = con.execute("SELECT count(*) FROM gaps").fetchone()[0]
    con.execute("""CREATE TABLE IF NOT EXISTS hits (domain VARCHAR, host VARCHAR, url VARCHAR,
                   page_type VARCHAR, warc_filename VARCHAR, off BIGINT, len BIGINT)""")
    con.execute("CREATE TABLE IF NOT EXISTS done_files (file VARCHAR PRIMARY KEY, hits BIGINT, at TIMESTAMP)")
    done = {r[0] for r in con.execute("SELECT file FROM done_files").fetchall()}
    todo = [f for f in files if f not in done]
    print("%d gap domains, %d index parts (%d already read) from %s"
          % (ngaps, len(files), len(files) - len(todo), where_from), flush=True)

    # the index's own column names, taken from the first part we read
    probe = (todo or files)[0]
    cols = {r[0].lower() for r in con.execute("DESCRIBE SELECT * FROM read_parquet('%s') LIMIT 0"
                                              % probe.replace("\\", "/")).fetchall()}
    dom = "url_host_registered_domain" if "url_host_registered_domain" in cols else "url_host_name"
    host = "url_host_name" if "url_host_name" in cols else dom
    mime = next((c for c in ("content_mime_detected", "content_mime_type") if c in cols), "")
    status = "fetch_status" if "fetch_status" in cols else ""
    off = next((c for c in ("warc_record_offset", "offset", "off") if c in cols), "")
    ln = next((c for c in ("warc_record_length", "length", "len") if c in cols), "")
    if not off or not ln or "warc_filename" not in cols or "url" not in cols:
        say({"ok": False, "error": "This does not look like the Common Crawl columnar index - "
                                   "no warc_filename / offset / length columns. Columns: %s"
                                   % ", ".join(sorted(cols))[:400]})
    cond = ["%s IN (SELECT domain FROM gaps)" % dom,
            "(regexp_replace(url, '^https?://[^/]+', '') IN ('', '/') OR regexp_matches(lower(url), '%s'))" % CONTACT_RX]
    if status:
        cond.append("%s = 200" % status)
    if mime:
        cond.append("%s LIKE '%%html%%'" % mime)
    q = ("""INSERT INTO hits SELECT lower({dom}) AS domain, lower({host}) AS host, url,
              CASE WHEN regexp_replace(url, '^https?://[^/]+', '') IN ('', '/') THEN 'home' ELSE 'contact' END AS page_type,
              warc_filename, CAST({off} AS BIGINT), CAST({ln} AS BIGINT)
            FROM read_parquet('{{f}}') WHERE {cond}"""
         .format(dom=dom, host=host, off=off, ln=ln, cond=" AND ".join(cond)))

    t0 = time.time()
    for i, f in enumerate(todo, 1):
        n0 = con.execute("SELECT count(*) FROM hits").fetchone()[0]
        try:
            con.execute(q.format(f=f.replace("\\", "/")))
        except Exception as e:
            print("  part %d/%d FAILED: %s" % (i, len(todo), str(e)[:200]), flush=True)
            continue
        n1 = con.execute("SELECT count(*) FROM hits").fetchone()[0]
        con.execute("INSERT OR REPLACE INTO done_files VALUES (?, ?, now())", [f, n1 - n0])
        el = time.time() - t0
        eta = (el / i) * (len(todo) - i)
        print("  [%3d/%3d] %s  +%s pages  (%s total, %dm%02ds elapsed, ETA %dm%02ds)"
              % (i, len(todo), os.path.basename(f)[:34], format(n1 - n0, ","), format(n1, ","),
                 el // 60, el % 60, eta // 60, eta % 60), flush=True)

    out = os.path.join(IDXOUT, "scan.parquet")
    print("collapsing to the best home and contact page per domain ...", flush=True)
    con.execute("""COPY (SELECT domain, host, url, page_type, warc_filename, off, len FROM hits
                   QUALIFY row_number() OVER (PARTITION BY domain, page_type
                     ORDER BY (host = domain OR host = 'www.' || domain) DESC, length(url)) = 1)
                   TO '%s' (FORMAT parquet)""" % out.replace("\\", "/"))
    r = con.execute("SELECT count(DISTINCT domain), count(*) FROM read_parquet('%s')" % out.replace("\\", "/")).fetchone()
    pages = con.execute("SELECT count(*) FROM hits").fetchone()[0]
    con.close()
    say({"ok": True, "gap_domains_checked": ngaps, "index_parts": len(files), "parts_read_now": len(todo),
         "pages_matched": pages, "domains_found": r[0], "pages_kept": r[1],
         "index": os.path.relpath(out, ROOT).replace(os.sep, "/"), "source": source, "read_from": where_from,
         "minutes": round((time.time() - t0) / 60, 1),
         "next": "Press Enrich from Common Crawl - it will fetch these pages and pull the addresses out."})


# ------------------------------------------------------------------ the gap list
def step_gaps(limit=0, tlds="", rebuild=False):
    if not os.path.exists(LEADS):
        say({"ok": False, "error": "No lead database yet at %s - build it on the Contacts page first." % LEADS})
    os.makedirs(OUT, exist_ok=True)
    con = duck()
    lp = LEADS.replace("\\", "/")
    cols = {r[0].lower() for r in con.execute("DESCRIBE SELECT * FROM read_parquet('%s')" % lp).fetchall()}
    if "domain" not in cols:
        say({"ok": False, "error": "The lead database has no domain column."})
    em = "email" if "email" in cols else "NULL"
    ph = "phone" if "phone" in cols else "NULL"
    comp = "company" if "company" in cols else "NULL"
    ctry = "country" if "country" in cols else "NULL"
    ind = "industry" if "industry" in cols else "NULL"
    where_tld = ""
    if tlds:
        ts = ",".join("'%s'" % t.strip().lower().lstrip(".") for t in tlds.split(",") if t.strip())
        if ts:
            where_tld = "WHERE regexp_extract(domain, '[^.]+$') IN (%s)" % ts
    con.execute(f"""CREATE TABLE d AS
        SELECT domain,
               max(CASE WHEN nullif(trim(coalesce({em}, '')), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_email,
               max(CASE WHEN nullif(trim(coalesce({ph}, '')), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_phone,
               any_value(nullif(trim(coalesce({comp}, '')), '')) AS company,
               any_value(nullif(trim(coalesce({ctry}, '')), '')) AS country,
               any_value(nullif(trim(coalesce({ind}, '')), '')) AS industry,
               count(*) AS rows_in_db
        FROM (SELECT split_part(split_part(lower(regexp_replace(trim(coalesce(domain, '')),
                         '^(https?://)?(www\\.)?', '')), '/', 1), ':', 1) AS domain,
                     {em} AS {'email' if em != 'NULL' else 'email'}, {ph} AS {'phone' if ph != 'NULL' else 'phone'},
                     {comp} AS company, {ctry} AS country, {ind} AS industry
              FROM read_parquet('{lp}'))
        WHERE domain <> '' AND domain LIKE '%.%' AND domain NOT LIKE '% %'
        GROUP BY domain""")
    con.execute(f"CREATE TABLE g AS SELECT * FROM (SELECT * FROM d WHERE has_email = 0 AND has_phone = 0) {where_tld}")
    total, gaps = con.execute("SELECT (SELECT count(*) FROM d), (SELECT count(*) FROM g)").fetchone()

    cb_dir = cc_dir()
    pg = pages_glob(cb_dir) if cb_dir else ""
    findable = None
    if pg:
        con.execute(f"""CREATE TABLE p AS
            SELECT lower(domain) AS domain, count(*) AS pages,
                   count(*) FILTER (WHERE page_type <> 'home') AS contact_pages
            FROM read_parquet('{pg}', union_by_name=true) GROUP BY 1""")
        con.execute("CREATE TABLE gp AS SELECT g.*, coalesce(p.pages, 0) AS cc_pages, "
                    "coalesce(p.contact_pages, 0) AS cc_contact_pages FROM g LEFT JOIN p USING (domain)")
        findable = con.execute("SELECT count(*) FROM gp WHERE cc_pages > 0").fetchone()[0]
    else:
        con.execute("CREATE TABLE gp AS SELECT g.*, 0 AS cc_pages, 0 AS cc_contact_pages FROM g")

    lim = "LIMIT %d" % int(limit) if limit else ""
    con.execute(f"""COPY (SELECT domain, company, country, industry, rows_in_db, cc_pages, cc_contact_pages
                   FROM gp ORDER BY cc_contact_pages DESC, cc_pages DESC, rows_in_db DESC {lim})
                   TO '{GAPS.replace(chr(92), '/')}' (HEADER, DELIMITER ',')""")
    n = sum(1 for _ in open(GAPS, encoding="utf-8")) - 1
    return say({"ok": True, "domains_in_database": total, "without_any_contact": gaps,
                "in_this_list": n, "findable_in_common_crawl": findable,
                "page_index": pg or "not found - only the gap list was built",
                "file": os.path.relpath(GAPS, ROOT).replace(os.sep, "/"),
                "next": "Enrich from Common Crawl, or cc_enrich.py run"})


# ------------------------------------------------------------------ build the fetch list
def step_work(limit=0):
    cb, d = load_cb()
    pg = pages_glob(d)
    if not pg:
        say({"ok": False, "error": "No Common Crawl page index (cc_pages/*.parquet) in %s. "
                                   "Build it there first - cc_index_scan.py." % d})
    if not os.path.exists(GAPS):
        say({"ok": False, "error": "No gap list yet - run gaps first."})
    con = duck()
    con.execute("CREATE TABLE gaps AS SELECT lower(domain) AS domain, company, country, industry "
                "FROM read_csv('%s', header=true, all_varchar=true)" % GAPS.replace("\\", "/"))
    if limit:
        con.execute("DELETE FROM gaps WHERE rowid >= %d" % int(limit))
    con.execute(f"""CREATE TABLE p AS SELECT lower(domain) AS domain, host, url, page_type, warc_filename,
                        CAST(off AS BIGINT) off, CAST(len AS BIGINT) len
                    FROM read_parquet('{pg}', union_by_name=true)
                    WHERE lower(domain) IN (SELECT domain FROM gaps)
                    QUALIFY row_number() OVER (PARTITION BY lower(domain), page_type
                        ORDER BY (host = domain OR host = 'www.' || domain) DESC, length(url)) = 1""")
    con.execute("""CREATE TABLE w AS
        WITH h AS (SELECT * FROM p WHERE page_type = 'home'),
             c AS (SELECT * FROM p WHERE page_type <> 'home'
                   QUALIFY row_number() OVER (PARTITION BY domain ORDER BY
                       CASE page_type WHEN 'contact' THEN 1 WHEN 'about' THEN 2 WHEN 'team' THEN 3 ELSE 4 END) = 1)
        SELECT g.domain, 'gap' AS grp,
               h.url h_url, h.warc_filename h_file, h.off h_off, h.len h_len,
               c.url c_url, c.warc_filename c_file, c.off c_off, c.len c_len
        FROM gaps g LEFT JOIN h ON h.domain = g.domain LEFT JOIN c ON c.domain = g.domain
        WHERE h.url IS NOT NULL OR c.url IS NOT NULL""")
    os.makedirs(OUT, exist_ok=True)
    con.execute("COPY (SELECT * FROM w) TO '%s' (FORMAT parquet)" % WORK.replace("\\", "/"))
    n, nh, nc = con.execute("SELECT count(*), count(h_url), count(c_url) FROM w").fetchone()
    return n, nh, nc


def done_domains():
    s = set()
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as fh:
            s = {l.strip() for l in fh if l.strip()}
    return s


# ------------------------------------------------------------------ fetch and parse
async def fetch_all(cb, rows, par, source):
    import aiohttp
    loop = asyncio.get_running_loop()
    q = asyncio.Queue()
    for r in rows:
        q.put_nowait(r)
    out, st = [], {"n": 0, "email": 0, "phone": 0, "fail": 0}
    t0 = time.time()

    async def ticker():
        while True:
            await asyncio.sleep(5)
            n = st["n"]
            el = max(1e-6, time.time() - t0)
            left = (len(rows) - n) / max(0.001, n / el)
            print("%d/%d domains | %d with an email, %d with a phone | %.0f/min | about %.0f min left"
                  % (n, len(rows), st["email"], st["phone"], n / el * 60, left / 60), flush=True)

    async def worker(f):
        while True:
            try:
                dom, grp, hu, hf_, ho, hl, cu, cf, co, cl = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            h_raw = await f.get(hf_, ho, hl) if hf_ else None
            c_raw = await f.get(cf, co, cl) if cf else None
            if not h_raw and not c_raw:
                st["fail"] += 1
            row = await loop.run_in_executor(None, cb.parse_site, dom, grp, hu, h_raw, cu, c_raw)
            st["n"] += 1
            st["email"] += bool(row.get("best_email"))
            st["phone"] += bool(row.get("phones"))
            out.append(row)

    async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=60, connect=20),
                                     connector=aiohttp.TCPConnector(limit=par + 10, ttl_dns_cache=3600)) as s:
        f = cb.Fetcher(s, source)
        tk = asyncio.create_task(ticker())
        try:
            await asyncio.gather(*[worker(f) for _ in range(par)])
        finally:
            tk.cancel()
    return out, st


def step_run(par=40, limit=0, source="hf"):
    cb, d = load_cb()
    n, nh, nc = step_work(limit)
    if not n:
        say({"ok": True, "found_in_common_crawl": 0,
             "note": "None of the domains without contacts have a page in the Common Crawl index you have. "
                     "Scraping more of the index (cc_hf_scrape.py) would widen it."})
    con = duck()
    rows = con.execute("SELECT domain, grp, h_url, h_file, h_off, h_len, c_url, c_file, c_off, c_len "
                       "FROM read_parquet('%s')" % WORK.replace("\\", "/")).fetchall()
    done = done_domains()
    rows = [r for r in rows if r[0] not in done]
    if not rows:
        say({"ok": True, "fetched": 0, "note": "Every domain in the list has been fetched already."})
    print("%d domains to fetch (%d home pages, %d contact pages), %d already done, %d parallel downloads"
          % (len(rows), nh, nc, len(done), par), flush=True)
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    out, st = asyncio.run(fetch_all(cb, rows, par, source))
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "enriched_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cb.COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        for r in out:
            fh.write(r["domain"] + "\n")
    got = [r for r in out if r.get("best_email")]
    return say({"ok": True, "fetched": st["n"], "with_email": st["email"], "with_phone": st["phone"],
                "nothing_downloaded": st["fail"],
                "hit_rate": "%.0f%%" % (100 * st["email"] / max(1, st["n"])),
                "file": os.path.relpath(path, ROOT).replace(os.sep, "/"),
                "sample": [{"domain": r["domain"], "best_email": r["best_email"],
                            "company": r.get("biz_name") or r.get("title", "")[:60]} for r in got[:5]]})


def step_status():
    files = sorted(glob.glob(os.path.join(OUT, "enriched_*.csv")))
    tot = with_em = 0
    for p in files:
        with open(p, encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh):
                tot += 1
                with_em += bool((r.get("best_email") or "").strip())
    gaps = 0
    if os.path.exists(GAPS):
        gaps = sum(1 for _ in open(GAPS, encoding="utf-8", errors="replace")) - 1
    return say({"ok": True, "gap_list": gaps, "runs": len(files), "domains_fetched": tot,
                "contacts_found": with_em,
                "hit_rate": "%.0f%%" % (100 * with_em / max(1, tot)) if tot else "-",
                "folder": os.path.relpath(OUT, ROOT).replace(os.sep, "/")})


def step_export():
    files = sorted(glob.glob(os.path.join(OUT, "enriched_*.csv")), reverse=True)
    if not files:
        say({"ok": False, "error": "Nothing enriched yet."})
    seen, rows, cols = set(), [], []
    for p in files:
        with open(p, encoding="utf-8", errors="replace", newline="") as fh:
            rd = csv.DictReader(fh)
            cols = cols or (rd.fieldnames or [])
            for r in rd:
                dom = (r.get("domain") or "").strip().lower()
                if not dom or dom in seen or not (r.get("best_email") or "").strip():
                    continue
                seen.add(dom)
                rows.append(r)
    out = os.path.join(OUT, "contacts_found.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return say({"ok": True, "domains_with_a_contact": len(rows),
                "file": os.path.relpath(out, ROOT).replace(os.sep, "/"),
                "note": "Ready to upload to Manyreach from the Outreach page."})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gaps", "scan", "run", "status", "export", "work"])
    ap.add_argument("--index", default="")        # folder with the local copy of the 300 index parts
    ap.add_argument("--from", dest="idx_source", default="local", choices=["local", "live"])
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--par", type=int, default=40)
    ap.add_argument("--tld", default="")
    ap.add_argument("--source", default="hf", choices=["hf", "s3", "https"])
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    if a.cmd == "gaps":
        step_gaps(a.limit, a.tld, a.rebuild)
    elif a.cmd == "scan":
        step_scan(a.idx_source, a.index, a.limit, a.fresh)
    elif a.cmd == "run":
        step_run(max(2, min(200, a.par)), a.limit, a.source)
    elif a.cmd == "work":
        n, nh, nc = step_work(a.limit)
        say({"ok": True, "domains_with_a_page": n, "home_pages": nh, "contact_pages": nc})
    elif a.cmd == "status":
        step_status()
    else:
        step_export()


if __name__ == "__main__":
    main()
