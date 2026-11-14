"""
SEO library - one searchable index over your collection of texts and articles.

    python seo_library.py index --dirs "D:\\Articles;C:\\Users\\admin\\Documents\\Texts"
    python seo_library.py search --q "fan gates price" --limit 10
    python seo_library.py status

Reads .txt .md .html .htm .docx (and .pdf when pypdf is installed), splits each into passages and
keeps them in seo_library.db next to this file (SQLite full-text search, nothing leaves the computer).
Re-running index only reads files that are new or changed since the last run.
"""
import os, re, sys, json, time, html, sqlite3, zipfile, argparse

APP = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(APP, "seo_library.db")
EXTS = (".txt", ".md", ".markdown", ".html", ".htm", ".docx", ".pdf", ".rtf")
CHUNK = 1400


def out(o):
    print(json.dumps(o, ensure_ascii=False), flush=True)


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, mtime REAL, words INT)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(title, body, doc UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
    return c


def text_of(p):
    ext = os.path.splitext(p)[1].lower()
    if ext == ".docx":
        with zipfile.ZipFile(p) as z:
            x = z.read("word/document.xml").decode("utf-8", "replace")
        x = re.sub(r"</w:p>", "\n", x)
        return html.unescape(re.sub(r"<[^>]+>", "", x))
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return None
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(p).pages)
    raw = open(p, "rb").read()
    for enc in ("utf-8", "cp1250", "latin-1"):
        try:
            t = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    if ext in (".html", ".htm"):
        t = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", t)
        t = re.sub(r"(?i)<(br|/p|/h\d|/li|/div)[^>]*>", "\n", t)
        t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    if ext == ".rtf":
        t = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", t)
    return t


def title_of(p, t):
    if p.lower().endswith((".html", ".htm")):
        try:
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", open(p, encoding="utf-8", errors="replace").read(20000))
            if m and m.group(1).strip():
                return html.unescape(m.group(1).strip())[:200]
        except OSError:
            pass
    for ln in t.splitlines():
        ln = ln.strip().lstrip("#").strip()
        if 8 <= len(ln) <= 160:
            return ln
    return os.path.splitext(os.path.basename(p))[0]


def chunks(t):
    t = re.sub(r"[ \t]+", " ", t)
    paras = [x.strip() for x in re.split(r"\n\s*\n|\n", t) if x.strip()]
    cur = ""
    for pa in paras:
        if len(cur) + len(pa) > CHUNK and cur:
            yield cur; cur = ""
        cur = (cur + "\n" + pa).strip()
        while len(cur) > CHUNK * 2:
            yield cur[:CHUNK]; cur = cur[CHUNK:]
    if cur:
        yield cur


def index(dirs):
    c = db()
    have = {p: m for p, m in c.execute("SELECT path, mtime FROM docs")}
    seen, new, changed, skipped, t0 = set(), 0, 0, 0, time.time()
    for d in dirs:
        d = os.path.expanduser(d.strip().strip('"'))
        if not d:
            continue
        if not os.path.isdir(d):
            print("Not a folder: " + d, flush=True); continue
        print("Reading " + d, flush=True)
        for base, sub, files in os.walk(d):
            sub[:] = [s for s in sub if not s.startswith((".", "__"))]
            for f in files:
                if not f.lower().endswith(EXTS):
                    continue
                p = os.path.join(base, f); seen.add(p)
                try:
                    m = os.path.getmtime(p)
                except OSError:
                    continue
                if have.get(p) == m:
                    continue
                try:
                    t = text_of(p)
                except Exception as e:
                    print("  could not read %s: %s" % (f, e), flush=True); skipped += 1; continue
                if not t or len(t.strip()) < 80:
                    skipped += 1; continue
                ti = title_of(p, t)
                old = c.execute("SELECT id FROM docs WHERE path=?", (p,)).fetchone()
                if old:
                    c.execute("DELETE FROM chunks WHERE doc=?", (old[0],)); c.execute("DELETE FROM docs WHERE id=?", (old[0],)); changed += 1
                else:
                    new += 1
                cur = c.execute("INSERT INTO docs(path,title,mtime,words) VALUES(?,?,?,?)", (p, ti, m, len(t.split())))
                c.executemany("INSERT INTO chunks(title,body,doc) VALUES(?,?,?)", [(ti, ch, cur.lastrowid) for ch in chunks(t)])
                if (new + changed) % 200 == 0:
                    c.commit(); print("  %d files read" % (new + changed), flush=True)
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    print("Done in %.1f min: %d new, %d changed, %d skipped (empty, unreadable or PDF without pypdf). %d documents in the library."
          % ((time.time() - t0) / 60, new, changed, skipped, total), flush=True)


def fts_query(q):
    words = re.findall(r"\w+", q, re.U)
    return " OR ".join('"%s"' % w for w in words if len(w) > 1) or '""'


def search(q, limit=10):
    if not os.path.exists(DB):
        return []
    c = db()
    rows = c.execute("SELECT d.path, d.title, snippet(chunks, 1, '[', ']', ' ... ', 40), chunks.body, bm25(chunks) "
                     "FROM chunks JOIN docs d ON d.id = chunks.doc WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
                     (fts_query(q), int(limit) * 3)).fetchall()
    res, per = [], {}
    for p, ti, sn, body, sc in rows:
        if per.get(p, 0) >= 2:
            continue
        per[p] = per.get(p, 0) + 1
        res.append({"path": p, "title": ti, "snippet": sn, "text": body, "score": round(-sc, 2)})
        if len(res) >= int(limit):
            break
    return res


def status():
    if not os.path.exists(DB):
        return {"documents": 0, "passages": 0, "words": 0}
    c = db()
    d, w = c.execute("SELECT COUNT(*), COALESCE(SUM(words),0) FROM docs").fetchone()
    return {"documents": d, "passages": c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], "words": w,
            "size_mb": round(os.path.getsize(DB) / 1e6, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "search", "status"])
    ap.add_argument("--dirs", default=""); ap.add_argument("--q", default=""); ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()
    if a.cmd == "index":
        index([x for x in re.split(r"[;\n]", a.dirs) if x.strip()])
    elif a.cmd == "search":
        out({"ok": True, "results": search(a.q, a.limit)})
    else:
        out({"ok": True, **status()})


if __name__ == "__main__":
    main()
