"""
lead_index.py - one searchable database of every contact and company in the Modulario folder.

  python lead_index.py build            combine everything into LEAD_INDEX\\leads.parquet (run again any time to refresh)
  python lead_index.py build --quick 2  test build from only the first 2 master files
  python lead_index.py info             what is in the database

Lead Studio calls the rest for you (Contacts page, Export buttons, job results):
  python lead_index.py search   --spec file.json     one page of results as JSON
  python lead_index.py export   --spec file.json     matching leads to a .json or .csv file
  python lead_index.py newcheck --spec file.json     how many emails in result files are new to the database

Why a database file and not plain JSON: 38 million contacts as JSON would be over 20 GB and far too
slow to filter. The database answers a filter in seconds; exports and job reports are written as JSON.

What is combined (each source is skipped if it is not there):
  contacts   master_parts, crawl sites (rescored), Klaviyo stores, OpenStreetMap results
  companies  crawl sites (rescored), Klaviyo stores, Crustdata, live site scan, Hugging Face company profiles
"""
import os, sys, csv, json, glob, time, argparse, subprocess

try:
    import duckdb
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "duckdb"])
    import duckdb

csv.field_size_limit(10 ** 9)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(HERE))
IDX = os.path.join(ROOT, "LEAD_INDEX")
LEADS = os.path.join(IDX, "leads.parquet")
CLEAN = os.path.join(ROOT, "_CLEAN_2026-11-02")

COLS = ["name", "title", "company", "industry", "website", "location", "country", "size", "email", "phone", "technologies", "source"]
SIZES = ["", "1-10", "11-50", "51-200", "201-500", "501-1,000", "1,001-5,000", "5,001+"]
FREEMAIL = ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "live.com", "msn.com", "me.com",
            "protonmail.com", "proton.me", "gmx.de", "gmx.net", "web.de", "mail.ru", "yandex.ru", "azet.sk", "centrum.sk",
            "zoznam.sk", "post.sk", "atlas.sk", "seznam.cz", "email.cz", "centrum.cz", "yahoo.co.uk", "googlemail.com")
ROLE_RX = ("^(info|sales|office|contact|hello|hi|support|admin|team|mail|enquir|inquir|kontakt|obchod|service|help|billing|"
           "accounts?|marketing|hr|jobs|careers|press|media|orders?|shop|store|booking|reception|general|customer|care)")
COUNTRIES = {"UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "USA": "US", "U.S.": "US", "U.S.A.": "US", "AMERICA": "US",
             "UNITED KINGDOM": "GB", "UK": "GB", "GREAT BRITAIN": "GB", "ENGLAND": "GB", "SCOTLAND": "GB", "WALES": "GB",
             "GERMANY": "DE", "DEUTSCHLAND": "DE", "SLOVAKIA": "SK", "SLOVENSKO": "SK", "SLOVAK REPUBLIC": "SK",
             "CZECH REPUBLIC": "CZ", "CZECHIA": "CZ", "CESKO": "CZ", "CANADA": "CA", "AUSTRALIA": "AU", "FRANCE": "FR",
             "NETHERLANDS": "NL", "SPAIN": "ES", "ITALY": "IT", "POLAND": "PL", "POLSKA": "PL", "AUSTRIA": "AT",
             "SWITZERLAND": "CH", "IRELAND": "IE", "NEW ZEALAND": "NZ", "INDIA": "IN", "BELGIUM": "BE", "SWEDEN": "SE",
             "NORWAY": "NO", "DENMARK": "DK", "FINLAND": "FI", "PORTUGAL": "PT", "HUNGARY": "HU", "ROMANIA": "RO",
             "BRAZIL": "BR", "MEXICO": "MX", "SINGAPORE": "SG", "SOUTH AFRICA": "ZA", "UNITED ARAB EMIRATES": "AE",
             "ISRAEL": "IL", "JAPAN": "JP", "CHINA": "CN", "HONG KONG": "HK", "TURKEY": "TR", "GREECE": "GR", "UKRAINE": "UA"}
NOT_COUNTRY_TLD = ("io", "co", "ai", "me", "tv", "cc", "ly", "to", "fm", "gg", "so", "sh", "is", "it", "in", "us", "eu", "ws", "bz", "vc")


def P(path):
    return "'" + path.replace("\\", "/").replace("'", "''") + "'"


def have(pattern):
    return bool(glob.glob(pattern, recursive=True))


def iso(expr):
    """SQL that turns a country name or code into a 2-letter code."""
    whens = " ".join("WHEN %s THEN '%s'" % (P(k), v) for k, v in COUNTRIES.items())
    return "(CASE upper(trim(coalesce(%s,''))) %s ELSE upper(trim(coalesce(%s,''))) END)" % (expr, whens, expr)


def tld_country(dom):
    skip = ",".join("'%s'" % t for t in NOT_COUNTRY_TLD)
    t = "regexp_extract(lower(%s), '\\.([a-z]{2})$', 1)" % dom
    return "(CASE WHEN %s = 'uk' THEN 'GB' WHEN %s = '' OR %s IN (%s) THEN '' ELSE upper(%s) END)" % (t, t, t, skip, t)


def size_rank(n):
    return ("(CASE WHEN %s IS NULL OR %s <= 0 THEN 0 WHEN %s <= 10 THEN 1 WHEN %s <= 50 THEN 2 WHEN %s <= 200 THEN 3 "
            "WHEN %s <= 500 THEN 4 WHEN %s <= 1000 THEN 5 WHEN %s <= 5000 THEN 6 ELSE 7 END)" % ((n,) * 8))


def connect(tmp=True):
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")
    if tmp:
        os.makedirs(os.path.join(IDX, "tmp"), exist_ok=True)
        con.execute("SET temp_directory=%s" % P(os.path.join(IDX, "tmp")))
    return con


def say(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


# ------------------------------------------------------------------ build
def site_scan_tech(src, dst):
    """site_scan.csv has one 0/1 column per technology - turn that into a 'domain,technologies' file."""
    skip = {"domain", "status", "http_code", "final_url", "redirected_off_domain", "lang", "title", "description", "copyright_year",
            "parked", "n_signals", "emails", "phones", "contact_page_url", "best_email", "has_form"}
    n = 0
    with open(src, encoding="utf-8", errors="replace", newline="") as fi, open(dst, "w", encoding="utf-8", newline="") as fo:
        rd = csv.reader(fi); head = next(rd, []); w = csv.writer(fo); w.writerow(["domain", "technologies"])
        flags = [(i, c) for i, c in enumerate(head) if c not in skip and not c.startswith("fit_")]
        for r in rd:
            t = [c for i, c in flags if i < len(r) and r[i] == "1"]
            if t and r:
                w.writerow([r[0].lower(), "|".join(t)]); n += 1
    return n


def build(quick=0):
    os.makedirs(IDX, exist_ok=True)
    con = connect(); t0 = time.time(); used = {}
    free = ",".join("'%s'" % d for d in FREEMAIL)
    con.execute("CREATE TABLE ct(email VARCHAR, first VARCHAR, last VARCHAR, title VARCHAR, company VARCHAR, domain VARCHAR, "
                "country VARCHAR, city VARCHAR, phone VARCHAR, source VARCHAR, pri INTEGER)")
    con.execute("CREATE TABLE co(domain VARCHAR, company VARCHAR, industry VARCHAR, emp BIGINT, country VARCHAR, region VARCHAR, "
                "city VARCHAR, tech VARCHAR, descr VARCHAR, phone VARCHAR, pri INTEGER)")
    rc = "all_varchar=true, ignore_errors=true, null_padding=true"

    # ---- contacts
    mp = sorted(glob.glob(os.path.join(CLEAN, "master_parts", "*.csv.gz")))
    if quick:
        mp = mp[:quick]
    if mp:
        say("contacts: master_parts (%d files) ..." % len(mp))
        names = "['email','domain','first','last','title','company','website','country','phone','is_role','is_freemail','seen','src']"
        con.execute("INSERT INTO ct SELECT lower(trim(email)), first, last, title, company, lower(domain), country, '', phone, 'master', 1 "
                    "FROM read_csv([%s], header=false, names=%s, %s) WHERE email LIKE '%%@%%' AND email <> 'email'"
                    % (",".join(P(f) for f in mp), names, rc))
        used["master_parts"] = con.execute("SELECT count(*) FROM ct").fetchone()[0]
    rs = os.path.join(CLEAN, "CC_BULK", "rescored", "sites_*.csv.gz")
    if have(rs):
        say("contacts + companies: crawl sites (rescored) ...")
        con.execute("CREATE TEMP TABLE rs AS SELECT * FROM read_csv(%s, header=true, union_by_name=true, %s)" % (P(rs), rc))
        con.execute("INSERT INTO ct SELECT lower(trim(e)), '', '', '', company, lower(domain), country, city, split_part(phones, ';', 1), 'crawl', 3 "
                    "FROM (SELECT *, unnest(string_split(regexp_replace(coalesce(nullif(emails_clean,''), best_email, ''), '[|,; ]+', ';', 'g'), ';')) AS e FROM rs) "
                    "WHERE e LIKE '%@%'")
        con.execute("INSERT INTO co SELECT lower(domain), coalesce(nullif(company,''), title), '', NULL, country, region, city, "
                    "lower(replace(technologies, ';', '|')), '', split_part(phones, ';', 1), 3 FROM rs WHERE domain <> '' AND coalesce(junk_site,'0') <> '1'")
        used["crawl_sites"] = con.execute("SELECT count(*) FROM rs").fetchone()[0]
        con.execute("DROP TABLE rs")
    kl = os.path.join(ROOT, "Klaviyo websites.csv")
    if os.path.exists(kl):
        say("contacts + companies: Klaviyo stores ...")
        con.execute("CREATE TEMP TABLE kl AS SELECT * FROM read_csv(%s, header=true, %s)" % (P(kl), rc))
        con.execute("INSERT INTO ct SELECT lower(trim(contact_email)), contact_first_name, contact_last_name, contact_job_title, merchant_name, "
                    "lower(domain), country_code, city, coalesce(nullif(contact_mobile,''), split_part(phones, ':', 1)), 'klaviyo', 2 "
                    "FROM kl WHERE contact_email LIKE '%@%'")
        con.execute("INSERT INTO ct SELECT lower(trim(split_part(emails, ':', 1))), '', '', '', merchant_name, lower(domain), country_code, city, "
                    "split_part(phones, ':', 1), 'klaviyo', 3 FROM kl WHERE emails LIKE '%@%'")
        con.execute("INSERT INTO co SELECT lower(domain), merchant_name, "
                    "trim(regexp_extract(split_part(categories, ':', 1), '([^/]+)$', 1)), try_cast(employee_count AS BIGINT), country_code, state, city, "
                    "lower(replace(technologies, ':', '|')), description, split_part(phones, ':', 1), 1 FROM kl WHERE domain <> ''")
        used["klaviyo_stores"] = con.execute("SELECT count(*) FROM kl").fetchone()[0]
        con.execute("DROP TABLE kl")
    osm = os.path.join(CLEAN, "OSM_*", "*_1_WITH_EMAIL_*.csv")
    if have(osm):
        say("contacts: OpenStreetMap results ...")
        con.execute("INSERT INTO ct SELECT lower(trim(email)), first_name, last_name, title, company, lower(domain), country, city, phone, 'osm', 2 "
                    "FROM read_csv(%s, header=true, union_by_name=true, %s) WHERE email LIKE '%%@%%'" % (P(osm), rc))
        con.execute("INSERT INTO co SELECT lower(domain), company, replace(subsegment, '_', ' '), NULL, country, '', city, '', '', phone, 2 "
                    "FROM read_csv(%s, header=true, union_by_name=true, %s) WHERE domain <> ''" % (P(os.path.join(CLEAN, "OSM_*", "*.csv")), rc))
        used["osm"] = len(glob.glob(osm))

    # ---- companies only
    cd = os.path.join(CLEAN, "crustdata_companies.csv")
    if os.path.exists(cd):
        say("companies: Crustdata ...")
        con.execute("INSERT INTO co SELECT lower(domain), company, split_part(industries, ';', 1), "
                    "try_cast(regexp_extract(replace(employees, ',', ''), '([0-9]+)', 1) AS BIGINT), '', '', '', '', description, '', 1 "
                    "FROM read_csv(%s, header=true, %s) WHERE matched = 'yes'" % (P(cd), rc))
        used["crustdata"] = 1
    ss = os.path.join(CLEAN, "site_scan.csv")
    if os.path.exists(ss) and not quick:
        say("companies: live site scan technologies ...")
        tmpf = os.path.join(IDX, "tmp", "site_scan_tech.csv")
        used["site_scan"] = site_scan_tech(ss, tmpf)
        con.execute("INSERT INTO co SELECT domain, '', '', NULL, '', '', '', technologies, '', '', 4 FROM read_csv(%s, header=true, %s)" % (P(tmpf), rc))
    hf = [f for f in (os.path.join(ROOT, "HF_DATASETS", "company", "companies.parquet"),) if os.path.exists(f)]
    if hf and not quick:
        say("companies: Hugging Face company profiles ...")
        con.execute("INSERT INTO co SELECT lower(regexp_extract(website_url, '^(?:https?://)?(?:www\\.)?([^/?#]+)', 1)), name, industry, "
                    "try_cast(associated_members_count AS BIGINT), json_extract_string(headquarter, '$.country'), "
                    "json_extract_string(headquarter, '$.geographicArea'), json_extract_string(headquarter, '$.city'), '', "
                    "left(description, 300), '', 2 FROM read_parquet(%s) WHERE website_url IS NOT NULL" % P(hf[0]))
        used["hf_company_profiles"] = 1

    n_ct = con.execute("SELECT count(*) FROM ct").fetchone()[0]
    if not n_ct:
        sys.exit("No contact files were found under %s - nothing to build." % ROOT)
    say("merging %s contact rows and %s company rows ..." % (format(n_ct, ","), format(con.execute("SELECT count(*) FROM co").fetchone()[0], ",")))
    f = lambda c: "arg_min(%s, pri) FILTER (WHERE coalesce(%s,'') <> '')" % (c, c)
    con.execute("CREATE TABLE tch AS SELECT domain, array_to_string(list_sort(list_distinct(list(t))), '|') AS tech FROM "
                "(SELECT domain, unnest(string_split(coalesce(tech,''), '|')) AS t FROM co) WHERE t <> '' GROUP BY domain")
    con.execute("CREATE TABLE comp AS SELECT c.domain, %s AS company, %s AS industry, max(emp) AS emp, %s AS country, %s AS region, %s AS city, "
                "coalesce(any_value(t.tech), '') AS tech, %s AS descr, %s AS phone FROM co c LEFT JOIN tch t USING (domain) "
                "WHERE c.domain LIKE '%%.%%' GROUP BY c.domain"
                % (f("c.company"), f("c.industry"), f("c.country"), f("c.region"), f("c.city"), f("c.descr"), f("c.phone")))
    con.execute("DROP TABLE co")
    con.execute("CREATE TABLE c1 AS SELECT * EXCLUDE (rn) FROM (SELECT *, row_number() OVER (PARTITION BY email ORDER BY pri, "
                "(coalesce(first,'') <> '')::INT + (coalesce(title,'') <> '')::INT DESC) rn FROM ct WHERE pri > 1 OR source <> 'master') WHERE rn = 1")
    say("writing leads.parquet ...")
    tmp_out = LEADS + ".building"
    ctry = "coalesce(nullif(%s,''), nullif(%s,''), %s)" % (iso("k.country"), iso("c.country"), tld_country("k.domain"))
    loc = "trim(concat_ws(', ', nullif(coalesce(nullif(k.city,''), c.city),''), nullif(c.region,'')))"
    sel = ("SELECT k.email, trim(concat_ws(' ', nullif(k.first,''), nullif(k.last,''))) AS name, coalesce(k.title,'') AS title, "
           "coalesce(nullif(k.company,''), c.company, '') AS company, coalesce(c.industry,'') AS industry, "
           "CASE WHEN k.domain IN (%s) THEN '' ELSE k.domain END AS website, %s AS location, %s AS country, %s AS size_rank, "
           "coalesce(nullif(k.phone,''), c.phone, '') AS phone, coalesce(c.tech,'') AS technologies, left(coalesce(c.descr,''), 300) AS description, "
           "regexp_matches(split_part(k.email,'@',1), %s) AS is_role, k.domain IN (%s) AS is_freemail, k.source "
           "FROM %%s k LEFT JOIN comp c ON c.domain = k.domain AND k.domain NOT IN (%s)"
           % (free, loc, ctry, size_rank("c.emp"), P(ROLE_RX), free, free))
    master = "(SELECT * FROM ct WHERE source = 'master')"
    extra = "(SELECT * FROM c1 WHERE email NOT IN (SELECT email FROM ct WHERE source = 'master'))"
    con.execute("COPY (%s UNION ALL %s) TO %s (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 250000)"
                % (sel % master, sel % extra, P(tmp_out)))
    os.replace(tmp_out, LEADS)

    say("counting filters ...")
    L = "read_parquet(%s)" % P(LEADS)
    top = lambda expr, n, where="": [[r[0], r[1]] for r in con.execute(
        "SELECT %s v, count(*) n FROM %s %s GROUP BY 1 HAVING v <> '' ORDER BY n DESC LIMIT %d" % (expr, L, where, n)).fetchall()]
    facets = {"countries": top("country", 80), "industries": top("industry", 120),
              "sizes": [[SIZES[r[0]], r[1]] for r in con.execute("SELECT size_rank, count(*) FROM %s WHERE size_rank > 0 GROUP BY 1 ORDER BY 1" % L).fetchall()],
              "technologies": [[r[0], r[1]] for r in con.execute(
                  "SELECT t, count(*) n FROM (SELECT unnest(string_split(technologies, '|')) t FROM %s WHERE technologies <> '') "
                  "GROUP BY 1 HAVING t <> '' ORDER BY n DESC LIMIT 150" % L).fetchall()],
              "sources": top("source", 20)}
    r = con.execute("SELECT count(*), count(*) FILTER (WHERE name <> ''), count(*) FILTER (WHERE title <> ''), count(*) FILTER (WHERE industry <> ''), "
                    "count(*) FILTER (WHERE technologies <> ''), count(*) FILTER (WHERE phone <> ''), count(DISTINCT website) FROM %s" % L).fetchone()
    info = {"built": time.strftime("%Y-%m-%d %H:%M"), "contacts": r[0], "with_name": r[1], "with_title": r[2], "with_industry": r[3],
            "with_technology": r[4], "with_phone": r[5], "companies": r[6], "sources": used, "minutes": round((time.time() - t0) / 60, 1),
            "mb": round(os.path.getsize(LEADS) / 1e6)}
    json.dump(facets, open(os.path.join(IDX, "facets.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(info, open(os.path.join(IDX, "index_info.json"), "w", encoding="utf-8"), indent=1)
    say("DONE in %.1f min: %s contacts at %s companies -> %s (%d MB)" % (info["minutes"], format(r[0], ","), format(r[6], ","), LEADS, info["mb"]))
    print(json.dumps(info, indent=1))


# ------------------------------------------------------------------ schema bridge (LEAD_INDEX/leads.parquet)
# leads.parquet may be written either by "build" here (old column set) or by
# lead_index_finalize.py / lead_index_v3.py (email,name,first_name,last_name,title,company,
# domain,website,phone,country,city,industry,technologies,source_file,source_count).
# src() hands the search/export SQL a table that always has the old column names.
V1_COLS = ("location", "size_rank", "is_freemail", "is_role", "source", "description")
_SRC_CACHE = {}


def parquet_cols(path):
    try:
        con = duckdb.connect()
        return [r[0] for r in con.execute("DESCRIBE SELECT * FROM read_parquet(%s) LIMIT 0" % P(path)).fetchall()]
    except Exception:
        return []


def src(path=None):
    """SQL for the leads table with v1 column names, whichever schema the file has."""
    path = path or LEADS
    if path in _SRC_CACHE:
        return _SRC_CACHE[path]
    cols = parquet_cols(path)
    if not cols or all(c in cols for c in V1_COLS):
        out = "read_parquet(%s)" % P(path)
    else:
        col = lambda c, d="''": ("coalesce(%s,'')" % c) if c in cols else d
        name = ("coalesce(nullif(%s,''), trim(coalesce(first_name,'') || ' ' || coalesce(last_name,'')))" % col("name")
                if "first_name" in cols else col("name"))
        site = "coalesce(nullif(%s,''), nullif(%s,''), '')" % (col("website"), col("domain"))
        loc = ("trim(trim(%s || CASE WHEN %s <> '' AND %s <> '' THEN ', ' ELSE '' END || %s))"
               % (col("city"), col("city"), col("country"), col("country")))
        ctry = iso(col("country"))
        if "domain" in cols:
            ctry = "(CASE WHEN %s <> '' THEN %s ELSE %s END)" % (ctry, ctry, tld_country(col("domain")))
        dom = "lower(regexp_extract(%s, '@(.+)$', 1))" % col("email")
        user = "lower(regexp_extract(%s, '^([^@]+)', 1))" % col("email")
        freemail = ",".join(P(d) for d in FREEMAIL)
        out = ("(SELECT %s AS name, %s AS title, %s AS company, %s AS industry, %s AS website, %s AS location, "
               "%s AS country, 0 AS size_rank, %s AS email, %s AS phone, %s AS technologies, "
               "coalesce(nullif(%s,''), 'lead_index') AS source, '' AS description, "
               "(%s IN (%s)) AS is_freemail, regexp_matches(%s, %s) AS is_role "
               "FROM read_parquet(%s))"
               % (name, col("title"), col("company"), col("industry"), site, loc, ctry,
                  col("email"), col("phone"), col("technologies"),
                  col("source_file", "'lead_index'"), dom, freemail, user, P("|".join(ROLE_RX) if isinstance(ROLE_RX, tuple) and len(ROLE_RX) > 1 else (ROLE_RX[0] if isinstance(ROLE_RX, tuple) else ROLE_RX)),
                  P(path)))
    _SRC_CACHE[path] = out
    return out


def view(con, path=None):
    """Put the bridged leads table behind a temp view and give back its name.

    DuckDB will not bind query parameters when a table function (read_parquet)
    sits inside a subquery, so the mapping has to live in a view instead of
    being pasted into the FROM clause.
    """
    con.execute("CREATE OR REPLACE TEMP VIEW leads_v AS SELECT * FROM " + src(path))
    return "leads_v"


def link():
    """Point Lead Studio at the leads.parquet that is already on disk: write facets.json
    and index_info.json from it, without rebuilding anything."""
    need_index()
    con = connect(tmp=False); t0 = time.time(); L = view(con)
    say("reading %s ..." % LEADS)
    top = lambda expr, n: [[r[0], r[1]] for r in con.execute(
        "SELECT %s v, count(*) n FROM %s GROUP BY 1 HAVING v <> '' ORDER BY n DESC LIMIT %d" % (expr, L, n)).fetchall()]
    facets = {"countries": top("country", 80), "industries": top("industry", 120), "sizes": [],
              "technologies": [[r[0], r[1]] for r in con.execute(
                  "SELECT t, count(*) n FROM (SELECT unnest(string_split(technologies, '|')) t FROM %s WHERE technologies <> '') "
                  "GROUP BY 1 HAVING t <> '' ORDER BY n DESC LIMIT 150" % L).fetchall()],
              "sources": top("source", 20)}
    r = con.execute("SELECT count(*), count(*) FILTER (WHERE name <> ''), count(*) FILTER (WHERE title <> ''), "
                    "count(*) FILTER (WHERE industry <> ''), count(*) FILTER (WHERE technologies <> ''), "
                    "count(*) FILTER (WHERE phone <> ''), count(DISTINCT website) FROM %s" % L).fetchone()
    info = {"built": time.strftime("%Y-%m-%d %H:%M"), "contacts": r[0], "with_name": r[1], "with_title": r[2],
            "with_industry": r[3], "with_technology": r[4], "with_phone": r[5], "companies": r[6],
            "sources": ["LEAD_INDEX/leads.parquet"], "minutes": round((time.time() - t0) / 60, 1),
            "mb": round(os.path.getsize(LEADS) / 1e6)}
    json.dump(facets, open(os.path.join(IDX, "facets.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(info, open(os.path.join(IDX, "index_info.json"), "w", encoding="utf-8"), indent=1)
    say("DONE in %.1f min: %s contacts linked" % (info["minutes"], format(r[0], ",")))
    print(json.dumps(info, indent=1))


# ------------------------------------------------------------------ search
def terms(v):
    if isinstance(v, list):
        v = ",".join(str(x) for x in v)
    return [t.strip() for t in str(v or "").replace("\n", ",").split(",") if t.strip()][:40]


def where(spec):
    w, p = [], []

    def any_like(cols, vals):
        parts = []
        for t in vals:
            parts.append("(" + " OR ".join("%s ILIKE ?" % c for c in cols) + ")")
            p.extend(["%" + t + "%"] * len(cols))
        if parts:
            w.append("(" + " OR ".join(parts) + ")")

    c = [x.upper() for x in terms(spec.get("country"))]
    if c:
        w.append("country IN (%s)" % ",".join("?" * len(c))); p.extend(c)
    any_like(["title"], terms(spec.get("titles")))
    any_like(["company", "description", "industry", "technologies", "website", "title"], terms(spec.get("keywords")))
    any_like(["industry"], terms(spec.get("industries")))
    any_like(["location"], terms(spec.get("location")))
    for t in terms(spec.get("technologies")):                       # every technology listed must be present
        w.append("technologies ILIKE ?"); p.append("%" + t + "%")
    sz = [SIZES.index(s) for s in terms(spec.get("sizes")) if s in SIZES and s]
    if sz:
        w.append("size_rank IN (%s)" % ",".join(str(i) for i in sz))
    src = terms(spec.get("sources"))
    if src:
        w.append("source IN (%s)" % ",".join("?" * len(src))); p.extend(src)
    must = set(terms(spec.get("must")))
    for key, cond in (("name", "name <> ''"), ("title", "title <> ''"), ("phone", "phone <> ''"), ("website", "website <> ''"),
                      ("company", "company <> ''"), ("business_email", "NOT is_freemail"), ("named_person", "NOT is_role"),
                      ("industry", "industry <> ''"), ("technology", "technologies <> ''")):
        if key in must:
            w.append(cond)
    return (" WHERE " + " AND ".join(w)) if w else "", p


SELECT = ("SELECT name, title, company, industry, website, location, country, "
          "CASE size_rank WHEN 1 THEN '1-10' WHEN 2 THEN '11-50' WHEN 3 THEN '51-200' WHEN 4 THEN '201-500' WHEN 5 THEN '501-1,000' "
          "WHEN 6 THEN '1,001-5,000' WHEN 7 THEN '5,001+' ELSE '' END AS size, email, phone, technologies, source FROM %s")


def need_index():
    if not os.path.exists(LEADS):
        print(json.dumps({"error": "no_index"})); sys.exit(0)


def search(spec):
    need_index()
    con = connect(tmp=False); t0 = time.time(); w, p = where(spec); L = view(con)
    if spec.get("count"):
        n = con.execute("SELECT count(*) FROM %s%s" % (L, w), p).fetchone()[0]
        print(json.dumps({"total": n, "ms": int((time.time() - t0) * 1000)})); return
    limit = max(1, min(int(spec.get("limit") or 50), 500)); offset = max(0, int(spec.get("offset") or 0))
    rows = con.execute((SELECT % L) + w + " LIMIT %d OFFSET %d" % (limit + 1, offset), p).fetchall()
    print(json.dumps({"cols": COLS, "rows": [list(r) for r in rows[:limit]], "more": len(rows) > limit, "offset": offset,
                      "ms": int((time.time() - t0) * 1000)}, ensure_ascii=False))


def export(spec):
    need_index()
    con = connect(); w, p = where(spec); L = view(con)
    limit = max(1, min(int(spec.get("limit") or 10000), 2000000))
    out = spec["out"]; os.makedirs(os.path.dirname(out), exist_ok=True)
    q = (SELECT % L) + w + " LIMIT %d" % limit
    say("exporting up to %s leads ..." % format(limit, ","))
    # COPY cannot take a subquery that carries bound parameters, so the rows are
    # collected into a temp table first and that table is what gets copied out.
    con.execute("CREATE OR REPLACE TEMP TABLE ex AS " + q, p)
    n = con.execute("SELECT count(*) FROM ex").fetchone()[0]
    say("collected %s leads, writing the file ..." % format(n, ","))
    if out.lower().endswith(".json"):
        con.execute("COPY ex TO %s (FORMAT json, ARRAY true)" % P(out))
    else:
        con.execute("COPY ex TO %s (HEADER, DELIMITER ',')" % P(out))
    say("DONE: %s leads -> %s (%.1f MB)" % (format(n, ","), out, os.path.getsize(out) / 1e6))


def newcheck(spec):
    """For result files of a job: rows, emails, and how many emails the database has never seen."""
    con = connect(tmp=False); out = []
    has = os.path.exists(LEADS)
    for f in spec.get("files", [])[:40]:
        item = {"path": f, "rows": 0, "emails": 0, "new": None}
        try:
            src = "read_csv(%s, header=true, all_varchar=true, ignore_errors=true, null_padding=true)" % P(f)
            cols = [d[0] for d in con.execute("DESCRIBE SELECT * FROM " + src).fetchall()]
            item["rows"] = con.execute("SELECT count(*) FROM " + src).fetchone()[0]
            ec = next((c for c in cols if c.lower() in ("email", "best_email", "e-mail", "contact_email")), None) or \
                next((c for c in cols if "email" in c.lower() and "status" not in c.lower() and "type" not in c.lower()), None)
            if ec:
                e = 'SELECT DISTINCT lower(trim("%s")) e FROM %s WHERE "%s" LIKE \'%%@%%\'' % (ec, src, ec)
                item["emails"] = con.execute("SELECT count(*) FROM (%s)" % e).fetchone()[0]
                if has and item["emails"]:
                    item["new"] = con.execute("SELECT count(*) FROM (%s) x WHERE e NOT IN (SELECT email FROM read_parquet(%s))" % (e, P(LEADS))).fetchone()[0]
        except Exception as ex:
            item["error"] = str(ex)[:200]
        out.append(item)
    print(json.dumps({"files": out, "has_index": has}))


def info():
    p = os.path.join(IDX, "index_info.json")
    print(open(p, encoding="utf-8").read() if os.path.exists(p) else json.dumps({"error": "no_index"}))


# ------------------------------------------------------------------ ingest (add new contacts / fill in missing fields)
DB = os.path.join(IDX, "leads.duckdb")
CONTACT_COLS = ["email", "name", "first_name", "last_name", "title", "company", "domain", "website",
                "phone", "country", "city", "industry", "technologies", "source_file"]
# every alias we accept for a column, first match wins
ALIASES = {
    "email": ("email", "e-mail", "e_mail", "mail", "best_email", "contact_email", "email_address", "emails"),
    "name": ("name", "full_name", "fullname", "contact_name", "contact", "person"),
    "first_name": ("first_name", "first", "fname", "firstname", "given_name", "meno"),
    "last_name": ("last_name", "last", "lname", "lastname", "surname", "family_name", "priezvisko"),
    "title": ("title", "job_title", "jobtitle", "position", "role", "funkcia"),
    "company": ("company", "company_name", "organization", "organisation", "firma", "nazov", "business_name", "account"),
    "domain": ("domain", "root_domain", "website_domain", "host"),
    "website": ("website", "url", "web", "site", "webpage", "homepage", "www"),
    "phone": ("phone", "phone_number", "telephone", "tel", "mobile", "telefon", "phones"),
    "country": ("country", "country_code", "krajina", "state_country"),
    "city": ("city", "town", "mesto", "location", "locality"),
    "industry": ("industry", "sector", "category", "nace", "odvetvie", "segment"),
    "technologies": ("technologies", "technology", "tech", "tech_stack", "stack"),
}


def q(col):
    return '"' + str(col).replace('"', '""') + '"'


def reader_sql(path):
    """DuckDB SQL that reads one upload, whatever format it is."""
    low = path.lower()
    if low.endswith(".parquet"):
        return "read_parquet(%s)" % P(path)
    if low.endswith(".json") or low.endswith(".jsonl") or low.endswith(".ndjson"):
        return "read_json_auto(%s)" % P(path)
    if low.endswith(".xlsx") or low.endswith(".xls"):
        return "read_xlsx(%s, all_varchar=true)" % P(path)
    return ("read_csv(%s, header=true, all_varchar=true, ignore_errors=true, null_padding=true, sample_size=-1)"
            % P(path))


def stage_select(con, path):
    """SELECT that maps an upload's own headers onto the contacts schema."""
    rd = reader_sql(path)
    cols = [d[0] for d in con.execute("DESCRIBE SELECT * FROM %s LIMIT 0" % rd).fetchall()]
    low = {c.lower().strip().replace(" ", "_"): c for c in cols}

    def pick(target):
        for a in ALIASES[target]:
            if a in low:
                return low[a]
        for a in ALIASES[target]:                       # then a looser contains-match
            for k, orig in low.items():
                if a in k:
                    return orig
        return None

    got, parts = {}, []
    for t in CONTACT_COLS:
        if t == "source_file":
            continue
        c = pick(t)
        got[t] = c
        parts.append("nullif(trim(CAST(%s AS VARCHAR)), '') AS %s" % (q(c), t) if c else "CAST(NULL AS VARCHAR) AS %s" % t)
    if not got.get("name") and (got.get("first_name") or got.get("last_name")):
        f = "coalesce(CAST(%s AS VARCHAR), '')" % q(got["first_name"]) if got.get("first_name") else "''"
        l = "coalesce(CAST(%s AS VARCHAR), '')" % q(got["last_name"]) if got.get("last_name") else "''"
        parts[CONTACT_COLS.index("name")] = "nullif(trim(%s || ' ' || %s), '') AS name" % (f, l)
    parts.append("%s AS source_file" % P(os.path.basename(path)))
    if not got.get("email") and not got.get("website") and not got.get("domain"):
        raise ValueError("%s has no email, website or domain column - nothing to key the contacts on. "
                         "Columns found: %s" % (os.path.basename(path), ", ".join(cols[:25])))
    return "SELECT " + ", ".join(parts) + " FROM " + rd


def ingest(spec):
    """Add uploaded contacts to leads.duckdb: brand new keys are inserted, keys already in the
    database only get their empty fields filled in. Then leads.parquet is refreshed."""
    files = [f for f in (spec.get("files") or []) if f]
    if not files:
        print(json.dumps({"error": "no_files"})); return
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        print(json.dumps({"error": "not found: " + ", ".join(missing[:5])})); return
    os.makedirs(os.path.join(IDX, "_tmp"), exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("PRAGMA memory_limit='%s'" % (spec.get("memory_limit") or "6GB"))
    con.execute("PRAGMA temp_directory=%s" % P(os.path.join(IDX, "_tmp")))
    con.execute("PRAGMA preserve_insertion_order=false")
    try:
        con.execute("PRAGMA enable_progress_bar")
    except Exception:
        pass
    cols = ", ".join(CONTACT_COLS)
    if not con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='contacts'").fetchone()[0]:
        say("no contacts table in leads.duckdb - creating it from leads.parquet ...")
        if os.path.exists(LEADS):
            con.execute("CREATE TABLE contacts AS SELECT %s, 1::BIGINT AS source_count FROM read_parquet(%s)" % (cols, P(LEADS)))
        else:
            con.execute("CREATE TABLE contacts (%s, source_count BIGINT)" % ", ".join(c + " VARCHAR" for c in CONTACT_COLS))
    con.execute("CREATE TABLE IF NOT EXISTS ingested_files (file VARCHAR, at TIMESTAMP, rows BIGINT, new BIGINT, updated BIGINT)")
    before = con.execute("SELECT count(*) FROM contacts").fetchone()[0]
    say("database holds %s contacts" % format(before, ","))
    key = "coalesce(nullif(lower(trim(email)), ''), nullif(lower(trim(domain)), ''))"
    # same key, but written against the contacts table - inside UPDATE ... FROM both tables
    # have an email and a domain column, so it has to be qualified
    key_c = key.replace("email", "contacts.email").replace("domain", "contacts.domain")
    report = []

    for path in files:
        say("reading %s ..." % os.path.basename(path))
        try:
            sel = stage_select(con, path)
        except Exception as e:
            say("SKIPPED: %s" % e); report.append({"file": os.path.basename(path), "error": str(e)[:300]}); continue
        con.execute("DROP TABLE IF EXISTS up_raw"); con.execute("DROP TABLE IF EXISTS up")
        con.execute("CREATE TEMP TABLE up_raw AS " + sel)
        rows = con.execute("SELECT count(*) FROM up_raw").fetchone()[0]
        # clean: lowercase email, derive the domain from the website or the email when it is missing
        con.execute("""CREATE TEMP TABLE up AS
            SELECT lower(trim(email)) AS email, name, first_name, last_name, title, company,
                   coalesce(nullif(lower(trim(domain)), ''),
                            nullif(lower(regexp_extract(coalesce(website, ''), '^(?:https?://)?(?:www\\.)?([^/?#]+)', 1)), ''),
                            nullif(lower(regexp_extract(coalesce(email, ''), '@(.+)$', 1)), '')) AS domain,
                   website, phone, country, city, industry, technologies, source_file
            FROM up_raw
            WHERE coalesce(nullif(trim(email), ''), nullif(trim(domain), ''), nullif(trim(website), '')) IS NOT NULL""")
        # one row per key, keeping the most complete value for every field
        agg = ", ".join("max(%s) AS %s" % (c, c) for c in CONTACT_COLS if c != "email")
        con.execute("DROP TABLE IF EXISTS up1")
        con.execute("CREATE TEMP TABLE up1 AS SELECT %s AS k, max(email) AS email, %s FROM up WHERE %s IS NOT NULL GROUP BY 1"
                    % (key, agg, key))
        n_keys = con.execute("SELECT count(*) FROM up1").fetchone()[0]
        say("  %s rows -> %s unique contacts" % (format(rows, ","), format(n_keys, ",")))

        say("  filling in missing fields on contacts already in the database ...")
        sets = ", ".join("%s = coalesce(nullif(contacts.%s, ''), up1.%s)" % (c, c, c)
                         for c in CONTACT_COLS if c not in ("email", "source_file"))
        con.execute("DROP TABLE IF EXISTS hit")
        con.execute("CREATE TEMP TABLE hit AS SELECT DISTINCT k FROM up1 WHERE k IN "
                    "(SELECT %s FROM contacts WHERE %s IS NOT NULL)" % (key, key))
        updated = con.execute("SELECT count(*) FROM hit").fetchone()[0]
        if updated:
            con.execute("UPDATE contacts SET %s FROM up1 WHERE up1.k = %s" % (sets, key_c))
        say("  adding the contacts the database has never seen ...")
        new = con.execute("SELECT count(*) FROM up1 WHERE k NOT IN (SELECT k FROM hit)").fetchone()[0]
        if new:
            con.execute("INSERT INTO contacts (%s, source_count) SELECT %s, 1 FROM up1 WHERE k NOT IN (SELECT k FROM hit)"
                        % (cols, cols))
        con.execute("INSERT INTO ingested_files VALUES (?, now(), ?, ?, ?)", [path, rows, new, updated])
        say("  DONE: %s new, %s updated" % (format(new, ","), format(updated, ",")))
        report.append({"file": os.path.basename(path), "rows": rows, "contacts": n_keys, "new": new, "updated": updated})
        for t in ("up_raw", "up", "up1", "hit"):
            con.execute("DROP TABLE IF EXISTS " + t)

    after = con.execute("SELECT count(*) FROM contacts").fetchone()[0]
    if spec.get("export") is not False:
        say("writing leads.parquet (%s contacts, a few minutes) ..." % format(after, ","))
        tmp = LEADS + ".new"
        con.execute("COPY (SELECT %s, source_count FROM contacts) TO %s (FORMAT parquet, COMPRESSION zstd)" % (cols, P(tmp)))
        con.close()
        if os.path.exists(LEADS):
            old = LEADS + ".old"
            if os.path.exists(old):
                os.replace(old, old + "~")
            os.replace(LEADS, old)
        os.replace(tmp, LEADS)
        say("refreshing the Contacts page figures ...")
        _SRC_CACHE.clear()
        try:
            link()
        except Exception as e:
            say("facets not refreshed: %s" % e)
    else:
        con.close()
    out = {"files": report, "contacts_before": before, "contacts_after": after, "added": after - before}
    say("INGEST COMPLETE: %s contacts in the database (+%s)" % (format(after, ","), format(after - before, ",")))
    print(json.dumps(out, indent=1, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "link", "ingest", "search", "export", "newcheck", "info"])
    ap.add_argument("--spec"); ap.add_argument("--quick", type=int, default=0)
    ap.add_argument("--file", action="append", default=[]); ap.add_argument("--no-export", action="store_true")
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8")) if a.spec else {}
    if a.file:
        spec.setdefault("files", []).extend(os.path.abspath(f) for f in a.file)
    if a.no_export:
        spec["export"] = False
    {"build": lambda: build(a.quick), "link": link, "ingest": lambda: ingest(spec), "search": lambda: search(spec), "export": lambda: export(spec),
     "newcheck": lambda: newcheck(spec), "info": info}[a.cmd]()


if __name__ == "__main__":
    main()
