# New domains feed (NRD)

Downloads every newly registered domain name in the world each day, merges the
sources, throws away what you have already seen, and cuts the rest into lists
you can actually work from.

Script: `..\nrd_feed.py` — stdlib Python only, nothing to install.

## In Lead Studio

Group **1. Every day**:

| Tool | What it does |
|---|---|
| New domains: daily download | the normal run — today plus any days you missed |
| New domains: status | index size, per-day counts, last run |
| New domains: re-cut slices | re-filter the newest day after you edit `config.json` (no network, instant) |
| New domains: check sources | which feeds are on, whether the domainkits key is set |
| New domains: run every morning | registers a Windows task so it runs at 07:30 by itself |

Group **2. Find leads**: *New domains: backfill 30 days* — run once to fill the index.

From a command line instead:

```
python nrd_feed.py daily
python nrd_feed.py backfill 30
python nrd_feed.py day 2026-11-12
python nrd_feed.py slice
python nrd_feed.py schedule 07:30
```

## Sources

| Source | Key | History | Volume | State |
|---|---|---|---|---|
| smet.cz | none | 90 days | ~390k/day, 1,647 TLDs | on |
| whoisextractor.com | yours, in config | 4 days | ~240k/day, 1,601 TLDs | on |
| domainkits.com | free `dk_...` | 3 days on free plan | ~300–350k/day, 1,400+ TLDs | off — needs a key |

smet.cz runs with no account at all, so the job works even if the keyed sources
fail. WhoisExtractor is live with your key (`pid=10`, Daily Registered Domains,
no WHOIS fields). To add domainkits, get a free key and set
`sources.domainkits.api_key` plus `enabled: true`.

All sources are merged and de-duplicated, so each extra one only ever increases
coverage — overlap costs you nothing. If one source is down or rate-limited the
run carries on with the others.

Keys live in `config.json`. You can keep them out of the file and use
environment variables instead: `WHOISEXTRACTOR_API_KEY`, `DOMAINKITS_API_KEY`.

Two WhoisExtractor quirks worth knowing: their dates run on **India time
(GMT+5:30)**, so their "day" sits a few hours out of step with the UTC days
everything else uses — dedup absorbs it, but a domain can land in the
neighbouring day's file. And only **one API key is active at a time**: if you
generate a new one on their site, the one in `config.json` stops working.

## What you get

```
NRD/
  config.json          sources, slices, retention — edit this
  daily/<date>.txt     merged, de-duplicated union for that UTC day
  out/<date>/
    sk_cz.csv          brand new .sk / .cz domains
    manufacturing.csv  keyword hits: kovovyroba, stav, cnc, logistic, …
    gates_fences.csv   brana, plot, vejar, zamecnictvi, …
    erp_saas.csv       erp, mes, wms, cloud, automat, …
    neighbours.csv     .at .de .pl .hu
    eu_generic.csv     .com/.shop/… whose name hints at SK/CZ
    new_today.txt      domains never seen in the last 90 days
    tld_counts.csv     volume per TLD
    summary.txt        the one file to read each morning
  index.sqlite         rolling 90-day seen-domain index
  raw/                 files exactly as downloaded, kept 14 days
```

Every `.csv` has a `domain` column, so it drops straight into the verify and
split tools in groups 4 and 5.

## Why this is worth running

A new `.sk` or `.cz` domain is usually a business that was registered days ago:
no website yet, no vendor, nobody has called them. That is the freshest
Modulario list you can get, and it refills every morning for free.

`gates_fences.csv` works the other way — it shows you new competitors in your
own market as they appear.

## Tuning it

Edit `config.json`, then run *New domains: re-cut slices* — it re-filters the
day already on disk without downloading anything, so you can iterate on
keywords in seconds.

- `slices[]` — add your own: `tlds`, `keywords`, or both (both = AND)
- `exclude_keywords` — kill parked/spam patterns globally
- `min_sld_len` — drops very short junk names
- `retention_days` — how long a domain counts as "already seen" (default 90)
- `keep_raw_days` — how long the downloaded originals stay (default 14)

If this folder ever sits on a network or synced drive and the index will not
open, point it at a local disk: `set NRD_DIR=C:\NRD`.

## Attribution — read this before you ship anything

smet.cz data is CC BY 4.0: free commercially, but it needs **visible** credit to
smet.cz linked to https://smet.cz/ anywhere you surface this data in a product,
UI, README or export. See `ATTRIBUTION.txt`. Do not put smet.cz in a User-Agent
string. Check the domainkits and WhoisExtractor terms before redistributing theirs.
