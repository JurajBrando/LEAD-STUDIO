"""
mailer.py - the sending side of Lead Studio, without AgentSDR.

Two ways to send:

  1. Manyreach - your campaigns, your warmed senders, their pacing and reply handling.
     You only hand it prospects.

       python mailer.py mr-check                       is the API key good, what does the account hold
       python mailer.py mr-campaigns                   campaigns with their ids and status
       python mailer.py mr-senders                     the sending mailboxes Manyreach has
       python mailer.py mr-lists                       prospect lists
       python mailer.py mr-push --campaign <id> [--list <id>] --in <csv> [--limit N] [--dry-run]
            100 prospects a call, 1.2s apart, resumable - the same contract as your
            Affiliate_Targets\\MANYREACH_IMPORT scripts. Re-running never uploads anyone twice.
       python mailer.py mr-raw --path /api/v1/campaigns [--method GET] [--spec body.json]

  2. Your own mailboxes over SMTP and IMAP - no third party in the middle.

       python mailer.py box-list                       the mailboxes saved on this computer
       python mailer.py box-check --account "<name>"   log in to SMTP and IMAP, report what answered
       python mailer.py box-send --account "<name>" --to a@b.com --subject "Hi" --text "..." [--dry-run]
       python mailer.py box-replies --account "<name>" [--days 7]
       python mailer.py box-campaign --spec spec.json  a whole CSV, paced, resumable, with a daily cap

Settings live in lead_studio_settings.json next to this file (Connections page).
Mailbox passwords are stored there in plain text on this computer - use an app password,
never your main account password, and keep that file to yourself.

A caution that comes from deliverability, not from me: a fresh mailbox that sends a hundred
cold emails on day one lands in spam and stays there. box-campaign paces sends and enforces a
daily cap per mailbox for that reason; raise the cap slowly over weeks.
"""
import os, re, sys, csv, ssl, json, time, random, sqlite3, smtplib, imaplib, argparse
import datetime, email, email.utils, email.header
import urllib.parse, urllib.request, urllib.error
from email.message import EmailMessage

csv.field_size_limit(10 ** 9)
APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(APP))
SETTINGS = os.path.join(APP, "lead_studio_settings.json")
LOGS = os.path.join(APP, "logs")
SENTLOG = os.path.join(LOGS, "mail_sent.csv")
STATE = os.path.join(LOGS, "mail_campaign_state.json")
MR_BASE = "https://api.manyreach.com"
EMAIL_RX = re.compile(r"^[^@\s,;]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
VAR_RX = re.compile(r"\{\{\s*([A-Za-z0-9_ .\-]+?)\s*(?:\|([^}]*))?\}\}")

# column name -> merge field, so {{first_name}} works whatever the file calls it
ALIAS = {"email": "email", "best_email": "email", "e_mail": "email", "email_address": "email", "contact_email": "email",
         "first_name": "first_name", "firstname": "first_name", "first": "first_name",
         "last_name": "last_name", "lastname": "last_name", "last": "last_name",
         "name": "name", "full_name": "name", "contact_name": "name",
         "company": "company", "company_name": "company", "organization": "company", "merchant_name": "company",
         "title": "title", "job_title": "title", "position": "title", "headline": "title",
         "domain": "domain", "website": "domain", "company_website": "domain", "url": "domain",
         "phone": "phone", "phone_number": "phone", "mobile": "phone",
         "city": "city", "country": "country", "industry": "industry", "linkedin": "linkedin"}


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(0 if obj.get("ok") else 1)


def settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def boxes():
    b = settings().get("mailboxes")
    return b if isinstance(b, list) else []


def box(name):
    bs = boxes()
    if not bs:
        return None, "No mailbox is saved yet. Add one on the Connections page."
    if not name:
        return bs[0], ""
    hit = next((b for b in bs if str(b.get("name", "")).strip().lower() == str(name).strip().lower()), None)
    if not hit:
        return None, "No mailbox called %r. Saved: %s" % (name, ", ".join(str(b.get("name")) for b in bs))
    return hit, ""


# ------------------------------------------------------------------ Manyreach
# The contract below is the one your own upload scripts in Affiliate_Targets\\MANYREACH_IMPORT use
# and that has already moved hundreds of thousands of prospects: /api/v2/prospects/bulk, the
# X-API-Key header, 100 rows a call, 60 calls a minute. Nothing here is guessed.
MR_API = "/api/v2"
MR_CHUNK, MR_GAP = 100, 1.2
MR_DB = os.path.join(LOGS, "manyreach_uploaded.sqlite")

# CSV column -> the field name Manyreach expects
MR_MAP = {"email": "email", "best_email": "email", "e_mail": "email", "email_address": "email",
          "first": "firstName", "first_name": "firstName", "firstname": "firstName", "contact_first": "firstName",
          "last": "lastName", "last_name": "lastName", "lastname": "lastName", "contact_last": "lastName",
          "company": "company", "company_name": "company", "business": "company", "brand": "company",
          "name": "company", "maps_name": "company", "li_company": "company",
          "title": "jobPosition", "job_title": "jobPosition", "contact_title": "jobPosition", "position": "jobPosition",
          "website": "website", "official_domain": "website", "url": "website",
          "domain": "domain", "company_domain": "domain",
          "industry": "industry", "vertical": "industry", "segment": "industry", "trade": "industry",
          "main_category": "industry", "category": "industry", "li_industry": "industry",
          "employees": "companySize", "size": "companySize", "company_size": "companySize", "li_employees": "companySize",
          "country": "country", "city": "city", "locality": "city", "state": "state", "region": "state",
          "phone": "phone", "telephones": "phone", "maps_phone": "phone",
          "linkedin": "personalSocial", "linkedin_url": "personalSocial",
          "custom1": "custom1", "custom2": "custom2", "custom3": "custom3", "custom4": "custom4"}
MR_LIM = {"domain": 180, "companySize": 32, "state": 128, "custom1": 2000, "custom2": 2000,
          "custom3": 2000, "custom4": 2000}
MR_BAD_STATUS = re.compile(r"invalid|bounce|undeliver|do_not|disposable|spamtrap", re.I)
MR_JUNK = re.compile(r"^(noreply|no-reply|donotreply|privacy|abuse|postmaster|webmaster|n/a)@"
                     r"|example\.com$|sentry|wixpress|\.(png|jpg|gif|webp)$", re.I)


def mr_key():
    return (settings().get("mr_key") or "").strip()


def mr_call(path, method="GET", payload=None, params=None, timeout=240):
    key = mr_key()
    if not key:
        return False, {"error": "No Manyreach API key saved. Manyreach > Account Settings > API, then paste it "
                                "on the Connections page."}, 0
    base = (settings().get("mr_base") or MR_BASE).rstrip("/")
    url = base + (path if path.startswith("/api/") else MR_API + path)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Accept": "application/json", "X-API-Key": key}
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                return True, json.loads(raw or "{}"), r.status
            except ValueError:
                return True, {"text": raw[:4000]}, r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1500]
        return False, {"http": e.code, "body": body, "retry_after": e.headers.get("Retry-After")}, e.code
    except Exception as e:
        return False, {"error": str(e)}, 0


def rows_of(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in ("data", "items", "results", "campaigns", "senders", "lists", "prospects", "records", "value"):
            v = body.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                for k2 in ("items", "data", "results"):
                    if isinstance(v.get(k2), list):
                        return v[k2]
    return []


def pick(d, *names):
    for n in names:
        for k in d:
            if k.lower().replace("_", "") == n.lower().replace("_", ""):
                v = d[k]
                if v not in (None, ""):
                    return v
    return ""


def mr_check():
    ok, body, code = mr_call("/campaigns")
    if not ok:
        return out({"ok": False, "channel": "manyreach", "http": code, "detail": body,
                    "hint": "The key is in Manyreach under Account Settings, API."})
    camps = rows_of(body)
    ok2, b2, c2 = mr_call("/senders")
    ok3, b3, c3 = mr_call("/lists")
    return out({"ok": True, "channel": "manyreach", "api": (settings().get("mr_base") or MR_BASE) + MR_API,
                "campaigns": len(camps), "senders": len(rows_of(b2)) if ok2 else 0,
                "lists": len(rows_of(b3)) if ok3 else 0,
                "uploaded_before": uploaded_count()})


def mr_campaigns():
    ok, body, code = mr_call("/campaigns")
    if not ok:
        return out({"ok": False, "channel": "manyreach", "http": code, "detail": body})
    items = [{"id": str(pick(c, "id", "campaignId", "uuid")), "name": str(pick(c, "name", "title", "campaignName")),
              "status": str(pick(c, "status", "state")), "prospects": pick(c, "prospectsCount", "prospects", "totalProspects"),
              "sent": pick(c, "sentCount", "sent", "emailsSent")}
             for c in rows_of(body) if isinstance(c, dict)]
    return out({"ok": True, "channel": "manyreach", "campaigns": items})


def sender_row(s):
    """One Manyreach sender, in the shape Lead Studio keeps a mailbox in.
    Manyreach never hands back the password - that stays with them, so it is left empty."""
    em = str(pick(s, "email", "fromEmail", "address", "username"))
    return {"id": str(pick(s, "senderId", "id", "uuid")), "email": em,
            "from_name": str(pick(s, "fromName", "name", "displayName")),
            "first_name": str(pick(s, "firstName")), "last_name": str(pick(s, "lastName")),
            "account_type": str(pick(s, "accountType")),
            "smtp_host": str(pick(s, "customSmtpServer", "smtpServer", "smtpHost")),
            "smtp_port": pick(s, "customSmtpPort", "smtpPort") or 587,
            "smtp_user": str(pick(s, "customSmtpUsername", "smtpUsername")) or em,
            "imap_host": str(pick(s, "customImapServer", "imapServer", "imapHost")),
            "imap_port": pick(s, "customImapPort", "imapPort") or 993,
            "imap_user": str(pick(s, "customImapUsername", "imapUsername")) or em,
            "daily_limit": pick(s, "dailyLimit", "limit") or 25,
            "delay_minutes": pick(s, "delayMinMinutes"),
            "warmup": bool(pick(s, "warmup", "warmupEnabled")),
            "warmup_daily_limit": pick(s, "warmupDailyLimit"),
            "warmup_reply_percent": pick(s, "warmupReplyPercent"),
            "disconnected": bool(pick(s, "disconnected")),
            "disconnection_reason": str(pick(s, "disconnectionReason"))}


def mr_all_senders(cap=4000):
    """Every sender, paging until the account runs out. The server caps a page at 30."""
    seen, items, page = set(), [], 1
    while page <= 200 and len(items) < cap:
        ok, body, code = mr_call("/senders", params={"page": page, "limit": 100})
        if not ok:
            return items, {"http": code, "detail": body, "page": page}
        rows = [r for r in rows_of(body) if isinstance(r, dict)]
        fresh = [r for r in rows if str(pick(r, "senderId", "id")) not in seen]
        if not fresh:
            break                      # the API ignored the page argument, or we reached the end
        for r in fresh:
            seen.add(str(pick(r, "senderId", "id")))
            items.append(sender_row(r))
        if len(rows) < 2:
            break
        page += 1
        time.sleep(1.1)                # their key allows 60 calls a minute
    return items, None


def mr_senders(every=False):
    if every:
        items, err = mr_all_senders()
        if err and not items:
            return out({"ok": False, "channel": "manyreach", "detail": err})
        return out({"ok": True, "channel": "manyreach", "senders": items, "count": len(items),
                    "connected": sum(1 for x in items if not x["disconnected"]),
                    "warning": err})
    ok, body, code = mr_call("/senders")
    if not ok:
        return out({"ok": False, "channel": "manyreach", "http": code, "detail": body})
    items = [sender_row(x) for x in rows_of(body) if isinstance(x, dict)]
    return out({"ok": True, "channel": "manyreach", "senders": items, "count": len(items)})


def mr_lists():
    ok, body, code = mr_call("/lists")
    if not ok:
        return out({"ok": False, "channel": "manyreach", "http": code, "detail": body})
    items = [{"id": str(pick(l, "id", "listId", "uuid")), "name": str(pick(l, "name", "title")),
              "prospects": pick(l, "prospectsCount", "count", "total")}
             for l in rows_of(body) if isinstance(l, dict)]
    return out({"ok": True, "channel": "manyreach", "lists": items})


# ---- the upload itself: same shape, same guards as your MANYREACH_IMPORT scripts
def mr_db():
    os.makedirs(LOGS, exist_ok=True)
    db = sqlite3.connect(MR_DB)
    db.execute("create table if not exists done(email text, target text, when_ts text, primary key(email, target))")
    db.commit()
    return db


def uploaded_count():
    try:
        db = mr_db()
        n = db.execute("select count(*) from done").fetchone()[0]
        db.close()
        return n
    except Exception:
        return 0


def mr_rows(path, limit=0, target_note=""):
    """Any CSV -> Manyreach prospects. Bad statuses, junk and duplicate addresses are dropped here,
    before anything leaves this computer."""
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if not os.path.exists(p):
        return [], {}, "There is no file at %s" % p
    stats = {"read": 0, "no_email": 0, "bad_status": 0, "junk": 0, "duplicate": 0}
    seen, rows = set(), []
    with open(p, encoding="utf-8-sig", errors="replace", newline="") as fh:
        rd = csv.DictReader(fh)
        if not rd.fieldnames:
            return [], stats, "That file has no header row."
        cols = {c: MR_MAP.get((c or "").strip().lower()) for c in rd.fieldnames}
        if "email" not in cols.values():
            return [], stats, "No email column in: " + ", ".join([c for c in rd.fieldnames if c][:12])
        for r in rd:
            stats["read"] += 1
            if MR_BAD_STATUS.search(str(r.get("email_status") or r.get("email_type") or "")):
                stats["bad_status"] += 1
                continue
            raw = ""
            for c, f in cols.items():
                if f == "email" and str(r.get(c) or "").strip():
                    raw = str(r[c]); break
            em = ""
            for cand in re.split(r"[|;,\s]+", raw.lower().strip()):
                if EMAIL_RX.match(cand):
                    em = cand; break
            if not em:
                stats["no_email"] += 1
                continue
            if MR_JUNK.search(em):
                stats["junk"] += 1
                continue
            if em in seen:
                stats["duplicate"] += 1
                continue
            seen.add(em)
            p_ = {"email": em}
            for c, f in cols.items():
                if not f or f == "email":
                    continue
                v = str(r.get(c) or "").strip()
                if v and v.lower() not in ("nan", "none", "n/a") and not p_.get(f):
                    p_[f] = v[:MR_LIM.get(f, 512)]
            if p_.get("domain"):
                p_["domain"] = re.sub(r"^(https?://)?(www\.)?", "", p_["domain"].lower()).split("/")[0][:180]
            for k in ("firstName", "lastName"):
                if p_.get(k) and p_[k].islower():
                    p_[k] = p_[k].title()
            if target_note:
                p_.setdefault("custom10", target_note[:512])
            rows.append(p_)
            if limit and len(rows) >= limit:
                break
    return rows, stats, ""


def mr_push(campaign="", list_id="", path="", limit=0, dry=False, add_only_if_new=True, resume=True):
    if not campaign and not list_id:
        return out({"ok": False, "error": "Give a campaign id, a list id, or both "
                                          "(mr-campaigns and mr-lists show them)."})
    target = "c%s/l%s" % (campaign or "-", list_id or "-")
    rows, stats, err = mr_rows(path, limit, target_note=campaign or list_id)
    if err:
        return out({"ok": False, "error": err})
    if not rows:
        return out({"ok": False, "error": "No usable email addresses in that file.", "stats": stats})
    db = mr_db() if resume else None
    if db:
        before = len(rows)
        rows = [r for r in rows
                if not db.execute("select 1 from done where email=? and target=?",
                                  (r["email"], target)).fetchone()]
        stats["already_uploaded"] = before - len(rows)
    params = {}
    if campaign:
        params["campaignId"] = campaign
    if list_id:
        params["listId"] = list_id
    if add_only_if_new:
        params["addOnlyIfNew"] = "true"
    if dry:
        if db:
            db.close()
        return out({"ok": True, "dry_run": True, "channel": "manyreach", "file": path, "target": params,
                    "would_upload": len(rows), "stats": stats, "first_rows": rows[:3],
                    "batches": (len(rows) + MR_CHUNK - 1) // MR_CHUNK,
                    "about": "%d calls at %.1fs apart - roughly %d minutes."
                             % ((len(rows) + MR_CHUNK - 1) // MR_CHUNK, MR_GAP,
                                max(1, round((len(rows) / MR_CHUNK) * MR_GAP / 60)))})
    if not rows:
        if db:
            db.close()
        return out({"ok": True, "channel": "manyreach", "uploaded": 0, "stats": stats,
                    "note": "Everyone in that file has already been uploaded to this target."})
    sent = inserted = updated = enrolled = 0
    last = [0.0]
    for i in range(0, len(rows), MR_CHUNK):
        batch = rows[i:i + MR_CHUNK]
        for attempt in range(10):
            w = MR_GAP - (time.time() - last[0])
            if w > 0:
                time.sleep(w)
            last[0] = time.time()
            ok, res, code = mr_call("/prospects/bulk", "POST", {"prospects": batch}, params)
            if code == 429:
                wait = int(res.get("retry_after") or 60)
                print("rate limit, waiting %ds" % wait, flush=True)
                time.sleep(wait + 2)
                continue
            if code >= 500:
                print("server error %s, retrying in 30s" % code, flush=True)
                time.sleep(30)
                continue
            if not ok:
                if db:
                    db.commit(); db.close()
                return out({"ok": False, "channel": "manyreach", "http": code, "detail": res,
                            "uploaded_before_stopping": sent, "stats": stats,
                            "note": "Nothing was lost - run it again and it carries on from here."})
            inserted += res.get("prospectsInserted", 0) or 0
            updated += res.get("prospectsUpdated", 0) or 0
            enrolled += (res.get("campaignAdded", res.get("subscriptionsAdded", 0)) or 0)
            sent += len(batch)
            if db:
                db.executemany("insert or ignore into done values(?,?,?)",
                               [(p["email"], target, datetime.datetime.now().isoformat(timespec="seconds"))
                                for p in batch])
                db.commit()
            print("%d/%d uploaded (new %d, updated %d, enrolled %d)"
                  % (sent, len(rows), inserted, updated, enrolled), flush=True)
            break
        else:
            if db:
                db.commit(); db.close()
            return out({"ok": False, "error": "Manyreach kept refusing after ten tries. Run it again to carry on.",
                        "uploaded_before_stopping": sent})
    if db:
        db.close()
    return out({"ok": True, "channel": "manyreach", "file": path, "target": params, "uploaded": sent,
                "new_in_crm": inserted, "updated": updated, "enrolled_in_campaign": enrolled, "stats": stats})


def mr_raw(path, method, spec):
    payload = None
    if spec:
        with open(spec, encoding="utf-8") as fh:
            payload = json.load(fh)
    ok, body, code = mr_call(path, method.upper(), payload)
    return out({"ok": ok and code < 300, "channel": "manyreach", "path": path, "method": method.upper(),
                "http": code, "response": body})


# ------------------------------------------------------------------ your own mailboxes
def smtp_open(b):
    host, port = str(b.get("smtp_host") or "").strip(), int(b.get("smtp_port") or 587)
    if not host:
        return None, "That mailbox has no SMTP server address."
    ctx = ssl.create_default_context()
    try:
        if port == 465 or str(b.get("smtp_security") or "").lower() == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=40, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=40)
            s.ehlo()
            if str(b.get("smtp_security") or "starttls").lower() != "none":
                s.starttls(context=ctx); s.ehlo()
        user = str(b.get("smtp_user") or b.get("from_email") or "")
        pw = str(b.get("smtp_pass") or "")
        if user and pw:
            s.login(user, pw)
        return s, ""
    except smtplib.SMTPAuthenticationError as e:
        return None, "SMTP refused the login: %s. Gmail and Outlook need an app password, not the account password." % (
            getattr(e, "smtp_error", b"") or e)
    except Exception as e:
        return None, "SMTP (%s:%s): %s" % (host, port, e)


def imap_open(b):
    host, port = str(b.get("imap_host") or "").strip(), int(b.get("imap_port") or 993)
    if not host:
        return None, "That mailbox has no IMAP server address."
    try:
        m = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context()) if port != 143 \
            else imaplib.IMAP4(host, port)
        if port == 143:
            m.starttls(ssl.create_default_context())
        m.login(str(b.get("imap_user") or b.get("smtp_user") or b.get("from_email") or ""),
                str(b.get("imap_pass") or b.get("smtp_pass") or ""))
        return m, ""
    except Exception as e:
        return None, "IMAP (%s:%s): %s" % (host, port, e)


def box_list():
    items = []
    for b in boxes():
        items.append({"name": b.get("name"), "from_email": b.get("from_email"), "from_name": b.get("from_name"),
                      "smtp": "%s:%s" % (b.get("smtp_host"), b.get("smtp_port") or 587),
                      "imap": "%s:%s" % (b.get("imap_host"), b.get("imap_port") or 993) if b.get("imap_host") else "",
                      "daily_limit": int(b.get("daily_limit") or 40), "sent_today": sent_today(b.get("name"))})
    return out({"ok": True, "mailboxes": items, "count": len(items)})


def box_check(name):
    b, err = box(name)
    if err:
        return out({"ok": False, "error": err})
    res = {"ok": False, "mailbox": b.get("name"), "from": b.get("from_email")}
    s, e1 = smtp_open(b)
    res["smtp"] = "ok" if s else "failed"
    res["smtp_detail"] = "" if s else e1
    if s:
        try:
            s.quit()
        except Exception:
            pass
    if b.get("imap_host"):
        m, e2 = imap_open(b)
        res["imap"] = "ok" if m else "failed"
        res["imap_detail"] = "" if m else e2
        if m:
            try:
                m.select("INBOX", readonly=True)
                typ, data = m.search(None, "UNSEEN")
                res["unread"] = len((data[0] or b"").split()) if typ == "OK" else None
                m.logout()
            except Exception as e:
                res["imap_detail"] = str(e)
    else:
        res["imap"] = "not set"
    res["ok"] = res["smtp"] == "ok"
    res["sent_today"] = sent_today(b.get("name"))
    res["daily_limit"] = int(b.get("daily_limit") or 40)
    return out(res)


def merge(text, fields, email_addr):
    """{{first_name}} and {{company|there}} - a missing field without a fallback leaves the row out."""
    missing = []
    f = dict(fields); f["email"] = email_addr
    f.setdefault("first_name", (f.get("name") or "").split(" ")[0] if f.get("name") else "")

    def sub(m):
        k = m.group(1).strip().lower().replace(" ", "_")
        v = str(f.get(k) or "").strip()
        if v:
            return v
        if m.group(2) is not None:
            return m.group(2).strip()
        missing.append(k)
        return ""
    return VAR_RX.sub(sub, text or ""), missing


def build_msg(b, to, subject, text, html=""):
    msg = EmailMessage()
    frm = str(b.get("from_email") or b.get("smtp_user") or "")
    msg["From"] = email.utils.formataddr((str(b.get("from_name") or ""), frm)) if b.get("from_name") else frm
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=frm.split("@")[-1] if "@" in frm else None)
    if b.get("reply_to"):
        msg["Reply-To"] = str(b["reply_to"])
    if b.get("unsubscribe_mailto"):
        msg["List-Unsubscribe"] = "<mailto:%s?subject=unsubscribe>" % b["unsubscribe_mailto"]
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def log_sent(account, to, subject, ok, detail=""):
    os.makedirs(LOGS, exist_ok=True)
    new = not os.path.exists(SENTLOG)
    with open(SENTLOG, "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["when", "day", "account", "to", "subject", "ok", "detail"])
        now = datetime.datetime.now()
        w.writerow([now.isoformat(timespec="seconds"), now.strftime("%Y-%m-%d"), account, to, subject,
                    "1" if ok else "0", detail[:300]])


def sent_today(account):
    if not os.path.exists(SENTLOG):
        return 0
    day = datetime.date.today().strftime("%Y-%m-%d")
    n = 0
    try:
        with open(SENTLOG, encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("day") == day and r.get("account") == account and r.get("ok") == "1":
                    n += 1
    except OSError:
        pass
    return n


def already_sent(account):
    """Every address this mailbox has ever sent to - so a rerun never emails the same person twice."""
    s = set()
    if not os.path.exists(SENTLOG):
        return s
    try:
        with open(SENTLOG, encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("account") == account and r.get("ok") == "1":
                    s.add((r.get("to") or "").strip().lower())
    except OSError:
        pass
    return s


def box_send(name, to, subject, text, dry=False):
    b, err = box(name)
    if err:
        return out({"ok": False, "error": err})
    to = str(to or "").strip()
    if not EMAIL_RX.match(to):
        return out({"ok": False, "error": "That is not an email address: %r" % to})
    if not str(subject or "").strip() or not str(text or "").strip():
        return out({"ok": False, "error": "A subject and a body, please."})
    if dry:
        msg = build_msg(b, to, subject, text)
        return out({"ok": True, "dry_run": True, "mailbox": b.get("name"), "headers": dict(msg.items()),
                    "body": text[:2000]})
    s, e = smtp_open(b)
    if not s:
        return out({"ok": False, "error": e})
    try:
        s.send_message(build_msg(b, to, subject, text))
        log_sent(b.get("name"), to, subject, True)
        return out({"ok": True, "mailbox": b.get("name"), "to": to, "subject": subject,
                    "sent_today": sent_today(b.get("name"))})
    except Exception as ex:
        log_sent(b.get("name"), to, subject, False, str(ex))
        return out({"ok": False, "error": str(ex)})
    finally:
        try:
            s.quit()
        except Exception:
            pass


def hdr(v):
    if not v:
        return ""
    try:
        return "".join(p.decode(c or "utf-8", "replace") if isinstance(p, bytes) else p
                       for p, c in email.header.decode_header(v))[:300]
    except Exception:
        return str(v)[:300]


def box_replies(name, days=7):
    b, err = box(name)
    if err:
        return out({"ok": False, "error": err})
    m, e = imap_open(b)
    if not m:
        return out({"ok": False, "error": e})
    try:
        m.select("INBOX", readonly=True)
        since = (datetime.date.today() - datetime.timedelta(days=max(1, int(days)))).strftime("%d-%b-%Y")
        typ, data = m.search(None, "SINCE", since)
        ids = (data[0] or b"").split()[-200:]
        items = []
        for i in reversed(ids):
            typ, d = m.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE IN-REPLY-TO)])")
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                continue
            msg = email.message_from_bytes(d[0][1])
            frm = hdr(msg.get("From"))
            items.append({"from": frm, "subject": hdr(msg.get("Subject")), "date": hdr(msg.get("Date")),
                          "is_reply": bool(msg.get("In-Reply-To")),
                          "address": (email.utils.parseaddr(frm)[1] or "").lower()})
        m.logout()
        auto = re.compile(r"(out of office|automatic reply|autoreply|undeliverable|delivery status|mailer-daemon)", re.I)
        return out({"ok": True, "mailbox": b.get("name"), "days": int(days), "messages": len(items),
                    "replies": [x for x in items if x["is_reply"] and not auto.search(x["subject"])],
                    "bounces": [x for x in items if auto.search(x["subject"])]})
    except Exception as ex:
        return out({"ok": False, "error": str(ex)})


def box_campaign(spec_path, dry=False):
    """One mailbox, one CSV, one message - paced, capped and resumable. Prints a line per send."""
    with open(spec_path, encoding="utf-8") as fh:
        sp = json.load(fh)
    b, err = box(sp.get("account"))
    if err:
        return out({"ok": False, "error": err})
    acct = b.get("name")
    subject_t, body_t = str(sp.get("subject") or ""), str(sp.get("body") or "")
    if not subject_t.strip() or not body_t.strip():
        return out({"ok": False, "error": "The campaign needs a subject and a body."})
    leads, err = read_leads(str(sp.get("file") or ""))
    if err:
        return out({"ok": False, "error": err})
    cap = int(sp.get("daily_limit") or b.get("daily_limit") or 40)
    done = sent_today(acct)
    room = max(0, cap - done)
    want = int(sp.get("limit") or 0) or len(leads)
    delay = max(5, int(sp.get("delay") or 45))
    seen = already_sent(acct) if sp.get("skip_already_sent", True) else set()
    queue = [l for l in leads if l["email"] not in seen][:min(want, room if room else 0)]

    if dry:
        prev = []
        for l in queue[:3]:
            su, m1 = merge(subject_t, l["fields"], l["email"])
            bo, m2 = merge(body_t, l["fields"], l["email"])
            prev.append({"to": l["email"], "subject": su, "body": bo, "missing_fields": sorted(set(m1 + m2))})
        return out({"ok": True, "dry_run": True, "mailbox": acct, "from": b.get("from_email"),
                    "in_file": len(leads), "already_emailed": len(leads) - len([l for l in leads if l["email"] not in seen]),
                    "sent_today": done, "daily_limit": cap, "would_send_now": len(queue),
                    "delay_seconds": delay, "preview": prev})

    if not queue:
        return out({"ok": True, "mailbox": acct, "sent": 0, "sent_today": done, "daily_limit": cap,
                    "note": "Nothing to send: the daily cap is reached, or everyone in the file was emailed before."})
    s, e = smtp_open(b)
    if not s:
        return out({"ok": False, "error": e})
    sent, skipped, failed = 0, 0, []
    try:
        for n, l in enumerate(queue, 1):
            su, m1 = merge(subject_t, l["fields"], l["email"])
            bo, m2 = merge(body_t, l["fields"], l["email"])
            if (m1 or m2) and sp.get("require_fields", True):
                skipped += 1
                print("skip %s - no value for %s" % (l["email"], ", ".join(sorted(set(m1 + m2)))), flush=True)
                continue
            try:
                s.send_message(build_msg(b, l["email"], su, bo, sp.get("html") and bo.replace("\n", "<br>") or ""))
                sent += 1
                log_sent(acct, l["email"], su, True)
                print("%d/%d sent to %s" % (n, len(queue), l["email"]), flush=True)
            except smtplib.SMTPServerDisconnected:
                s, e2 = smtp_open(b)
                if not s:
                    failed.append({"to": l["email"], "error": e2}); break
                continue
            except Exception as ex:
                failed.append({"to": l["email"], "error": str(ex)[:200]})
                log_sent(acct, l["email"], su, False, str(ex))
                print("FAILED %s - %s" % (l["email"], ex), flush=True)
                if len(failed) >= 5:
                    print("five failures in a row - stopping", flush=True); break
            if n < len(queue):
                time.sleep(delay + random.uniform(0, delay * 0.4))
    finally:
        try:
            s.quit()
        except Exception:
            pass
    return out({"ok": not failed, "mailbox": acct, "sent": sent, "skipped_missing_fields": skipped,
                "failed": failed, "sent_today": sent_today(acct), "daily_limit": cap,
                "left_in_file": max(0, len(leads) - len(seen) - sent),
                "log": os.path.relpath(SENTLOG, APP)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["mr-check", "mr-campaigns", "mr-senders", "mr-lists", "mr-push", "mr-raw",
                                    "box-list", "box-check", "box-send", "box-replies", "box-campaign"])
    ap.add_argument("--campaign"); ap.add_argument("--list", dest="lst"); ap.add_argument("--in", dest="inp")
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--all-again", action="store_true")
    ap.add_argument("--all", dest="every", action="store_true")
    ap.add_argument("--path"); ap.add_argument("--method", default="GET"); ap.add_argument("--spec")
    ap.add_argument("--account"); ap.add_argument("--to"); ap.add_argument("--subject"); ap.add_argument("--text")
    ap.add_argument("--days", type=int, default=7); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "mr-check":
        mr_check()
    elif a.cmd == "mr-campaigns":
        mr_campaigns()
    elif a.cmd == "mr-senders":
        mr_senders(a.every)
    elif a.cmd == "mr-lists":
        mr_lists()
    elif a.cmd == "mr-push":
        mr_push(a.campaign or "", a.lst or "", a.inp or "", a.limit, a.dry_run, resume=not a.all_again)
    elif a.cmd == "mr-raw":
        if not a.path:
            out({"ok": False, "error": "--path /api/v1/campaigns is required"})
        mr_raw(a.path, a.method, a.spec)
    elif a.cmd == "box-list":
        box_list()
    elif a.cmd == "box-check":
        box_check(a.account)
    elif a.cmd == "box-send":
        box_send(a.account, a.to, a.subject, a.text, a.dry_run)
    elif a.cmd == "box-replies":
        box_replies(a.account, a.days)
    else:
        if not a.spec:
            out({"ok": False, "error": "--spec campaign.json is required"})
        box_campaign(a.spec, a.dry_run)


if __name__ == "__main__":
    main()
