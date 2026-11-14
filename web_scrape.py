"""
web_scrape.py - the two live-web jobs, on their own: find a site's contact pages,
and crawl a site for addresses. No Common Crawl, no archive - these read the sites as they are today.

  python web_scrape.py links  --in domains.csv [--par 20] [--limit N]
        One request per site. Pulls every link off the homepage, works out which are
        contact / about / team / legal / careers pages and which are subdomains,
        and writes the best contact page it found per domain.
        -> WEB_SCRAPE/links_<date>.csv  and  WEB_SCRAPE/contact_pages_<date>.csv

  python web_scrape.py emails --in domains.csv [--par 20] [--pages 5] [--delay 1.0] [--limit N]
        Homepage first, then follows the most promising internal pages (contact, kontakt,
        impressum, o-nas, team ...) up to --pages per site, and reads addresses and phone
        numbers off all of them. -> WEB_SCRAPE/emails_<date>.csv

  python web_scrape.py status     what has been done
  python web_scrape.py export     all addresses found so far, one row per domain

Both are resumable: a domain already done is skipped. Both honour robots.txt unless you
pass --ignore-robots, identify themselves in the User-Agent, and leave a pause between
requests to the same site. Keep --par sane: these are other people's servers.
"""
import os, re, sys, csv, json, time, glob, asyncio, argparse, datetime
import urllib.parse, urllib.robotparser

csv.field_size_limit(10 ** 9)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(HERE))
OUT = os.path.join(ROOT, "WEB_SCRAPE")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 (+contact-finder; polite, obeys robots.txt)")
MAX_BYTES = 1_500_000
EMAIL_RX = re.compile(r"(?<![a-z0-9._%+\-])[a-z0-9][a-z0-9._%+\-]{0,63}@[a-z0-9\-]{1,63}"
                      r"(?:\.[a-z0-9\-]{1,63}){0,4}\.[a-z]{2,24}\b")
OBF_RX = re.compile(r"(?<![a-z0-9._%+\-])([a-z0-9._%+\-]{1,64}) ?(?:\[at\]|\(at\)|\{at\}| at ) ?"
                    r"([a-z0-9\-]{1,63}(?: ?(?:\[dot\]|\(dot\)| dot |\.) ?[a-z0-9\-]{1,63}){1,4})")
CF_RX = re.compile(r'data-cfemail="([0-9a-f]+)"|/cdn-cgi/l/email-protection#([0-9a-f]+)')
TEL_RX = re.compile(r'href=["\']tel:([+0-9()\-. ]{7,20})')
A_RX = re.compile(r'<a\b[^>]*?href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', re.S | re.I)
TAG_RX = re.compile(r"<[^>]+>")
TITLE_RX = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
JUNK = ("sentry", "wixpress", "example.", "domain.com", "yourdomain", "email.com", "@2x", "godaddy",
        "squarespace", "wix.com", "sentry.io", "@sentry", "jquery", "bootstrap", "cloudflare")
BAD_END = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico", ".pdf", ".mp4", ".woff", ".woff2")
ROLE = ("info", "kontakt", "contact", "office", "sales", "obchod", "predaj", "hello", "ahoj", "mail",
        "firma", "post", "podatelna", "sekretariat")
NOISE = ("noreply", "no-reply", "donotreply", "postmaster", "abuse", "privacy", "webmaster", "mailer-daemon")

# what a link looks like when it leads to the page you want. Slovak, Czech, German, Polish,
# Hungarian and English, because that is what his lists are full of.
KIND_RX = [
    ("contact", r"kontakt|contact|contacto|contatti|kapcsolat|napiste|napis-nam|write-us|reach-us|get-in-touch"),
    ("about",   r"o-nas|o_nas|onas|about|about-us|uber-uns|ueber-uns|rolunk|quienes|chi-siamo|firma|spolocnost|company"
                r"|our-story|who-we-are|founder|zakladatel|press|partner|wholesale|velkoobchod"),
    ("team",    r"team|tim|tym|ludia|people|staff|zamestnanci|vedenie|management|leadership"),
    # legal pages are where a Slovak or Czech company actually prints its address, IČO and mailbox
    ("legal",   r"impressum|imprint|legal|mentions-legales|obchodne-podmienky|vseobecne-podmienky|terms|privacy"
                r"|gdpr|ochrana-osobnych|ochrana-udaju|zasady|policies|refund|reklamacie|shipping|doprava"),
    ("careers", r"kariera|kariera|career|jobs|praca|prace|stellenangebote|volne-miesta"),
    ("support", r"podpora|support|helpdesk|servis|service"),
]
KIND_RX = [(k, re.compile(v, re.I)) for k, v in KIND_RX]
CRAWL_ORDER = ("contact", "legal", "about", "team", "support", "careers")
# when a site's menu is built by JavaScript there are no links to read, so try the usual addresses
FALLBACK_PATHS = [("contact", "/kontakt"), ("contact", "/contact"), ("contact", "/contact-us"),
                  ("contact", "/kontakty"), ("contact", "/pages/contact"),
                  ("legal", "/impressum"), ("legal", "/obchodne-podmienky"),
                  ("legal", "/ochrana-osobnych-udajov"), ("legal", "/privacy-policy"),
                  ("legal", "/policies/privacy-policy"), ("legal", "/privacy"),
                  ("about", "/o-nas"), ("about", "/about"), ("about", "/about-us"), ("about", "/pages/about")]


def say(o):
    print(json.dumps(o, ensure_ascii=False, indent=1), flush=True)
    sys.exit(0 if o.get("ok", True) else 1)


def need_aiohttp():
    try:
        import aiohttp                                        # noqa: F401
    except ImportError:
        say({"ok": False, "error": "aiohttp is not installed. Run: pip install aiohttp"})


def cf_decode(hexs):
    try:
        b = bytes.fromhex(hexs)
        k = b[0]
        return "".join(chr(c ^ k) for c in b[1:])
    except Exception:
        return ""


def find_emails(html):
    """Plain addresses, [at]/[dot] ones, and Cloudflare-obfuscated ones."""
    low = html.lower()
    out = set(EMAIL_RX.findall(low))
    for a, b in OBF_RX.findall(low):
        d = re.sub(r"\s*(?:\[dot\]|\(dot\)| dot )\s*", ".", b).replace(" ", "")
        e = (a + "@" + d).strip(".")
        if EMAIL_RX.fullmatch(e):
            out.add(e)
    for m in CF_RX.findall(html):
        e = cf_decode(m[0] or m[1]).lower()
        if EMAIL_RX.fullmatch(e):
            out.add(e)
    return {e for e in out
            if not e.endswith(BAD_END) and not any(j in e for j in JUNK) and len(e) < 100
            and not re.match(r"^[0-9a-f]{16,}@", e)}


def rank_email(e, domain):
    """Lower is better: the site's own role address beats a personal one beats a free one."""
    user, _, host = e.partition("@")
    own = host == domain or host.endswith("." + domain) or domain.endswith("." + host)
    role = any(user == r or user.startswith(r) for r in ROLE)
    noise = any(n in user for n in NOISE)
    return (noise, not own, not role, len(e))


def host_of(u):
    try:
        return urllib.parse.urlsplit(u).hostname or ""
    except ValueError:
        return ""


def reg_domain(h):
    """Good enough for 'is this the same company': keeps two labels, three for co.uk-style."""
    p = (h or "").lower().lstrip(".").split(".")
    if len(p) < 3:
        return ".".join(p)
    if p[-2] in ("co", "com", "org", "net", "gov", "ac", "edu") and len(p[-1]) == 2:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def kind_of(url, text=""):
    s = (urllib.parse.unquote(url) + " " + text).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    for k, rx in KIND_RX:
        if rx.search(s):
            return k
    return ""


def read_domains(path, limit=0):
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if not os.path.exists(p):
        return [], "There is no file at %s" % p
    seen, out = set(), []
    with open(p, encoding="utf-8-sig", errors="replace", newline="") as fh:
        head = fh.readline()
        fh.seek(0)
        if "," in head or ";" in head or "\t" in head:
            rd = csv.DictReader(fh, delimiter=";" if head.count(";") > head.count(",") else ",")
            col = None
            for c in (rd.fieldnames or []):
                if (c or "").strip().lower() in ("domain", "website", "url", "web", "site", "company_domain"):
                    col = c
                    break
            if not col:
                return [], "No domain, website or url column in: " + ", ".join([c for c in (rd.fieldnames or [])][:10])
            vals = (r.get(col) for r in rd)
        else:
            vals = (l for l in fh)
        for v in vals:
            d = re.sub(r"^\s*(?:https?://)?(?:www\.)?", "", str(v or "").strip(), flags=re.I)
            d = d.split("/")[0].split(":")[0].strip().lower().strip(".,;")
            if not d or "." not in d or " " in d or d in seen:
                continue
            seen.add(d)
            out.append(d)
            if limit and len(out) >= limit:
                break
    return out, ""


def ledger(name):
    return os.path.join(OUT, "_done_%s.txt" % name)


def done_set(name):
    p = ledger(name)
    if not os.path.exists(p):
        return set()
    with open(p, encoding="utf-8") as fh:
        return {l.strip() for l in fh if l.strip()}


def mark_done(name, domains):
    os.makedirs(OUT, exist_ok=True)
    with open(ledger(name), "a", encoding="utf-8") as fh:
        for d in domains:
            fh.write(d + "\n")


# ------------------------------------------------------------------ fetching
class Web:
    def __init__(self, session, delay=1.0, obey_robots=True):
        self.s = session
        self.delay = max(0.0, delay)
        self.obey = obey_robots
        self.robots = {}
        self.last = {}

    async def allowed(self, url):
        if not self.obey:
            return True
        sp = urllib.parse.urlsplit(url)
        key = sp.scheme + "://" + (sp.netloc or "")
        rp = self.robots.get(key)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            lines = []
            # same scheme and port as the page we are about to ask for, http-only sites included
            for base in (key, ("http://" if sp.scheme == "https" else "https://") + (sp.netloc or "")):
                try:
                    async with self.s.get(base + "/robots.txt", allow_redirects=True,
                                          headers={"User-Agent": UA}) as r:
                        if r.status == 200:
                            lines = (await r.text(errors="replace")).splitlines()
                            break
                        if r.status in (401, 403):      # robots itself is protected: stay out
                            lines = ["User-agent: *", "Disallow: /"]
                            break
                except Exception:
                    continue
            rp.parse(lines)
            self.robots[key] = rp
        try:
            return rp.can_fetch(UA, url)
        except Exception:
            return True

    async def get(self, url):
        """One page. Returns (final_url, status, html)."""
        h = host_of(url)
        if not h:
            return url, 0, ""
        wait = self.delay - (time.time() - self.last.get(h, 0))
        if wait > 0:
            await asyncio.sleep(wait)
        self.last[h] = time.time()
        if not await self.allowed(url):
            return url, -1, ""
        try:
            async with self.s.get(url, allow_redirects=True, headers={"User-Agent": UA}) as r:
                ct = (r.headers.get("Content-Type") or "").lower()
                if r.status != 200 or ("html" not in ct and "text" not in ct and ct):
                    return str(r.url), r.status, ""
                raw = await r.content.read(MAX_BYTES)
                return str(r.url), r.status, raw.decode(r.charset or "utf-8", errors="replace")
        except asyncio.TimeoutError:
            return url, 0, ""
        except Exception:
            return url, 0, ""

    async def home(self, domain):
        for u in ("https://" + domain, "http://" + domain):
            fu, code, html = await self.get(u)
            if html:
                return fu, code, html
            if code == -1:
                return u, -1, ""
        return "https://" + domain, 0, ""


def links_on(page_url, html):
    base = page_url
    out = []
    for href, text in A_RX.findall(html or "")[:800]:
        href = href.strip()
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        try:
            u = urllib.parse.urljoin(base, href)
        except ValueError:
            continue
        if not u.lower().startswith("http") or u.lower().endswith(BAD_END):
            continue
        txt = TAG_RX.sub(" ", text)
        txt = re.sub(r"\s+", " ", txt).strip()[:120]
        out.append((u.split("#")[0], txt))
    return out


# ------------------------------------------------------------------ link extractor
async def do_links(domains, par, delay, obey, out_rows, best_rows, st):
    import aiohttp
    q = asyncio.Queue()
    for d in domains:
        q.put_nowait(d)

    async def worker(w):
        while True:
            try:
                dom = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            fu, code, html = await w.home(dom)
            st["n"] += 1
            if not html:
                st["unreachable"] += 1
                best_rows.append({"domain": dom, "status": "BLOCKED_BY_ROBOTS" if code == -1 else "NO_PAGE",
                                  "home_url": fu, "contact_url": "", "contact_kind": "", "links": 0,
                                  "internal": 0, "subdomains": ""})
                continue
            rd = reg_domain(dom)
            seen, internal, subs, found = set(), 0, set(), {}
            for u, txt in links_on(fu, html):
                if u in seen:
                    continue
                seen.add(u)
                h = host_of(u)
                same = reg_domain(h) == rd
                if same:
                    internal += 1
                    if h and h not in (dom, "www." + dom):
                        subs.add(h)
                k = kind_of(u, txt)
                if k:
                    out_rows.append({"domain": dom, "page": fu, "url": u, "text": txt, "kind": k,
                                     "internal": int(same), "subdomain": h if same and h not in (dom, "www." + dom) else ""})
                    if same and k not in found:
                        found[k] = u
            st["links"] += len(seen)
            st["with_contact"] += bool(found.get("contact"))
            pick = next(((k, found[k]) for k in CRAWL_ORDER if k in found), ("", ""))
            best_rows.append({"domain": dom, "status": "OK", "home_url": fu,
                              "contact_url": pick[1], "contact_kind": pick[0], "links": len(seen),
                              "internal": internal, "subdomains": ";".join(sorted(subs)[:10])})

    conn = aiohttp.TCPConnector(limit=par + 10, ttl_dns_cache=3600, ssl=False)
    async with aiohttp.ClientSession(trust_env=True, connector=conn,
                                     timeout=aiohttp.ClientTimeout(total=25, connect=12)) as s:
        w = Web(s, delay, obey)
        await asyncio.gather(*[worker(w) for _ in range(par)])


def step_links(path, par, limit, delay, obey):
    need_aiohttp()
    doms, err = read_domains(path, limit)
    if err:
        say({"ok": False, "error": err})
    done = done_set("links")
    doms = [d for d in doms if d not in done]
    if not doms:
        say({"ok": True, "scanned": 0, "note": "Every domain in that file has been scanned already."})
    os.makedirs(OUT, exist_ok=True)
    print("%d sites, %d at a time, %.1fs between requests to the same site%s"
          % (len(doms), par, delay, "" if obey else " (ignoring robots.txt)"), flush=True)
    rows, best, st = [], [], {"n": 0, "links": 0, "with_contact": 0, "unreachable": 0}
    t0 = time.time()

    async def runner():
        tk = asyncio.create_task(progress(st, len(doms), t0))
        try:
            await do_links(doms, par, delay, obey, rows, best, st)
        finally:
            tk.cancel()
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(runner())
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    lp = os.path.join(OUT, "links_%s.csv" % ts)
    bp = os.path.join(OUT, "contact_pages_%s.csv" % ts)
    write(lp, ["domain", "page", "url", "text", "kind", "internal", "subdomain"], rows)
    write(bp, ["domain", "status", "home_url", "contact_url", "contact_kind", "links", "internal", "subdomains"], best)
    mark_done("links", [b["domain"] for b in best])
    return say({"ok": True, "scanned": st["n"], "unreachable": st["unreachable"],
                "links_kept": len(rows), "sites_with_a_contact_page": st["with_contact"],
                "contact_pages": os.path.relpath(bp, ROOT).replace(os.sep, "/"),
                "all_links": os.path.relpath(lp, ROOT).replace(os.sep, "/"),
                "next": "Crawl for emails uses these contact pages first."})


# ------------------------------------------------------------------ email crawler
async def do_emails(domains, par, pages, delay, obey, rows, st):
    import aiohttp
    q = asyncio.Queue()
    for d in domains:
        q.put_nowait(d)

    async def worker(w):
        while True:
            try:
                dom = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            fu, code, html = await w.home(dom)
            st["n"] += 1
            if not html:
                st["unreachable"] += 1
                rows.append({"domain": dom, "status": "BLOCKED_BY_ROBOTS" if code == -1 else "NO_PAGE",
                             "pages_crawled": 0, "best_email": "", "emails": "", "phones": "",
                             "found_on": "", "home_url": fu, "title": ""})
                continue
            rd = reg_domain(dom)
            emails, phones, found_on = {}, set(), {}
            title = TAG_RX.sub(" ", (TITLE_RX.search(html).group(1) if TITLE_RX.search(html) else "")).strip()[:160]
            for e in find_emails(html):
                emails.setdefault(e, fu)
            phones |= {re.sub(r"\s+", "", t) for t in TEL_RX.findall(html.lower())}
            # which internal pages are worth a look, best kind first
            cand, seen = {}, set()
            for u, txt in links_on(fu, html):
                if u in seen or reg_domain(host_of(u)) != rd:
                    continue
                seen.add(u)
                k = kind_of(u, txt)
                if k and k in CRAWL_ORDER:
                    cand.setdefault(k, []).append(u)
            todo = []
            for k in CRAWL_ORDER:
                for u in cand.get(k, [])[:2]:
                    if u != fu and len(todo) < pages:
                        todo.append((k, u))
            if len(todo) < min(3, pages):
                # nothing useful in the menu (JavaScript navigation, or a one-page site): guess the usual paths
                base = fu.rstrip("/")
                have = {u for _, u in todo}
                for k, path in FALLBACK_PATHS:
                    if len(todo) >= pages:
                        break
                    u = base + path
                    if u not in have and u != fu:
                        have.add(u)
                        todo.append((k, u))
            crawled = 1
            for k, u in todo:
                _, c2, h2 = await w.get(u)
                if not h2:
                    continue
                crawled += 1
                for e in find_emails(h2):
                    if e not in emails:
                        emails[e] = u
                        found_on[e] = k
                phones |= {re.sub(r"\s+", "", t) for t in TEL_RX.findall(h2.lower())}
            ranked = sorted(emails, key=lambda e: rank_email(e, rd))
            best = ranked[0] if ranked else ""
            st["email"] += bool(best)
            st["phone"] += bool(phones)
            st["pages"] += crawled
            rows.append({"domain": dom, "status": "OK", "pages_crawled": crawled, "best_email": best,
                         "emails": ";".join(ranked[:15]), "phones": ";".join(sorted(phones)[:5]),
                         "found_on": emails.get(best, ""), "home_url": fu, "title": title})

    conn = aiohttp.TCPConnector(limit=par + 10, ttl_dns_cache=3600, ssl=False)
    async with aiohttp.ClientSession(trust_env=True, connector=conn,
                                     timeout=aiohttp.ClientTimeout(total=25, connect=12)) as s:
        w = Web(s, delay, obey)
        await asyncio.gather(*[worker(w) for _ in range(par)])


async def progress(st, total, t0):
    while True:
        await asyncio.sleep(5)
        n = st["n"]
        el = max(1e-6, time.time() - t0)
        rate = n / el
        bits = ["%d/%d sites" % (n, total), "%.0f/min" % (rate * 60)]
        if "email" in st:
            bits.append("%d with an address" % st["email"])
        if "with_contact" in st:
            bits.append("%d with a contact page" % st["with_contact"])
        bits.append("about %.0f min left" % ((total - n) / max(0.001, rate) / 60))
        print(" | ".join(bits), flush=True)


def write(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def step_emails(path, par, pages, limit, delay, obey):
    need_aiohttp()
    doms, err = read_domains(path, limit)
    if err:
        say({"ok": False, "error": err})
    done = done_set("emails")
    doms = [d for d in doms if d not in done]
    if not doms:
        say({"ok": True, "crawled": 0, "note": "Every domain in that file has been crawled already."})
    os.makedirs(OUT, exist_ok=True)
    print("%d sites, up to %d pages each, %d at a time, %.1fs between requests to the same site%s"
          % (len(doms), pages + 1, par, delay, "" if obey else " (ignoring robots.txt)"), flush=True)
    rows, st = [], {"n": 0, "email": 0, "phone": 0, "pages": 0, "unreachable": 0}
    t0 = time.time()

    async def runner():
        tk = asyncio.create_task(progress(st, len(doms), t0))
        try:
            await do_emails(doms, par, pages, delay, obey, rows, st)
        finally:
            tk.cancel()
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(runner())
    p = os.path.join(OUT, "emails_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    write(p, ["domain", "status", "best_email", "emails", "phones", "pages_crawled", "found_on",
              "home_url", "title"], rows)
    mark_done("emails", [r["domain"] for r in rows])
    return say({"ok": True, "crawled": st["n"], "pages_fetched": st["pages"], "unreachable": st["unreachable"],
                "with_email": st["email"], "with_phone": st["phone"],
                "hit_rate": "%.0f%%" % (100 * st["email"] / max(1, st["n"])),
                "file": os.path.relpath(p, ROOT).replace(os.sep, "/"),
                "sample": [{"domain": r["domain"], "best_email": r["best_email"]}
                           for r in rows if r["best_email"]][:5]})


def step_status():
    def count(pat, field=""):
        tot = hit = 0
        for p in glob.glob(os.path.join(OUT, pat)):
            with open(p, encoding="utf-8", errors="replace", newline="") as fh:
                for r in csv.DictReader(fh):
                    tot += 1
                    if field and (r.get(field) or "").strip():
                        hit += 1
        return tot, hit
    e_tot, e_hit = count("emails_*.csv", "best_email")
    c_tot, c_hit = count("contact_pages_*.csv", "contact_url")
    return say({"ok": True, "sites_scanned_for_links": c_tot, "contact_pages_found": c_hit,
                "sites_crawled_for_emails": e_tot, "addresses_found": e_hit,
                "hit_rate": "%.0f%%" % (100 * e_hit / max(1, e_tot)) if e_tot else "-",
                "folder": os.path.relpath(OUT, ROOT).replace(os.sep, "/")})


def step_export():
    best, cols = {}, []
    for p in sorted(glob.glob(os.path.join(OUT, "emails_*.csv")), reverse=True):
        with open(p, encoding="utf-8", errors="replace", newline="") as fh:
            rd = csv.DictReader(fh)
            cols = cols or (rd.fieldnames or [])
            for r in rd:
                d = (r.get("domain") or "").strip().lower()
                if d and d not in best and (r.get("best_email") or "").strip():
                    best[d] = r
    if not best:
        say({"ok": False, "error": "No addresses found yet."})
    out = os.path.join(OUT, "contacts_found.csv")
    write(out, cols, list(best.values()))
    return say({"ok": True, "domains_with_an_address": len(best),
                "file": os.path.relpath(out, ROOT).replace(os.sep, "/"),
                "note": "Ready to upload to Manyreach from the Outreach page."})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["links", "emails", "status", "export"])
    ap.add_argument("--in", dest="inp", default="")
    ap.add_argument("--par", type=int, default=20)
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--ignore-robots", action="store_true")
    a = ap.parse_args()
    obey = not a.ignore_robots
    par = max(1, min(100, a.par))
    if a.cmd == "links":
        step_links(a.inp, par, a.limit, a.delay, obey)
    elif a.cmd == "emails":
        step_emails(a.inp, par, max(0, min(25, a.pages)), a.limit, a.delay, obey)
    elif a.cmd == "status":
        step_status()
    else:
        step_export()


if __name__ == "__main__":
    main()
