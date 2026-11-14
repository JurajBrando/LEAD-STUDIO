#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_redpoints_tools.py - register the Red Points pipeline in Lead Studio.

Put this next to lead_studio.py (in LEAD_STUDIO) and run it once:

    python install_redpoints_tools.py

It adds the newly-registered-domain, hiring-signal and Red Points tools to
lead_studio_tools.json, skipping anything already there, and backs the file up
first. Safe to run twice - it only ever adds what is missing.

    python install_redpoints_tools.py --check     say what is missing, change nothing
"""

import json
import os
import shutil
import sys
import datetime as dt

APP = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(APP, "lead_studio_tools.json")

D = "LEAD_STUDIO"

ENTRIES = [
    # ---------------------------------------------------- the whole chain, one button
    {"id": "pipe_run", "group": "1. Every day", "name": "** RUN THE WHOLE PIPELINE **",
     "desc": "One run of the entire chain in order: new domains (smet.cz + WhoisExtractor) -> fraud listings (Watchlist + Phishing.Database) -> Red Points cases against your 561k brand dataset -> cross-check new domains against the fraud lists. A failing stage does not stop the rest. Ends with a report naming the two handoff files: sk_cz.csv for Modulario outreach and redpoints_cases.csv for Manyreach.",
     "dir": D, "script": "daily_pipeline.py", "args": "run", "output": "LEAD_STUDIO/PIPELINE"},
    {"id": "pipe_run_all", "group": "1. Every day", "name": "Run the whole pipeline + hiring signals",
     "desc": "Same chain plus the affiliate hiring-signal match, which is off by default because it is a separate workstream.",
     "dir": D, "script": "daily_pipeline.py", "args": "run --with-jobs", "output": "LEAD_STUDIO/PIPELINE"},
    {"id": "pipe_report", "group": "1. Every day", "name": "Pipeline: last report",
     "desc": "Re-prints the latest run: which stages succeeded, the day's counts, and where the handoff files are.",
     "dir": D, "script": "daily_pipeline.py", "args": "report", "output": "LEAD_STUDIO/PIPELINE"},
    {"id": "pipe_stages", "group": "1. Every day", "name": "Pipeline: stages",
     "desc": "The stage order, which are on, and whether each script is actually present.",
     "dir": D, "script": "daily_pipeline.py", "args": "stages", "output": "LEAD_STUDIO/PIPELINE"},
    {"id": "pipe_schedule", "group": "1. Every day", "name": "Pipeline: run every morning",
     "desc": "ONE Windows task at 07:15 for the whole chain. Use this instead of the three separate morning tasks - it prints the commands to remove those so nothing runs twice.",
     "dir": D, "script": "daily_pipeline.py", "args": "schedule 07:15", "output": "LEAD_STUDIO/PIPELINE"},

    # ---------------------------------------------------- newly registered domains
    {"id": "nrd_daily", "group": "1. Every day", "name": "New domains: daily download",
     "desc": "Downloads today's newly registered domains from smet.cz (no key) and WhoisExtractor, merges and de-duplicates them, flags what is genuinely new against a rolling 90-day index, and cuts slices: new .sk/.cz domains, manufacturing, gates and fences, ERP/SaaS.",
     "dir": D, "script": "nrd_feed.py", "args": "daily", "output": "LEAD_STUDIO/NRD/out"},
    {"id": "nrd_status", "group": "1. Every day", "name": "New domains: status",
     "desc": "What is on disk, index size, per-day counts.",
     "dir": D, "script": "nrd_feed.py", "args": "status", "output": "LEAD_STUDIO/NRD"},
    {"id": "nrd_slice", "group": "1. Every day", "name": "New domains: re-cut slices",
     "desc": "Re-runs the TLD and keyword filters over the newest day already downloaded. No network, instant.",
     "dir": D, "script": "nrd_feed.py", "args": "slice", "output": "LEAD_STUDIO/NRD/out"},
    {"id": "nrd_backfill", "group": "2. Find leads", "name": "New domains: backfill 30 days",
     "desc": "Fetches the last 30 closed days from smet.cz to fill the index in one go.",
     "dir": D, "script": "nrd_feed.py", "args": "backfill 30", "output": "LEAD_STUDIO/NRD/daily"},
    {"id": "nrd_sources", "group": "1. Every day", "name": "New domains: check sources",
     "desc": "Which domain feeds are on and whether their keys are in place.",
     "dir": D, "script": "nrd_feed.py", "args": "sources", "output": "LEAD_STUDIO/NRD"},
    {"id": "nrd_schedule", "group": "1. Every day", "name": "New domains: run every morning",
     "desc": "Registers a Windows task so the daily download runs by itself at 07:30.",
     "dir": D, "script": "nrd_feed.py", "args": "schedule 07:30", "output": "LEAD_STUDIO/NRD"},

    # ---------------------------------------------------- hiring signals
    {"id": "jobs_daily", "group": "1. Every day", "name": "Hiring signals: daily match",
     "desc": "Pulls new job postings from public HuggingFace datasets, matches each against your offers (HYROS, GoHighLevel, micro1, PLAI, QuoteIQ, Modulario, Dermis), enriches the company with how hard it is hiring, and writes one CSV per offer.",
     "dir": D, "script": "job_signals.py", "args": "daily", "output": "LEAD_STUDIO/JOBS/out"},
    {"id": "jobs_offers", "group": "1. Every day", "name": "Hiring signals: offer rules",
     "desc": "Which job titles each offer catches, and the cold-email angle for each.",
     "dir": D, "script": "job_signals.py", "args": "offers", "output": "LEAD_STUDIO/JOBS"},
    {"id": "jobs_match", "group": "1. Every day", "name": "Hiring signals: re-match cached",
     "desc": "Re-runs the offer rules over postings already downloaded today. No network, instant.",
     "dir": D, "script": "job_signals.py", "args": "match", "output": "LEAD_STUDIO/JOBS/out"},
    {"id": "jobs_status", "group": "1. Every day", "name": "Hiring signals: status",
     "desc": "What is on disk, how many postings have been pitched, recent runs.",
     "dir": D, "script": "job_signals.py", "args": "status", "output": "LEAD_STUDIO/JOBS"},
    {"id": "jobs_schedule", "group": "1. Every day", "name": "Hiring signals: run every morning",
     "desc": "Windows task so the hiring-signal match runs by itself at 08:15.",
     "dir": D, "script": "job_signals.py", "args": "schedule 08:15", "output": "LEAD_STUDIO/JOBS"},
    {"id": "jobs_search_pm", "group": "2. Find leads", "name": "Hiring signals: search 'performance marketing'",
     "desc": "Full-text search across the job datasets for companies hiring paid-media people, scored and matched to an offer. Edit the args to search any title.",
     "dir": D, "script": "job_signals.py", "args": "search \"performance marketing\" --limit 60",
     "output": "LEAD_STUDIO/JOBS/out"},
    {"id": "jobs_search_data", "group": "2. Find leads", "name": "Hiring signals: search 'data annotation'",
     "desc": "Finds companies running human-data or labelling work in house - the micro1 data-partner profile.",
     "dir": D, "script": "job_signals.py", "args": "search \"data annotation\" --limit 60",
     "output": "LEAD_STUDIO/JOBS/out"},
    {"id": "jobs_company", "group": "3. Enrich", "name": "Hiring signals: look up a company",
     "desc": "How hard one company is hiring, and which offers its open roles point at. Change the name in the args.",
     "dir": D, "script": "job_signals.py", "args": "company \"Stripe\"", "output": "LEAD_STUDIO/JOBS"},
    {"id": "jobs_companies", "group": "3. Enrich", "name": "Hiring signals: refresh company table",
     "desc": "Re-downloads the company hiring-intensity table. Refreshes weekly anyway.",
     "dir": D, "script": "job_signals.py", "args": "companies", "output": "LEAD_STUDIO/JOBS"},
    {"id": "jobs_demand", "group": "5. Score and segment", "name": "Hiring signals: title demand by country",
     "desc": "Most-posted job titles in a country and which offers they point at. Change SK to any ISO code.",
     "dir": D, "script": "job_signals.py", "args": "demand SK", "output": "LEAD_STUDIO/JOBS"},

    # ---------------------------------------------------- Red Points: collect
    {"id": "bp_backfill", "group": "8. Red Points", "name": "1. Collect: backfill fake-shop corpus",
     "desc": "RUN THIS FIRST, ONCE. Walks all 788 pages of Watchlist Internet (~39,000 fraudulent and problematic shops, 9 categories) at 1.5s a page, about 20 minutes. Nothing downstream works until the corpus is on disk.",
     "dir": D, "script": "brand_protect.py", "args": "backfill 788", "output": "LEAD_STUDIO/BRAND"},
    {"id": "bp_daily", "group": "8. Red Points", "name": "2. Collect: daily fraud listings",
     "desc": "Newest fake-shop listings plus the Phishing.Database deltas (391k active phishing domains), matched against BRAND/brands.txt including typos and transpositions. One case per registered domain.",
     "dir": D, "script": "brand_protect.py", "args": "daily", "output": "LEAD_STUDIO/BRAND/out"},
    {"id": "bp_refresh", "group": "8. Red Points", "name": "Collect: refresh phishing list",
     "desc": "Re-downloads the ~391k active phishing domains (11 MB). Happens weekly by itself.",
     "dir": D, "script": "brand_protect.py", "args": "refresh", "output": "LEAD_STUDIO/BRAND"},
    {"id": "bp_schedule", "group": "8. Red Points", "name": "Collect: run every morning",
     "desc": "Windows task so the fraud check runs by itself at 07:50.",
     "dir": D, "script": "brand_protect.py", "args": "schedule 07:50", "output": "LEAD_STUDIO/BRAND"},
    {"id": "bp_status", "group": "8. Red Points", "name": "Collect: status",
     "desc": "Index size per source, upstream totals, when the phishing list was last pulled.",
     "dir": D, "script": "brand_protect.py", "args": "status", "output": "LEAD_STUDIO/BRAND"},

    # ---------------------------------------------------- Red Points: cases
    {"id": "rc_build", "group": "8. Red Points", "name": "3. Cases: build pitch-ready cases",
     "desc": "THE MONEY STEP. Crosses the fake-shop corpus against your 560,827-brand enriched dataset, keeps the brands above the ICP threshold, enriches each from your own columns (email, phone, revenue, tier, priority), and writes the same columns as your WATCHLIST_BRANDS_contact_forms.csv - including the message to paste, German for DACH and English elsewhere. Sorted ICP first, then tier, then revenue.",
     "dir": D, "script": "redpoints_cases.py", "args": "build", "output": "LEAD_STUDIO/BRAND/out"},
    {"id": "rc_top", "group": "8. Red Points", "name": "4. Cases: top cases",
     "desc": "The highest-priority cases from the last build: brand, market, fake shop, tier, revenue, contact email.",
     "dir": D, "script": "redpoints_cases.py", "args": "top 40", "output": "LEAD_STUDIO/BRAND/out"},
    {"id": "rc_sample", "group": "8. Red Points", "name": "5. Cases: check message wording",
     "desc": "Prints a few complete messages so you can read the German and English wording before anything is sent.",
     "dir": D, "script": "redpoints_cases.py", "args": "sample 3", "output": "LEAD_STUDIO/BRAND/out"},
    {"id": "rc_build_all", "group": "8. Red Points", "name": "Cases: build including below-ICP brands",
     "desc": "Same as the build step but keeps brands under the $100K threshold too. Many more rows, much weaker. Use only when the ICP run comes back thin.",
     "dir": D, "script": "redpoints_cases.py", "args": "build --all", "output": "LEAD_STUDIO/BRAND/out"},
    {"id": "rc_stats", "group": "8. Red Points", "name": "Cases: dataset overview",
     "desc": "What is in the 561k enriched dataset: brands, ICP shortlist, emails, phones, imitation domains per segment, and how old the imitation scan is.",
     "dir": D, "script": "redpoints_cases.py", "args": "stats", "output": "LEAD_STUDIO/BRAND"},

    # ---------------------------------------------------- scam radar (early warning)
    {"id": "radar_daily", "group": "8. Red Points", "name": "A. Radar: find new scam sites",
     "desc": "THE EARLY-WARNING STEP. Crosses the last 3 days of newly registered domains against all 560,827 brands and the naming patterns that confirmed Austrian fake shops actually use, then removes everything already on a fraud list. What is left is domains that look like a fake shop being set up right now - before any watchdog has listed it. Brand owner contacts are attached from your three Red Points folders.",
     "dir": D, "script": "scam_radar.py", "args": "daily 3", "output": "LEAD_STUDIO/RADAR/out"},
    {"id": "radar_patterns", "group": "8. Red Points", "name": "Radar: learn fake-shop patterns",
     "desc": "Mines the Watchlist corpus for the words real fake shops put around a brand name (geschaft, kaufen, versand, osterreich, schuhe...) and saves them as the pattern list the radar scans for. Run after the corpus backfill, then once a month.",
     "dir": D, "script": "scam_radar.py", "args": "patterns 40", "output": "LEAD_STUDIO/RADAR"},
    {"id": "radar_sources", "group": "8. Red Points", "name": "Radar: brand and contact folders",
     "desc": "Shows the three Red Points folders it reads (561k enriched, 561k by segment, Red Points working folder), how many CSVs each holds, and whether the NRD feed and fraud index are in place.",
     "dir": D, "script": "scam_radar.py", "args": "sources", "output": "LEAD_STUDIO/RADAR"},
    {"id": "radar_contacts", "group": "3. Enrich", "name": "Radar: look up a brand's contacts",
     "desc": "Resolves one brand across all three Red Points folders: official domain, company, country, tier, revenue, emails, phones, socials and known imitations. Change the name in the args.",
     "dir": D, "script": "scam_radar.py", "args": "contacts giesswein", "output": "LEAD_STUDIO/RADAR"},
    {"id": "radar_report", "group": "8. Red Points", "name": "Radar: last scan",
     "desc": "Re-prints the latest early-warning scan.",
     "dir": D, "script": "scam_radar.py", "args": "report", "output": "LEAD_STUDIO/RADAR"},

    # ---------------------------------------------------- Red Points: cross-checks
    {"id": "bp_cross_nrd", "group": "8. Red Points", "name": "6. Cross-check: new domains vs fraud",
     "desc": "Takes the last 7 days from the newly-registered-domain feed and finds which are already on a fraud list, plus which are lookalikes of your brands. Catches a fake shop at registration, before it has customers.",
     "dir": D, "script": "brand_protect.py", "args": "cross-nrd 7", "output": "LEAD_STUDIO/BRAND/out"},
    {"id": "bp_victims", "group": "8. Red Points", "name": "Prospecting: most impersonated brands",
     "desc": "Mines the whole fake-shop corpus for the brand words that recur. A brand being ridden by fake shops needs brand protection, so this doubles as a prospect list. Run after the backfill.",
     "dir": D, "script": "brand_protect.py", "args": "victims 60", "output": "LEAD_STUDIO/BRAND/out"},
    {"id": "bp_check", "group": "8. Red Points", "name": "Look up a domain or brand",
     "desc": "Is this domain on a fraud list, and what else listed contains the same brand stem? Change the word in the args.",
     "dir": D, "script": "brand_protect.py", "args": "check modulario", "output": "LEAD_STUDIO/BRAND"},
    {"id": "bp_brands", "group": "8. Red Points", "name": "Brands watched",
     "desc": "Shows the brands in BRAND/brands.txt and how each will be matched.",
     "dir": D, "script": "brand_protect.py", "args": "brands", "output": "LEAD_STUDIO/BRAND"},
    {"id": "bp_import_brands", "group": "8. Red Points", "name": "Import brands from brands.csv",
     "desc": "Fills BRAND/brands.txt from _CLEAN_2026-11-02/SIGNALS/brands.csv (1,551 brands). Pass a path in the args to use a different file.",
     "dir": D, "script": "brand_protect.py", "args": "import-brands", "output": "LEAD_STUDIO/BRAND"},
]

GROUP_ORDER = ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."]


def main(argv):
    check = "--check" in argv
    if not os.path.exists(TOOLS):
        print("lead_studio_tools.json not found next to this script.")
        print("Put install_redpoints_tools.py in the LEAD_STUDIO folder and run it there.")
        print("Looked for:", TOOLS)
        return 1

    with open(TOOLS, encoding="utf-8") as f:
        data = json.load(f)
    tools = data.get("tools")
    if not isinstance(tools, list):
        print("lead_studio_tools.json has no 'tools' list - stopping rather than guessing.")
        return 1

    have = {t.get("id") for t in tools}
    missing = [e for e in ENTRIES if e["id"] not in have]
    present = [e for e in ENTRIES if e["id"] in have]

    print("lead_studio_tools.json: %d tools registered" % len(tools))
    print("pipeline entries already there: %d" % len(present))
    print("pipeline entries missing:       %d" % len(missing))
    if missing:
        for e in missing:
            print("   + %-18s %s" % (e["id"], e["name"]))
    if check:
        print("\n--check: nothing written.")
        return 0
    if not missing:
        print("\nNothing to do - everything is already registered.")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = TOOLS + ".bak_" + stamp
    shutil.copy2(TOOLS, bak)
    print("\nbacked up -> %s" % os.path.basename(bak))

    # keep each new tool next to its own group
    for e in missing:
        g = e.get("group", "")
        pos = None
        for i in range(len(tools) - 1, -1, -1):
            if str(tools[i].get("group", "")).startswith(g[:2]):
                pos = i + 1
                break
        if pos is None:
            pos = len(tools)
        tools.insert(pos, e)

    tmp = TOOLS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TOOLS)
    print("added %d tool(s); %d now registered." % (len(missing), len(tools)))

    missing_scripts = sorted({e["script"] for e in ENTRIES
                              if not os.path.exists(os.path.join(APP, e["script"]))})
    if missing_scripts:
        print("\nNOTE - these scripts are referenced but not in this folder yet:")
        for s in missing_scripts:
            print("   ", s)
        print("Their tools will show as unavailable until the file is there.")

    print("\nReload Lead Studio.")
    print("\nOnce, before the first run (group 8. Red Points):")
    print("  Import brands from brands.csv")
    print("  1. Collect: backfill fake-shop corpus   (~20 min; nothing downstream")
    print("                                           works until this is done)")
    print("  Radar: learn fake-shop patterns          (after the backfill)")
    print("\nThen, daily (group 1. Every day):")
    print("  ** RUN THE WHOLE PIPELINE **            runs the entire chain")
    print("  Pipeline: run every morning             one 07:15 task for all of it")
    print("\nBefore sending anything (group 8):")
    print("  5. Cases: check message wording")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
