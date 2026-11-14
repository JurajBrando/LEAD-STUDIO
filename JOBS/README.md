# Hiring signals -> offer matches

A company that is hiring tells you what it is short of. This pulls fresh job
postings from public HuggingFace datasets, matches each one to one of your
offers, enriches the company with how hard it is hiring, and writes a
ready-to-send CSV per offer.

Script: `..\job_signals.py` — stdlib Python only, nothing to install.

## In Lead Studio

**1. Every day** — `daily` (the main run), `offer rules`, `re-match cached`,
`status`, `run every morning`.
**2. Find leads** — two saved searches (`performance marketing`, `data annotation`).
**3. Enrich** — `look up a company`, `refresh company table`.
**5. Score and segment** — `title demand by country`.

```
python job_signals.py daily
python job_signals.py search "media buyer" --country DE --limit 60
python job_signals.py company "Adyen"
python job_signals.py demand SK
python job_signals.py offers
```

## Measured on the first live run (13 Nov 2026)

36,000 postings pulled over 3 days → **654 matched** after dedup:

| Offer | Matches |
|---|---|
| A.I. Scale (own) | 252 |
| GoHighLevel | 189 |
| HYROS | 63 |
| micro1 — engineers | 54 |
| Modulario | 27 |
| PLAI | 27 |
| micro1 — data partnership | 23 |
| QuoteIQ | 11 |
| HYROS AIR | 5 |
| Dermis | 3 |

## Read this before you trust the output

**These datasets are ATS feeds, so they skew hard to large tech firms.** The
top companies in that first run were Salesforce, OpenAI, Stripe, NVIDIA,
Cloudflare, Databricks. Exactly one CZ match and zero SK. That shapes which
offers this is actually good for:

- **micro1 data partnership — strongest fit.** Big AI labs hiring Human Data
  programme managers, AI Tutors, red-teamers and annotation leads are
  precisely the companies micro1 pays a referral on. Highest value per hit of
  anything here. This alone justifies the daily run.
- **GoHighLevel / HYROS — usable, read with care.** Real hiring signals, but
  these are enterprise marketing orgs, not the SMBs and agencies that
  normally convert on those offers. Treat as a research list, not a send list.
- **Modulario — weak from this source.** Slovak and Czech SMB manufacturers do
  not post on Greenhouse, Lever or Ashby. Your NRD feed (`nrd_feed.py`,
  `sk_cz.csv`) is a far better Modulario source. Keep Modulario matches here
  as a bonus, not the channel.

### What Aramente actually is

Worth being precise, because the dataset has two faces. Browse
`Aramente/eu-tech-jobs` as a whole and much of it is **RemoteOK and
WeWorkRemotely aggregation** — `country: "XX"`, location "Remote", the real
geography buried in prose like "Aggregated from RemoteOK (Germany)". That part
is near-useless: it reposts other companies' Ashby/Greenhouse jobs that
cleanfeeds already carries, with worse metadata.

The **daily diffs**, which is all this script reads, are a different animal.
Measured over three days (6,940 rows):

| Upstream feed | Share |
|---|---|
| welcometothejungle | 17% |
| workday (Thales, Chanel, NVIDIA FR, Amgen, PayPal) | 29% |
| greenhouse / smartrecruiters / ashby / lever / personio | 8% |
| `removed` + `changed` events (id only, no posting) | 45% |
| remoteok + weworkremotely | **0.2%** |

So the diffs are genuinely European employer ATS feeds. The 45% are job
closures and edits carrying only an id — not postings at all — and are
dropped. `exclude_feeds` in `config.json` drops the remote-board aggregators
outright, so if the mix ever shifts toward RemoteOK it cannot pollute your
lists. Every row carries a `feed` column naming its upstream, so you can always
see where a lead came from.

Last run: 141 Aramente matches — 55 welcometothejungle, 40 workday, 17
greenhouse, 10 ashby, 8 smartrecruiters, 7 lever, 2 personio. No aggregator
rows at all.

Set `countries` in `config.json` to `["SK","CZ","AT","DE","PL","HU"]` to keep
only those, at the cost of volume.

Country detection is best-effort: the upstream `location` field is free text
("2 Locations", "Remote", "Hybrid"), so about 60% come through with no country
even after city-name and state-code handling. Filtering on country throws away
most rows. It is a scalpel, not a default. Last run resolved: US 75, FR 55,
IE 49, GB 31, DE 17, ES 11, PL 10, CZ 1, SK 0.

## Tuning

`offer_rules.json` is the whole brain. Each offer has `patterns` (job-title
fragments), `neg` (kill words), `weight`, and `pitch` — the angle that goes in
the email. Edit it, then run *re-match cached*: no network, instant, so you can
iterate in seconds.

Patterns match at **word starts**, not bare substrings. That matters: plain
substring matching made `erp` fire on "Ent**erp**rise" and produced 601 false
Modulario matches on the first run. Patterns of 4 characters or fewer (`erp`,
`mes`, `sdr`, `ppc`) are anchored at both ends; longer ones stay open-ended, so
`data annotat` still catches "Data Annotation".

**`Ataraxis` has no patterns yet** — it is skipped until you add the job titles
that signal a need for it, plus a pitch line.

Scoring = offer weight + hiring intensity (`new_jobs_7d`, `open_jobs` from the
company table). `min_score` in `config.json` sets the floor. A score of 9+ means
a matching role at a company that is visibly scaling.

Every posting is keyed in `seen_jobs.sqlite` on both URL and company+title, so
re-posts and multi-location duplicates never reach you twice, and nothing gets
pitched twice. Retention 60 days.

## Notes

- `search` uses the HuggingFace search index, which throws transient 500s
  (`index is loading`) constantly. The script retries with backoff; a source
  that stays down is skipped and the run continues.
- A free HF token in `config.json` (`hf_token`) or `HF_TOKEN` raises search
  rate limits. Not required.
- Today's `aramente` diff is usually published later in the day, so a 404 on
  the current date is normal — `days_back` covers it on the next run.
- Attribution: four of the seven datasets are CC BY 4.0. See `ATTRIBUTION.txt`
  before you put any of this in front of a client.
