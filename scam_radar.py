#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scam_radar.py - catch a fake shop before anybody lists it.

brand_protect.py finds fake shops that a consumer body has ALREADY published.
Useful, but by then the shop has been trading for weeks. This runs the other
way round:

    today's ~390k newly registered domains   (nrd_feed.py)
                      x
    560,827 brands with contacts             (your Red Points folders)
                      x
    the naming patterns that confirmed Austrian fake shops actually use
                      -
    everything already on a fraud list       (fraud.sqlite)
                      =
    domains that look like a fake shop being set up right now

The pattern vocabulary is not guessed. 'patterns' mines it out of the
Watchlist Internet corpus: drykorngeschaft.com is brand+geschaft,
giesswein.com.de is brand+com.de, boseaustria.com is brand+country. Those are
what German-language fake shops are actually called, and the radar looks for
the same shapes in domains registered in the last few days.

A candidate is a LEAD, NOT A FINDING. These domains are on no fraud list -
that is the whole point - so the message this writes says a domain was newly
registered using the brand's name, and never that anyone has called it
fraudulent. Keep it that way.

Stdlib only.

Commands
    daily [days]        scan the last N days of new domains (default 3)
    patterns [n]        mine the fake-shop naming patterns from the corpus
    contacts <brand>    resolve one brand's contacts across your folders
    sources             the brand/contact folders and what each provides
    report              re-print the latest scan

Output: <this folder>/RADAR/out/<date>/scam_candidates.csv
"""

import csv
import glob
import json
import os
import re
import sqlite3
import sys
import datetime as dt

APP = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.environ.get("RADAR_DIR") or os.path.join(APP, "RADAR"))
OUT = os.path.join(BASE, "out")
CONFIG = os.path.join(BASE, "config.json")
PATTERNS = os.path.join(BASE, "patterns.json")

csv.field_size_limit(min(2 ** 31 - 1, sys.maxsize))

DEFAULT_CONFIG = {
    "brand_sources": [
        {"id": "enriched", "path": "", "kind": "segment_csvs", "enabled": True,
         "note": "redpoints_561k_enriched_scanned - 560,827 brands, 50 columns: ICP flag, fit tier, revenue, emails, phones, imitation scan. The primary source."},
        {"id": "by_segment", "path": "", "kind": "segment_csvs", "enabled": False,
         "note": "redpoints_561k_by_segment - the pre-enrichment version of the same brands. Off: the enriched folder supersedes it. Turn on only if the enriched one is unavailable."},
        {"id": "redpoints_folder", "path": "", "kind": "loose_csvs", "enabled": True,
         "note": "the Red Points working folder - any CSV in it with a brand column is read for extra contacts, including hand-built files."}
    ],
    "nrd_dir": "",
    "_nrd_dir_note": "Blank = ..\\NRD from nrd_feed.py, next to this script.",
    "fraud_index": "",
    "_fraud_index_note": "Blank = ..\\BRAND\\fraud.sqlite from brand_protect.py.",
    "days": 3,
    "icp_only": True,
    "_icp_only_note": "Only raise candidates for brands above the ICP threshold. Off means 560k brands in the fuzzy index and a flood of worthless hits.",
    "min_score": 4,
    "home_tlds": ["de", "at", "ch", "sk", "cz", "com", "shop", "store", "online", "eu"],
    "suspicious_tlds": ["shop", "store", "online", "site", "xyz", "top", "icu", "vip",
                        "club", "sbs", "cyou", "buzz", "live", "fun", "monster", "rest"],
    "sender_name": "Juraj Brandobur",
    "dach_countries": ["AT", "DE", "CH"],
    "write_messages": True
}

# Seed vocabulary. 'patterns' adds whatever the corpus actually shows on top.
# Weighted by how strongly the word suggests a fake shop rather than a real one.
SEED_AFFIXES = {
    # country and language markers - the classic "local official store" lie
    "de": 3, "at": 3, "ch": 2, "eu": 2, "deutschland": 4, "osterreich": 4,
    "österreich": 4, "austria": 4, "germany": 3, "schweiz": 3, "wien": 3,
    # shop words
    "shop": 3, "store": 3, "geschaft": 4, "geschäft": 4, "laden": 3, "markt": 2,
    "onlineshop": 4, "webshop": 3, "eshop": 3, "shopde": 4, "shopat": 4,
    # buying words
    "kaufen": 3, "bestellen": 3, "versand": 3, "lieferung": 3, "lager": 3,
    "angebot": 3, "angebote": 3, "sale": 3, "outlet": 3, "rabatt": 3,
    "billig": 3, "guenstig": 3, "günstig": 3, "preis": 2, "reduziert": 3,
    "schlussverkauf": 4, "ausverkauf": 4, "clearance": 3, "discount": 3,
    # authority words
    "official": 3, "offiziell": 3, "originale": 3, "original": 2, "echte": 3,
    # product nouns that DACH fake shops bolt onto a brand
    "schuhe": 3, "stiefel": 3, "sneaker": 3, "mode": 3, "kleidung": 3,
    "bekleidung": 3, "jacken": 3, "mantel": 3, "taschen": 3, "tasche": 3,
    "uhren": 3, "schmuck": 3, "brillen": 3, "moebel": 3, "möbel": 3,
    "kuche": 3, "küche": 3, "garten": 3, "sport": 2, "fitness": 2,
    "baby": 2, "kinder": 2, "spielzeug": 3, "apotheke": 3, "kosmetik": 3,
    "pflege": 2, "werkzeug": 3, "technik": 2, "elektro": 2,
    # generic English equivalents
    "shoes": 3, "boots": 3, "bags": 3, "watches": 3, "jewelry": 3,
    "furniture": 3, "clothing": 3, "fashion": 2, "beauty": 2,
    "outletstore": 4, "outlet-store": 4, "onlinestore": 3
}

TIER_RANK = {"Enterprise tier": 4, "Professional fit": 3, "Starter fit": 2,
             "Mega (>$500M, excluded)": 1}


def say(*a):
    sys.stdout.write(" ".join(str(x) for x in a) + "\n")
    sys.stdout.flush()


def load_json(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(d))


def save_json(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    t = p + ".tmp"
    with open(t, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(t, p)


def load_config():
    os.makedirs(BASE, exist_ok=True)
    if not os.path.exists(CONFIG):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        parent = os.path.dirname(APP)
        guess = {"enriched": "redpoints_561k_enriched_scanned",
                 "by_segment": "redpoints_561k_by_segment",
                 "redpoints_folder": "Red Points"}
        for s in cfg["brand_sources"]:
            p = os.path.join(parent, guess.get(s["id"], ""))
            if os.path.isdir(p):
                s["path"] = p
        save_json(CONFIG, cfg)
        say("wrote a starter config:", CONFIG)
        for s in cfg["brand_sources"]:
            say("   %-18s %s" % (s["id"], s["path"] or "NOT FOUND - set it by hand"))
    cfg = load_json(CONFIG, DEFAULT_CONFIG)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def nrd_dir(cfg):
    return cfg.get("nrd_dir") or os.path.join(APP, "NRD")


def fraud_index(cfg):
    return cfg.get("fraud_index") or os.path.join(APP, "BRAND", "fraud.sqlite")


def norm_brand(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())


def split_domain(d):
    two = {"co.uk", "com.de", "com.au", "com.br", "co.at", "com.pl", "com.tr"}
    p = d.split(".")
    if len(p) >= 3 and ".".join(p[-2:]) in two:
        return p[-3], ".".join(p[-2:])
    return (p[-2] if len(p) >= 2 else d), p[-1]


def defang(d):
    return (d or "").replace(".", "[.]")


# ---------------------------------------------------------------- brand index

def segment_files(d):
    return [f for f in sorted(glob.glob(os.path.join(d, "*.csv")))
            if not os.path.basename(f).startswith(("_", "WATCHLIST"))]


def source_files(src):
    p = src.get("path") or ""
    if not p or not os.path.isdir(p):
        return []
    if src.get("kind") == "segment_csvs":
        return segment_files(p)
    return sorted(glob.glob(os.path.join(p, "*.csv")))


def brand_col(fieldnames):
    for c in (fieldnames or []):
        if c and c.strip().lower() in ("brand", "brand_name", "name", "company"):
            return c
    return None


def build_brand_sets(cfg):
    """Pass 1: brand names only. Keeps memory sane across 560k rows."""
    all_b, fuzzy_b = set(), set()
    seen_files = 0
    for src in cfg.get("brand_sources", []):
        if not src.get("enabled"):
            continue
        files = source_files(src)
        if not files:
            say("  %-18s no CSVs found at %s" % (src["id"], src.get("path") or "(unset)"))
            continue
        say("  %-18s %d file(s)" % (src["id"], len(files)))
        for fn in files:
            seen_files += 1
            try:
                with open(fn, encoding="utf-8-sig", errors="replace", newline="") as f:
                    rd = csv.DictReader(f)
                    bc = brand_col(rd.fieldnames)
                    if not bc:
                        continue
                    for row in rd:
                        b = norm_brand(row.get(bc))
                        if len(b) < 4:
                            continue
                        tier = (row.get("fit_tier") or "").strip()
                        icp = (row.get("icp_flag") or "").strip().upper() == "Y"
                        worth = icp or TIER_RANK.get(tier, 0) >= 2
                        if not row.get("fit_tier") and not row.get("icp_flag"):
                            worth = True      # files without tiering: treat as worth it
                        all_b.add(b)
                        if worth or not cfg.get("icp_only", True):
                            fuzzy_b.add(b)
            except Exception as e:
                say("    could not read %s (%s)" % (os.path.basename(fn), e))
    return all_b, fuzzy_b, seen_files


def brand_records(cfg, wanted):
    """Pass 2: full rows, only for the brands that matched."""
    out = {}
    for src in cfg.get("brand_sources", []):
        if not src.get("enabled"):
            continue
        for fn in source_files(src):
            try:
                with open(fn, encoding="utf-8-sig", errors="replace", newline="") as f:
                    rd = csv.DictReader(f)
                    bc = brand_col(rd.fieldnames)
                    if not bc:
                        continue
                    for row in rd:
                        b = norm_brand(row.get(bc))
                        if b not in wanted:
                            continue
                        cur = out.get(b)
                        if cur is None:
                            # prefer the real company name: "Steve Madden" reads
                            # like a person wrote it, "Stevemadden" does not
                            disp = (row.get("company") or "").strip() or (row.get(bc) or b)
                            out[b] = dict(row, _source=src["id"], _display=disp)
                        else:
                            for k, v in row.items():   # fill blanks from later files
                                if v and not cur.get(k):
                                    cur[k] = v
            except Exception:
                continue
    return out


def pick_emails(row):
    raw = row.get("emails") or row.get("generic_emails") or row.get("email") or ""
    es = [x.strip() for x in re.split(r"[|;,]\s*", raw) if x.strip() and "@" in x]
    generic = [e for e in es if re.match(
        r"^(info|office|kontakt|contact|hello|hallo|support|service|sales|presse|"
        r"press|legal|marketing|admin|mail|shop)@", e, re.I)]
    return generic or es


# ---------------------------------------------------------------- patterns

def open_fraud(cfg):
    p = fraud_index(cfg)
    if not os.path.exists(p):
        return None
    try:
        return sqlite3.connect("file:%s?mode=ro" % p.replace("?", ""), uri=True, timeout=60)
    except Exception:
        try:
            return sqlite3.connect(p, timeout=60)
        except Exception:
            return None


def watchlist_domains(con):
    if con is None:
        return []
    try:
        return [r[0] for r in con.execute(
            "SELECT domain FROM fraud WHERE source='watchlist'")]
    except Exception:
        return []


def cmd_patterns(cfg, topn=40):
    """Learn the affixes confirmed fake shops put around a brand name."""
    con = open_fraud(cfg)
    doms = watchlist_domains(con)
    if not doms:
        say("The fraud index has no Watchlist entries yet.")
        say("Run: brand_protect.py backfill 788")
        say("\nUntil then the radar uses its seed pattern list (%d German/Austrian"
            % len(SEED_AFFIXES))
        say("affixes: geschaft, kaufen, versand, osterreich, ...).")
        return
    say("mining %d confirmed fake-shop domains" % len(doms))
    say("building the brand index to find what sits around each brand name")
    all_b, fuzzy_b, _ = build_brand_sets(cfg)
    if not all_b:
        say("no brands loaded - check 'sources'")
        return
    sys.path.insert(0, APP)
    import brand_protect as BP
    m = BP.BrandMatcher(sorted(all_b), {"brand_match": {
        "contains": True, "edit_distance": 0, "min_brand_len": 4,
        "min_fuzzy_len": 6, "min_contains_len": 8,
        "affix_words": [], "generic_words": []}})
    affix, tlds, shapes = {}, {}, {}
    hits = 0
    for d in doms:
        sld, tld = split_domain(d)
        h = m.hits(d)
        if not h:
            continue
        hits += 1
        b = h[0][0]
        tlds[tld] = tlds.get(tld, 0) + 1
        flat = re.sub(r"[^a-z0-9]", "", sld)
        rest = flat.replace(b, "", 1)
        if rest:
            affix[rest] = affix.get(rest, 0) + 1
            shapes["brand+affix" if flat.startswith(b) else "affix+brand"] = \
                shapes.get("brand+affix" if flat.startswith(b) else "affix+brand", 0) + 1
        else:
            shapes["exact"] = shapes.get("exact", 0) + 1
    say("\n%d of %d listed domains carry a known brand name" % (hits, len(doms)))
    say("\n%-22s %s" % ("AFFIX AROUND BRAND", "FAKE SHOPS USING IT"))
    top = sorted(affix.items(), key=lambda kv: -kv[1])[:int(topn)]
    for a, c in top:
        say("  %-20s %d" % (a[:20], c))
    say("\n%-22s %s" % ("TLD", "COUNT"))
    for t, c in sorted(tlds.items(), key=lambda kv: -kv[1])[:15]:
        say("  .%-19s %d" % (t, c))
    say("\nshapes: %s" % ", ".join("%s %d" % kv for kv in
                                   sorted(shapes.items(), key=lambda kv: -kv[1])))
    learned = {a: (4 if c >= 20 else 3 if c >= 5 else 2) for a, c in affix.items()
               if len(a) >= 2 and c >= 2}
    merged = dict(SEED_AFFIXES)
    merged.update(learned)
    save_json(PATTERNS, {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                         "from_listed_domains": len(doms), "matched": hits,
                         "affixes": merged,
                         "tlds": sorted(tlds, key=lambda t: -tlds[t])[:40]})
    say("\nsaved %d affixes -> %s" % (len(merged), PATTERNS))
    say("The daily scan now looks for these shapes in newly registered domains.")


def load_patterns():
    p = load_json(PATTERNS, {})
    aff = p.get("affixes") or dict(SEED_AFFIXES)
    return aff, (p.get("tlds") or [])


# ---------------------------------------------------------------- messages

LEGAL_FORMS = {"gmbh", "ag", "ug", "kg", "ohg", "mbh", "se", "sa", "sas", "sarl",
               "bv", "nv", "ab", "as", "oy", "aps", "spa", "srl", "sl", "sro",
               "s.r.o", "s.r.o.", "a.s", "a.s.", "ltd", "ltd.", "limited", "llc",
               "inc", "inc.", "incorporated", "corp", "corp.", "corporation",
               "plc", "pty", "pte", "co", "co.", "kgaa", "holding", "holdings"}


def greeting_name(display, brand):
    """The name to greet, not the name on the invoice.

    German glues '-Team' onto the name, so 'Giesswein Walkwaren AG-Team' reads
    like a mail merge. Strip the legal form, then use the first word when it is
    the brand itself, and the whole thing when the brand spans it ('Steve
    Madden').
    """
    words = [w for w in re.split(r"\s+", (display or "").strip()) if w]
    while words and words[-1].lower().strip(",.") in LEGAL_FORMS:
        words.pop()
    if not words:
        return (brand or "").capitalize()
    flat = re.sub(r"[^a-z0-9]", "", " ".join(words).lower())
    first = re.sub(r"[^a-z0-9]", "", words[0].lower())
    if first == brand:
        return words[0]
    if flat == brand:
        return " ".join(words)
    return " ".join(words)


def msg_de(brand, dom, when, pattern, sender):
    return ("Hallo %s-Team, kurzer Hinweis: die Domain %s wurde am %s neu registriert "
            "und verwendet Ihren Markennamen (%s). Sie steht derzeit auf keiner "
            "Betrugsliste - erfahrungsgemäß ist das genau der Zeitpunkt, bevor ein "
            "Fake-Shop online geht, und der beste Moment zum Handeln. Ich arbeite als "
            "Affiliate-Partner mit Red Points zusammen, die solche Shops, "
            "Marktplatz-Angebote und Social-Media-Fakes finden und entfernen lassen. "
            "Gerne schicke ich Ihnen kostenlos die vollständige Liste der Domains, die "
            "Ihren Namen verwenden. Viele Grüße, %s"
            ) % (brand, defang(dom), when, pattern, sender)


def msg_en(brand, dom, when, pattern, sender):
    return ("Hi %s team, quick heads-up: the domain %s was registered on %s and uses "
            "your brand name (%s). It is not on any fraud list yet - in my experience "
            "that is the window just before a fake shop goes live, and the best time to "
            "act. I'm an affiliate partner of Red Points, who find and take down fake "
            "shops, marketplace listings and social accounts. Happy to send you the full "
            "list of domains using your name, free. Best, %s"
            ) % (brand, defang(dom), when, pattern, sender)


# ---------------------------------------------------------------- daily scan

COLS = ["candidate_domain", "registered", "brand", "brand_display", "match_type",
        "pattern", "score", "why", "official_site", "country", "market",
        "contact_emails", "phones", "est_online_revenue_usd", "icp_flag", "fit_tier",
        "segment", "known_imitations", "tld", "already_listed", "message_to_paste"]


def cmd_daily(cfg, days=None):
    days = int(days or cfg.get("days") or 3)
    nd = os.path.join(nrd_dir(cfg), "daily")
    if not os.path.isdir(nd):
        say("No newly-registered-domain feed at %s" % nd)
        say("Run nrd_feed.py daily first (or set nrd_dir in config.json).")
        return
    files = sorted(f for f in os.listdir(nd) if f.endswith(".txt"))[-days:]
    if not files:
        say("the NRD feed has no daily files yet - run nrd_feed.py daily")
        return

    say("brand sources")
    all_b, fuzzy_b, nfiles = build_brand_sets(cfg)
    if not all_b:
        say("\nNo brands loaded. Check the paths with 'sources'.")
        return
    say("  %d brands, %d eligible for typo matching" % (len(all_b), len(fuzzy_b)))

    affixes, _ptlds = load_patterns()
    sus = set(cfg.get("suspicious_tlds") or [])
    say("  %d fake-shop affixes in the pattern list" % len(affixes))

    sys.path.insert(0, APP)
    import brand_protect as BP
    base = {"contains": True, "min_brand_len": 4, "min_fuzzy_len": 6,
            "min_contains_len": 8,
            "affix_words": sorted(affixes.keys()),
            "generic_words": BP.DEFAULT_CONFIG["brand_match"]["generic_words"]}
    exact_m = BP.BrandMatcher(sorted(all_b), {"brand_match": dict(base, edit_distance=0)})
    fuzzy_m = BP.BrandMatcher(sorted(fuzzy_b), {"brand_match": dict(base, edit_distance=1)})

    con = open_fraud(cfg)
    listed = set()
    if con is not None:
        try:
            listed = set(r[0] for r in con.execute("SELECT domain FROM fraud"))
        except Exception:
            listed = set()
    say("  %d domains already on a fraud list (these are excluded)" % len(listed))

    cand = []
    scanned = 0
    for fn in files:
        day = fn[:-4]
        with open(os.path.join(nd, fn), encoding="utf-8", errors="replace") as f:
            doms = [x.strip() for x in f if x.strip()]
        scanned += len(doms)
        found = 0
        for d in doms:
            if d in listed:
                continue
            h = exact_m.hits(d) or fuzzy_m.hits(d)
            if not h:
                continue
            b, how = h[0]
            sld, tld = split_domain(d)
            flat = re.sub(r"[^a-z0-9]", "", sld)
            rest = flat.replace(b, "", 1)
            score = 0
            why = []
            if how in ("exact", "brand as a word"):
                score += 3; why.append("brand used as-is")
            elif how.startswith(("brand +", "'")):
                score += 4; why.append("brand wrapped in a shop word")
            elif how.startswith("typo"):
                score += 3; why.append("one-letter variation of the brand")
            else:
                score += 2; why.append("brand inside the name")
            pat = ""
            if rest and rest in affixes:
                pat = rest
                score += int(affixes[rest])
                why.append("'%s' is used by confirmed fake shops" % rest)
            if tld in sus:
                score += 2; why.append(".%s is a common fake-shop ending" % tld)
            cand.append({"domain": d, "day": day, "brand": b, "how": how,
                         "pattern": pat or rest, "score": score,
                         "why": "; ".join(why), "tld": tld})
            found += 1
        say("  %s  %7d new domains  %4d candidates" % (day, len(doms), found))

    if not cand:
        say("\nNo candidates. Either the brand index is small or the days are quiet.")
        return

    wanted = set(c["brand"] for c in cand)
    say("\nresolving contacts for %d brand(s)" % len(wanted))
    recs = brand_records(cfg, wanted)
    say("  %d resolved" % len(recs))

    minscore = int(cfg.get("min_score") or 0)
    dach = set(x.upper() for x in (cfg.get("dach_countries") or []))
    sender = cfg.get("sender_name") or "Juraj Brandobur"
    rows = []
    for c in cand:
        r = recs.get(c["brand"]) or {}
        tier = (r.get("fit_tier") or "").strip()
        icp = (r.get("icp_flag") or "").strip().upper() == "Y"
        score = c["score"] + (3 if icp else TIER_RANK.get(tier, 0))
        if score < minscore:
            continue
        disp = (r.get("_display") or c["brand"]).strip()
        if disp.islower():
            disp = disp[:1].upper() + disp[1:]
        country = (r.get("country") or "").strip().upper()
        market = "DACH" if country in dach else "Other"
        emails = pick_emails(r)
        try:
            when = dt.date.fromisoformat(c["day"]).strftime("%d.%m.%Y")
        except Exception:
            when = c["day"]
        pat = c["pattern"] or c["how"]
        msg = ""
        if cfg.get("write_messages", True):
            greet = greeting_name(disp, c["brand"])
            msg = (msg_de if market == "DACH" else msg_en)(greet, c["domain"], when, pat, sender)
        rows.append({
            "candidate_domain": c["domain"], "registered": c["day"],
            "brand": c["brand"], "brand_display": disp, "match_type": c["how"],
            "pattern": pat, "score": score, "why": c["why"],
            "official_site": ("https://" + r["official_domain"]) if r.get("official_domain") else "",
            "country": country, "market": market,
            "contact_emails": " | ".join(emails[:3]),
            "phones": " | ".join([x.strip() for x in re.split(
                r"[|;]\s*", r.get("phones") or "") if x.strip()][:2]),
            "est_online_revenue_usd": r.get("est_online_revenue_usd") or "",
            "icp_flag": r.get("icp_flag") or "", "fit_tier": tier,
            "segment": r.get("segment") or "",
            "known_imitations": r.get("n_imitation_domains") or "",
            "tld": c["tld"], "already_listed": "no", "message_to_paste": msg})

    rows.sort(key=lambda r: (-r["score"], r["brand"]))
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    od = os.path.join(OUT, today)
    os.makedirs(od, exist_ok=True)
    p = os.path.join(od, "scam_candidates.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    withc = sum(1 for r in rows if r["contact_emails"])
    icpn = sum(1 for r in rows if r["icp_flag"].upper() == "Y")
    lines = ["Scam radar - %s" % today, "=" * 34, "",
             "scanned          %d new domains over %d day(s)" % (scanned, len(files)),
             "candidates       %d (score >= %d)" % (len(rows), minscore),
             "  in your ICP    %d" % icpn,
             "  with a contact %d" % withc,
             "  DACH           %d" % sum(1 for r in rows if r["market"] == "DACH"), ""]
    lines.append("%-34s %-20s %-5s %s" % ("CANDIDATE", "BRAND", "SCORE", "WHY"))
    for r in rows[:25]:
        lines.append("%-34s %-20s %-5s %s" % (
            r["candidate_domain"][:34], r["brand_display"][:20], r["score"], r["why"][:48]))
    lines += ["", "These domains are on NO fraud list. They are leads, not findings.",
              "Verify before contacting anyone, and never call a site fraudulent.",
              "", "file: %s" % p]
    with open(os.path.join(od, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    for ln in lines:
        say(ln)


def cmd_sources(cfg):
    say("brand and contact sources\n")
    for s in cfg.get("brand_sources", []):
        files = source_files(s)
        state = "on " if s.get("enabled") else "off"
        say("[%s] %-18s %s" % (state, s["id"], s.get("path") or "PATH NOT SET"))
        say("      %d CSV file(s)%s" % (len(files),
                                        "" if files else "   <- nothing readable here"))
        if s.get("note"):
            say("      %s" % s["note"])
        say("")
    say("NRD feed:     %s" % nrd_dir(cfg))
    say("fraud index:  %s%s" % (fraud_index(cfg),
                                "" if os.path.exists(fraud_index(cfg)) else "   <- MISSING"))
    aff, _ = load_patterns()
    say("patterns:     %d affixes%s" % (
        len(aff), "" if os.path.exists(PATTERNS) else " (seed list; run 'patterns')"))
    say("\nedit: %s" % CONFIG)


def cmd_contacts(cfg, brand):
    b = norm_brand(brand)
    if not b:
        say("give me a brand name")
        return
    recs = brand_records(cfg, {b})
    r = recs.get(b)
    if not r:
        say("'%s' not found in any configured source." % brand)
        say("Check 'sources', or the spelling - matching strips punctuation.")
        return
    say("brand        %s   (from %s)" % (r.get("_display"), r.get("_source")))
    for k in ("official_domain", "company", "country", "city", "segment", "fit_tier",
              "icp_flag", "priority_score", "est_online_revenue_usd", "employees",
              "n_imitation_domains", "n_strong_imitations", "tranco_rank"):
        if r.get(k):
            say("%-13s %s" % (k, str(r[k])[:90]))
    em = pick_emails(r)
    if em:
        say("emails       %s" % " | ".join(em[:6]))
    if r.get("phones"):
        say("phones       %s" % str(r["phones"])[:90])
    for k in ("linkedin", "instagram", "facebook"):
        if r.get(k):
            say("%-13s %s" % (k, r[k]))
    if r.get("top_imitation_examples"):
        say("\nknown imitations: %s" % str(r["top_imitation_examples"])[:160])


def cmd_report():
    reps = sorted(glob.glob(os.path.join(OUT, "*", "summary.txt")))
    if not reps:
        say("no scan yet - run 'daily'")
        return
    say(open(reps[-1], encoding="utf-8").read())


def main(argv):
    cfg = load_config()
    cmd = (argv[0] if argv else "daily").lower()
    rest = argv[1:]
    if cmd == "daily":
        cmd_daily(cfg, rest[0] if rest else None)
    elif cmd == "patterns":
        cmd_patterns(cfg, rest[0] if rest else 40)
    elif cmd == "sources":
        cmd_sources(cfg)
    elif cmd == "contacts":
        cmd_contacts(cfg, rest[0] if rest else "")
    elif cmd == "report":
        cmd_report()
    else:
        say(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        say("\nstopped")
