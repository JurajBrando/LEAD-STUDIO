"""
Lead Studio - the newer sections: Unibox, channel queues (LinkedIn, Instagram, YouTube, X, contact forms),
the Video maker and Agile Assets, the SEO library with Ollama Cloud, and Ads connectors over MCP.

lead_studio.py hands every /api/plus/... request to this module (handle_get, handle_post, handle_upload).
Settings that belong only to these sections live in studio_plus_settings.json next to this file.
"""
import os, re, sys, json, time, shutil, datetime, threading, subprocess, importlib.util
import urllib.request, urllib.error, email.utils
from concurrent.futures import ThreadPoolExecutor

M = None                                     # the lead_studio module, set by init()
APP = os.path.dirname(os.path.abspath(__file__))
PSET = os.path.join(APP, "studio_plus_settings.json")
CHANNELS = {"linkedin": "LinkedIn", "instagram": "Instagram", "youtube": "YouTube", "twitter": "X / Twitter",
            "forms": "Contact forms"}
URL_HINTS = {"linkedin": r"linkedin", "instagram": r"instagram|^ig", "youtube": r"youtube|^yt|channel",
             "twitter": r"twitter|^x$|^x_|x_url|tweet", "forms": r"contact.?(page|url|form)|form.?url|^contact$"}
VAR_RX = re.compile(r"\{\{\s*([^}|]+?)\s*(?:\|([^}]*))?\}\}")
AUDIO = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac")
IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp")
LYRICS = (".txt", ".md", ".lrc")


def init(module):
    global M
    M = module


# ------------------------------------------------------------------ settings
DEFAULTS = {"agile_email": "", "agile_mailbox": "", "seo_dirs": "", "seo_model": "", "ads_servers": []}


def pset():
    s = dict(DEFAULTS)
    try:
        with open(PSET, encoding="utf-8") as fh:
            s.update({k: v for k, v in json.load(fh).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return s


def psave(s):
    tmp = PSET + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, PSET)


def mask(v):
    v = str(v or "")
    return ("..." + v[-4:]) if len(v) > 8 else ("set" if v else "")


def public():
    s = pset()
    return {"agile_email": s["agile_email"], "agile_mailbox": s["agile_mailbox"], "seo_dirs": s["seo_dirs"],
            "seo_model": s["seo_model"],
            "ads_servers": [{"name": x.get("name"), "kind": x.get("kind"), "url": x.get("url", ""),
                             "command": x.get("command", ""), "args": x.get("args", []),
                             "headers": {k: mask(v) for k, v in (x.get("headers") or {}).items()},
                             "env": {k: mask(v) for k, v in (x.get("env") or {}).items()},
                             "preset": x.get("preset", "")} for x in s["ads_servers"]]}


# ------------------------------------------------------------------ small helpers
def rel(p):
    return os.path.relpath(p, M.ROOT).replace(os.sep, "/")


def ws(*parts):
    p = os.path.join(M.ROOT, *parts); os.makedirs(p, exist_ok=True); return p


def list_media(folder, exts):
    base = ws(*folder.split("/"))
    items = []
    for b, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith((".", "_"))]
        for f in files:
            if f.lower().endswith(exts) and ".part." not in f:
                p = os.path.join(b, f)
                items.append({"path": rel(p), "name": f, "mb": round(os.path.getsize(p) / 1e6, 1),
                              "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    return sorted(items, key=lambda x: x["when"], reverse=True)


def open_url(url):
    if not re.match(r"^https?://", str(url or "")):
        return {"ok": False, "error": "Only web addresses can be opened."}
    try:
        if hasattr(os, "startfile"):
            os.startfile(url)                  # the default browser, where you are signed in
        else:
            import webbrowser; webbrowser.open(url)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mailer():
    spec = importlib.util.spec_from_file_location("ls_mailer", os.path.join(APP, "mailer.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def mailbox(name):
    for b in (M.load_settings().get("mailboxes") or []):
        if isinstance(b, dict) and str(b.get("name", "")).lower() == str(name or "").lower():
            return b
    return None


# ------------------------------------------------------------------ Ollama Cloud
def ollama_chat(prompt, model="", system="", timeout=240):
    s = M.load_settings(); key = M.ollama_key(s)
    model = model or pset().get("seo_model") or s.get("ollama_model") or ""
    if not key:
        return None, "No Ollama key yet - add it under Connections (free at ollama.com/settings/keys)."
    if not model:
        return None, "Pick a model first."
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = json.dumps({"model": model, "stream": False, "messages": msgs}).encode()
    req = urllib.request.Request(s["ollama_base"].rstrip("/") + "/api/chat", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return (d.get("message") or {}).get("content", "").strip(), ""
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 429:
            return None, "Ollama Cloud's usage limit for this period is reached (HTTP 429). It resets; or pick a smaller model."
        return None, "Ollama answered HTTP %d: %s" % (e.code, detail)
    except Exception as e:
        return None, "Could not reach Ollama: %s" % e


# ------------------------------------------------------------------ Unibox: replies from every mailbox
def unibox(days=7):
    boxes = [b for b in (M.load_settings().get("mailboxes") or []) if isinstance(b, dict) and b.get("imap_host")]
    if not boxes:
        return {"ok": False, "error": "No mailbox has IMAP set up. Add the IMAP server under Senders."}

    def one(b):
        r = M.channel("box-replies", {"account": b["name"], "days": days}, timeout=90)
        return b["name"], r
    replies, bounces, errors = [], 0, []
    with ThreadPoolExecutor(8) as ex:
        for name, r in ex.map(one, boxes[:60]):
            if not r.get("ok"):
                errors.append({"mailbox": name, "error": r.get("error") or "no answer"}); continue
            bounces += len(r.get("bounces") or [])
            for x in r.get("replies") or []:
                x["mailbox"] = name
                try:
                    x["ts"] = email.utils.parsedate_to_datetime(x.get("date") or "").timestamp()
                except Exception:
                    x["ts"] = 0
                replies.append(x)
    replies.sort(key=lambda x: -x["ts"])
    return {"ok": True, "mailboxes": len(boxes), "checked": min(60, len(boxes)), "replies": replies,
            "bounces": bounces, "errors": errors, "days": days}


# ------------------------------------------------------------------ channel queues
def qdir(ch):
    return ws("OUTREACH_QUEUES", ch)


def guess_cols(cols, ch):
    lc = [c.lower() for c in cols]
    pick = lambda rx: next((cols[i] for i, c in enumerate(lc) if re.search(rx, c)), "")
    url = pick(URL_HINTS.get(ch, "url"))
    if not url and ch == "forms":
        url = pick(r"website|^url$|domain|site")
    if not url:
        url = pick(r"profile|url|link")
    return {"url": url, "name": pick(r"^first_?name$|^name$|full_?name|contact"), "company": pick(r"company|organi[sz]ation|business")}


def merge(text, row):
    f = {k.strip().lower().replace(" ", "_"): v for k, v in row.items()}
    if "first_name" not in f and f.get("name"):
        f["first_name"] = str(f["name"]).split(" ")[0]

    def sub(m):
        v = str(f.get(m.group(1).strip().lower().replace(" ", "_")) or "").strip()
        return v or (m.group(2) or "").strip()
    return VAR_RX.sub(sub, text or "")


def queue_cols(b):
    t = M.read_table(str(b.get("file") or ""), limit=5)
    if not t["cols"]:
        return {"ok": False, "error": "That file is not a readable CSV in the workspace."}
    return {"ok": True, "cols": t["cols"], "sample": t["rows"][:3], "guess": guess_cols(t["cols"], b.get("channel", ""))}


def queue_build(b):
    ch = str(b.get("channel") or "")
    if ch not in CHANNELS:
        return {"ok": False, "error": "Unknown channel."}
    t = M.read_table(str(b.get("file") or ""), limit=min(1000, max(1, int(b.get("limit") or 300))))
    if not t["cols"]:
        return {"ok": False, "error": "That file is not a readable CSV in the workspace."}
    cols, uc = t["cols"], str(b.get("url_col") or "")
    if uc not in cols:
        return {"ok": False, "error": "Pick the column that holds the profile or page link."}
    rows, seen = [], set()
    for r in t["rows"]:
        row = dict(zip(cols, r))
        url = str(row.get(uc) or "").strip()
        if not url:
            continue
        if not re.match(r"^https?://", url):
            url = "https://" + url.lstrip("/")
        if url.lower() in seen:
            continue
        seen.add(url.lower())
        rows.append({"url": url, "name": str(row.get(b.get("name_col") or "") or ""),
                     "company": str(row.get(b.get("company_col") or "") or ""),
                     "fields": {k: v for k, v in list(row.items())[:40] if str(v).strip()},
                     "message": merge(b.get("template") or "", row), "status": "todo", "at": ""})
    if not rows:
        return {"ok": False, "error": "No row in that file has a link in the column you picked."}
    name = re.sub(r"[^\w\- ]", "", str(b.get("name") or "")).strip() or \
        "%s %s" % (os.path.splitext(os.path.basename(str(b.get("file"))))[0], datetime.date.today().isoformat())
    q = {"channel": ch, "name": name, "source": str(b.get("file")), "template": b.get("template") or "",
         "instruction": b.get("instruction") or "", "created": datetime.datetime.now().isoformat(timespec="seconds"), "rows": rows}
    with open(os.path.join(qdir(ch), name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(q, fh, ensure_ascii=False, indent=1)
    return {"ok": True, "name": name, "rows": len(rows)}


def queue_path(ch, name):
    if ch not in CHANNELS or not re.fullmatch(r"[\w\- ]{1,120}", str(name or "")):
        return None
    return os.path.join(qdir(ch), name + ".json")


def queue_list(ch):
    if ch not in CHANNELS:
        return {"ok": False, "error": "Unknown channel."}
    items = []
    for f in sorted(os.listdir(qdir(ch)), reverse=True):
        if f.endswith(".json"):
            try:
                q = json.load(open(os.path.join(qdir(ch), f), encoding="utf-8"))
                st = [r["status"] for r in q["rows"]]
                items.append({"name": q["name"], "created": q.get("created", ""), "rows": len(st),
                              "sent": st.count("sent"), "skipped": st.count("skipped"), "source": q.get("source", "")})
            except (OSError, ValueError, KeyError):
                continue
    return {"ok": True, "queues": items}


QLOCK = threading.Lock()


def queue_get(ch, name):
    p = queue_path(ch, name)
    if not p or not os.path.exists(p):
        return {"ok": False, "error": "That queue is gone."}
    return {"ok": True, "queue": json.load(open(p, encoding="utf-8"))}


def queue_update(b):
    p = queue_path(b.get("channel"), b.get("name"))
    if not p or not os.path.exists(p):
        return {"ok": False, "error": "That queue is gone."}
    with QLOCK:
        q = json.load(open(p, encoding="utf-8"))
        i = int(b.get("idx", -1))
        if not 0 <= i < len(q["rows"]):
            return {"ok": False, "error": "No such row."}
        r = q["rows"][i]
        if "message" in b:
            r["message"] = str(b["message"])[:5000]
        if b.get("status") in ("todo", "sent", "skipped"):
            r["status"] = b["status"]; r["at"] = datetime.datetime.now().isoformat(timespec="minutes") if b["status"] != "todo" else ""
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(q, fh, ensure_ascii=False, indent=1)
        os.replace(p + ".tmp", p)
    return {"ok": True, "row": r}


CH_STYLE = {"linkedin": "a LinkedIn connection note or message (connection notes must stay under 300 characters)",
            "instagram": "an Instagram direct message", "youtube": "a YouTube comment under their latest video",
            "twitter": "a reply or direct message on X (Twitter), under 280 characters",
            "forms": "a message for the contact form on their website"}


def ai_draft(b):
    ch = str(b.get("channel") or "")
    fields = b.get("fields") or {}
    prompt = ("Write %s from me to this person or company. Plain, specific and short, no flattery, no hashtags, no emojis, "
              "no exclamation marks. Refer to one concrete thing in their record. Reply with the message text only.\n\n"
              "What I want to say / my offer:\n%s\n\nTheir record:\n%s"
              % (CH_STYLE.get(ch, "a short outreach message"), str(b.get("instruction") or b.get("template") or "")[:2000],
                 json.dumps(fields, ensure_ascii=False)[:3000]))
    txt, err = ollama_chat(prompt, b.get("model") or "")
    return {"ok": bool(txt), "message": txt or "", "error": err}


# ------------------------------------------------------------------ Video maker and Agile Assets
def video_run(b):
    spec = b.get("spec") or {}
    if not spec.get("audio"):
        return None, "Pick at least one song."
    if not spec.get("image"):
        return None, "Pick a background picture."
    if not spec.get("lyrics") and not spec.get("timing"):
        return None, "Pick the lyrics file."
    sp = os.path.join(M.LOGS, "video_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=1)
    n = len(spec["audio"])
    return M.start_job(None, argv=[os.path.join(APP, "video_maker.py"), "--spec", sp],
                       name="Video maker: %s" % (spec.get("title") or spec.get("album") or ("%d songs" % n if n > 1 else
                                                                                            os.path.basename(spec["audio"][0]))))


def save_lyrics(b):
    name = re.sub(r"[^\w\- ]", "", str(b.get("name") or "")).strip() or "lyrics_" + datetime.datetime.now().strftime("%Y%m%d_%H%M")
    p = os.path.join(ws("VIDEO_STUDIO", "inputs"), name + ".txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(str(b.get("text") or ""))
    return {"ok": True, "path": rel(p)}


def agile_send(b):
    s = pset()
    to = s.get("agile_email", "").strip()
    if "@" not in to:
        return {"ok": False, "error": "Save your Agile Assets inbox address first (Agile Assets page)."}
    box = mailbox(s.get("agile_mailbox"))
    if not box:
        return {"ok": False, "error": "Pick which of your mailboxes sends to Agile Assets."}
    p = M.safe_path(str(b.get("file") or ""))
    if not p or not os.path.isfile(p):
        return {"ok": False, "error": "That file is not in the workspace."}
    mb = os.path.getsize(p) / 1e6
    if mb > 24:
        return {"ok": False, "error": "%.0f MB is too big for email (most servers stop at 25 MB). Make a 9:16 short "
                                      "or upload this one in Agile Assets by drag and drop." % mb}
    mm = mailer()
    msg = mm.build_msg(box, to, str(b.get("subject") or os.path.splitext(os.path.basename(p))[0])[:200],
                       str(b.get("caption") or ""))
    ext = os.path.splitext(p)[1].lower()
    mt = {".mp4": ("video", "mp4"), ".mov": ("video", "quicktime"), ".png": ("image", "png"),
          ".jpg": ("image", "jpeg"), ".jpeg": ("image", "jpeg")}.get(ext, ("application", "octet-stream"))
    with open(p, "rb") as fh:
        msg.add_attachment(fh.read(), maintype=mt[0], subtype=mt[1], filename=os.path.basename(p))
    srv, err = mm.smtp_open(box)
    if not srv:
        return {"ok": False, "error": err}
    try:
        srv.send_message(msg); srv.quit()
    except Exception as e:
        return {"ok": False, "error": "Sending failed: %s" % e}
    return {"ok": True, "sent_to": to, "mb": round(mb, 1)}


# ------------------------------------------------------------------ SEO
def seo_mod():
    spec = importlib.util.spec_from_file_location("ls_seo", os.path.join(APP, "seo_library.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


MODES = {
    "ideas": "List 15 article ideas for this topic. For each: a working title, the search intent, the main keyword and "
             "two secondary keywords, and one sentence on the angle. Group them by search intent.",
    "titles": "Write 20 SEO titles (under 60 characters) and 10 meta descriptions (under 155 characters) for this topic.",
    "outline": "Write a detailed article outline: H1, H2 and H3 headings, what each section covers, the keywords to work "
               "in, FAQ questions at the end, and internal-link ideas.",
    "keywords": "Build a keyword map: main keyword, 20 long-tail variations, questions people ask, and a cluster of "
                "supporting articles, as a table.",
    "article": "Write a complete, original article (about 1200 words) with headings, practical detail and a short FAQ. "
               "Do not copy sentences from the notes; use them only as background facts.",
    "rewrite": "Rewrite the notes into a fresh, original article on the topic, with a new structure and your own wording.",
}


def seo_ideas(b):
    topic = str(b.get("topic") or "").strip()
    if not topic:
        return {"ok": False, "error": "Write a topic or a keyword first."}
    mode = b.get("mode") if b.get("mode") in MODES else "ideas"
    lang = str(b.get("language") or "").strip()
    notes, used = "", []
    if b.get("use_library", True):
        try:
            hits = seo_mod().search(topic, int(b.get("n") or 6))
        except Exception as e:
            hits = []; notes = ""
        for h in hits:
            used.append({"title": h["title"], "path": h["path"]})
            notes += "\n\n### From: %s\n%s" % (h["title"], h["text"][:1800])
    prompt = ("Topic: %s\n%s\n\nTask: %s%s\n\n%s" %
              (topic, ("Extra direction: " + str(b["extra"])) if b.get("extra") else "", MODES[mode],
               (" Write it in %s." % lang) if lang else "",
               ("Background notes from my own article library (use them for facts and angles):" + notes) if notes else
               "There are no notes from my library for this topic - use general knowledge."))
    txt, err = ollama_chat(prompt, b.get("model") or "", system="You are an experienced SEO content strategist. Answer in Markdown.", timeout=300)
    if not txt:
        return {"ok": False, "error": err}
    p = os.path.join(ws("SEO_IDEAS"), "%s %s - %s.md" % (datetime.datetime.now().strftime("%Y-%m-%d %H%M"), mode,
                                                         re.sub(r"[^\w\- ]", "", topic)[:60]))
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# %s (%s)\n\n%s\n\n---\nSources from the library: %s\n" % (topic, mode, txt, ", ".join(u["title"] for u in used) or "none"))
    return {"ok": True, "text": txt, "file": rel(p), "sources": used}


# ------------------------------------------------------------------ Ads: a small MCP client
PROTO = "2025-06-18"
PRESETS = {
    "hyros": {"name": "HYROS", "kind": "stdio", "command": "npx", "args": ["-y", "hyros-mcp"], "env": {"HYROS_API_KEY": ""},
              "preset": "hyros",
              "help": "Runs the open-source hyros-mcp server on this computer (needs Node.js). Paste your key from "
                      "HYROS > Settings > Integrations > API."},
    "http": {"name": "", "kind": "http", "url": "", "headers": {"Authorization": ""}, "preset": "http",
             "help": "Any remote MCP server address - Zapier MCP, Pipedream, Composio, or a vendor's own. Put the token "
                     "in the Authorization header as 'Bearer ...' if it needs one."},
    "stdio": {"name": "", "kind": "stdio", "command": "", "args": [], "env": {}, "preset": "stdio",
              "help": "Any MCP server you start with a command, e.g. npx -y <package> or uvx <package>."},
}


def server(name):
    return next((x for x in pset()["ads_servers"] if x.get("name") == name), None)


def ads_save(b):
    x = b.get("server") or {}
    name = re.sub(r"[^\w\- .]", "", str(x.get("name") or "")).strip()[:60]
    if not name:
        return {"ok": False, "error": "Give the connector a name."}
    kind = "http" if x.get("kind") == "http" else "stdio"
    s = pset()
    old = next((y for y in s["ads_servers"] if y.get("name") == name), {})
    rec = {"name": name, "kind": kind, "preset": str(x.get("preset") or old.get("preset") or "")}
    if kind == "http":
        if not re.match(r"^https?://", str(x.get("url") or "")):
            return {"ok": False, "error": "The address must start with https://"}
        rec["url"] = str(x["url"]).strip()
        rec["headers"] = {k: (v if str(v).strip() and not str(v).startswith("...") and v != "set" else (old.get("headers") or {}).get(k, ""))
                          for k, v in (x.get("headers") or {}).items() if str(k).strip()}
    else:
        if not str(x.get("command") or "").strip():
            return {"ok": False, "error": "Which command starts the server? For example npx"}
        rec["command"] = str(x["command"]).strip()
        args = x.get("args") or []
        rec["args"] = args if isinstance(args, list) else [a for a in str(args).split() if a]
        rec["env"] = {k: (v if str(v).strip() and not str(v).startswith("...") and v != "set" else (old.get("env") or {}).get(k, ""))
                      for k, v in (x.get("env") or {}).items() if str(k).strip()}
    s["ads_servers"] = [y for y in s["ads_servers"] if y.get("name") != name] + [rec]
    psave(s)
    return {"ok": True, "plus": public()}


def ads_delete(name):
    s = pset(); s["ads_servers"] = [y for y in s["ads_servers"] if y.get("name") != name]; psave(s)
    return {"ok": True, "plus": public()}


def _sse_or_json(raw, ctype, want_id):
    if "event-stream" in (ctype or ""):
        for block in raw.split("\n\n"):
            data = "".join(l[5:].strip() for l in block.splitlines() if l.startswith("data:"))
            if data:
                try:
                    m = json.loads(data)
                except ValueError:
                    continue
                if m.get("id") == want_id:
                    return m
        return None
    return json.loads(raw) if raw.strip() else None


def _http(srv, msgs, timeout=90):
    sid, res = None, None
    for m in msgs:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": PROTO}
        h.update({k: v for k, v in (srv.get("headers") or {}).items() if v})
        if sid:
            h["Mcp-Session-Id"] = sid
        req = urllib.request.Request(srv["url"], data=json.dumps(m).encode(), method="POST", headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                sid = r.headers.get("Mcp-Session-Id") or sid
                raw = r.read().decode("utf-8", "replace")
                if "id" in m:
                    res = _sse_or_json(raw, r.headers.get("Content-Type"), m["id"])
                    if res and res.get("error"):
                        return res
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            if e.code == 401:
                return {"error": {"message": "The server wants you to sign in (HTTP 401). Put the token in the Authorization header. "
                                             "Servers that only allow a browser sign-in (OAuth) cannot be used from here." + (" " + body if body else "")}}
            return {"error": {"message": "HTTP %d: %s" % (e.code, body)}}
        except Exception as e:
            return {"error": {"message": str(e)}}
    return res


def _stdio(srv, msgs, timeout=150):
    cmd = shutil.which(srv["command"]) or srv["command"]
    env = dict(os.environ); env.update({k: v for k, v in (srv.get("env") or {}).items() if v})
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.Popen([cmd] + list(srv.get("args") or []), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, creationflags=flags, cwd=APP)
    except OSError as e:
        return {"error": {"message": "Could not start %s: %s. Is it installed (Node.js for npx)?" % (srv["command"], e)}}
    lines, err = [], []
    threading.Thread(target=lambda: [lines.append(l) for l in iter(p.stdout.readline, b"")], daemon=True).start()
    threading.Thread(target=lambda: [err.append(l) for l in iter(p.stderr.readline, b"")], daemon=True).start()
    res = None
    try:
        for m in msgs:
            p.stdin.write((json.dumps(m) + "\n").encode()); p.stdin.flush()
            if "id" not in m:
                continue
            end = time.time() + timeout
            while time.time() < end:
                hit = None
                for l in list(lines):
                    try:
                        j = json.loads(l)
                    except ValueError:
                        continue
                    if j.get("id") == m["id"]:
                        hit = j; break
                if hit:
                    res = hit; break
                if p.poll() is not None:
                    break
                time.sleep(0.1)
            if res is None or res.get("id") != m["id"]:
                tail = b"".join(err[-15:]).decode("utf-8", "replace")[-800:]
                return {"error": {"message": "The server did not answer%s. %s" % (" (it stopped)" if p.poll() is not None else " in time", tail)}}
            if res.get("error"):
                return res
    finally:
        try:
            p.kill()
        except Exception:
            pass
    return res


def mcp(srv, method, params):
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": PROTO, "capabilities": {}, "clientInfo": {"name": "lead-studio", "version": "2"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}]
    r = _http(srv, msgs) if srv.get("kind") == "http" else _stdio(srv, msgs)
    if not r:
        return {"ok": False, "error": "No answer from the server."}
    if r.get("error"):
        return {"ok": False, "error": r["error"].get("message") or str(r["error"])}
    return {"ok": True, "result": r.get("result")}


def ads_tools(name):
    srv = server(name)
    if not srv:
        return {"ok": False, "error": "No connector called %s." % name}
    r = mcp(srv, "tools/list", {})
    if r["ok"]:
        r["tools"] = [{"name": t.get("name"), "description": (t.get("description") or "")[:400],
                       "schema": t.get("inputSchema") or {}} for t in (r.pop("result") or {}).get("tools", [])]
    return r


def ads_call(b):
    srv = server(b.get("name"))
    if not srv:
        return {"ok": False, "error": "No such connector."}
    args = b.get("args") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            return {"ok": False, "error": "The arguments are not valid JSON."}
    r = mcp(srv, "tools/call", {"name": str(b.get("tool") or ""), "arguments": args})
    if r["ok"]:
        res = r.pop("result") or {}
        r["text"] = "\n\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        r["is_error"] = bool(res.get("isError"))
        if res.get("structuredContent") is not None:
            r["structured"] = res["structuredContent"]
    return r


# ------------------------------------------------------------------ routing
def handle_get(path, q):
    if path == "/api/plus/state":
        return {"ok": True, "plus": public(), "channels": CHANNELS, "presets": PRESETS, "modes": list(MODES)}
    return None


def handle_post(path, b):
    if path == "/api/plus/settings":
        s = pset()
        for k in ("agile_email", "agile_mailbox", "seo_dirs", "seo_model"):
            if k in b:
                s[k] = str(b[k]).strip()[:2000]
        psave(s); return {"ok": True, "plus": public()}
    if path == "/api/plus/media":
        return {"ok": True, "audio": list_media("VIDEO_STUDIO/inputs", AUDIO), "images": list_media("VIDEO_STUDIO/inputs", IMAGES),
                "lyrics": list_media("VIDEO_STUDIO/inputs", LYRICS),
                "outputs": list_media("VIDEO_STUDIO/output", (".mp4",)),
                "timings": list_media("VIDEO_STUDIO/output", (".timing.csv",)), "inputs": rel(ws("VIDEO_STUDIO", "inputs"))}
    if path == "/api/plus/video/run":
        jid, err = video_run(b); return {"job": jid, "error": err}
    if path == "/api/plus/video/lyrics":
        return save_lyrics(b)
    if path == "/api/plus/agile/send":
        return agile_send(b)
    if path == "/api/plus/openurl":
        return open_url(b.get("url"))
    if path == "/api/plus/unibox":
        return unibox(max(1, min(60, int(b.get("days") or 7))))
    if path == "/api/plus/queue/cols":
        return queue_cols(b)
    if path == "/api/plus/queue/build":
        return queue_build(b)
    if path == "/api/plus/queue/list":
        return queue_list(str(b.get("channel") or ""))
    if path == "/api/plus/queue/get":
        return queue_get(str(b.get("channel") or ""), str(b.get("name") or ""))
    if path == "/api/plus/queue/update":
        return queue_update(b)
    if path == "/api/plus/ai/draft":
        return ai_draft(b)
    if path == "/api/plus/seo/index":
        dirs = str(b.get("dirs") or "").strip()
        if not dirs:
            return {"error": "Add at least one folder."}
        s = pset(); s["seo_dirs"] = dirs; psave(s)
        jid, err = M.start_job(None, argv=[os.path.join(APP, "seo_library.py"), "index", "--dirs", dirs], name="SEO library: index")
        return {"job": jid, "error": err}
    if path == "/api/plus/seo/status":
        try:
            return {"ok": True, **seo_mod().status()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if path == "/api/plus/seo/search":
        try:
            return {"ok": True, "results": seo_mod().search(str(b.get("q") or ""), int(b.get("limit") or 20))}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if path == "/api/plus/seo/ideas":
        return seo_ideas(b)
    if path == "/api/plus/ollama/test":
        txt, err = ollama_chat("Reply with the single word: ready", b.get("model") or "", timeout=120)
        return {"ok": bool(txt), "text": txt, "error": err}
    if path == "/api/plus/ads/save":
        return ads_save(b)
    if path == "/api/plus/ads/delete":
        return ads_delete(str(b.get("name") or ""))
    if path == "/api/plus/ads/tools":
        return ads_tools(str(b.get("name") or ""))
    if path == "/api/plus/ads/call":
        return ads_call(b)
    return None


def handle_upload(h, q):
    """Raw file upload from the page into VIDEO_STUDIO/inputs (songs, pictures, lyrics)."""
    name = os.path.basename(str(q.get("name") or ""))
    if not name or not name.lower().endswith(AUDIO + IMAGES + LYRICS):
        return {"ok": False, "error": "Songs (mp3, wav, m4a...), pictures (jpg, png, webp...) or lyrics (.txt) only."}
    n = int(h.headers.get("Content-Length") or 0)
    if n <= 0 or n > 600 * 1024 * 1024:
        return {"ok": False, "error": "Empty or larger than 600 MB."}
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    p = os.path.join(ws("VIDEO_STUDIO", "inputs"), name)
    with open(p + ".part", "wb") as fh:
        left = n
        while left > 0:
            chunk = h.rfile.read(min(1 << 20, left))
            if not chunk:
                break
            fh.write(chunk); left -= len(chunk)
    os.replace(p + ".part", p)
    return {"ok": True, "path": rel(p)}
