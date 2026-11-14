#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
redpoints_cases.py - turn confirmed fake shops into pitch-ready Red Points cases.

You already built the hard part by hand: WATCHLIST_BRANDS_contact_forms.csv,
32 rows, each one a brand whose fake shop a consumer-protection body has
publicly listed, with the evidence link and a message ready to paste. That is
the strongest cold open there is - you are not claiming a problem, you are
forwarding somebody else's published finding.

This scales that file. It crosses the full Watchlist Internet corpus (39,358
listings, collected by brand_protect.py) against your 560,827-brand enriched
dataset, keeps the brands worth pitching, and writes the same columns you
already use - including the message, in German for DACH and English
elsewhere, in your wording.

It reads your dataset; it never writes to it.

Stdlib only.

Commands
    build [--all]      match the corpus, enrich, write the cases file
                         --all also keeps brands below the ICP threshold
    stats              what is in your 561k dataset
    top [n]            highest-priority cases from the last build
    sample [n]         print a few full messages to check the wording

Output: <this folder>/BRAND/out/<date>/redpoints_cases.csv
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
BRAND_DIR = os.path.abspath(os.environ.get("BRAND_DIR") or os.path.join(APP, "BRAND"))
INDEX = os.path.join(BRAND_DIR, "fraud.sqlite")
OUT = os.path.join(BRAND_DIR, "out")
CONFIG = os.path.join(BRAND_DIR, "cases_config.json")

csv.field_size_limit(min(2 ** 31 - 1, sys.maxsize))

DEFAULT_CONFIG = {
    "dataset_dir": "",
    "_dataset_dir_note": "Folder holding the enriched per-segment CSVs. Blank = ..\\redpoints_561k_enriched_scanned next to the Modulario folder.",
    "sender_name": "Juraj Brandobur",
    "dach_countries": ["AT", "DE", "CH"],
    "fuzzy_for_icp_only": True,
    "_fuzzy_note": "Typo matching is only applied to brands above the ICP threshold. A one-letter match on a sub-$100K brand is coincidence, and indexing all 560k brands for fuzzy search costs gigabytes.",
    "keep_below_icp": False,
    "max_lookalike_examples": 6
}

TIER_RANK = {"Enterprise tier": 4, "Professional fit": 3, "Starter fit": 2,
             "Mega (>$500M, excluded)": 1}

COLS = ["brand", "official_site", "country", "market", "fake_shop",
        "listed_on_watchlist", "watchlist_url", "other_lookalikes_found",
        "lookalike_examples", "generic_emails", "phones", "linkedin",
        "instagram", "est_online_revenue_usd", "in_561k_dataset",
        "contact_form_url", "status", "notes", "message_to_paste",
        # appended, so the file still opens as your original schema plus extras
        "segment", "icp_flag", "fit_tier", "priority_score",
        "n_imitation_domains", "n_strong_imitations", "match_type", "category"]


def say(*a):
    sys.stdout.write(" ".join(str(x) for x in a) + "\n")
    sys.stdout.flush()


def load_config():
    os.makedirs(BRAND_DIR, exist_ok=True)
    if not os.path.exists(CONFIG):
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=1)
        say("wrote a starter config:", CONFIG)
    try:
        c = json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        c = dict(DEFAULT_CONFIG)
    for k, v in DEFAULT_CONFIG.items():
        c.setdefault(k, v)
    return c


def dataset_dir(cfg):
    if cfg.get("dataset_dir"):
        return cfg["dataset_dir"]
    parent = os.path.dirname(APP)
    for name in ("redpoints_561k_enriched_scanned", "redpoints_561k_by_segment"):
        p = os.path.join(parent, name)
        if os.path.isdir(p):
            return p
    return ""


def seg_files(d):
    return [f for f in sorted(glob.glob(os.path.join(d, "*.csv")))
            if not os.path.basename(f).startswith(("_", "WATCHLIST"))]


def norm_brand(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())


def defang(d):
    return (d or "").replace(".", "[.]")


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


def message_de(brand, fake, date_str, url, sender):
    return ("Hallo %s-Team, kurzer Hinweis: Watchlist Internet hat am %s den Shop %s "
            "als betrügerisch gelistet. Er nutzt Ihren Markennamen, um Kunden in "
            "Österreich und Deutschland anzusprechen. Eintrag: %s . Falls das noch "
            "nicht bei Ihnen angekommen ist: Ich arbeite als Affiliate-Partner mit "
            "Red Points zusammen, die solche Shops, Marktplatz-Angebote und "
            "Social-Media-Fakes finden und entfernen lassen. Gerne schicke ich Ihnen "
            "kostenlos eine Liste weiterer Fake-Domains mit Ihrem Namen. "
            "Viele Grüße, %s") % (brand, date_str, defang(fake), url, sender)


def message_en(brand, fake, date_str, url, sender):
    return ("Hi %s team, quick heads-up: on %s Watchlist Internet (Austria's consumer "
            "fraud watchlist) listed %s as a fraudulent shop using your brand name. "
            "Listing: %s . In case it's useful: I'm an affiliate partner of Red Points, "
            "who find and take down fake shops, marketplace listings and social "
            "accounts. Happy to send a free list of other lookalike domains using your "
            "name. Best, %s") % (brand, date_str, defang(fake), url, sender)


def pretty_date(iso):
    try:
        y, m, d = iso.split("-")
        return "%s.%s.%s" % (d, m, y)
    except Exception:
        return iso or ""


# ---------------------------------------------------------------- build

def read_watchlist(con):
    try:
        rows = con.execute(
            "SELECT domain, category, listed, url FROM fraud "
            "WHERE source='watchlist'").fetchall()
    except sqlite3.OperationalError:
        rows = [(d, c, l, "") for d, c, l in con.execute(
            "SELECT domain, category, listed FROM fraud WHERE source='watchlist'")]
    return rows


def watchlist_url(domain, category, stored):
    if stored:
        return stored
    base = ("liste-problematischer-shops" if "roblemat" in (category or "")
            else "liste-betruegerischer-shops")
    return "https://www.watchlist-internet.at/%s/shop/%s/" % (
        base, re.sub(r"[^a-z0-9]", "", domain))


def cmd_build(cfg, keep_all=False):
    d = dataset_dir(cfg)
    if not d:
        say("Could not find the enriched dataset folder.")
        say("Set dataset_dir in %s" % CONFIG)
        return
    files = seg_files(d)
    if not files:
        say("no per-segment CSVs in", d)
        return
    if not os.path.exists(INDEX):
        say("No fraud index at %s" % INDEX)
        say("Run brand_protect.py backfill 788 first - that is what collects the corpus.")
        return

    con = sqlite3.connect(INDEX, timeout=60)
    wl = read_watchlist(con)
    con.close()
    if not wl:
        say("The index holds no Watchlist entries yet.")
        say("Run: brand_protect.py backfill 788")
        return
    say("corpus: %d Watchlist listings" % len(wl))
    say("dataset: %s (%d segment files)" % (d, len(files)))

    # pass 1 - brand names only, so memory stays small
    keep_all = keep_all or bool(cfg.get("keep_below_icp"))
    all_brands = set()
    fuzzy_brands = set()
    rows_seen = 0
    for fn in files:
        with open(fn, encoding="utf-8-sig", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                rows_seen += 1
                b = norm_brand(row.get("brand"))
                if len(b) < 4:
                    continue
                tier = (row.get("fit_tier") or "").strip()
                icp = (row.get("icp_flag") or "").strip().upper() == "Y"
                worth = icp or TIER_RANK.get(tier, 0) >= 2
                if worth or keep_all:
                    all_brands.add(b)
                if worth:
                    fuzzy_brands.add(b)
    say("read %d brand rows -> %d matchable, %d eligible for typo matching" % (
        rows_seen, len(all_brands), len(fuzzy_brands)))
    if not all_brands:
        say("nothing matchable. Use --all to include brands below the ICP threshold.")
        return

    sys.path.insert(0, APP)
    import brand_protect as BP
    mcfg = {"brand_match": {"contains": True, "edit_distance": 1, "min_brand_len": 4,
                            "min_fuzzy_len": 6, "min_contains_len": 8,
                            "affix_words": BP.DEFAULT_CONFIG["brand_match"]["affix_words"],
                            "generic_words": BP.DEFAULT_CONFIG["brand_match"]["generic_words"]}}
    exact_m = BP.BrandMatcher(sorted(all_brands), {"brand_match":
              dict(mcfg["brand_match"], edit_distance=0)})
    fuzzy_m = BP.BrandMatcher(sorted(fuzzy_brands), mcfg) if (
        fuzzy_brands and cfg.get("fuzzy_for_icp_only", True)) else exact_m
    say("indexes built")

    # match the corpus
    matched = {}
    for domain, category, listed, url in wl:
        hits = exact_m.hits(domain) or fuzzy_m.hits(domain)
        if not hits:
            continue
        b, how = hits[0]
        prev = matched.get(b)
        rec = (domain, category, listed or "", url or "", how)
        if prev is None or (listed or "") > (prev[2] or ""):
            matched[b] = rec
    say("%d brand(s) matched a listed fake shop" % len(matched))
    if not matched:
        say("\nNo overlap. Most likely the corpus is not backfilled yet "
            "(brand_protect.py status will say how many Watchlist rows it holds).")
        return

    # pass 2 - pull full records only for the brands that matched
    sender = cfg.get("sender_name") or "Juraj Brandobur"
    dach = set(x.upper() for x in (cfg.get("dach_countries") or []))
    maxex = int(cfg.get("max_lookalike_examples") or 6)
    out = []
    for fn in files:
        with open(fn, encoding="utf-8-sig", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                b = norm_brand(row.get("brand"))
                if b not in matched:
                    continue
                fake, category, listed, url, how = matched[b]
                brand_disp = (row.get("brand") or b).strip()
                brand_disp = brand_disp[:1].upper() + brand_disp[1:]
                country = (row.get("country") or "").strip().upper()
                market = "DACH" if country in dach else "Other"
                wurl = watchlist_url(fake, category, url)
                pd = pretty_date(listed)
                msg = (message_de if market == "DACH" else message_en)(
                    greeting_name(brand_disp, b), fake, pd, wurl, sender)
                ex = [x.strip() for x in
                      re.split(r"\s*\|\s*", row.get("top_imitation_examples") or "") if x.strip()]
                emails = [x.strip() for x in
                          re.split(r"\s*\|\s*", row.get("emails") or "") if x.strip()]
                generic = [e for e in emails if re.match(
                    r"^(info|office|kontakt|contact|hello|hallo|support|service|sales|"
                    r"presse|press|legal|marketing|admin|mail)@", e, re.I)]
                out.append({
                    "brand": brand_disp,
                    "official_site": ("https://" + row["official_domain"]
                                      if row.get("official_domain") else ""),
                    "country": country, "market": market, "fake_shop": fake,
                    "listed_on_watchlist": pd, "watchlist_url": wurl,
                    "other_lookalikes_found": row.get("n_imitation_domains") or "",
                    "lookalike_examples": " | ".join(ex[:maxex]),
                    "generic_emails": " | ".join((generic or emails)[:3]),
                    "phones": " | ".join([x.strip() for x in re.split(
                        r"\s*\|\s*", row.get("phones") or "") if x.strip()][:2]),
                    "linkedin": row.get("linkedin") or "",
                    "instagram": row.get("instagram") or "",
                    "est_online_revenue_usd": row.get("est_online_revenue_usd") or "",
                    "in_561k_dataset": "yes",
                    "contact_form_url": "", "status": "", "notes": "",
                    "message_to_paste": msg,
                    "segment": row.get("segment") or "",
                    "icp_flag": row.get("icp_flag") or "",
                    "fit_tier": row.get("fit_tier") or "",
                    "priority_score": row.get("priority_score") or "",
                    "n_imitation_domains": row.get("n_imitation_domains") or "",
                    "n_strong_imitations": row.get("n_strong_imitations") or "",
                    "match_type": how, "category": category,
                })
                matched.pop(b, None)

    def sortkey(r):
        try:
            rev = float(r["est_online_revenue_usd"] or 0)
        except Exception:
            rev = 0.0
        try:
            pri = float(r["priority_score"] or 0)
        except Exception:
            pri = 0.0
        return (0 if r["icp_flag"].upper() == "Y" else 1,
                -TIER_RANK.get(r["fit_tier"].strip(), 0), -rev, -pri)
    out.sort(key=sortkey)

    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    od = os.path.join(OUT, day)
    os.makedirs(od, exist_ok=True)
    p = os.path.join(od, "redpoints_cases.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    icp = sum(1 for r in out if r["icp_flag"].upper() == "Y")
    withc = sum(1 for r in out if r["generic_emails"])
    say("\n--- %d cases ---" % len(out))
    say("  in your ICP        %d" % icp)
    say("  with an email      %d" % withc)
    say("  DACH (German msg)  %d" % sum(1 for r in out if r["market"] == "DACH"))
    say("\n%-26s %-10s %-22s %s" % ("BRAND", "MARKET", "FAKE SHOP", "TIER"))
    for r in out[:20]:
        say("%-26s %-10s %-22s %s" % (r["brand"][:26], r["market"],
                                      r["fake_shop"][:22], r["fit_tier"][:26]))
    say("\nsaved: %s" % p)
    say("\nSame columns as your WATCHLIST_BRANDS file, plus segment/tier/score at the\n"
        "end. contact_form_url, status and notes are left blank for you to work in.")
    say("Check a few messages before sending: redpoints_cases.py sample 3")


def cmd_stats(cfg):
    d = dataset_dir(cfg)
    if not d:
        say("dataset not found; set dataset_dir in %s" % CONFIG)
        return
    say("dataset:", d)
    sm = os.path.join(d, "_summary_by_segment.csv")
    if os.path.exists(sm):
        tot = {}
        rows = list(csv.DictReader(open(sm, encoding="utf-8-sig", errors="replace")))
        for k in ("brands", "in_ICP_shortlist", "with_email", "with_phone",
                  "with_imitations_registered_recently", "total_imitation_domains"):
            tot[k] = sum(int(float(r.get(k) or 0)) for r in rows)
        say("\n%d segments" % len(rows))
        for k, v in tot.items():
            say("  %-38s %s" % (k, format(v, ",")))
        say("\ntop segments by brand count")
        for r in sorted(rows, key=lambda x: -int(float(x.get("brands") or 0)))[:8]:
            say("  %-34s %8s brands  %8s imitation domains" % (
                (r.get("segment") or "")[:34], format(int(float(r.get("brands") or 0)), ","),
                format(int(float(r.get("total_imitation_domains") or 0)), ",")))
    say("\nNote: the imitation scan in this dataset is dated 2026-10-17. Anything")
    say("registered since then is not in it - that is the gap the daily feeds fill.")


def cmd_sample(cfg, n=3):
    days = sorted(glob.glob(os.path.join(OUT, "*", "redpoints_cases.csv")))
    if not days:
        say("no cases file yet - run 'build' first")
        return
    rows = list(csv.DictReader(open(days[-1], encoding="utf-8-sig", errors="replace")))
    say("%s  (%d cases)\n" % (days[-1], len(rows)))
    for r in rows[:int(n)]:
        say("=" * 72)
        say("%s   %s   %s   %s" % (r["brand"], r["market"], r["fit_tier"],
                                   r["est_online_revenue_usd"]))
        say("fake shop: %s   listed %s" % (r["fake_shop"], r["listed_on_watchlist"]))
        say("contact:   %s %s" % (r["generic_emails"], r["phones"]))
        say("")
        say(r["message_to_paste"])
        say("")


def cmd_top(cfg, n=25):
    days = sorted(glob.glob(os.path.join(OUT, "*", "redpoints_cases.csv")))
    if not days:
        say("no cases file yet - run 'build' first")
        return
    rows = list(csv.DictReader(open(days[-1], encoding="utf-8-sig", errors="replace")))
    say("%s\n" % days[-1])
    say("%-24s %-8s %-20s %-18s %-12s %s" % ("BRAND", "MARKET", "FAKE SHOP",
                                             "TIER", "REVENUE", "EMAIL"))
    for r in rows[:int(n)]:
        say("%-24s %-8s %-20s %-18s %-12s %s" % (
            r["brand"][:24], r["market"], r["fake_shop"][:20], r["fit_tier"][:18],
            (r["est_online_revenue_usd"] or "")[:12],
            (r["generic_emails"] or "")[:34]))


def main(argv):
    cfg = load_config()
    cmd = (argv[0] if argv else "build").lower()
    rest = argv[1:]
    if cmd == "build":
        cmd_build(cfg, keep_all=("--all" in rest))
    elif cmd == "stats":
        cmd_stats(cfg)
    elif cmd == "sample":
        cmd_sample(cfg, rest[0] if rest else 3)
    elif cmd == "top":
        cmd_top(cfg, rest[0] if rest else 25)
    else:
        say(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        say("\nstopped")
