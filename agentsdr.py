"""
agentsdr.py - hands leads from Lead Studio to AgentSDR, the outreach and CRM app.

Lead Studio keeps doing the Python work: scraping, the contact database, AI enrichment, verification.
AgentSDR does the sending, the replies and the pipeline. This file is the road between them.

  python agentsdr.py convert --in "<any lead csv>" [--out <folder>] [--chunk 5000]
      Rewrites a lead file with the headers AgentSDR's importer recognizes, split into files
      small enough to import (it takes 5,000 rows at a time). No key needed - you drag the
      files into AgentSDR yourself. Works on any CSV with an email column.

  python agentsdr.py prepare --spec search.json [--out <folder>] [--chunk 5000]
      The same, straight out of the contact database: filter, then write the import files.

  python agentsdr.py campaigns
      The email campaigns in your AgentSDR, with their ids.

  python agentsdr.py push --campaign <id> --in <csv> [--limit N] [--dry-run]
      Adds the leads to that campaign through the API, 500 at a time, and remembers where it
      stopped, so running it again continues instead of starting over. Columns the importer
      does not know become merge fields you can write as {{companySize}} in the sequence.

  python agentsdr.py stop --campaign <id> --emails a@b.com,c@d.com
      Ends those people's sequence in that campaign. Not an unsubscribe.

The address and API key live in lead_studio_settings.json (Connections page). Create the key in
AgentSDR under Settings, API keys - it is shown once.
"""
import os, re, sys, csv, json, time, glob, argparse, subprocess, urllib.request, urllib.error

csv.field_size_limit(10 ** 9)
APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(APP))
SETTINGS = os.path.join(APP, "lead_studio_settings.json")
OUTDIR = os.path.join(ROOT, "AGENTSDR_IMPORT")
STATE = os.path.join(APP, "logs", "agentsdr_push_state.json")
BATCH, CHUNK = 500, 5000

# what AgentSDR's importer recognizes; everything else is kept as a merge field
KNOWN = {"email": "email", "e_mail": "email", "email_address": "email", "best_email": "email", "contact_email": "email",
         "first_name": "first name", "first": "first name", "firstname": "first name", "contact_first_name": "first name",
         "last_name": "last name", "last": "last name", "lastname": "last name", "contact_last_name": "last name",
         "name": "name", "full_name": "name", "title": "title", "job_title": "title", "job_position": "title",
         "position": "title", "headline": "title", "contact_job_title": "title",
         "company": "company", "company_name": "company", "merchant_name": "company", "organization": "company",
         "domain": "domain", "website": "domain", "company_domain": "domain", "company_website": "domain", "url": "domain",
         "phone": "phone", "phone_number": "phone", "mobile": "phone", "whatsapp": "phone", "contact_mobile": "phone",
         "linkedin": "linkedin", "linkedin_url": "linkedin", "linkedin_profile": "linkedin", "notes": "notes"}
ORDER = ["email", "first name", "last name", "name", "title", "company", "domain", "phone", "linkedin", "notes"]
API_FIELD = {"email": "email", "first name": "firstName", "last name": "lastName", "title": "title",
             "company": "company", "domain": "companyDomain", "linkedin": "linkedinUrl"}
DROP = {"lumrid_result", "lumrid", "email_status", "verification", "source_file", "seen_in_files", "is_role",
        "is_freemail", "custom_2", "fit", "priority"}
# the Modulario export files name four columns "Custom 1..4"; give them names worth writing in an email
RENAME = {"custom_1": "angle", "custom_3": "emailType", "custom_4": "salesBand", "subsegment": "segment",
          "estimated_monthly_sales_raw": "monthlySales", "employee_count": "employees", "installed_apps_names": "apps"}
EMAIL_RX = re.compile(r"^[^@\s,;]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def cfg():
    s = {"agentsdr_url": "http://localhost:3000", "agentsdr_key": ""}
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            s.update({k: v for k, v in json.load(fh).items() if k in s and v})
    except (OSError, ValueError):
        pass
    return s


def out(obj, code=None):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit((0 if obj.get("ok", True) else 1) if code is None else code)


def say(m):
    print(time.strftime("%H:%M:%S ") + m, flush=True)


def norm(h):
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def merge_name(h):
    """A header AgentSDR does not know becomes a merge field: Company Size -> companySize."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", (h or "").strip()) if p]
    if not parts:
        return ""
    first = parts[0] if len(parts) == 1 else parts[0].lower()      # one word: keep any camelCase it already has
    n = first[:1].lower() + first[1:] + "".join(p[:1].upper() + p[1:].lower() for p in parts[1:])
    return ("field" + n[:1].upper() + n[1:]) if n[:1].isdigit() else n


def plan(header, keep_extras=True):
    """header -> (index of each AgentSDR column, [(index, merge field name)])"""
    cols, extras, seen = {}, [], set()
    for i, h in enumerate(header):
        k = norm(h)
        f = KNOWN.get(k)
        if f and f not in seen:
            cols[f] = i; seen.add(f)
        elif keep_extras and k and k not in DROP and not f:
            m = RENAME.get(k) or merge_name(h)
            if m and m not in [x[1] for x in extras]:
                extras.append((i, m))
    return cols, extras


def row_value(r, i):
    return (r[i].strip() if i is not None and i < len(r) and r[i] else "")


def clean_domain(v):
    v = re.sub(r"^https?://", "", (v or "").strip(), flags=re.I).split("/")[0].strip().lower()
    return v[4:] if v.startswith("www.") else v


def read_rows(path, keep_extras=True):
    """Every usable row of a lead file as (dict of known fields, dict of merge fields)."""
    op = (lambda p: __import__("gzip").open(p, "rt", encoding="utf-8-sig", errors="replace", newline="")) \
        if path.lower().endswith(".gz") else (lambda p: open(p, encoding="utf-8-sig", errors="replace", newline=""))
    with op(path) as fh:
        rd = csv.reader(fh, delimiter="\t" if path.lower().endswith((".tsv", ".tsv.gz")) else ",")
        header = next(rd, [])
        cols, extras = plan(header, keep_extras)
        if "email" not in cols and "linkedin" not in cols:
            raise ValueError("No email or linkedin column in %s" % os.path.basename(path))
        for r in rd:
            e = row_value(r, cols.get("email")).lower()
            li = row_value(r, cols.get("linkedin"))
            if not (EMAIL_RX.match(e) if e else li):
                continue
            d = {"email": e}
            for f in ORDER[1:]:
                v = row_value(r, cols.get(f))
                if v:
                    d[f] = clean_domain(v) if f == "domain" else v
            if d.get("name") and not d.get("first name"):
                p = d.pop("name").split()
                d["first name"] = p[0]
                if len(p) > 1:
                    d["last name"] = " ".join(p[1:])
            d.pop("name", None)
            yield d, {m: row_value(r, i) for i, m in extras if row_value(r, i)}


def write_chunks(rows, name, outdir, chunk):
    """Import files of at most `chunk` rows, because that is what AgentSDR takes at once."""
    os.makedirs(outdir, exist_ok=True)
    files, part, n, w, fh, head = [], 0, 0, None, None, None
    for d, extra in rows:
        if head is None:
            head = ORDER[:3] + ORDER[4:] + sorted(extra)
        if n % chunk == 0:
            if fh:
                fh.close()
            part += 1
            p = os.path.join(outdir, "%s_%02d.csv" % (name, part))
            fh = open(p, "w", encoding="utf-8", newline="")
            w = csv.DictWriter(fh, fieldnames=head, extrasaction="ignore"); w.writeheader()
            files.append(p)
        w.writerow({**d, **extra}); n += 1
    if fh:
        fh.close()
    return files, n


def convert(path, outdir, chunk, name=None):
    src = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if not os.path.isfile(src):
        out({"ok": False, "error": "No such file: %s" % src})
    name = name or re.sub(r"[^A-Za-z0-9_\-]+", "_", os.path.splitext(os.path.basename(src))[0])[:50]
    say("reading %s" % os.path.basename(src))
    try:
        files, n = write_chunks(read_rows(src), name, outdir, chunk)
    except ValueError as e:
        out({"ok": False, "error": str(e)})
    say("DONE: %s leads in %d file%s" % (format(n, ","), len(files), "" if len(files) == 1 else "s"))
    out({"ok": True, "leads": n, "files": [os.path.relpath(f, ROOT).replace(os.sep, "/") for f in files],
         "next": "In AgentSDR: Leads, Import people - or a campaign's own import if you want the extra columns as merge fields."})


def prepare(spec_path, outdir, chunk):
    """Filter the contact database, then write AgentSDR import files."""
    spec = json.load(open(spec_path, encoding="utf-8"))
    tmp = os.path.join(outdir, "_from_database.csv")
    os.makedirs(outdir, exist_ok=True)
    spec["out"] = tmp
    spec.setdefault("limit", 100000)
    sp = tmp + ".spec.json"
    json.dump(spec, open(sp, "w", encoding="utf-8"))
    say("searching the contact database")
    r = subprocess.run([sys.executable, "-u", os.path.join(APP, "lead_index.py"), "export", "--spec", sp],
                       capture_output=True, text=True, env=dict(os.environ, LS_ROOT=ROOT))
    for f in (sp,):
        try:
            os.remove(f)
        except OSError:
            pass
    if not os.path.exists(tmp):
        out({"ok": False, "error": (r.stderr or r.stdout or "the database export failed")[-600:]})
    convert(tmp, outdir, chunk, name=re.sub(r"[^A-Za-z0-9_\-]+", "_", str(spec.get("name") or "database_leads"))[:50])


def api(path, method="GET", payload=None, timeout=90):
    c = cfg()
    if not c["agentsdr_key"]:
        out({"ok": False, "error": "No AgentSDR API key saved. In AgentSDR: Settings, API keys - then paste it on the "
                                   "Connections page of Lead Studio."})
    url = c["agentsdr_url"].rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Authorization": "Bearer " + c["agentsdr_key"], "Accept": "application/json"}
    if data:
        h["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:600]
        msg = {401: "AgentSDR refused the key. Create a new one under Settings, API keys.",
               404: "No such campaign, or this AgentSDR has no public API yet (a older install runs scripts/create-api-keys.ts once).",
               503: "AgentSDR is holding campaign changes right now (PAUSE_CAMPAIGN_MUTATIONS)."}.get(e.code)
        return False, {"http": e.code, "error": msg or ("AgentSDR answered HTTP %d" % e.code), "body": body}
    except Exception as e:
        return False, {"error": "Could not reach %s: %s" % (url, e),
                       "hint": "Is AgentSDR running? Its address is set on the Connections page."}


def campaigns():
    ok, r = api("/api/v1/campaigns")
    if not ok:
        out({"ok": False, **r})
    cs = r.get("campaigns", [])
    out({"ok": True, "campaigns": [{"id": c.get("id"), "name": c.get("name"), "status": c.get("status")} for c in cs],
         "count": len(cs)})


def state_load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def state_save(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w", encoding="utf-8"), indent=1)


def push(campaign, path, limit, dry):
    src = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if not os.path.isfile(src):
        out({"ok": False, "error": "No such file: %s" % src})
    key = "%s|%s" % (campaign, os.path.abspath(src))
    st = state_load(); done = int(st.get(key, {}).get("sent", 0))
    batch, sent, tot = [], 0, {"imported": 0, "skippedDuplicate": 0, "skippedSuppressed": 0, "failed": 0}
    if done:
        say("continuing after the %s leads already sent from this file" % format(done, ","))
    say("campaign %s" % campaign)

    def flush():
        nonlocal batch, sent
        if not batch:
            return True
        ok, r = api("/api/v1/campaigns/%s/leads" % campaign, "POST", {"leads": batch})
        if not ok:
            say("stopped: " + str(r.get("error") or r)); return False
        for k in ("imported", "skippedDuplicate", "skippedSuppressed"):
            tot[k] += int(r.get(k) or 0)
        tot["failed"] += len(r.get("failed") or [])
        for f in (r.get("failed") or [])[:3]:
            say("  refused: %s - %s" % (f.get("email"), f.get("error")))
        sent += len(batch); batch = []
        st[key] = {"sent": done + sent, "at": time.strftime("%Y-%m-%d %H:%M")}; state_save(st)
        say("sent %s | imported %s, duplicates %s, suppressed %s, refused %s"
            % tuple(format(x, ",") for x in (done + sent, tot["imported"], tot["skippedDuplicate"],
                                             tot["skippedSuppressed"], tot["failed"])))
        return True

    try:
        for i, (d, extra) in enumerate(read_rows(src)):
            if i < done:
                continue
            if limit and sent + len(batch) >= limit:
                break
            if not d.get("email"):
                continue
            lead = {API_FIELD[k]: v for k, v in d.items() if k in API_FIELD}
            if extra:
                lead["customFields"] = {k: v[:500] for k, v in list(extra.items())[:25]}
            batch.append(lead)
            if dry:
                out({"ok": True, "dry_run": True, "campaign": campaign, "first_lead": lead,
                     "note": "This is what one lead looks like on the way to AgentSDR. Nothing was sent."})
            if len(batch) >= BATCH and not flush():
                break
        else:
            flush()
    except ValueError as e:
        out({"ok": False, "error": str(e)})
    out({"ok": True, "campaign": campaign, "file": os.path.basename(src), "sent_this_run": sent,
         "sent_in_total_from_this_file": done + sent, **tot})


def stop(campaign, emails):
    es = [e.strip().lower() for e in re.split(r"[,\s;]+", emails or "") if "@" in e][:500]
    if not es:
        out({"ok": False, "error": "Give at least one email address."})
    ok, r = api("/api/v1/campaigns/%s/leads/stop" % campaign, "POST", {"emails": es})
    out({"ok": ok, **r})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["convert", "prepare", "campaigns", "push", "stop"])
    ap.add_argument("--in", dest="inp"); ap.add_argument("--spec"); ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--chunk", type=int, default=CHUNK); ap.add_argument("--campaign"); ap.add_argument("--emails")
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--name"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    outdir = a.out if os.path.isabs(a.out) else os.path.join(ROOT, a.out)
    chunk = max(1, min(a.chunk, CHUNK))
    if a.cmd == "convert":
        if not a.inp:
            out({"ok": False, "error": "--in <csv> is required"})
        convert(a.inp, outdir, chunk, a.name)
    elif a.cmd == "prepare":
        if not a.spec:
            out({"ok": False, "error": "--spec <search.json> is required"})
        prepare(a.spec, outdir, chunk)
    elif a.cmd == "campaigns":
        campaigns()
    elif a.cmd == "push":
        if not (a.campaign and a.inp):
            out({"ok": False, "error": "--campaign <id> and --in <csv> are required"})
        push(a.campaign, a.inp, a.limit, a.dry_run)
    else:
        if not a.campaign:
            out({"ok": False, "error": "--campaign <id> is required"})
        stop(a.campaign, a.emails)


if __name__ == "__main__":
    main()
