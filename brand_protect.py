#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
brand_protect.py - fake-shop and phishing intelligence, for Red Points work.

Pulls the domains that consumer-protection bodies and phishing trackers have
already confirmed as fraudulent, matches them against the brands you watch,
and tells you three things:

  1. which fraud domains are impersonating your brands (evidence, today)
  2. what was newly listed as fraudulent since the last run
  3. which brands are being impersonated most across the whole fake-shop
     corpus - because a brand under attack is a brand that needs protecting,
     which makes this a prospect list as well as a detection tool

Stdlib only. No pip install.

Commands
    daily                 new fraud listings + phishing deltas, matched to your brands
    backfill [pages]      walk the full Watchlist Internet corpus (788 pages, be patient)
    refresh               re-download the phishing domain list (11 MB, weekly is plenty)
    check <domain|brand>  is this listed? any lookalikes of it listed?
    victims [n]           brands most impersonated across the corpus - Red Points prospects
    cross-nrd [days]      cross your newly-registered-domain feed against the fraud sets
    brands                show the brands being watched
    import-brands [csv]   fill brands.txt from an existing brands.csv
    status
    schedule [HH:MM]      Windows daily task (default 07:50)
    unschedule

Everything lands in <this folder>/BRAND/.

Sources
    watchlist-internet.at  OIAT / Austrian consumer protection. 39,358 entries
                           across 9 categories, newest first, plain HTML
                           pagination. robots.txt permits these paths; this
                           script still waits between requests.
    Phishing.Database      github.com/Phishing-Database/Phishing.Database
                           ~500k active phishing domains, NEW-today deltas.

Both are other people's work and other people's data. The listings are
allegations by those bodies, not findings of fact - treat a hit as a lead to
verify, never as proof, and do not republish a list wholesale.
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
import urllib.request

APP = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.environ.get("BRAND_DIR") or os.path.join(APP, "BRAND"))
RAW = os.path.join(BASE, "raw")
OUT = os.path.join(BASE, "out")
CONFIG = os.path.join(BASE, "config.json")
BRANDS = os.path.join(BASE, "brands.txt")
STATE = os.path.join(BASE, "state.json")
INDEX = os.path.join(BASE, "fraud.sqlite")
ATTRIB = os.path.join(BASE, "ATTRIBUTION.txt")

UA = "LeadStudio-BrandProtect/1.0 (brand protection research; contact via site owner)"
WL = "https://www.watchlist-internet.at"
PDB = "https://raw.githubusercontent.com/Phishing-Database/Phishing.Database/master"
TASK_NAME = "LeadStudio Brand Protect Daily"

csv.field_size_limit(min(2 ** 31 - 1, sys.maxsize))

ATTRIBUTION_TEXT = """\
Sources used by brand_protect.py
================================

Watchlist Internet  -  https://www.watchlist-internet.at/
  Run by OIAT (Oesterreichisches Institut fuer angewandte Telekommunikation),
  an Austrian consumer-protection initiative. Their lists of fraudulent and
  problematic shops are published for public warning purposes.
  Credit them wherever you surface this data. Their robots.txt permits the
  list paths; this script waits between requests and does not hammer them.

Phishing.Database  -  https://github.com/Phishing-Database/Phishing.Database
  Community-maintained lists of active phishing domains, links and IPs.
  Check the repository's own licence before redistributing.

IMPORTANT
  Every entry in these lists is an ALLEGATION by the body that published it,
  not a judicial finding. A match is a lead to verify before you act on it or
  put it in front of a client. Do not republish either corpus wholesale, and
  do not describe a business as fraudulent on the strength of a list entry
  alone.
"""

DEFAULT_CONFIG = {
    "watchlist": {
        "enabled": True,
        "list_path": "/unserioese-webseiten/",
        "_list_path_note": "The master list: 39,358 entries over 9 categories, which contains both the fraudulent-shops and problematic-shops lists as categories. Narrow it to /liste-betruegerischer-shops/ or /liste-problematischer-shops/ if you only want one.",
        "daily_pages": 6,
        "_daily_pages_note": "Pages to read on a daily run. 50 entries a page, newest first, so 6 pages covers several days of new listings.",
        "page_delay_seconds": 1.5
    },
    "phishing_database": {
        "enabled": True,
        "domains_active": "phishing-domains-ACTIVE.txt",
        "domains_new_today": "phishing-domains-NEW-today.txt",
        "links_new_today": "phishing-links-NEW-today.txt",
        "refresh_active_days": 7,
        "_note": "domains-ACTIVE is ~11 MB and re-downloaded weekly. NEW-today is small and fetched on every run."
    },
    "brand_match": {
        "contains": True,
        "edit_distance": 1,
        "_edit_distance_note": "0 disables fuzzy matching. 1 catches one typo (amzon) and one transposition (amaozn). 2 is far too noisy.",
        "min_brand_len": 4,
        "_min_brand_len_note": "A brand shorter than this is ignored entirely - 3 letters matches everything.",
        "min_fuzzy_len": 6,
        "_min_fuzzy_len_note": "Fuzzy matching only for brands at least this long. At 4-5 characters a one-edit match is coincidence, not a squat ('drsa' matched 'desa.id').",
        "min_contains_len": 8,
        "_min_contains_len_note": "A brand is only looked for INSIDE a longer word if it is at least this long. Lower it and short brands run riot ('mont' matched 'montgomerycountylinks.org', 'nfinity' matched 'infinityfreeapp.com').",
        "affix_words": ["shop", "store", "sale", "sales", "outlet", "online", "official",
                        "login", "secure", "account", "verify", "support", "service", "pay",
                        "shoes", "kaufen", "angebot", "de", "at", "sk", "cz", "eu", "us", "uk"],
        "generic_words": ["studio", "boutique", "interior", "interiors", "triangle", "dream",
                          "dreams", "infinity", "monthly", "month", "global", "digital",
                          "fashion", "beauty", "garden", "home", "house", "group", "world",
                          "market", "center", "centre", "direct", "prime", "smart", "simple",
                          "mobile", "media", "studio24", "design", "designs", "collection"],
        "_generic_words_note": "Everyday words that happen to resemble brands. A generic word only matches a brand it equals exactly - never by typo or substring. Add to this list whenever a false positive annoys you."
    },
    "home_tlds": ["sk", "cz", "at", "de", "hu", "pl"],
    "nrd_dir": "",
    "_nrd_dir_note": "Path to the NRD folder from nrd_feed.py. Blank = ..\\NRD next to this script.",
    "retention_days": 365,
    "schedule_time": "07:50"
}

SEED_BRANDS = """\
# One brand per line. Lines starting with # are ignored.
# Put the brands you (or your Red Points clients) watch here - the bare brand
# word, not a domain. 'nike', not 'nike.com'.
#
# Brands shorter than min_brand_len in config.json are matched exactly only.
# Start small: 10 good brands beat 200 vague ones, because every extra brand
# widens the fuzzy net and costs you precision.
"""


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
    if not os.path.exists(BRANDS):
        with open(BRANDS, "w", encoding="utf-8") as f:
            f.write(SEED_BRANDS)
        say("created an empty brand list:", BRANDS)


def load_json(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(d))


def save_json(p, data):
    t = p + ".tmp"
    with open(t, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(t, p)


def load_config():
    ensure_dirs()
    if not os.path.exists(CONFIG):
        save_json(CONFIG, DEFAULT_CONFIG)
        say("wrote a starter config:", CONFIG)
    c = load_json(CONFIG, DEFAULT_CONFIG)
    for k, v in DEFAULT_CONFIG.items():
        if k not in c:
            c[k] = v
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                c[k].setdefault(k2, v2)
    return c


def load_brands():
    out = []
    if os.path.exists(BRANDS):
        with open(BRANDS, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    out.append(re.sub(r"[^a-z0-9]", "", line))
    return [b for b in dict.fromkeys(out) if b]


def http(url, timeout=180, tries=4, quiet=False):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding", "").lower() == "gzip" or data[:2] == b"\x1f\x8b":
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                return data
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (404, 403, 401):
                break
            time.sleep(2 + 3 * i)
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(2 + 2 * i)
    if not quiet:
        say("    failed:", last)
    return None


# ---------------------------------------------------------------- domains

DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,252}\.[a-z0-9\-]{2,63}$")
TWO_LEVEL = {"co.uk", "org.uk", "com.au", "com.br", "co.jp", "co.nz", "com.tr",
             "com.pl", "com.ua", "co.in", "com.mx", "co.za", "com.cn", "com.es"}


def norm_domain(d):
    d = (d or "").strip().lower().rstrip(".")
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].split("?")[0]
    d = re.sub(r":\d+$", "", d)          # vitalwelt-apotheken.de:80
    d = re.sub(r"^www\.", "", d)
    return d if DOMAIN_RE.match(d) else ""


def split_domain(d):
    p = d.split(".")
    if len(p) >= 3 and ".".join(p[-2:]) in TWO_LEVEL:
        return p[-3], ".".join(p[-2:])
    return (p[-2] if len(p) >= 2 else d), p[-1]


def core_words(sld):
    """Split an sld into comparable word-ish chunks."""
    parts = [x for x in re.split(r"[^a-z0-9]+", sld) if x]
    return parts or [sld]


def within_edit(a, b, maxd):
    """Bounded Damerau-Levenshtein, with early exit.

    Transposition counts as ONE edit, not two. That matters: swapping two
    adjacent letters ('nkie' for 'nike') is the most common typosquat there
    is, and plain Levenshtein scores it 2, so it would slip past a distance
    of 1 - exactly the domains you most want caught.
    """
    if a == b:
        return True
    if maxd <= 0 or abs(len(a) - len(b)) > maxd:
        return False
    prev2 = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if (prev2 is not None and i > 1 and j > 1
                    and ca == b[j - 2] and a[i - 2] == cb):
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            best = min(best, cur[j])
        if best > maxd:
            return False
        prev2, prev = prev, cur
    return prev[-1] <= maxd


class BrandMatcher:
    """Matches a domain against thousands of brands, precision first.

    SPEED. The naive version (every brand against every domain) is hopeless:
    1,500 brands over 400k domains ran past ten minutes before being killed.
    Brands are bucketed by their first k characters for substring work, and a
    deletion index (the SymSpell trick) answers distance-1 questions in a few
    dict lookups. Candidates are always confirmed with the real distance
    function, so the speed costs no accuracy. ~140k domains/second.

    PRECISION, which matters more. The first version flattened a domain's
    hyphens and looked for a brand anywhere inside the result, which produced
    almost nothing but noise: 'mont' matched montgomerycountylinks.org,
    'nfinity' matched infinityfreeapp.com, 'barr' matched updateterbarru.com.
    So matching is now tiered and word-aware, strongest first:

      1. a brand IS one of the domain's words            -> exact
      2. the other words are only shop/store/login/...   -> brand + affix
      3. a word is one edit from a brand                 -> typo
      4. a long brand appears inside a longer word       -> contains

    Each tier has its own length floor, because the shorter the brand the more
    a loose match is coincidence. Everyday words ('studio', 'boutique') only
    ever match a brand they equal exactly.
    """

    KGRAM = 4

    def __init__(self, brands, cfg):
        bm = cfg["brand_match"]
        self.contains = bool(bm.get("contains", True))
        self.maxd = int(bm.get("edit_distance") or 0)
        self.minlen = max(int(bm.get("min_brand_len") or 4), 3)
        self.min_fuzzy = max(int(bm.get("min_fuzzy_len") or 6), self.minlen)
        self.min_contains = max(int(bm.get("min_contains_len") or 8), self.minlen)
        self.affix = set(bm.get("affix_words") or [])
        self.generic = set(bm.get("generic_words") or [])

        self.all = set(b for b in brands if len(b) >= self.minlen)
        self.dropped_short = [b for b in brands if len(b) < self.minlen]

        k = min(self.KGRAM, self.min_contains)
        self.k = k
        self.by_prefix = {}
        for b in self.all:
            if len(b) >= self.min_contains:
                self.by_prefix.setdefault(b[:k], []).append(b)
        for v in self.by_prefix.values():
            v.sort(key=len, reverse=True)

        self.deletions = {}
        if self.maxd > 0:
            for b in self.all:
                if len(b) >= self.min_fuzzy:
                    for v in self._dels(b):
                        self.deletions.setdefault(v, []).append(b)

    @staticmethod
    def _dels(w):
        yield w
        for i in range(len(w)):
            yield w[:i] + w[i + 1:]

    def _fuzzy(self, w):
        if len(w) < self.min_fuzzy or w in self.generic:
            return None
        seen = set()
        for v in self._dels(w):
            for b in self.deletions.get(v, ()):
                if b in seen:
                    continue
                seen.add(b)
                if b != w and within_edit(w, b, self.maxd):
                    return b
        return None

    def _contained(self, word):
        if len(word) <= self.min_contains or word in self.generic:
            return None
        k = self.k
        for i in range(0, len(word) - k + 1):
            for b in self.by_prefix.get(word[i:i + k], ()):
                if len(b) < len(word) and word.startswith(b, i):
                    return b
        return None

    def hits(self, domain):
        sld, _tld = split_domain(domain)
        words = [w for w in core_words(sld) if w]

        # 1 + 2: a brand is one of the words
        for i, w in enumerate(words):
            if w in self.all:
                others = [x for x in words[:i] + words[i + 1:]]
                if others and all(x in self.affix for x in others):
                    return [(w, "brand + '%s'" % "-".join(others))]
                if not others:
                    return [(w, "exact")]
                return [(w, "brand as a word")]

        # 2b: one word is brand+affix glued together (nikeshop)
        for w in words:
            # no length cap here: the stem is confirmed by exact lookup, so
            # 'eastpakshop' is as safe a match as 'eastpak-shop'
            if w not in self.generic:
                for a in self.affix:
                    if len(a) >= 2 and w.endswith(a):
                        stem = w[:-len(a)]
                        if stem in self.all:
                            return [(stem, "brand + '%s'" % a)]
                    if len(a) >= 2 and w.startswith(a):
                        stem = w[len(a):]
                        if stem in self.all:
                            return [(stem, "'%s' + brand" % a)]

        # 3: a word is one edit from a brand
        if self.maxd > 0:
            for w in words:
                b = self._fuzzy(w)
                if b:
                    return [(b, "typo of '%s'" % b)]

        # 4: a long brand sits inside a longer word
        if self.contains:
            for w in words:
                b = self._contained(w)
                if b:
                    return [(b, "contains brand")]
        return []


_MATCHER = {}


def get_matcher(brands, cfg):
    key = id(brands)
    m = _MATCHER.get(key)
    if m is None:
        m = _MATCHER[key] = BrandMatcher(brands, cfg)
    return m


def brand_hits(domain, brands, cfg):
    return get_matcher(brands, cfg).hits(domain)


# ---------------------------------------------------------------- index

def open_index():
    ensure_dirs()
    con = sqlite3.connect(INDEX, timeout=60)
    for mode in ("WAL", "DELETE"):
        try:
            con.execute("PRAGMA journal_mode=%s" % mode)
            con.execute("""CREATE TABLE IF NOT EXISTS fraud (
                domain TEXT NOT NULL, source TEXT NOT NULL, category TEXT,
                listed TEXT, first_seen TEXT NOT NULL, url TEXT,
                PRIMARY KEY (domain, source))""")
            cols = [r[1] for r in con.execute("PRAGMA table_info(fraud)")]
            if "url" not in cols:
                con.execute("ALTER TABLE fraud ADD COLUMN url TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS fraud_seen ON fraud(first_seen)")
            con.execute("CREATE INDEX IF NOT EXISTS fraud_src ON fraud(source)")
            con.commit()
            return con
        except sqlite3.OperationalError as e:
            last = e
    raise SystemExit("Could not open %s (%s). On a synced drive, set BRAND_DIR "
                     "to a local path." % (INDEX, last))


def add_rows(con, rows, source, today):
    """rows: (domain, category, listed[, url]). Returns the ones new to the index."""
    cur = con.cursor()
    fresh = []
    buf = []
    for r in rows:
        d, cat, listed = r[0], r[1], r[2]
        url = r[3] if len(r) > 3 else ""
        buf.append((d, source, cat, listed, today, url))
        if len(buf) >= 10000:
            fresh += _flush(cur, buf, source)
            buf = []
    if buf:
        fresh += _flush(cur, buf, source)
    con.commit()
    return fresh


def _flush(cur, buf, source):
    qs = ",".join("?" * len(buf))
    cur.execute("SELECT domain FROM fraud WHERE source=? AND domain IN (%s)" % qs,
                [source] + [b[0] for b in buf])
    have = set(x[0] for x in cur.fetchall())
    new = [b for b in buf if b[0] not in have]
    cur.executemany("INSERT OR IGNORE INTO fraud (domain,source,category,listed,first_seen,url) "
                    "VALUES (?,?,?,?,?,?)", new)
    return [(b[0], b[2], b[3]) for b in new]


# ---------------------------------------------------------------- watchlist

ITEM_RE = re.compile(
    r'<div class="site-item">(.*?)</div>\s*</div>\s*</div>', re.S)
LINK_RE = re.compile(r'class="[^"]*site-item__link[^"]*"[^>]*>\s*([^<]+?)\s*<', re.S)
HREF_RE = re.compile(r'<a[^>]*class="[^"]*site-item__link[^"]*"[^>]*href="([^"]+)"', re.S)
HREF_RE2 = re.compile(r'href="([^"]+)"[^>]*class="[^"]*site-item__link[^"]*"', re.S)
CAT_RE = re.compile(r'class="[^"]*btn--category[^"]*"[^>]*>\s*([^<]+?)\s*<', re.S)
DATE_RE = re.compile(r'class="[^"]*site-item__listed-date[^"]*"[^>]*>\s*([\d.]+)\s*<', re.S)
HITS_RE = re.compile(r'([\d.]+)\s*Treffer')


def parse_watchlist(html):
    """The page is server-rendered TYPO3/Solr - plain HTML, no JS needed."""
    out = []
    # split on the item opener; the closing-tag nesting is not worth a regex
    chunks = html.split('<div class="site-item">')[1:]
    for ch in chunks:
        m = LINK_RE.search(ch)
        if not m:
            continue
        d = norm_domain(m.group(1))
        if not d:
            continue
        cm = CAT_RE.search(ch)
        dm = DATE_RE.search(ch)
        listed = ""
        if dm:
            try:
                dd, mm, yy = dm.group(1).split(".")
                listed = "%s-%s-%s" % (yy, mm, dd)
            except Exception:
                listed = dm.group(1)
        hm = HREF_RE.search(ch) or HREF_RE2.search(ch)
        href = hm.group(1) if hm else ""
        if href.startswith("/"):
            href = WL + href
        out.append((d, (cm.group(1).strip() if cm else ""), listed, href))
    return out


def pull_watchlist(cfg, pages, start=1):
    wc = cfg["watchlist"]
    path = wc.get("list_path") or "/unserioese-webseiten/"
    delay = float(wc.get("page_delay_seconds") or 1.5)
    rows = []
    total = None
    for pg in range(start, start + pages):
        url = WL + path + ("?tx_solr%5Bpage%5D=" + str(pg) if pg > 1 else "")
        data = http(url, timeout=90)
        if data is None:
            say("    page %d did not load - stopping here" % pg)
            break
        html = data.decode("utf-8", "replace")
        if total is None:
            hm = HITS_RE.search(re.sub(r"<[^>]+>", " ", html))
            if hm:
                total = hm.group(1).replace(".", "")
                say("    corpus reports %s entries" % total)
        got = parse_watchlist(html)
        if not got:
            say("    page %d held no entries - end of list" % pg)
            break
        rows += got
        say("    page %-4d %4d entries  (newest on page: %s)" % (
            pg, len(got), got[0][2] or "?"))
        time.sleep(delay)
    return rows, total


# ---------------------------------------------------------------- phishing db

def pull_phishing(cfg, state, full=False):
    pc = cfg["phishing_database"]
    rows = []
    # small daily deltas
    for key in ("domains_new_today", "links_new_today"):
        fn = pc.get(key)
        if not fn:
            continue
        say("  phishing db ->", fn)
        data = http("%s/%s" % (PDB, fn), timeout=120)
        if data is None:
            continue
        n = 0
        for line in data.decode("utf-8", "replace").splitlines():
            d = norm_domain(line)
            if d:
                rows.append((d, "phishing new today", "", ""))
                n += 1
        say("    %d entries" % n)
    # the big active list, weekly
    fn = pc.get("domains_active")
    days = float(pc.get("refresh_active_days") or 7)
    last = state.get("phishing_active_at")
    due = full or not last
    if last and not due:
        try:
            age = (dt.datetime.now(dt.timezone.utc) -
                   dt.datetime.fromisoformat(last)).days
            due = age >= days
        except Exception:
            due = True
    if fn and due:
        say("  phishing db ->", fn, "(about 11 MB)")
        data = http("%s/%s" % (PDB, fn), timeout=600)
        if data is not None:
            with open(os.path.join(RAW, "phishing-domains-ACTIVE.txt.gz"), "wb") as f:
                f.write(gzip.compress(data))
            n = 0
            for line in data.decode("utf-8", "replace").splitlines():
                d = norm_domain(line)
                if d:
                    rows.append((d, "phishing active", "", ""))
                    n += 1
            say("    %d active phishing domains" % n)
            state["phishing_active_at"] = dt.datetime.now(
                dt.timezone.utc).isoformat(timespec="seconds")
    elif fn:
        say("  phishing db: active list is recent, skipped (refresh with 'refresh')")
    return rows


# ---------------------------------------------------------------- outputs

HIT_COLS = ["registrable", "brand", "match_type", "subdomains", "example_host",
            "source", "category", "listed", "found_on"]
NEW_COLS = ["domain", "sld", "tld", "source", "category", "listed", "home_tld"]


def write_hits(day, hits, new_rows, cfg):
    d = os.path.join(OUT, day)
    os.makedirs(d, exist_ok=True)
    home = set(cfg.get("home_tlds") or [])

    # One case per registrable domain, not per host. A phishing kit throws
    # hundreds of subdomains at one registration (300 x *.paypal-merchant.ru),
    # and you file one complaint against the registration, not 300.
    rolled = {}
    for h in hits:
        k = (h["registrable"], h["brand"])
        r = rolled.get(k)
        if r is None:
            rolled[k] = dict(h, subdomains=1)
        else:
            r["subdomains"] += 1
            if len(h["example_host"]) < len(r["example_host"]):
                r["example_host"] = h["example_host"]
    hits_rolled = sorted(rolled.values(), key=lambda r: (-r["subdomains"], r["registrable"]))

    # Which brands produced the hits, and by which tier. A brand showing up
    # only ever as "typo of" is usually a junk entry in the brand list rather
    # than a brand under attack - this is the file to prune from.
    per_brand = {}
    for h in hits_rolled:
        t = h["match_type"]
        tier = ("exact" if t in ("exact", "brand as a word") else
                "affix" if t.startswith(("brand +", "'")) else
                "typo" if t.startswith("typo") else "contains")
        b = per_brand.setdefault(h["brand"], {"brand": h["brand"], "exact": 0,
                                              "affix": 0, "typo": 0, "contains": 0,
                                              "domains": 0})
        b[tier] += 1
        b["domains"] += 1
    with open(os.path.join(d, "brand_hit_counts.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "domains", "exact", "affix",
                                          "typo", "contains"])
        w.writeheader()
        for b in sorted(per_brand.values(), key=lambda r: -r["domains"]):
            w.writerow(b)
    with open(os.path.join(d, "brand_hits.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HIT_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(hits_rolled)
    with open(os.path.join(d, "brand_hits_all_hosts.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["example_host", "registrable", "brand",
                                          "match_type", "source", "category", "listed"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(hits, key=lambda r: r["registrable"]))

    rows = []
    for dom, cat, listed, src in new_rows:
        sld, tld = split_domain(dom)
        rows.append({"domain": dom, "sld": sld, "tld": tld, "source": src,
                     "category": cat, "listed": listed,
                     "home_tld": "1" if tld in home else ""})
    with open(os.path.join(d, "new_listings.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NEW_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    hr = [r for r in rows if r["home_tld"]]
    with open(os.path.join(d, "new_listings_home_tlds.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NEW_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(hr)

    cats = {}
    for r in rows:
        cats[r["category"] or r["source"]] = cats.get(r["category"] or r["source"], 0) + 1
    with open(os.path.join(d, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("Fraud and phishing listings - %s\n%s\n" % (day, "=" * 40))
        f.write("new to the index    %d\n" % len(rows))
        f.write("on your TLDs (%s)  %d\n" % (",".join(sorted(home)), len(hr)))
        f.write("brand hits          %d host(s) over %d registered domain(s)\n" % (
            len(hits), len(hits_rolled)))
        tiers = {}
        for h in hits_rolled:
            t = h["match_type"]
            k = ("exact" if t in ("exact", "brand as a word") else
                 "affix" if t.startswith(("brand +", "'")) else
                 "typo" if t.startswith("typo") else "contains")
            tiers[k] = tiers.get(k, 0) + 1
        f.write("  by tier           %s\n\n" % ", ".join(
            "%s %d" % (k, v) for k, v in sorted(tiers.items(), key=lambda kv: -kv[1])))
        if cats:
            f.write("by category\n")
            for k, v in sorted(cats.items(), key=lambda kv: -kv[1]):
                f.write("  %-28s %d\n" % (k[:28], v))
        if hits_rolled:
            f.write("\nbrand hits, one line per registered domain\n")
            for h in hits_rolled[:40]:
                f.write("  %-38s %-14s x%-5d %s\n" % (
                    h["registrable"][:38], h["brand"], h["subdomains"], h["match_type"]))
        f.write("\nEvery entry is an allegation by the listing body, not a finding of\n"
                "fact. Verify before acting. See BRAND/ATTRIBUTION.txt.\n")
    return len(rows), len(hr), hits_rolled, per_brand


# ---------------------------------------------------------------- commands

def cmd_daily(cfg, state, con, pages=None):
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    brands = load_brands()
    say("watching %d brand(s)%s" % (len(brands), "" if brands else
                                    " - brands.txt is empty, so no brand matching will happen"))
    collected = []

    if cfg["watchlist"].get("enabled", True):
        n = int(pages or cfg["watchlist"].get("daily_pages") or 6)
        say("\nWatchlist Internet (%d pages)" % n)
        rows, total = pull_watchlist(cfg, n)
        if rows:
            fresh = add_rows(con, rows, "watchlist", today)
            say("    %d of %d were new to the index" % (len(fresh), len(rows)))
            collected += [(d, c, l, "watchlist") for d, c, l in fresh]
            if total:
                state["watchlist_total"] = total

    if cfg["phishing_database"].get("enabled", True):
        say("\nPhishing.Database")
        prows = pull_phishing(cfg, state)
        if prows:
            by = {}
            for r in prows:
                by.setdefault(r[1], []).append(r)
            for cat, rs in by.items():
                fresh = add_rows(con, rs, cat, today)
                say("    %s: %d new of %d" % (cat, len(fresh), len(rs)))
                collected += [(d, c, l, cat) for d, c, l in fresh]

    if not collected:
        say("\nnothing new was listed since the last run.")
        state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        save_json(STATE, state)
        return

    hits = []
    if brands:
        say("\nmatching %d new listings against %d brands" % (len(collected), len(brands)))
        m = get_matcher(brands, cfg)
        say("    indexes: %d substring prefixes, %d deletion keys" % (
            len(m.by_prefix), len(m.deletions)))
        if m.dropped_short:
            say("    ignoring %d brand(s) under %d characters: %s" % (
                len(m.dropped_short), m.minlen, ", ".join(m.dropped_short[:6])))
        t0 = time.time()
        done = 0
        for dom, cat, listed, src in collected:
            done += 1
            if done % 100000 == 0:
                say("    %d/%d (%.0fs)" % (done, len(collected), time.time() - t0))
            sld, tld = split_domain(dom)
            for b, how in brand_hits(dom, brands, cfg):
                hits.append({"registrable": "%s.%s" % (sld, tld), "brand": b,
                             "match_type": how, "example_host": dom, "subdomains": 1,
                             "source": src, "category": cat,
                             "listed": listed, "found_on": today})
    n_new, n_home, rolled, per_brand = write_hits(today, hits, collected, cfg)
    state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state.setdefault("days", {})[today] = {"new": n_new, "hits": len(hits)}
    save_json(STATE, state)
    say("\n--- done ---")
    say("  %d newly listed, %d on your TLDs" % (n_new, n_home))
    say("  %d brand hits over %d registered domain(s)" % (len(hits), len(rolled)))
    tiers = {}
    for h in rolled:
        t = h["match_type"]
        k = ("exact" if t in ("exact", "brand as a word") else
             "affix" if t.startswith(("brand +", "'")) else
             "typo" if t.startswith("typo") else "contains")
        tiers[k] = tiers.get(k, 0) + 1
    if tiers:
        say("  by tier: %s" % ", ".join("%s %d" % (k, v) for k, v in
                                        sorted(tiers.items(), key=lambda kv: -kv[1])))
    strong = [h for h in rolled if h["match_type"] in ("exact", "brand as a word")
              or h["match_type"].startswith(("brand +", "'"))]
    if strong:
        say("\n  strongest cases (brand used as a word, or brand + shop/store/login):")
        for h in strong[:15]:
            say("    %-38s %-14s x%-4d %s" % (
                h["registrable"][:38], h["brand"], h["subdomains"], h["match_type"]))
    elif rolled:
        say("\n  no exact or affix matches today; the typo tier found:")
        for h in rolled[:10]:
            say("    %-38s %-14s %s" % (h["registrable"][:38], h["brand"], h["match_type"]))
    noisy = [b for b in per_brand.values()
             if b["typo"] >= 1 and b["exact"] == 0 and b["affix"] == 0]
    if len(noisy) >= 5:
        say("\n  %d brand(s) matched only by typo and never exactly - usually a sign the\n"
            "  brand list holds a truncated or generic entry. Worth pruning:" % len(noisy))
        say("    %s" % ", ".join(sorted(b["brand"] for b in noisy)[:14]))
        say("  full breakdown: brand_hit_counts.csv")
    say("\nfiles: %s" % os.path.join(OUT, today))


def cmd_backfill(cfg, state, con, pages):
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    say("walking the Watchlist corpus, %d pages, %.1fs apart" % (
        pages, float(cfg["watchlist"].get("page_delay_seconds") or 1.5)))
    say("At 50 entries a page this is about %d entries.\n" % (pages * 50))
    rows, total = pull_watchlist(cfg, pages)
    if not rows:
        say("nothing came back")
        return
    fresh = add_rows(con, rows, "watchlist", today)
    say("\n%d entries read, %d new to the index" % (len(rows), len(fresh)))
    state["backfilled_pages"] = pages
    save_json(STATE, state)


def cmd_check(cfg, con, arg):
    brands = load_brands()
    q = norm_domain(arg) or re.sub(r"[^a-z0-9]", "", (arg or "").lower())
    if not q:
        say("give me a domain or a brand word")
        return
    exact = con.execute("SELECT domain,source,category,listed,first_seen FROM fraud "
                        "WHERE domain=?", (q,)).fetchall()
    if exact:
        say("LISTED: %s" % q)
        for d, s, c, l, fs in exact:
            say("  %-22s %-26s listed %s (we saw it %s)" % (s, c or "-", l or "?", fs))
    else:
        say("not listed: %s" % q)
    sld, _ = split_domain(q) if "." in q else (q, "")
    stem = re.sub(r"[^a-z0-9]", "", sld)
    if len(stem) >= 4:
        like = con.execute("SELECT domain,source,category,listed FROM fraud "
                           "WHERE domain LIKE ? LIMIT 60", ("%" + stem + "%",)).fetchall()
        like = [r for r in like if r[0] != q]
        if like:
            say("\n%d listed domain(s) contain '%s':" % (len(like), stem))
            for d, s, c, l in like[:40]:
                say("  %-44s %-20s %s" % (d[:44], c or s, l or ""))
        else:
            say("\nnothing else listed contains '%s'" % stem)
    if brands:
        hits = brand_hits(q, brands, cfg) if "." in q else []
        if hits:
            say("\nthis domain looks like your brand(s): %s" % ", ".join(
                "%s (%s)" % (b, h) for b, h in hits))


def cmd_victims(cfg, con, n=40):
    """Which brand words recur across fake-shop domains.

    The brands being impersonated are the companies that need brand
    protection - so this doubles as a Red Points prospect list.
    """
    say("mining the fake-shop corpus for the brand words that recur\n")
    rows = con.execute("SELECT domain FROM fraud WHERE source='watchlist'").fetchall()
    if not rows:
        say("the index has no Watchlist entries yet - run 'backfill 200' first")
        return
    say("corpus: %d domains" % len(rows))
    stop = set("""shop store online sale sales outlet shoping shopping buy best cheap
official site web webshop the and for new top deal deals discount de at ch sk cz com
net org eu info live icu xyz top vip club fun online store space website press host
mall market markt kaufen guenstig billig angebot verkauf preis bestellen lager
versand express service center point world global group company inc ltd gmbh""".split())
    freq = {}
    for (d,) in rows:
        sld, _ = split_domain(d)
        for w in core_words(sld):
            if len(w) < 4 or w in stop or w.isdigit():
                continue
            freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda kv: -kv[1])[:int(n)]
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    d = os.path.join(OUT, day)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "impersonated_words.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write("word,fake_domains\n")
        for w, c in sorted(freq.items(), key=lambda kv: -kv[1])[:2000]:
            f.write("%s,%d\n" % (w, c))
    say("\n%-26s %s" % ("WORD", "FAKE DOMAINS USING IT"))
    for w, c in top:
        say("%-26s %d" % (w, c))
    say("\nsaved: %s" % p)
    say("\nRead this as a lead list, not a verdict: a word recurring across many\n"
        "fake shops usually means a brand is being ridden, but generic retail\n"
        "words surface here too. Eyeball it before you pitch anyone.")


def cmd_cross_nrd(cfg, con, days=7):
    """Newly registered domains that are already on a fraud list."""
    nrd = cfg.get("nrd_dir") or os.path.join(APP, "NRD")
    daily = os.path.join(nrd, "daily")
    if not os.path.isdir(daily):
        say("no NRD feed found at %s" % daily)
        say("Run nrd_feed.py first, or set nrd_dir in config.json.")
        return
    files = sorted(f for f in os.listdir(daily) if f.endswith(".txt"))[-int(days):]
    if not files:
        say("the NRD feed has no daily files yet")
        return
    brands = load_brands()
    say("checking %d day(s) of newly registered domains against the fraud index\n" % len(files))
    rows = []
    for fn in files:
        day = fn[:-4]
        with open(os.path.join(daily, fn), encoding="utf-8", errors="replace") as f:
            doms = [x.strip() for x in f if x.strip()]
        listed, squat = 0, 0
        for d in doms:
            r = con.execute("SELECT source,category,listed FROM fraud WHERE domain=?",
                            (d,)).fetchone()
            if r:
                rows.append({"domain": d, "registered": day, "kind": "already listed",
                             "detail": "%s / %s" % (r[0], r[1] or "-"), "brand": ""})
                listed += 1
            elif brands:
                for b, how in brand_hits(d, brands, cfg):
                    rows.append({"domain": d, "registered": day, "kind": "brand lookalike",
                                 "detail": how, "brand": b})
                    squat += 1
                    break
        say("  %s  %6d new domains  %3d already listed  %3d brand lookalikes" % (
            day, len(doms), listed, squat))
    if not rows:
        say("\nno overlap found.")
        return
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    d = os.path.join(OUT, day)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "nrd_cross_check.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["domain", "registered", "kind", "detail", "brand"])
        w.writeheader()
        w.writerows(rows)
    say("\n%d overlaps -> %s" % (len(rows), p))
    for r in rows[:20]:
        say("  %-44s %-16s %s %s" % (r["domain"][:44], r["kind"], r["brand"], r["detail"]))


def cmd_import_brands(cfg, path=None):
    """Pull a brand column out of an existing CSV into brands.txt."""
    cand = [path] if path else [
        os.path.join(os.path.dirname(APP), "_CLEAN_2026-11-02", "SIGNALS", "brands.csv"),
        os.path.join(os.path.dirname(APP), "Red Points", "brands.csv"),
        os.path.join(APP, "brands.csv"),
    ]
    src = next((p for p in cand if p and os.path.exists(p)), None)
    if not src:
        say("no brands.csv found. Looked in:")
        for p in cand:
            say("   ", p)
        say("\nPass a path: brand_protect.py import-brands \"C:\\path\\brands.csv\"")
        return
    say("reading", src)
    rows = []
    with open(src, encoding="utf-8-sig", errors="replace") as f:
        rd = csv.DictReader(f)
        cols = [c for c in (rd.fieldnames or [])]
        pick = next((c for c in cols if c and c.strip().lower() in
                     ("brand", "brand_name", "name", "company")), None)
        if not pick:
            say("no brand/name/company column found. Columns:", cols)
            return
        say("using column '%s'" % pick)
        for r in rd:
            v = (r.get(pick) or "").strip().lower()
            v = re.sub(r"\.(com|net|org|shop|store|co|io|de|sk|cz)(\.[a-z]{2})?$", "", v)
            v = re.sub(r"[^a-z0-9]", "", v)
            if len(v) >= 3:
                rows.append(v)
    uniq = list(dict.fromkeys(rows))
    short = [b for b in uniq if len(b) < int(cfg["brand_match"].get("min_brand_len") or 4)]
    with open(BRANDS, "w", encoding="utf-8") as f:
        f.write("# Imported from %s on %s\n" % (src, dt.date.today().isoformat()))
        f.write("# %d brands. Lines starting with # are ignored.\n" % len(uniq))
        if short:
            f.write("# %d are shorter than min_brand_len and will be matched exactly only.\n"
                    % len(short))
        f.write("#\n")
        for b in uniq:
            f.write(b + "\n")
    say("wrote %d brands to %s" % (len(uniq), BRANDS))
    if short:
        say("%d are very short (%s...) - matched exactly only, which is what you want"
            % (len(short), ", ".join(short[:5])))
    say("\nNow run 'daily'. With this many brands the fuzzy pass does real work:")
    say("expect a couple of minutes over the full phishing corpus, not seconds.")


def cmd_brands(cfg):
    b = load_brands()
    if not b:
        say("brands.txt is empty: %s" % BRANDS)
        say("Add one brand word per line - 'nike', not 'nike.com'.")
        return
    bm = cfg["brand_match"]
    say("%d brand(s) watched, fuzzy distance %s, exact-only below %s characters\n" % (
        len(b), bm.get("edit_distance"), bm.get("min_brand_len")))
    for x in b:
        say("  %-24s %s" % (x, "(exact only - short)" if len(x) < int(bm.get("min_brand_len") or 4) else ""))


def cmd_status(cfg, state, con):
    say("folder:   ", BASE)
    say("last run: ", state.get("last_run") or "never")
    try:
        for src, n, lo, hi in con.execute(
                "SELECT source, COUNT(*), MIN(first_seen), MAX(first_seen) "
                "FROM fraud GROUP BY source ORDER BY 2 DESC"):
            say("  %-24s %8d  %s .. %s" % (src, n, lo, hi))
        tot = con.execute("SELECT COUNT(DISTINCT domain) FROM fraud").fetchone()[0]
        say("  %-24s %8d distinct domains" % ("TOTAL", tot))
    except Exception as e:
        say("index unreadable:", e)
    if state.get("watchlist_total"):
        say("\nWatchlist reports %s entries upstream" % state["watchlist_total"])
    if state.get("phishing_active_at"):
        say("phishing active list downloaded %s" % state["phishing_active_at"][:10])
    say("brands watched: %d" % len(load_brands()))


def cmd_schedule(cfg, when=None):
    when = when or cfg.get("schedule_time") or "07:50"
    if os.name != "nt":
        say("Windows only. cron equivalent:")
        say("  50 7 * * *  %s %s daily" % (sys.executable, os.path.abspath(__file__)))
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


def main(argv):
    cfg = load_config()
    state = load_json(STATE, {})
    cmd = (argv[0] if argv else "daily").lower()
    rest = argv[1:]

    if cmd == "brands":
        return cmd_brands(cfg)
    if cmd in ("import-brands", "import_brands", "importbrands"):
        return cmd_import_brands(cfg, rest[0] if rest else None)
    if cmd == "schedule":
        return cmd_schedule(cfg, rest[0] if rest else None)
    if cmd == "unschedule":
        if os.name != "nt":
            return say("nothing to remove")
        import subprocess
        r = subprocess.run(['schtasks', '/Delete', '/TN', TASK_NAME, '/F'],
                           capture_output=True, text=True)
        return say((r.stdout or "").strip() or (r.stderr or "").strip() or "removed")

    con = open_index()
    try:
        if cmd == "daily":
            cmd_daily(cfg, state, con)
        elif cmd == "refresh":
            say("re-downloading the active phishing list")
            rows = pull_phishing(cfg, state, full=True)
            if rows:
                today = dt.datetime.now(dt.timezone.utc).date().isoformat()
                by = {}
                for r in rows:
                    by.setdefault(r[1], []).append(r)
                for cat, rs in by.items():
                    say("  %s: %d new" % (cat, len(add_rows(con, rs, cat, today))))
            save_json(STATE, state)
        elif cmd == "backfill":
            cmd_backfill(cfg, state, con, int(rest[0]) if rest else 788)
        elif cmd == "check":
            if not rest:
                say("usage: brand_protect.py check mybrand-shop.de")
            else:
                cmd_check(cfg, con, rest[0])
        elif cmd == "victims":
            cmd_victims(cfg, con, rest[0] if rest else 40)
        elif cmd in ("cross-nrd", "cross_nrd", "crossnrd"):
            cmd_cross_nrd(cfg, con, rest[0] if rest else 7)
        elif cmd == "status":
            cmd_status(cfg, state, con)
        else:
            say(__doc__)
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        say("\nstopped")
