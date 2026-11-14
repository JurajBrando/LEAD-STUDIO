"""
split_files.py - cut a big CSV into pieces small enough for whatever has to swallow them.

Your verifier takes files of at most 10,000 rows and under 10 MB, 20 files a day.
That is the default here, so the plain command already does the right thing.

  python split_files.py rows   --in big.csv [--rows 10000] [--mb 9.5] [--out FOLDER]
        Splits by row count AND size at once - a part is closed when it hits either limit,
        so no part is ever too long or too heavy. The header is repeated in every part.

  python split_files.py size   --in big.csv [--mb 9.5]        size only
  python split_files.py day    --in big.csv [--files 20]      only as many parts as one day allows
  python split_files.py bundle --in FOLDER  [--mb 500]        zip a folder's files into bundles
  python split_files.py plan   --in big.csv                   how many parts and days, without writing

Parts land in SPLIT/<name>/ beside the original unless you pass --out.
"""
import os, re, sys, csv, glob, json, math, zipfile, argparse

csv.field_size_limit(10 ** 9)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(HERE))
VERIFIER_ROWS, VERIFIER_MB, VERIFIER_FILES = 10000, 9.5, 20


def say(o):
    print(json.dumps(o, ensure_ascii=False, indent=1), flush=True)
    sys.exit(0 if o.get("ok", True) else 1)


def resolve(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def rel(p):
    try:
        return os.path.relpath(p, ROOT).replace(os.sep, "/")
    except ValueError:
        return p


def out_folder(src, given):
    if given:
        d = resolve(given)
    else:
        d = os.path.join(os.path.dirname(src), "SPLIT",
                         re.sub(r"\W+", "_", os.path.splitext(os.path.basename(src))[0])[:60])
    os.makedirs(d, exist_ok=True)
    return d


def split_csv(src, rows_cap, mb_cap, out, max_files=0):
    """One pass, two limits. A part closes on whichever it reaches first."""
    size_cap = int(mb_cap * 1024 * 1024) if mb_cap else 0
    stem = os.path.splitext(os.path.basename(src))[0]
    parts, n, total = [], 0, 0
    fh_out = writer = None
    path = ""
    rows_here = bytes_here = 0

    def open_part():
        nonlocal fh_out, writer, path, n, rows_here, bytes_here
        n += 1
        path = os.path.join(out, "%s_part_%03d.csv" % (stem, n))
        fh_out = open(path, "w", encoding="utf-8-sig", newline="")
        writer = csv.writer(fh_out)
        writer.writerow(header)
        fh_out.flush()
        rows_here, bytes_here = 0, fh_out.tell()

    def close_part():
        if fh_out:
            fh_out.flush()
            size = fh_out.tell()
            fh_out.close()
            parts.append({"file": rel(path), "rows": rows_here, "mb": round(size / 1048576, 2)})

    with open(src, encoding="utf-8-sig", errors="replace", newline="") as fh:
        rd = csv.reader(fh)
        try:
            header = next(rd)
        except StopIteration:
            return [], 0
        open_part()
        for row in rd:
            writer.writerow(row)
            rows_here += 1
            total += 1
            # tell() after a write is exact and costs nothing next to the write itself
            bytes_here = fh_out.tell()
            full = (rows_cap and rows_here >= rows_cap) or (size_cap and bytes_here >= size_cap)
            if full:
                close_part()
                if max_files and len(parts) >= max_files:
                    fh_out = None
                    return parts, total
                open_part()
        close_part()
    if parts and parts[-1]["rows"] == 0:
        os.remove(resolve(parts[-1]["file"]))
        parts.pop()
    return parts, total


def count_rows(src):
    with open(src, encoding="utf-8-sig", errors="replace", newline="") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def step_split(kind, inp, rows_cap, mb_cap, out, max_files):
    src = resolve(inp or "")
    if not inp or not os.path.exists(src):
        say({"ok": False, "error": "There is no file at %s" % (src or "(nothing given)")})
    if os.path.isdir(src):
        say({"ok": False, "error": "That is a folder. Use: split_files.py bundle --in <folder>"})
    d = out_folder(src, out)
    parts, total = split_csv(src, rows_cap, mb_cap, d, max_files)
    if not parts:
        say({"ok": False, "error": "That file has no rows under its header."})
    written = sum(p["rows"] for p in parts)
    # when we stopped at the day's limit, the source still holds rows we never read
    in_source = count_rows(src) if max_files and len(parts) >= max_files else total
    left = max(0, in_source - written)
    return say({"ok": True, "source": rel(src), "rows_in_source": in_source, "parts": len(parts),
                "rows_written": written, "rows_left_over": left,
                "row_limit": rows_cap or None, "size_limit_mb": mb_cap or None,
                "folder": rel(d), "files": parts[:40],
                "days_at_%d_a_day" % VERIFIER_FILES: math.ceil(len(parts) / VERIFIER_FILES),
                "note": "Every part keeps the header row." if not left else
                        "Stopped at %d parts; %s rows are still in the source. Run again tomorrow with "
                        "--out somewhere new." % (max_files, "{:,}".format(left))})


def step_plan(inp, rows_cap, mb_cap):
    src = resolve(inp or "")
    if not inp or not os.path.exists(src):
        say({"ok": False, "error": "There is no file at %s" % (src or "(nothing given)")})
    size = os.path.getsize(src)
    rows = count_rows(src)
    by_rows = math.ceil(rows / rows_cap) if rows_cap else 1
    by_size = math.ceil(size / (mb_cap * 1048576)) if mb_cap else 1
    parts = max(1, by_rows, by_size)
    return say({"ok": True, "source": rel(src), "rows": rows, "mb": round(size / 1048576, 2),
                "parts_needed": parts, "limited_by": "rows" if by_rows >= by_size else "size",
                "days_at_%d_a_day" % VERIFIER_FILES: math.ceil(parts / VERIFIER_FILES),
                "note": "Nothing was written - this only counts."})


def step_bundle(inp, mb_cap, out):
    """Zip a folder's files into bundles, none bigger than the limit. Big files go alone."""
    d = resolve(inp or "")
    if not inp or not os.path.isdir(d):
        say({"ok": False, "error": "There is no folder at %s" % (d or "(nothing given)")})
    cap = int((mb_cap or 500) * 1048576)
    files = sorted((os.path.join(d, f) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))),
                   key=os.path.getsize, reverse=True)
    if not files:
        say({"ok": False, "error": "That folder has no files directly in it."})
    dest = resolve(out) if out else os.path.join(d, "_bundled")
    os.makedirs(dest, exist_ok=True)
    bundles, cur, cur_size = [], [], 0
    for f in files:
        s = os.path.getsize(f)
        if s >= cap:
            bundles.append([f])
            continue
        if cur_size + s > cap and cur:
            bundles.append(cur); cur, cur_size = [], 0
        cur.append(f); cur_size += s
    if cur:
        bundles.append(cur)
    made = []
    for i, group in enumerate(bundles, 1):
        zp = os.path.join(dest, "bundle_%03d.zip" % i)
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in group:
                z.write(f, os.path.basename(f))
        made.append({"file": rel(zp), "files_inside": len(group),
                     "mb": round(os.path.getsize(zp) / 1048576, 2)})
    return say({"ok": True, "folder": rel(d), "bundles": len(made), "into": rel(dest), "files": made[:40]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["rows", "size", "day", "bundle", "plan"])
    ap.add_argument("--in", dest="inp", default="")
    ap.add_argument("--rows", type=int, default=VERIFIER_ROWS)
    ap.add_argument("--mb", type=float, default=VERIFIER_MB)
    ap.add_argument("--files", type=int, default=VERIFIER_FILES)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.cmd == "plan":
        step_plan(a.inp, a.rows, a.mb)
    elif a.cmd == "bundle":
        step_bundle(a.inp, a.mb if a.mb != VERIFIER_MB else 500, a.out)
    elif a.cmd == "size":
        step_split("size", a.inp, 0, a.mb, a.out, 0)
    elif a.cmd == "day":
        step_split("day", a.inp, a.rows, a.mb, a.out, max(1, a.files))
    else:
        step_split("rows", a.inp, a.rows, a.mb, a.out, 0)


if __name__ == "__main__":
    main()
