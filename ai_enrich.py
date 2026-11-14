"""
ai_enrich.py - add one AI-written column to a lead file, row by row, through Ollama Cloud.

Started by Lead Studio (AI enrich page) or by hand:
  python ai_enrich.py --spec logs\\enrich_20261112_101500.json

The spec holds: input, output, columns, instruction, new_column, limit, model.
The API key comes from the OLLAMA_API_KEY environment variable or lead_studio_settings.json.

Resumable: rows already in the output file are skipped, so run it again to continue.
One request at a time - the free Ollama tier runs a single cloud model at once.
The rows you enrich are sent to Ollama's servers.
"""
import os, sys, re, csv, json, gzip, time, argparse, urllib.request, urllib.error

csv.field_size_limit(10 ** 9)
HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM = ("You enrich business lead records for a sales researcher. You get one record and one task. "
          "Reply with only the value for the new field: one line, no preamble, no quotes, no explanation "
          "unless the task asks for one. If the record does not contain enough to answer, reply: unknown")


def settings():
    s = {"ollama_key": "", "ollama_base": "https://ollama.com", "ollama_model": ""}
    try:
        with open(os.path.join(HERE, "lead_studio_settings.json"), encoding="utf-8") as fh:
            s.update(json.load(fh))
    except (OSError, ValueError):
        pass
    s["ollama_key"] = (os.environ.get("OLLAMA_API_KEY") or s["ollama_key"] or "").strip()
    return s


def open_text(p):
    if p.lower().endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8-sig", errors="replace", newline="")
    return open(p, encoding="utf-8-sig", errors="replace", newline="")


def clean(text):
    text = re.sub(r"<think>.*?</think>", " ", text or "", flags=re.S | re.I)
    return " ".join(text.split()).strip(' "\'')[:600]


class Stop(Exception):
    pass


def ask(base, key, model, prompt):
    body = json.dumps({"model": model, "stream": False, "options": {"temperature": 0.2},
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}).encode("utf-8")
    for attempt in range(7):
        req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            return clean((data.get("message") or {}).get("content") or data.get("response") or "")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            if e.code in (401, 403):
                raise Stop("Ollama rejected the API key (HTTP %d). Save a valid key under Connections." % e.code)
            if e.code == 404:
                raise Stop("Ollama does not know the model '%s'. Pick another one under Connections. %s" % (model, detail))
            if e.code in (429, 500, 502, 503, 504):
                wait = min(300, 20 * 2 ** attempt)
                print("   Ollama is busy or the limit was reached (HTTP %d) - waiting %ds" % (e.code, wait), flush=True)
                time.sleep(wait); continue
            raise Stop("Ollama answered HTTP %d: %s" % (e.code, detail))
        except Exception as e:
            print("   network problem (%s) - retrying in 20s" % type(e).__name__, flush=True); time.sleep(20)
    raise Stop("Ollama kept refusing. The free limit for this session or week is probably used up - run again later, it resumes.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    s = settings()
    key, base, model = s["ollama_key"], spec.get("base") or s["ollama_base"], spec.get("model") or s["ollama_model"]
    if not key:
        sys.exit("No Ollama API key. Save one in Lead Studio under Connections.")
    src, out, new = spec["input"], spec["output"], spec["new_column"]
    limit = int(spec.get("limit") or 50)

    done = 0
    if os.path.exists(out):
        with open_text(out) as fh:
            done = max(0, sum(1 for _ in csv.reader(fh)) - 1)
    print("input : %s\noutput: %s\nmodel : %s\nnew column: %s   rows this run: up to %d   already done: %d\n"
          % (src, out, model, new, limit, done), flush=True)

    with open_text(src) as fin:
        rd = csv.reader(fin, delimiter="\t" if src.lower().endswith(".tsv") else ",")
        cols = next(rd, [])
        use = [c for c in (spec.get("columns") or cols) if c in cols] or cols
        idx = [cols.index(c) for c in use]
        mode = "a" if done else "w"
        with open(out, mode, newline="", encoding="utf-8-sig" if not done else "utf-8") as fout:
            w = csv.writer(fout)
            if not done:
                w.writerow(cols + [new])
            n = unknown = 0; t0 = time.time()
            try:
                for i, row in enumerate(rd):
                    if i < done:
                        continue
                    if n >= limit:
                        break
                    row = row + [""] * (len(cols) - len(row))
                    record = "\n".join("%s: %s" % (c, row[j][:1500]) for c, j in zip(use, idx) if row[j].strip())
                    if record:
                        answer = ask(base, key, model, "Task: %s\n\nRecord:\n%s" % (spec["instruction"], record))
                    else:
                        answer = "unknown"
                    unknown += answer.lower() == "unknown"
                    w.writerow(row[:len(cols)] + [answer]); fout.flush(); n += 1
                    if n <= 5 or n % 10 == 0:
                        rate = n / max(1e-6, time.time() - t0) * 60
                        print("%5d  %-40s -> %s   (%.0f rows/min)" % (done + n, (row[idx[0]] if idx else "")[:40], answer[:90], rate), flush=True)
            except Stop as e:
                print("\nSTOPPED after %d rows: %s" % (n, e), flush=True); sys.exit(2)
    print("\nDone. %d rows written this run (%d answered 'unknown'). File: %s" % (n, unknown, out), flush=True)


if __name__ == "__main__":
    main()
