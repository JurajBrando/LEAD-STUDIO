"""
Lead Studio 2 - lead enrichment workspace for the Modulario folder.

Start: double-click START_LEAD_STUDIO.bat (or LeadStudio.exe after BUILD_EXE.bat).
It opens http://127.0.0.1:8765 in your browser and runs only on this computer.

  lead_studio.py            the local server and job runner (this file, standard library only)
  ui/index.html             the interface
  lead_studio_tools.json    the tool list - add your own entries freely
  lead_index.py             builds and searches one database of every contact in the workspace
  connect.py                WhatsApp (OpenWA), social posting (Postiz), thread reading (Agent Reach)
  ai_enrich.py              row-by-row enrichment through Ollama Cloud
  lead_studio_mcp.py        MCP server (stdio) so Claude Desktop and other MCP clients can drive Lead Studio
  lead_studio_settings.json created on first save: keys, model, MCP token (stays on this PC)
"""
import os, sys, re, csv, json, glob, gzip, time, shlex, shutil, secrets, threading, subprocess, webbrowser
import importlib.util, datetime, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

csv.field_size_limit(10 ** 9)
VERSION = "2.0"
APP = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(APP))        # the Modulario folder
LOGS = os.path.join(APP, "logs"); os.makedirs(LOGS, exist_ok=True)
SETTINGS = os.path.join(APP, "lead_studio_settings.json")
PORT = int(os.environ.get("LS_PORT") or 8765)
TOKEN = secrets.token_hex(16)
SKIP_DIRS = {"__pycache__", "LEAD_STUDIO", "LEAD_INDEX", "_to_delete", "_TO_DELETE", "domain_output", "node_modules", ".git",
             "HF_DATASETS", "OSM_CACHE", "logs"}
PIP = {"dns": "dnspython", "flask_cors": "flask-cors", "ahocorasick": "pyahocorasick"}
OLLAMA_BASE = "https://ollama.com"
QUIET = False            # True when running as an MCP stdio server: nothing may be printed to stdout


def say(*a):
    print(*a, file=sys.stderr if QUIET else sys.stdout, flush=True)


# ------------------------------------------------------------------ settings
CHANNEL_KEYS = {"wa_url": "http://localhost:2785", "wa_key": "", "wa_session": "default",
                "postiz_url": "https://api.postiz.com", "postiz_key": "", "reach_cmd": "agent-reach",
                "mr_base": "https://app.manyreach.com", "mr_key": "", "cc_dir": "", "cc_index_dir": ""}
# a mailbox: name, from_name, from_email, reply_to, smtp_host/port/user/pass/security,
# imap_host/port/user/pass, daily_limit, unsubscribe_mailto
MAILBOX_STR = ("name", "from_name", "from_email", "reply_to", "smtp_host", "smtp_user", "smtp_pass",
               "smtp_security", "imap_host", "imap_user", "imap_pass", "unsubscribe_mailto", "from_manyreach")
MAILBOX_INT = ("smtp_port", "imap_port", "daily_limit")


def load_settings():
    s = {"ollama_key": "", "ollama_base": OLLAMA_BASE, "ollama_model": "", "mcp_token": "", "mailboxes": []}
    s.update(CHANNEL_KEYS)
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            s.update({k: v for k, v in json.load(fh).items() if k in s})
    except (OSError, ValueError):
        pass
    if not s["mcp_token"]:
        s["mcp_token"] = secrets.token_hex(20); save_settings(s)
    return s


def save_settings(s):
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1)
    os.replace(tmp, SETTINGS)


def ollama_key(s=None):
    return (os.environ.get("OLLAMA_API_KEY") or (s or load_settings())["ollama_key"] or "").strip()


def public_settings():
    s = load_settings(); k = ollama_key(s)
    return {"ollama_has_key": bool(k), "ollama_key_hint": ("..." + k[-4:]) if len(k) > 8 else "",
            "ollama_key_from_env": bool(os.environ.get("OLLAMA_API_KEY")),
            "ollama_base": s["ollama_base"], "ollama_model": s["ollama_model"], "mcp_token": s["mcp_token"],
            "wa_url": s["wa_url"], "wa_session": s["wa_session"], "wa_has_key": bool(s["wa_key"]),
            "postiz_url": s["postiz_url"], "postiz_has_key": bool(s["postiz_key"]), "reach_cmd": s["reach_cmd"],
            "mr_base": s["mr_base"], "mr_has_key": bool(str(s["mr_key"]).strip()),
            "cc_dir": s["cc_dir"], "cc_index_dir": s["cc_index_dir"],
            "mailbox_count": len(s.get("mailboxes") or []),
            "mailboxes_without_password": sum(1 for b in (s.get("mailboxes") or [])
                                              if isinstance(b, dict) and not b.get("smtp_pass"))}


def public_boxes(s=None):
    """The mailboxes without their passwords - the UI never needs to see those."""
    s = s or load_settings()
    items = []
    for b in (s.get("mailboxes") or []):
        if not isinstance(b, dict):
            continue
        items.append({k: b.get(k, "") for k in ("name", "from_name", "from_email", "reply_to", "smtp_host",
                                                "smtp_user", "smtp_security", "imap_host", "imap_user",
                                                "unsubscribe_mailto", "from_manyreach")}
                     | {"smtp_port": int(b.get("smtp_port") or 587), "imap_port": int(b.get("imap_port") or 993),
                        "daily_limit": int(b.get("daily_limit") or 40),
                        "has_smtp_pass": bool(b.get("smtp_pass")), "has_imap_pass": bool(b.get("imap_pass")),
                        "manyreach_warmup": bool(b.get("manyreach_warmup")),
                        "manyreach_disconnected": bool(b.get("manyreach_disconnected"))})
    return items


def ollama_models():
    """Ask Ollama Cloud which models this key can use. Returns (names, error)."""
    s = load_settings(); k = ollama_key(s)
    if not k:
        return [], "No Ollama API key saved yet."
    req = urllib.request.Request(s["ollama_base"].rstrip("/") + "/api/tags", headers={"Authorization": "Bearer " + k})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        return sorted(m.get("name") or m.get("model") or "" for m in data.get("models", [])), ""
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return [], "Ollama rejected the key (HTTP %d). Create a new one at ollama.com/settings/keys." % e.code
        return [], "Ollama answered HTTP %d." % e.code
    except Exception as e:
        return [], "Could not reach %s: %s" % (s["ollama_base"], e)


# ------------------------------------------------------------------ tools
def find_python():
    if not getattr(sys, "frozen", False):
        return [sys.executable]
    for c in ("python", "py", "python3"):
        p = shutil.which(c)
        if p:
            return [p]
    return None


PY = find_python()


def has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def load_tools(discover=True):
    with open(os.path.join(APP, "lead_studio_tools.json"), encoding="utf-8") as fh:
        tools = json.load(fh)["tools"]
    known = {os.path.normpath(os.path.join(ROOT, t["dir"], t["script"])) for t in tools}
    for base, dirs, files in (os.walk(ROOT) if discover else []):          # every other script, three folders deep
        depth = os.path.relpath(base, ROOT).count(os.sep) + (0 if base == ROOT else 1)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and depth < 3]
        for f in sorted(files):
            p = os.path.normpath(os.path.join(base, f))
            if not f.endswith(".py") or p in known or re.search(r"_before_|backup|^_", f):
                continue
            try:
                head = open(p, encoding="utf-8", errors="replace").read(3000)
            except OSError:
                continue
            m = re.search(r'("""|\'\'\')(.*?)\1', head, re.S)
            desc = " ".join(m.group(2).split())[:220] if m else "(no description in the file)"
            rel = os.path.relpath(base, ROOT).replace(os.sep, "/")
            tools.append({"id": "auto_" + re.sub(r"\W+", "_", os.path.relpath(p, ROOT)), "group": "9. Other scripts found",
                          "name": f, "desc": desc, "dir": rel, "script": f, "args": "", "output": rel})
    for t in tools:
        t["exists"] = os.path.exists(os.path.join(ROOT, t["dir"], t["script"]))
        t["missing"] = [m for m in t.get("needs", []) if not has_module(m)] if not getattr(sys, "frozen", False) else []
    return tools


TOOLS = None
TOOLS_LOCK = threading.Lock()


def tools(refresh=False):
    """The registered tools at once; scripts found by walking the folder are added in the background."""
    global TOOLS
    if refresh:
        try:
            found = load_tools()
            with TOOLS_LOCK:
                TOOLS = found
        except Exception as e:
            say("tool list could not be read:", e)
    with TOOLS_LOCK:
        if TOOLS is None:
            try:
                TOOLS = load_tools(discover=False)
            except Exception as e:
                say("tool list could not be read:", e); TOOLS = []
        return TOOLS


def discover_async():
    threading.Thread(target=lambda: tools(refresh=True), daemon=True).start()


# ------------------------------------------------------------------ jobs
JOBS = {}        # job id -> dict(tool, name, proc, log, started, ended, code)
LOCK = threading.Lock()
JOB_ID = re.compile(r"^[0-9]{8}_[0-9]{6}_[A-Za-z0-9_\-]{1,80}$")


def start_job(tool, args="", pip=None, argv=None, name=None, cwd=None):
    if PY is None:
        return None, "Python was not found on this computer. Install it from python.org first."
    tid = tool["id"] if tool else ("pip" if pip else "job")
    jid = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + re.sub(r"[^A-Za-z0-9_\-]", "_", tid)[:60]
    while jid in JOBS:
        jid += "_"
    log = os.path.join(LOGS, jid + ".log")
    if pip:
        cmd, cwd, name = PY + ["-m", "pip", "install"] + pip, ROOT, "Install: " + " ".join(pip)
    elif argv:
        cmd, cwd = PY + ["-u"] + argv, cwd or APP
    else:
        cwd = os.path.join(ROOT, tool["dir"]); name = tool["name"]
        try:
            extra = shlex.split(args or "", posix=(os.name != "nt"))
        except ValueError as e:
            return None, "Could not read the arguments: " + str(e)
        cmd = PY + ["-u", tool["script"]] + [a.strip('"') for a in extra]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", LS_ROOT=ROOT)
    fh = open(log, "w", encoding="utf-8", errors="replace")
    fh.write("> " + " ".join(cmd) + "\n  in " + cwd + "\n\n"); fh.flush()
    try:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, creationflags=flags)
    except Exception as e:
        fh.write("COULD NOT START: " + str(e) + "\n"); fh.close(); return None, str(e)
    outdir = (tool or {}).get("output") or (tool or {}).get("dir") or ""
    with LOCK:
        JOBS[jid] = {"id": jid, "tool": tid, "name": name or tid, "proc": proc, "log": log, "started": time.time(),
                     "ended": None, "code": None, "fh": fh, "outdir": outdir, "before": snapshot(outdir)}
    return jid, ""


def job_view(j):
    code = j["proc"].poll()
    if code is not None and j["ended"] is None:
        j["ended"] = time.time(); j["code"] = code
        try:
            j["fh"].close()
        except Exception:
            pass
    return {"id": j["id"], "tool": j["tool"], "name": j["name"], "running": code is None, "code": code, "outdir": j.get("outdir", ""),
            "minutes": round(((j["ended"] or time.time()) - j["started"]) / 60, 1),
            "started": time.strftime("%H:%M", time.localtime(j["started"]))}


def list_jobs():
    with LOCK:
        return [job_view(j) for j in sorted(JOBS.values(), key=lambda x: -x["started"])][:60]


def stop_job(jid):
    j = JOBS.get(jid)
    if not j or j["proc"].poll() is not None:
        return False
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(j["proc"].pid), "/T", "/F"], capture_output=True)
        else:
            j["proc"].terminate()
    except Exception:
        return False
    return True


def tail(path, n=400):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2); size = fh.tell(); fh.seek(max(0, size - 80000))
            data = fh.read().decode("utf-8", errors="replace")
        return "\n".join(data.replace("\r", "\n").split("\n")[-n:])
    except OSError:
        return ""


def job_log(jid, n=400):
    if not JOB_ID.match(jid or ""):
        return ""
    j = JOBS.get(jid)
    return tail(j["log"] if j else os.path.join(LOGS, jid + ".log"), n)


# ------------------------------------------------------------------ files and tables
def safe_path(rel):
    p = os.path.normpath(os.path.join(ROOT, rel or "."))
    try:
        return p if os.path.commonpath([p, ROOT]) == ROOT else None
    except ValueError:
        return None


def is_table(name):
    n = name.lower()
    return n.endswith((".csv", ".csv.gz", ".tsv"))


def open_text(p):
    if p.lower().endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8-sig", errors="replace", newline="")
    return open(p, encoding="utf-8-sig", errors="replace", newline="")


def read_table(rel, limit=200, offset=0, q="", max_scan=3_000_000):
    """One page of a CSV / CSV.GZ / TSV. Counts all matching rows unless the file is very large."""
    p = safe_path(rel)
    out = {"cols": [], "rows": [], "total": 0, "offset": offset, "more": False, "counted": True}
    if not p or not os.path.isfile(p) or not is_table(p):
        return out
    limit = max(1, min(int(limit), 1000)); offset = max(0, int(offset)); q = (q or "").lower()
    big = os.path.getsize(p) > 150 * 1024 * 1024
    with open_text(p) as fh:
        rd = csv.reader(fh, delimiter="\t" if p.lower().endswith(".tsv") else ",")
        out["cols"] = next(rd, [])
        seen = 0
        for i, r in enumerate(rd):
            if q and q not in " ".join(r).lower():
                if i >= max_scan:
                    out["counted"] = False; break
                continue
            if seen >= offset and len(out["rows"]) < limit:
                out["rows"].append(r)
            seen += 1
            if len(out["rows"]) >= limit and (big or i >= max_scan):
                out["counted"] = False; out["more"] = True; break
        out["total"] = seen
        if out["counted"]:
            out["more"] = seen > offset + len(out["rows"])
    return out


def list_files(rel):
    p = safe_path(rel)
    if not p or not os.path.isdir(p):
        return []
    items = []
    try:
        names = os.listdir(p)
    except OSError:
        return []
    for f in names:
        fp = os.path.join(p, f)
        try:
            st = os.stat(fp)
        except OSError:
            continue
        d = os.path.isdir(fp)
        items.append({"name": f, "dir": d, "table": (not d) and is_table(f), "kb": st.st_size // 1024, "t": st.st_mtime,
                      "when": time.strftime("%d.%m.%Y %H:%M", time.localtime(st.st_mtime))})
    return sorted(items, key=lambda x: (not x["dir"], -x["t"]))[:400]


def hot_lists():
    d = os.path.join(ROOT, "_CLEAN_2026-11-02", "SIGNALS", "HOT_TODAY")
    return [os.path.basename(f) for f in sorted(glob.glob(os.path.join(d, "hot_????-??-??.csv")), reverse=True)[:30]]


# ------------------------------------------------------------------ AI enrichment
def start_enrich(spec):
    """Start ai_enrich.py as a job. spec: input, columns, instruction, new_column, limit, model."""
    if not ollama_key():
        return None, "Add your Ollama API key under Connections first."
    src = safe_path(str(spec.get("input", "")))
    if not src or not os.path.isfile(src) or not is_table(src):
        return None, "Choose a CSV file inside the workspace."
    cols = [str(c) for c in (spec.get("columns") or [])][:40]
    instr = str(spec.get("instruction", "")).strip()
    if not instr:
        return None, "Write what the AI should work out for each row."
    new = re.sub(r"[^A-Za-z0-9_]+", "_", str(spec.get("new_column") or "ai_note")).strip("_")[:40] or "ai_note"
    try:
        limit = max(1, min(int(spec.get("limit") or 50), 200000))
    except (TypeError, ValueError):
        limit = 50
    model = str(spec.get("model") or load_settings()["ollama_model"] or "").strip()
    if not model:
        return None, "Choose a model under Connections first."
    stem = re.sub(r"(\.csv)?(\.gz)?$|\.tsv$", "", os.path.basename(src), flags=re.I)
    out = os.path.join(os.path.dirname(src), "%s__ai_%s.csv" % (stem, new))
    job = {"input": src, "output": out, "columns": cols, "instruction": instr[:4000], "new_column": new,
           "limit": limit, "model": model}
    sp = os.path.join(LOGS, "enrich_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(job, fh, indent=1)
    jid, err = start_job({"id": "ai_enrich"}, argv=[os.path.join(APP, "ai_enrich.py"), "--spec", sp],
                         name="AI enrich: %s -> %s" % (os.path.basename(src), new))
    return jid, err


# ------------------------------------------------------------------ the lead database
INDEX_DIR = os.path.join(ROOT, "LEAD_INDEX")
LEAD_INDEX = os.path.join(APP, "lead_index.py")
FILTERS = ("country", "titles", "keywords", "industries", "location", "technologies", "sizes", "sources", "must")


def run_index(args, timeout=240, spec=None):
    """Run lead_index.py and give back whatever JSON it printed."""
    if PY is None:
        return {"error": "Python was not found on this computer."}
    sp = None
    if spec is not None:
        sp = os.path.join(LOGS, "q_%s.json" % secrets.token_hex(5))
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        args = args + ["--spec", sp]
    try:
        p = subprocess.run(PY + ["-u", LEAD_INDEX] + args, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, LS_ROOT=ROOT, PYTHONIOENCODING="utf-8"), cwd=APP)
    except subprocess.TimeoutExpired:
        return {"error": "The search took longer than %d seconds." % timeout}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if sp:
            try:
                os.remove(sp)
            except OSError:
                pass
    txt = (p.stdout or "").strip()
    start = txt.find("{")
    try:
        return json.loads(txt[start:]) if start >= 0 else {"error": (p.stderr or txt or "no answer")[-700:]}
    except ValueError:
        return {"error": (p.stderr or txt)[-700:]}


def clean_spec(b):
    s = {k: b.get(k) for k in FILTERS if b.get(k)}
    for k in ("limit", "offset"):
        if b.get(k) is not None:
            s[k] = b[k]
    return s


def index_json(name):
    try:
        with open(os.path.join(INDEX_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def leads_home():
    return {"info": index_json("index_info.json"), "facets": index_json("facets.json"),
            "exports": [f["name"] for f in list_files("LEAD_INDEX/exports") if not f["dir"]][:40]}


def start_build():
    return start_job({"id": "lead_index_build"}, argv=[LEAD_INDEX, "build"],
                     name="Build the lead database", cwd=APP)


def start_ingest(files, refresh=True):
    """Add uploaded contact files to leads.duckdb (new contacts in, blanks filled in on known ones)."""
    paths, miss = [], []
    up = os.path.join(INDEX_DIR, "uploads")
    os.makedirs(up, exist_ok=True)
    for f in files[:50]:
        f = str(f).strip().strip('"')
        for cand in (f, os.path.join(ROOT, f), os.path.join(up, f), os.path.join(INDEX_DIR, f)):
            if os.path.isfile(cand):
                paths.append(os.path.abspath(cand)); break
        else:
            miss.append(f)
    if miss:
        return None, "Could not find: " + ", ".join(miss[:5]) + ". Put the file in LEAD_INDEX/uploads or give the full path."
    if not paths:
        return None, "No file to import."
    spec = {"files": paths, "export": bool(refresh)}
    sp = os.path.join(LOGS, "ingest_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1)
    return start_job({"id": "lead_ingest"}, argv=[LEAD_INDEX, "ingest", "--spec", sp],
                     name="Import contacts -> lead database (%d file%s)" % (len(paths), "" if len(paths) == 1 else "s"),
                     cwd=APP)


def save_upload(h, q):
    """Raw contact file posted from the Contacts page -> LEAD_INDEX/uploads."""
    name = os.path.basename(str(q.get("name") or ""))
    if not name.lower().endswith((".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json", ".jsonl", ".ndjson", ".parquet")):
        return {"ok": False, "error": "CSV, TSV, XLSX, JSON or Parquet files only."}
    n = int(h.headers.get("Content-Length") or 0)
    if n <= 0 or n > 2000 * 1024 * 1024:
        return {"ok": False, "error": "Empty, or larger than 2 GB."}
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    up = os.path.join(INDEX_DIR, "uploads")
    os.makedirs(up, exist_ok=True)
    path = os.path.join(up, name)
    with open(path + ".part", "wb") as fh:
        left = n
        while left > 0:
            chunk = h.rfile.read(min(1 << 20, left))
            if not chunk:
                break
            fh.write(chunk); left -= len(chunk)
    os.replace(path + ".part", path)
    return {"ok": True, "path": path, "name": name, "mb": round(os.path.getsize(path) / 1e6, 1)}


def start_export(b):
    spec = clean_spec(b)
    kind = "json" if str(b.get("format") or "json").lower() == "json" else "csv"
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(b.get("name") or "leads")).strip("_")[:50] or "leads"
    out = os.path.join(INDEX_DIR, "exports", "%s_%s.%s" % (name, datetime.datetime.now().strftime("%Y%m%d_%H%M"), kind))
    try:
        spec["limit"] = max(1, min(int(b.get("limit") or 50000), 2000000))
    except (TypeError, ValueError):
        spec["limit"] = 50000
    spec["out"] = out
    sp = os.path.join(LOGS, "export_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1)
    jid, err = start_job({"id": "lead_export"}, argv=[LEAD_INDEX, "export", "--spec", sp],
                         name="Export leads -> " + os.path.basename(out), cwd=APP)
    return jid, err, out


# ------------------------------------------------------------------ what a job actually produced
def snapshot(rel):
    """name -> size for the result files of a tool, so we can tell afterwards what is new."""
    base = safe_path(rel or "")
    seen = {}
    if not base or not os.path.isdir(base):
        return seen
    for d, dirs, files in os.walk(base):
        if os.path.relpath(d, base).count(os.sep) > 1:
            dirs[:] = []
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if is_table(f) or f.lower().endswith((".json", ".xlsx")):
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                    seen[os.path.relpath(p, ROOT).replace(os.sep, "/")] = (st.st_size, int(st.st_mtime))
                except OSError:
                    pass
    return seen


def job_leads(jid, force=False):
    """The point of a job: which lead files it wrote, how many rows, and how many of the emails are new."""
    j = JOBS.get(jid)
    cache = os.path.join(LOGS, jid + ".leads.json") if JOB_ID.match(jid or "") else None
    if not force and cache and os.path.exists(cache) and (not j or j["proc"].poll() is not None):
        try:
            return json.load(open(cache, encoding="utf-8"))
        except ValueError:
            pass
    if not j:
        return {"files": [], "note": "This job is from an earlier session."}
    before = j.get("before") or {}
    now = snapshot(j.get("outdir") or "")
    since = j["started"] - 2                       # a fast job can finish inside the same second
    changed = [k for k, v in now.items() if before.get(k) != v or v[1] >= since]
    changed.sort(key=lambda k: -now[k][0])
    res = run_index(["newcheck"], timeout=180, spec={"files": [os.path.join(ROOT, k) for k in changed[:12]]})
    files = []
    for f in res.get("files", []):
        files.append({"path": os.path.relpath(f["path"], ROOT).replace(os.sep, "/"), "rows": f.get("rows", 0),
                      "emails": f.get("emails", 0), "new": f.get("new"), "fresh": os.path.relpath(f["path"], ROOT).replace(os.sep, "/") not in before})
    view = {"files": files, "rows": sum(f["rows"] for f in files), "emails": sum(f["emails"] for f in files),
            "new": sum(f["new"] or 0 for f in files), "has_index": res.get("has_index", False),
            "running": j["proc"].poll() is None, "name": j["name"], "error": res.get("error", "")}
    if cache and not view["running"]:
        try:
            json.dump(view, open(cache, "w", encoding="utf-8"), indent=1)
        except OSError:
            pass
    return view


def job_preview(jid, limit=25):
    """The first rows of the biggest file a job produced - the new leads themselves, not a log."""
    v = job_leads(jid)
    best = max(v["files"], key=lambda f: f["rows"], default=None)
    if not best or not best["path"].lower().endswith((".csv", ".csv.gz", ".tsv")):
        return {"file": "", "cols": [], "rows": []}
    t = read_table(best["path"], limit=limit)
    return {"file": best["path"], "cols": t["cols"], "rows": t["rows"], "total": best["rows"]}


# ------------------------------------------------------------------ channels (WhatsApp, social, threads)
CONNECT = os.path.join(APP, "connect.py")
MAILER = os.path.join(APP, "mailer.py")
CCENRICH = os.path.join(APP, "cc_enrich.py")
WEBSCRAPE = os.path.join(APP, "web_scrape.py")
def mr_push_args(b):
    """campaign id, list id, or both - Manyreach takes either on the same bulk call."""
    a = []
    for k, flag in (("campaign", "--campaign"), ("list", "--list")):
        v = re.sub(r"[^0-9A-Za-z\-]", "", str(b.get(k) or ""))[:64]
        if v:
            a += [flag, v]
    f = safe_path(str(b.get("file") or b.get("in") or b.get("path") or ""))
    if f and os.path.exists(f):
        a += ["--in", os.path.relpath(f, ROOT).replace(os.sep, "/")]
    try:
        if int(b.get("limit") or 0) > 0:
            a += ["--limit", str(int(b["limit"]))]
    except ValueError:
        pass
    if b.get("all_again"):
        a.append("--all-again")
    return a


CHANNEL_CMDS = {"whatsapp-check": [], "postiz-channels": [], "reach-channels": [],
                "whatsapp-send": ["to", "text"], "reach-read": ["url"],
                # Manyreach and your own mailboxes, both in mailer.py
                "mr-check": [], "mr-campaigns": [], "mr-senders": [], "mr-lists": [],
                "box-list": [], "box-check": ["account"], "box-replies": ["account"],
                "box-send": ["account", "to", "subject", "text"],
                "mr-raw": ["path"]}
MAILER_CMDS = {c for c in CHANNEL_CMDS if c.startswith(("mr-", "box-"))}


def channel(cmd, args=None, dry=False, timeout=130):
    if cmd not in CHANNEL_CMDS and cmd not in ("postiz-post", "mr-push"):
        return {"ok": False, "error": "Unknown channel command."}
    if PY is None:
        return {"ok": False, "error": "Python was not found on this computer."}
    argv = [MAILER if (cmd in MAILER_CMDS or cmd == "mr-push") else CONNECT, cmd]
    args = args or {}
    if cmd == "mr-raw":
        argv += ["--method", str(args.get("method") or "GET")[:8]]
    if cmd == "mr-push":
        argv += mr_push_args(args)
    if cmd == "box-replies" and args.get("days"):
        argv += ["--days", str(int(args["days"]))]
    if cmd == "mr-push":
        pass                                    # its arguments were built above
    elif cmd == "postiz-post":
        sp = os.path.join(LOGS, "post_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump(args, fh, indent=1)
        argv += ["--spec", sp]
    else:
        for k in CHANNEL_CMDS[cmd]:
            if not str(args.get(k) or "").strip():
                return {"ok": False, "error": "%s is required." % k}
            argv += ["--" + k, str(args[k])[:4000]]
    if dry:
        argv.append("--dry-run")
    try:
        p = subprocess.run(PY + ["-u"] + argv, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"), cwd=APP)
        return json.loads((p.stdout or "").strip() or '{"ok": false, "error": "no answer"}')
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The channel did not answer in time."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def save_mailbox(b):
    """Add or replace one mailbox. A blank password keeps the one already saved."""
    name = str(b.get("name") or "").strip()[:80]
    if not name:
        return {"error": "The mailbox needs a name - whatever you want to call it."}
    frm = str(b.get("from_email") or "").strip()
    if "@" not in frm:
        return {"error": "The From address must be a real email address."}
    if not str(b.get("smtp_host") or "").strip():
        return {"error": "An SMTP server address is required, for example smtp.gmail.com."}
    s = load_settings()
    boxes = [x for x in (s.get("mailboxes") or []) if isinstance(x, dict)]
    old = next((x for x in boxes if str(x.get("name", "")).lower() == name.lower()), {})
    rec = dict(old)
    for k in MAILBOX_STR:
        if k in b and str(b[k]).strip():
            rec[k] = str(b[k]).strip()[:300]
    for k in MAILBOX_INT:
        if str(b.get(k) or "").strip():
            try:
                rec[k] = max(1, int(b[k]))
            except ValueError:
                pass
    rec["name"] = name
    rec.setdefault("smtp_port", 587); rec.setdefault("imap_port", 993); rec.setdefault("daily_limit", 40)
    rec.setdefault("smtp_user", rec.get("from_email", ""))
    boxes = [x for x in boxes if str(x.get("name", "")).lower() != name.lower()] + [rec]
    s["mailboxes"] = boxes
    save_settings(s)
    return {"ok": True, "settings": public_settings()}


def delete_mailbox(name):
    s = load_settings()
    s["mailboxes"] = [x for x in (s.get("mailboxes") or [])
                      if isinstance(x, dict) and str(x.get("name", "")).lower() != str(name).lower()]
    save_settings(s)
    return {"ok": True, "settings": public_settings()}


def import_senders(b):
    """Copy the senders out of Manyreach into Lead Studio as mailboxes.
    Manyreach does not give out the passwords, so each one arrives without its own;
    the rest of the setup - server, port, username, daily limit - comes across."""
    res = run_tool_json([MAILER, "mr-senders", "--all"], 900)
    if not res.get("ok"):
        return {"error": (res.get("detail") or {}).get("error") or res.get("error") or "Manyreach did not answer"}
    senders = res.get("senders") or []
    only_connected = bool(b.get("only_connected"))
    s = load_settings()
    have = {str(x.get("name", "")).lower(): x for x in (s.get("mailboxes") or []) if isinstance(x, dict)}
    added = updated = skipped = no_smtp = 0
    for sn in senders:
        if only_connected and sn.get("disconnected"):
            skipped += 1
            continue
        if not sn.get("smtp_host") or not sn.get("email"):
            no_smtp += 1                    # a Gmail/Outlook OAuth sender has no SMTP details to copy
            continue
        name = sn["email"]                  # the address is the name: unique, and recognisable
        rec = dict(have.get(name.lower()) or {})
        keep_pass = rec.get("smtp_pass", "")
        keep_imap = rec.get("imap_pass", "")
        rec.update({"name": name, "from_name": sn.get("from_name") or "", "from_email": sn["email"],
                    "smtp_host": sn["smtp_host"], "smtp_port": int(sn.get("smtp_port") or 587),
                    "smtp_user": sn.get("smtp_user") or sn["email"],
                    "smtp_security": "ssl" if int(sn.get("smtp_port") or 587) == 465 else "starttls",
                    "imap_host": sn.get("imap_host") or "", "imap_port": int(sn.get("imap_port") or 993),
                    "imap_user": sn.get("imap_user") or sn["email"],
                    "daily_limit": int(sn.get("daily_limit") or 25),
                    "from_manyreach": sn.get("id", ""),
                    "manyreach_warmup": bool(sn.get("warmup")),
                    "manyreach_disconnected": bool(sn.get("disconnected"))})
        if keep_pass:
            rec["smtp_pass"] = keep_pass
        if keep_imap:
            rec["imap_pass"] = keep_imap
        if name.lower() in have:
            updated += 1
        else:
            added += 1
        have[name.lower()] = rec
    s["mailboxes"] = list(have.values())
    save_settings(s)
    return {"ok": True, "from_manyreach": len(senders), "added": added, "updated": updated,
            "skipped_disconnected": skipped, "no_smtp_details": no_smtp,
            "total_mailboxes": len(s["mailboxes"]),
            "need_a_password": sum(1 for x in s["mailboxes"] if not x.get("smtp_pass")),
            "settings": public_settings()}


def start_mail_campaign(b):
    """Send a whole CSV from one of your own mailboxes, as a job you can watch and stop."""
    # only the keys actually given: a None here would switch off a default that protects the send
    spec = {k: b[k] for k in ("account", "file", "subject", "body", "limit", "delay", "daily_limit",
                              "html", "require_fields", "skip_already_sent")
            if b.get(k) is not None and b.get(k) != ""}
    if not str(spec.get("account") or "").strip():
        return None, "Pick a mailbox first."
    p = safe_path(str(spec.get("file") or ""))
    if not p or not os.path.exists(p):
        return None, "That lead file is not in the workspace."
    spec["file"] = os.path.relpath(p, ROOT).replace(os.sep, "/")
    if not str(spec.get("subject") or "").strip() or not str(spec.get("body") or "").strip():
        return None, "A subject and a body, please."
    os.makedirs(LOGS, exist_ok=True)
    sp = os.path.join(LOGS, "mailcamp_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1, ensure_ascii=False)
    argv = [MAILER, "box-campaign", "--spec", sp]
    if b.get("dry_run"):
        argv.append("--dry-run")
    return start_job(None, argv=argv,
                     name=("Preview" if b.get("dry_run") else "Email") + " from %s: %s"
                          % (spec["account"], str(spec["subject"])[:60]))


def start_mr_push(b):
    """Upload a lead file into a Manyreach campaign and/or list, as a job you can watch.
    100 prospects a call, 1.2s apart, and it never uploads the same address to the same target twice."""
    if not str(b.get("campaign") or "").strip() and not str(b.get("list") or "").strip():
        return None, "Pick a Manyreach campaign or a list first."
    p = safe_path(str(b.get("file") or b.get("path") or ""))
    if not p or not os.path.exists(p):
        return None, "That lead file is not in the workspace."
    return start_job(None, argv=[MAILER, "mr-push"] + mr_push_args(b),
                     name="Manyreach upload: %s" % os.path.basename(p))


def cc_enrich_run(b):
    """Read the gap list, fetch those domains' stored pages out of Common Crawl, keep what they hold."""
    argv = [CCENRICH, "run", "--source", str(b.get("source") or "hf")]
    for k, flag, lo, hi in (("limit", "--limit", 1, 5_000_000), ("par", "--par", 2, 200)):
        try:
            v = int(b.get(k) or 0)
            if v:
                argv += [flag, str(max(lo, min(hi, v)))]
        except ValueError:
            pass
    return start_job(None, argv=argv, name="Common Crawl enrichment")


def web_scrape_run(cmd, b):
    """The two live-web jobs: find each site's contact pages, or crawl sites for addresses."""
    p = safe_path(str(b.get("file") or ""))
    if not p or not os.path.exists(p):
        return None, "Pick a file of domains inside the workspace."
    argv = [WEBSCRAPE, cmd, "--in", os.path.relpath(p, ROOT).replace(os.sep, "/")]
    for k, flag, lo, hi in (("par", "--par", 1, 100), ("pages", "--pages", 0, 25),
                            ("limit", "--limit", 1, 5_000_000)):
        try:
            v = int(b.get(k) or 0)
            if v:
                argv += [flag, str(max(lo, min(hi, v)))]
        except ValueError:
            pass
    try:
        d = float(b.get("delay"))
        argv += ["--delay", "%.2f" % max(0.0, min(30.0, d))]
    except (TypeError, ValueError):
        pass
    if b.get("ignore_robots"):
        argv.append("--ignore-robots")
    return start_job(None, argv=argv,
                     name=("Contact pages: " if cmd == "links" else "Crawl for emails: ") + os.path.basename(p))


def cc_gaps(b):
    """Rebuild the list of domains that have nobody to write to. Quick, so it runs inline."""
    argv = [CCENRICH, "gaps"]
    if str(b.get("tld") or "").strip():
        argv += ["--tld", re.sub(r"[^A-Za-z,.]", "", str(b["tld"]))[:60]]
    try:
        if int(b.get("limit") or 0) > 0:
            argv += ["--limit", str(int(b["limit"]))]
    except ValueError:
        pass
    return run_tool_json(argv, 900)


def run_tool_json(argv, timeout=300):
    """Run one of our own scripts and hand back whatever JSON it printed."""
    try:
        p = subprocess.run(PY + ["-u"] + argv, capture_output=True, text=True, timeout=timeout,
                           cwd=ROOT, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": "That took longer than %d seconds and was stopped." % timeout}
    txt = (p.stdout or "").strip()
    i = txt.find("{")
    if i >= 0:
        try:
            return json.loads(txt[i:])
        except ValueError:
            pass
    return {"error": (p.stderr or txt or "No answer from the script.").strip()[-800:]}


# ------------------------------------------------------------------ MCP (Model Context Protocol)
MCP_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
_S, _I = {"type": "string"}, {"type": "integer"}
MCP_TOOLS = [
    {"name": "list_lead_tools", "description": "List the tools registered in Lead Studio (scrapers, scoring, verification, upload). Returns id, group, name, what it does and default arguments.",
     "inputSchema": {"type": "object", "properties": {"group": {"type": "string", "description": "Only tools whose group contains this text"}}}},
    {"name": "run_lead_tool", "description": "Start a registered Lead Studio tool as a background job on the user's computer. Returns a job id; follow it with get_job_log. Tools that upload or send need confirm=true, and only after the user has agreed.",
     "inputSchema": {"type": "object", "properties": {"tool_id": _S, "args": {"type": "string", "description": "Command-line arguments; omit for the tool's defaults"}, "confirm": {"type": "boolean"}}, "required": ["tool_id"]}},
    {"name": "list_jobs", "description": "Jobs started in this Lead Studio session, newest first, with running state.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_job_log", "description": "The last lines of a job's log.",
     "inputSchema": {"type": "object", "properties": {"job_id": _S, "lines": _I}, "required": ["job_id"]}},
    {"name": "stop_job", "description": "Stop a running job.", "inputSchema": {"type": "object", "properties": {"job_id": _S}, "required": ["job_id"]}},
    {"name": "list_files", "description": "List a folder inside the Lead Studio workspace (path relative to the workspace root, '' for the root).",
     "inputSchema": {"type": "object", "properties": {"path": _S}}},
    {"name": "read_leads", "description": "Read rows from a CSV / CSV.GZ lead file in the workspace. Optional text filter. At most 100 rows per call; use offset to page.",
     "inputSchema": {"type": "object", "properties": {"path": _S, "query": _S, "limit": _I, "offset": _I}, "required": ["path"]}},
    {"name": "ai_enrich", "description": "Start an Ollama Cloud enrichment job: for each row of a CSV, send the chosen columns with an instruction and write the answer into a new column of a new file next to the input.",
     "inputSchema": {"type": "object", "properties": {"input": _S, "columns": {"type": "array", "items": _S}, "instruction": _S,
                                                       "new_column": _S, "limit": _I, "model": _S}, "required": ["input", "instruction"]}},
    {"name": "search_leads", "description": "Search the one combined lead database built from every contact and company file in the workspace. Filters: country (2-letter codes), titles, keywords (company, description, industry, technology, website), industries, location, technologies (all must be present), sizes (1-10, 11-50, 51-200, 201-500, 501-1,000, 1,001-5,000, 5,001+), sources, and must (any of: name, title, phone, website, company, industry, technology, business_email, named_person). Set count=true for the number of matches only.",
     "inputSchema": {"type": "object", "properties": {"country": {"type": "array", "items": _S}, "titles": {"type": "array", "items": _S},
                                                       "keywords": {"type": "array", "items": _S}, "industries": {"type": "array", "items": _S},
                                                       "location": {"type": "array", "items": _S}, "technologies": {"type": "array", "items": _S},
                                                       "sizes": {"type": "array", "items": _S}, "sources": {"type": "array", "items": _S},
                                                       "must": {"type": "array", "items": _S}, "limit": _I, "offset": _I,
                                                       "count": {"type": "boolean"}}}},
    {"name": "export_leads", "description": "Write every lead matching a search to a JSON or CSV file in LEAD_INDEX/exports. Same filters as search_leads. Returns a job id and the file name.",
     "inputSchema": {"type": "object", "properties": {"filters": {"type": "object"}, "format": {"type": "string", "enum": ["json", "csv"]},
                                                       "name": _S, "limit": _I}}},
    {"name": "build_lead_index", "description": "Rebuild the combined lead database from the files in the workspace. Takes a long time on a large folder; run it after new scrapes.",
     "inputSchema": {"type": "object", "properties": {"confirm": {"type": "boolean"}}}},
    {"name": "import_contacts", "description": "Upload contacts into the lead database. Takes CSV, XLSX, JSON or Parquet files (a path under the workspace, or drop them in LEAD_INDEX/uploads). Contacts that are new are added; for a contact or website already in the database only the empty fields are filled in - nothing is overwritten. Headers are matched automatically (email, name, company, website/domain, phone, country, city, industry, technologies). Returns a job id; leads.parquet and the Contacts page are refreshed at the end unless refresh=false.",
     "inputSchema": {"type": "object", "properties": {"files": {"type": "array", "items": _S}, "refresh": {"type": "boolean"}},
                      "required": ["files"]}},
    {"name": "lead_database_info", "description": "What is in the lead database: how many contacts and companies, which sources, when it was built, and the available countries, industries, technologies and sizes.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "job_results", "description": "What a job actually produced: the lead files it wrote, their row and email counts, and how many of those emails are new to the lead database. Use this instead of reading the log.",
     "inputSchema": {"type": "object", "properties": {"job_id": _S, "preview": {"type": "boolean", "description": "Also return the first rows of the biggest file"}}, "required": ["job_id"]}},
    {"name": "check_channels", "description": "Check the outreach channels set up on this computer: WhatsApp through OpenWA, social accounts through Postiz, readable threads through Agent Reach.",
     "inputSchema": {"type": "object", "properties": {"which": {"type": "string", "enum": ["whatsapp", "postiz", "reach", "all"]}}}},
    {"name": "send_whatsapp", "description": "Send ONE WhatsApp message through the user's OpenWA gateway. WhatsApp bans numbers that message strangers in bulk, so this is one message per call and needs confirm=true after the user has agreed to that exact message and number.",
     "inputSchema": {"type": "object", "properties": {"to": {"type": "string", "description": "Phone number with country code"}, "text": _S,
                                                       "confirm": {"type": "boolean"}}, "required": ["to", "text"]}},
    {"name": "schedule_post", "description": "Create a social post through the user's Postiz. Needs channel ids from check_channels, and confirm=true after the user has agreed to the text. Leave date empty to post now.",
     "inputSchema": {"type": "object", "properties": {"content": _S, "channels": {"type": "array", "items": _S},
                                                       "date": {"type": "string", "description": "2026-11-20T09:30:00Z"},
                                                       "confirm": {"type": "boolean"}}, "required": ["content", "channels"]}},
    {"name": "read_thread", "description": "Read a conversation or thread from a supported channel through Agent Reach, by URL, without opening the app.",
     "inputSchema": {"type": "object", "properties": {"url": _S}, "required": ["url"]}},
]


def mcp_call(name, a):
    if name == "list_lead_tools":
        g = str(a.get("group") or "").lower()
        return [{"id": t["id"], "group": t["group"], "name": t["name"], "what_it_does": t["desc"], "default_args": t.get("args", ""),
                 "needs_confirm": bool(t.get("confirm")), "available": t["exists"]} for t in tools() if g in t["group"].lower()]
    if name == "run_lead_tool":
        t = next((x for x in tools() if x["id"] == a.get("tool_id")), None)
        if not t or not t["exists"]:
            raise ValueError("No such tool. Call list_lead_tools for the ids.")
        if t.get("confirm") and not a.get("confirm"):
            raise ValueError("This tool needs the user's go-ahead: %s Ask them, then call again with confirm=true." % t["confirm"])
        jid, err = start_job(t, str(a["args"])[:2000] if a.get("args") is not None else t.get("args", ""))
        if err:
            raise ValueError(err)
        return {"job_id": jid, "tool": t["name"]}
    if name == "list_jobs":
        return list_jobs()
    if name == "get_job_log":
        return {"log": job_log(str(a.get("job_id", "")), max(10, min(int(a.get("lines") or 120), 400)))}
    if name == "stop_job":
        return {"stopped": stop_job(str(a.get("job_id", "")))}
    if name == "list_files":
        return [{"name": f["name"], "folder": f["dir"], "kb": f["kb"], "changed": f["when"]} for f in list_files(str(a.get("path") or ""))]
    if name == "read_leads":
        t = read_table(str(a.get("path", "")), limit=min(int(a.get("limit") or 50), 100), offset=int(a.get("offset") or 0),
                       q=str(a.get("query") or ""))
        if not t["cols"]:
            raise ValueError("Not a readable CSV inside the workspace.")
        t["rows"] = [[c[:300] for c in r] for r in t["rows"]]
        return t
    if name == "ai_enrich":
        jid, err = start_enrich(a)
        if err:
            raise ValueError(err)
        return {"job_id": jid}
    if name == "search_leads":
        s = clean_spec(a)
        s["limit"] = min(int(a.get("limit") or 25), 100)
        if a.get("count"):
            s["count"] = True
        r = run_index(["search"], timeout=300, spec=s)
        if r.get("error") == "no_index" or r.get("error") == "":
            raise ValueError("The lead database has not been built yet. Call build_lead_index first.")
        if r.get("error"):
            raise ValueError(r["error"])
        if "rows" in r:
            r["leads"] = [dict(zip(r["cols"], row)) for row in r.pop("rows")]
        return r
    if name == "export_leads":
        b = dict(a.get("filters") or {}); b.update({k: a[k] for k in ("format", "name", "limit") if a.get(k)})
        jid, err, out = start_export(b)
        if err:
            raise ValueError(err)
        return {"job_id": jid, "file": os.path.relpath(out, ROOT).replace(os.sep, "/")}
    if name == "import_contacts":
        jid, err = start_ingest(a.get("files") or [], a.get("refresh") is not False)
        if err:
            raise ValueError(err)
        return {"job_id": jid, "note": "Follow it with get_job_log. New contacts are added, known ones only get empty fields filled in."}
    if name == "build_lead_index":
        if not a.get("confirm"):
            raise ValueError("Rebuilding reads every lead file in the workspace and can run for a long time. "
                             "Ask the user, then call again with confirm=true.")
        jid, err = start_build()
        if err:
            raise ValueError(err)
        return {"job_id": jid, "note": "Follow it with job_results or get_job_log."}
    if name == "lead_database_info":
        h = leads_home()
        if not h["info"]:
            return {"built": False, "note": "No database yet - call build_lead_index."}
        return {"built": True, "about": h["info"], "filters_available": h["facets"], "exports": h["exports"]}
    if name == "job_results":
        jid = str(a.get("job_id", ""))
        v = job_leads(jid)
        if a.get("preview"):
            v["preview"] = job_preview(jid)
        return v
    if name == "check_channels":
        w = str(a.get("which") or "all")
        res = {}
        for key, cmd in (("whatsapp", "whatsapp-check"), ("postiz", "postiz-channels"), ("reach", "reach-channels")):
            if w in (key, "all"):
                res[key] = channel(cmd)
        return res
    if name == "send_whatsapp":
        if not a.get("confirm"):
            raise ValueError("Show the user the number and the exact message and get their agreement, then call again "
                             "with confirm=true. WhatsApp bans numbers that message strangers in bulk.")
        return channel("whatsapp-send", {"to": a.get("to"), "text": a.get("text")})
    if name == "schedule_post":
        if not a.get("confirm"):
            raise ValueError("Show the user the post text, the channels and the time, then call again with confirm=true.")
        return channel("postiz-post", {"content": a.get("content"), "channels": a.get("channels") or [],
                                       "date": a.get("date") or "", "now": not a.get("date")})
    if name == "read_thread":
        return channel("reach-read", {"url": a.get("url")})
    raise ValueError("Unknown tool: %s" % name)


def mcp_handle(msg):
    """One JSON-RPC message in, one response dict out (None for notifications)."""
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    ok = lambda r: {"jsonrpc": "2.0", "id": mid, "result": r}
    if method == "initialize":
        v = params.get("protocolVersion")
        return ok({"protocolVersion": v if v in MCP_VERSIONS else MCP_VERSIONS[0], "capabilities": {"tools": {}},
                   "serverInfo": {"name": "lead-studio", "version": VERSION},
                   "instructions": "Lead Studio runs lead-generation tools on the user's own computer and reads the lead files they produce. Paths are relative to the workspace root."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": MCP_TOOLS})
    if method == "tools/call":
        try:
            res = mcp_call(params.get("name"), params.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}], "isError": False})
        except Exception as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: %s" % method}}


# ------------------------------------------------------------------ the newer sections (studio_plus.py)
def load_plus():
    try:
        spec = importlib.util.spec_from_file_location("studio_plus", os.path.join(APP, "studio_plus.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        mod.init(sys.modules[__name__])
        return mod
    except Exception as e:
        say("studio_plus.py could not be loaded:", e)
        return None


PLUS = load_plus()


# ------------------------------------------------------------------ HTTP
def page():
    try:
        with open(os.path.join(APP, "ui", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        try:
            with open(os.path.join(APP, "ui", "plus.js"), encoding="utf-8") as fh:
                html = html.replace("</body>", "<script>\n" + fh.read() + "\n</script>\n</body>", 1)
        except OSError:
            pass
        return html.replace("__TOKEN__", TOKEN)
    except OSError:
        return "<h1>Lead Studio</h1><p>The file ui/index.html is missing next to lead_studio.py.</p>"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else (obj if isinstance(obj, str) else json.dumps(obj)).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def ok_host(self):
        if (self.headers.get("Host") or "").split(":")[0] not in ("127.0.0.1", "localhost"):
            return False
        o = self.headers.get("Origin")
        return (not o) or urllib.parse.urlparse(o).hostname in ("127.0.0.1", "localhost")

    def body(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except Exception:
            return None

    def do_GET(self):
        if not self.ok_host():
            return self.send({"error": "local only"}, 403)
        u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/":
            return self.send(page(), ctype="text/html")
        if u.path == "/mcp":
            return self.send({"error": "POST JSON-RPC here"}, 405)
        if q.get("t") != TOKEN:
            return self.send({"error": "bad token"}, 403)
        if u.path == "/api/state":
            return self.send({"tools": tools(), "jobs": list_jobs(), "root": ROOT, "app": APP, "python": bool(PY),
                              "hot": hot_lists(), "settings": public_settings(), "version": VERSION, "port": PORT})
        if u.path == "/api/log":
            jid = q.get("id", ""); j = JOBS.get(jid)
            return self.send({"log": job_log(jid), "job": job_view(j) if j else None})
        if u.path == "/api/table":
            try:
                return self.send(read_table(q.get("path", ""), q.get("limit") or 200, q.get("offset") or 0, q.get("q", "")))
            except Exception as e:
                return self.send({"cols": [], "rows": [], "total": 0, "error": str(e)})
        if u.path == "/api/mailboxes":
            return self.send({"mailboxes": public_boxes()})
        if u.path == "/api/files":
            return self.send({"items": list_files(q.get("path", "")), "path": q.get("path", "")})
        if u.path == "/api/ollama/models":
            names, err = ollama_models()
            return self.send({"models": names, "error": err})
        if u.path == "/api/leads/home":
            return self.send(leads_home())
        if u.path == "/api/job/leads":
            return self.send(job_leads(q.get("id", "")))
        if u.path == "/api/job/preview":
            return self.send(job_preview(q.get("id", "")))
        if u.path.startswith("/api/plus/") and PLUS:
            r = PLUS.handle_get(u.path, q)
            if r is not None:
                return self.send(r)
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        if not self.ok_host():
            return self.send({"error": "local only"}, 403)
        u0 = urllib.parse.urlparse(self.path)
        if u0.path == "/api/leads/upload":
            q0 = dict(urllib.parse.parse_qsl(u0.query))
            if q0.get("t") != TOKEN:
                return self.send({"error": "bad token"}, 403)
            return self.send(save_upload(self, q0))
        if u0.path == "/api/plus/upload":
            q0 = dict(urllib.parse.parse_qsl(u0.query))
            if q0.get("t") != TOKEN or not PLUS:
                return self.send({"error": "bad token"}, 403)
            return self.send(PLUS.handle_upload(self, q0))
        b = self.body()
        if urllib.parse.urlparse(self.path).path == "/mcp":
            auth = self.headers.get("Authorization") or ""
            if auth != "Bearer " + load_settings()["mcp_token"]:
                return self.send({"error": "missing or wrong MCP token"}, 401)
            if b is None:
                return self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
            res = [r for r in (mcp_handle(m) for m in b) if r] if isinstance(b, list) else mcp_handle(b)
            if not res:
                self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers(); return
            return self.send(res)
        if b is None or b.get("t") != TOKEN:
            return self.send({"error": "bad token"}, 403)
        if self.path == "/api/run":
            t = next((x for x in tools() if x["id"] == b.get("tool")), None)
            if not t or not t["exists"]:
                return self.send({"error": "This script is not in the folder."}, 400)
            jid, err = start_job(t, str(b.get("args", ""))[:2000])
            return self.send({"job": jid, "error": err})
        if self.path == "/api/pip":
            mods = [PIP.get(m, m) for m in b.get("mods", []) if re.fullmatch(r"[A-Za-z0-9_\-]+", str(m))]
            jid, err = start_job(None, pip=mods) if mods else (None, "nothing to install")
            return self.send({"job": jid, "error": err})
        if self.path == "/api/stop":
            return self.send({"ok": stop_job(b.get("id", ""))})
        if self.path == "/api/open":
            p = safe_path(b.get("path", ""))
            if p and os.path.exists(p) and hasattr(os, "startfile"):
                os.startfile(p if os.path.isdir(p) else os.path.dirname(p))
            return self.send({"ok": bool(p)})
        if self.path == "/api/reload":
            tools(refresh=True); return self.send({"ok": True})
        if self.path == "/api/settings":
            s = load_settings()
            if "ollama_key" in b and str(b["ollama_key"]).strip():
                s["ollama_key"] = str(b["ollama_key"]).strip()[:300]
            if b.get("forget_key"):
                s["ollama_key"] = ""
            if b.get("ollama_base"):
                base = str(b["ollama_base"]).strip().rstrip("/")
                if not re.match(r"^https?://[A-Za-z0-9.\-:]+(/[\w\-./]*)?$", base):
                    return self.send({"error": "The address must look like https://ollama.com"})
                s["ollama_base"] = base
            if "ollama_model" in b:
                s["ollama_model"] = str(b["ollama_model"])[:120]
            if b.get("new_mcp_token"):
                s["mcp_token"] = secrets.token_hex(20)
            save_settings(s)
            return self.send({"ok": True, "settings": public_settings()})
        if self.path == "/api/today/save":
            sid = b.get("id")
            if not sid:
                return self.send({"error": "No script id"})
            cols = b.get("cols", [])
            rows = b.get("rows", [])
            total = b.get("total", len(rows))
            today_results = os.path.join(APP, "today_results.json")
            try:
                data = {}
                if os.path.exists(today_results):
                    with open(today_results, encoding="utf-8") as f:
                        data = json.load(f)
                data[sid] = {"cols": cols, "rows": rows, "total": total, "saved_at": dt.datetime.now(dt.timezone.utc).isoformat()}
                with open(today_results, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
                return self.send({"ok": True})
            except Exception as e:
                return self.send({"error": str(e)})
        if self.path == "/api/today/load":
            sid = b.get("id")
            today_results = os.path.join(APP, "today_results.json")
            try:
                if not os.path.exists(today_results):
                    return self.send({}) if sid == "_all" else self.send({"error": "No results saved"})
                with open(today_results, encoding="utf-8") as f:
                    data = json.load(f)
                if sid == "_all":
                    return self.send(data)
                if sid not in data:
                    return self.send({"error": "No results for this script"})
                return self.send(data[sid])
            except Exception as e:
                return self.send({"error": str(e)})
        if self.path == "/api/today/clear":
            sid = b.get("id")
            today_results = os.path.join(APP, "today_results.json")
            try:
                if os.path.exists(today_results):
                    with open(today_results, encoding="utf-8") as f:
                        data = json.load(f)
                    if sid in data:
                        del data[sid]
                        with open(today_results, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=1)
                return self.send({"ok": True})
            except Exception as e:
                return self.send({"error": str(e)})
        if self.path == "/api/enrich":
            jid, err = start_enrich(b)
            return self.send({"job": jid, "error": err})
        if self.path == "/api/leads/search":
            return self.send(run_index(["search"], timeout=300, spec=clean_spec(b)))
        if self.path == "/api/leads/count":
            s = clean_spec(b); s["count"] = True
            return self.send(run_index(["search"], timeout=300, spec=s))
        if self.path == "/api/leads/build":
            jid, err = start_build()
            return self.send({"job": jid, "error": err})
        if self.path == "/api/leads/import":
            jid, err = start_ingest(b.get("files") or [], b.get("refresh") is not False)
            return self.send({"error": err} if err else {"job": jid})
        if self.path == "/api/leads/export":
            jid, err, out = start_export(b)
            return self.send({"job": jid, "error": err, "file": os.path.relpath(out, ROOT).replace(os.sep, "/")})
        if self.path == "/api/channel":
            return self.send(channel(str(b.get("cmd", "")), b.get("args") or {}, bool(b.get("dry_run"))))
        if self.path in ("/api/web/links", "/api/web/emails"):
            jid, err = web_scrape_run(self.path.rsplit("/", 1)[1], b)
            return self.send({"job": jid, "error": err})
        if self.path == "/api/web/status":
            return self.send(run_tool_json([WEBSCRAPE, "status"], 300))
        if self.path == "/api/web/export":
            return self.send(run_tool_json([WEBSCRAPE, "export"], 600))
        if self.path == "/api/cc/gaps":
            return self.send(cc_gaps(b))
        if self.path == "/api/cc/scan":
            argv = [CCENRICH, "scan", "--from", "live" if b.get("source") == "live" else "local"]
            if str(b.get("index") or "").strip():
                argv += ["--index", str(b["index"]).strip().strip('"')]
            try:
                if int(b.get("limit") or 0) > 0:
                    argv += ["--limit", str(int(b["limit"]))]
            except ValueError:
                pass
            if b.get("fresh"):
                argv.append("--fresh")
            if str(b.get("index") or "").strip():          # remember the folder for next time
                try:
                    with open(SETTINGS, encoding="utf-8") as fh:
                        raw = json.load(fh)
                    raw["cc_index_dir"] = str(b["index"]).strip().strip('"')
                    tmp = SETTINGS + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(raw, fh, indent=1)
                    os.replace(tmp, SETTINGS)
                except Exception:
                    pass
            jid, err = start_job({"id": "cc_scan"}, argv=argv, name="Scan the Common Crawl index for the gap domains", cwd=ROOT)
            return self.send({"error": err} if err else {"job": jid})
        if self.path == "/api/cc/run":
            jid, err = cc_enrich_run(b)
            return self.send({"job": jid, "error": err})
        if self.path == "/api/cc/status":
            return self.send(run_tool_json([CCENRICH, "status"], 300))
        if self.path == "/api/cc/export":
            return self.send(run_tool_json([CCENRICH, "export"], 900))
        if self.path == "/api/mailbox/import":
            return self.send(import_senders(b))
        if self.path == "/api/mailbox":
            if b.get("action") == "delete":
                return self.send(delete_mailbox(str(b.get("name") or "")))
            return self.send(save_mailbox(b.get("box") or {}))
        if self.path == "/api/mail/campaign":
            jid, err = start_mail_campaign(b)
            return self.send({"job": jid, "error": err})
        if self.path == "/api/mail/push":
            jid, err = start_mr_push(b)
            return self.send({"job": jid, "error": err})
        if self.path == "/api/channel/settings":
            s = load_settings()
            for k in ("wa_url", "postiz_url", "mr_base", "cc_dir"):
                if b.get(k):
                    v = str(b[k]).strip().rstrip("/")
                    if not re.match(r"^https?://[A-Za-z0-9.\-:]+(/[\w\-./]*)?$", v):
                        return self.send({"error": "The address must look like http://localhost:2785"})
                    s[k] = v
            for k in ("wa_session", "reach_cmd"):
                if b.get(k):
                    s[k] = str(b[k]).strip()[:120]
            for k in ("wa_key", "postiz_key", "mr_key"):
                if str(b.get(k) or "").strip():
                    s[k] = str(b[k]).strip()[:400]
                if b.get("forget_" + k):
                    s[k] = ""
            save_settings(s)
            return self.send({"ok": True, "settings": public_settings()})
        if self.path.startswith("/api/plus/") and PLUS:
            try:
                r = PLUS.handle_post(self.path, b)
            except Exception as e:
                r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            if r is not None:
                return self.send(r)
        self.send({"error": "not found"}, 404)


# ------------------------------------------------------------------ its own window
# Chromium-based browsers can run a page as a plain window with no tabs, no address bar
# and its own taskbar button: --app=<url>. With its own profile folder it is a separate
# process, so closing the window closes Lead Studio.
APP_EXES = [
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe",
]


def app_exe():
    if os.name == "nt":
        for p in APP_EXES:
            p = os.path.expandvars(p)
            if "%" not in p and os.path.exists(p):
                return p
        return ""
    for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        p = shutil.which(n)
        if p:
            return p
    return ""


def open_window(url):
    """Open Lead Studio as its own window. Falls back to the default browser."""
    exe = app_exe()
    if not exe or os.environ.get("LS_BROWSER"):
        webbrowser.open(url)
        return None
    prof = os.path.join(APP, ".appwindow")
    try:
        os.makedirs(prof, exist_ok=True)
        return subprocess.Popen([exe, "--app=" + url, "--user-data-dir=" + prof,
                                 "--window-size=1500,950", "--no-first-run", "--no-default-browser-check",
                                 "--disable-features=Translate,DefaultBrowserPrompt",
                                 "--app-shell-host-window-size=1500x950"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print("could not open the app window (%s) - using the default browser" % e)
        webbrowser.open(url)
        return None


def quit_with_window(proc, srv):
    """Closing the window closes Lead Studio, the way a normal program behaves."""
    t0 = time.time()
    proc.wait()
    if time.time() - t0 < 5:
        return                      # the browser handed the page to another process; keep running
    print("\nWindow closed - Lead Studio is stopping. Running tools keep going.")
    threading.Thread(target=srv.shutdown, daemon=True).start()
    time.sleep(1.5)
    os._exit(0)


def main():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        print(f"Lead Studio is already running: http://127.0.0.1:{PORT}")
        open_window(f"http://127.0.0.1:{PORT}")
        return
    url = f"http://127.0.0.1:{PORT}"
    print(f"Lead Studio {VERSION} is running: {url}\nWorkspace: {ROOT}\n"
          "Closing the Lead Studio window stops it (running tools keep going).\n"
          "This console can stay out of the way; close it to stop everything.")
    load_settings()
    tools(); discover_async()
    if not os.environ.get("LS_NO_BROWSER"):
        def launch():
            p = open_window(url)
            if p:
                threading.Thread(target=quit_with_window, args=(p, srv), daemon=True).start()
        threading.Timer(0.8, launch).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
