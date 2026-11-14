# Brand protection — fake shops and phishing

Pulls the domains that consumer-protection bodies and phishing trackers have
already confirmed as fraudulent, matches them against the brands you watch, and
gives you one case per registered domain.

Script: `..\brand_protect.py` — stdlib only, nothing to install.

## Sources (both verified live)

| Source | Size | Shape |
|---|---|---|
| watchlist-internet.at `/unserioese-webseiten/` | **39,358** entries, 9 categories | TYPO3 + Solr, server-rendered HTML, 50/page, newest first |
| Phishing.Database `phishing-domains-ACTIVE.txt` | **391,462** domains (11 MB) | plain text, weekly refresh |
| Phishing.Database `*-NEW-today.txt` | small daily deltas | fetched every run |

The `/unserioese-webseiten/` page is a **superset** of the two lists you sent —
it contains both as categories, plus six more: Automatisierte Warnungen,
Anwaltskanzleien, Betrügerische Jobangebote, Speditionen, Finanzbetrug,
Abo-Fallen, Urlaubsbuchung. So the script reads the master list and you get
your two for free, with the category on every row. Narrow `list_path` in
`config.json` if you only want one.

No JavaScript and no browser needed: the list paginates as
`?tx_solr[page]=N`. Their robots.txt permits these paths (only `/typo3/` is
disallowed); the script still waits 1.5s between pages.

## Your brand list

Already filled, from your own file: `_CLEAN_2026-11-02\SIGNALS\brands.csv`
gave **1,551 brands**. The *brands watched* tool re-imports it any time
(`import-brands`, which also takes an explicit path).

That file is not a list of brands you own — it is a list of brands with
anti-counterfeiting contacts (`counterfeit@eastpak.com`,
`legal@neweracap.com`). So this tool is doing **Red Points prospecting**: find
fake shops riding those brands, and the evidence is the pitch. Two brands,
`cbx` and `niq`, are under four characters and are ignored — three letters
matches everything.

If you also want to protect your own brands (the gate business, HeroFactory,
Modulario), add those words to `brands.txt` by hand. They survive a re-import
only if you add them to the CSV, so the CSV is the better place.

### Use the right corpus for this list

Measured against the **phishing** corpus (391,462 domains): 117 hits over 113
registered domains, but only **one** exact match. That is the expected result,
not a fault — phishing kits impersonate banks, Microsoft and PayPal, not
consumer retail brands. Your list is retail, so the **Watchlist Internet**
fake-shop corpus is the one that will light up for it. Run
*backfill full corpus* before judging the output.

### Prune the brand list from evidence

`brand_hit_counts.csv` shows every brand that matched and by which tier. A
brand that only ever matches by **typo** and never exactly is usually a
truncated or generic entry rather than a brand under attack — 49 of yours
behave that way (`beauti`, `rboutique`, `revelse`, `studiom`, `healthco`,
`athome`, `angelareed`...). The daily run names them. Delete them from the CSV
and precision rises.

For a high-precision pass, set `edit_distance: 0` in `config.json`: that keeps
exact and affix matches only (`delsey-shop.cz`, `eastpakshop.de`) and drops the
typo tier entirely.

## Matching

Tiered, strongest first; the first tier that fires wins:

| Tier | Example | Floor |
|---|---|---|
| **exact** — brand is one of the domain's words | `clarins.store` | 4 chars |
| **affix** — other words are only shop/store/login/... | `delsey-shop.cz`, `eastpakshop.de` | 4 chars |
| **typo** — a word is one edit from a brand | `pangaai.com`, `eastpakk.com` | 6 chars |
| **contains** — a long brand inside a longer word | — | 8 chars |

Each tier has its own length floor because the shorter the brand, the more a
loose match is coincidence. Everyday words (`studio`, `boutique`, `interior`)
only ever match a brand they equal exactly — extend `generic_words` whenever a
false positive annoys you.

Typo matching is **Damerau**-Levenshtein, so a transposition counts as one
edit. That is deliberate: `nkie` for `nike` is the commonest squat there is,
and plain Levenshtein scores it 2, so it would slip past distance 1.

### Why it is built this way

The first version flattened hyphens and looked for a brand anywhere in the
result. Against your real 1,551 brands that produced 904 hits that were almost
all garbage: `mont` matched `montgomerycountylinks.org`, `nfinity` matched
`infinityfreeapp.com`, `barr` matched `updateterbarru.com`. The tiered version
returns 117, with none of those. If you loosen the floors, that noise comes
back.

It also has to be quick. Checking every brand against every domain is ~600
million comparisons and ran past ten minutes before being killed. Brands are
now bucketed by leading characters for substring work, and a deletion index
answers distance-1 questions in a few dict lookups; candidates are always
confirmed with the real distance function, so the speed costs no accuracy.
**~140,000 domains/second**, and the full 391k corpus matches in about 3
seconds.

## The prospecting angle

*most impersonated brands* mines the whole corpus for brand words recurring
across fake-shop domains. **A brand being ridden by fake shops is a brand that
needs brand protection** — so the victims list is also a Red Points prospect
list. Read it as leads, not verdicts: generic retail words surface there too.

## Cross-check with new domains

*cross-check new domains* takes the last 7 days from `nrd_feed.py` and finds
which newly registered domains are already on a fraud list, and which are
lookalikes of your brands. That catches a fake shop **at registration**,
before it has customers — the earliest possible signal, and the thing the
existing `lookalikes` tool was reaching for.

## Read this before you act on a hit

Every entry in these lists is an **allegation by the body that published it**,
not a judicial finding. A match is a lead to verify, never proof. Do not
describe a business as fraudulent on the strength of a list entry alone, and
do not republish either corpus wholesale. Credit Watchlist Internet (ÖIAT)
wherever you surface their data. See `ATTRIBUTION.txt`.
