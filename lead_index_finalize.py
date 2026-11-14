"""
lead_index_finalize.py - verify the parquet shards already on disk and merge
them into a working database, without re-ingesting the source files.

  python lead_index_finalize.py verify     # check every shard, report, change nothing
  python lead_index_finalize.py build      # verify, then merge the healthy shards

Progress is printed for every stage: shard-by-shard during the merge (with ETA)
and bucket-by-bucket during the dedup, plus DuckDB's own progress bar for each
long-running statement.

Options:
  --memory-limit 8GB     DuckDB memory cap (default 6GB)
  --buckets N            dedup in N hash buckets (default 8). Higher = less
                         memory per pass but more scans. --buckets 1 = single pass.
  --skip-merge           reuse the raw_contacts table already in leads.duckdb
                         and go straight to the dedup stage
  --resume               skip shards already recorded in merged_shards
  --include-snapshots    also merge raw_contacts_<timestamp>.parquet backups
  --keep-batches         leave batch_*.parquet where they are
  --no-raw-rewrite       do not rewrite raw_contacts.parquet
"""
import os
import sys
import json
import time
import shutil
import argparse
from pathlib import Path
from datetime import datetime

try:
    import duckdb
except ImportError:
    print("Installing duckdb...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "duckdb"])
    import duckdb

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("LS_ROOT", HERE.parent))
IDX = ROOT / "LEAD_INDEX"
LEADS = IDX / "leads.parquet"
DB = IDX / "leads.duckdb"
LOG = IDX / "finalize.log"
REPORT = IDX / "verify_report.json"

SCHEMA = ["file_path", "row_number", "email", "name", "first_name", "last_name",
          "title", "company", "domain", "website", "phone", "country", "city",
          "industry", "technologies", "source_file"]

# columns carried into the deduped contacts table
CONTACT_COLS = ["email", "name", "first_name", "last_name", "title", "company",
                "domain", "website", "phone", "country", "city", "industry",
                "technologies", "source_file"]


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def hms(seconds):
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}h{m:02d}m{s:02d}s" if h else f"{m:d}m{s:02d}s"


def bar(done, total, width=28):
    filled = int(width * done / total) if total else width
    pct = 100.0 * done / total if total else 100.0
    return f"[{'#' * filled}{'.' * (width - filled)}] {pct:5.1f}%"


def sql_path(p):
    return str(p).replace("'", "''")


def connect(args, database=None):
    con = duckdb.connect(str(database) if database else ":memory:")
    con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
    (IDX / "_tmp").mkdir(parents=True, exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{sql_path(IDX / '_tmp')}'")
    con.execute("PRAGMA preserve_insertion_order=false")
    # DuckDB's own in-statement progress bar, so long statements are not silent
    try:
        con.execute("PRAGMA enable_progress_bar")
        con.execute("PRAGMA enable_print_progress_bar")
    except Exception:
        pass
    return con


def collect_shards(args):
    shards = []
    raw = IDX / "raw_contacts.parquet"
    if raw.exists():
        shards.append(raw)
    if args.include_snapshots:
        shards += sorted(IDX.glob("raw_contacts_*.parquet"))
    shards += sorted(IDX.glob("batch_*.parquet"))
    return shards


def verify(args):
    """Open every shard, check schema and row count. Returns (ok, overlaps)."""
    shards = collect_shards(args)
    if not shards:
        log(f"No parquet shards found in {IDX}")
        return [], []

    con = connect(args)
    ok, broken = [], []
    total_rows = 0
    file_path_sets = {}
    t0 = time.time()

    log(f"Verifying {len(shards)} shards in {IDX} ...")
    for i, p in enumerate(shards, 1):
        entry = {"file": p.name, "size_mb": round(p.stat().st_size / 1048576, 1)}
        try:
            cols = [r[0] for r in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{sql_path(p)}')").fetchall()]
            rows = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{sql_path(p)}')").fetchone()[0]
            missing = [c for c in SCHEMA if c not in cols]
            entry.update(rows=rows, missing_columns=missing,
                         extra_columns=[c for c in cols if c not in SCHEMA])

            if rows == 0:
                entry["status"] = "empty"
                broken.append(entry)
                log(f"  {bar(i, len(shards))} SKIP  {p.name}: 0 rows")
                continue
            if missing:
                entry["status"] = "schema_mismatch"
                broken.append(entry)
                log(f"  {bar(i, len(shards))} BAD   {p.name}: missing {missing}")
                continue

            src = con.execute(
                f"SELECT DISTINCT file_path FROM read_parquet('{sql_path(p)}')").fetchall()
            file_path_sets[p.name] = {r[0] for r in src}
            entry.update(status="ok", source_files=len(file_path_sets[p.name]))
            ok.append(entry)
            total_rows += rows
            log(f"  {bar(i, len(shards))} OK    {p.name}: {rows:,} rows, "
                f"{len(file_path_sets[p.name])} source files")

        except Exception as e:
            entry.update(status="unreadable", error=str(e).split("\n")[0])
            broken.append(entry)
            log(f"  {bar(i, len(shards))} BAD   {p.name}: {entry['error']}")

    seen, overlaps = {}, []
    for shard, paths in file_path_sets.items():
        for fp in paths:
            if fp in seen:
                overlaps.append({"source_file": fp, "shards": [seen[fp], shard]})
            else:
                seen[fp] = shard
    con.close()

    log("")
    log(f"Healthy shards: {len(ok)}  |  Problem shards: {len(broken)}  "
        f"({hms(time.time() - t0)})")
    log(f"Rows across healthy shards: {total_rows:,}")
    log(f"Distinct source files covered: {len(seen):,}")
    if overlaps:
        log(f"Overlapping source files: {len(overlaps):,} -> rows will be "
            "deduplicated on (file_path, row_number)")
    else:
        log("No overlap between shards -> straight concatenation, no dedup pass needed")

    REPORT.write_text(json.dumps({
        "generated": datetime.now().isoformat(timespec="seconds"),
        "index_dir": str(IDX),
        "healthy": ok, "problems": broken,
        "total_rows": total_rows,
        "distinct_source_files": len(seen),
        "overlapping_source_files": len(overlaps),
        "overlap_examples": overlaps[:20],
    }, indent=2), encoding="utf-8")
    log(f"Report written to {REPORT}")
    return ok, overlaps


def merge_shards(con, ok, overlaps, args):
    """Load the healthy shards into raw_contacts, one shard at a time."""
    cols = ", ".join(SCHEMA)
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS raw_contacts (
            file_path VARCHAR, row_number BIGINT, email VARCHAR, name VARCHAR,
            first_name VARCHAR, last_name VARCHAR, title VARCHAR, company VARCHAR,
            domain VARCHAR, website VARCHAR, phone VARCHAR, country VARCHAR,
            city VARCHAR, industry VARCHAR, technologies VARCHAR, source_file VARCHAR)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS merged_shards (
            shard VARCHAR, rows BIGINT, merged_at TIMESTAMP)
    """)

    done_shards = set()
    if args.resume:
        done_shards = {r[0] for r in con.execute("SELECT shard FROM merged_shards").fetchall()}
        if done_shards:
            log(f"  resuming: {len(done_shards)} shards already merged")

    todo = [e for e in ok if e["file"] not in done_shards]
    target_rows = sum(e["rows"] for e in todo)
    log(f"Merging {len(todo)} shards ({target_rows:,} rows) into {DB} ...")

    t0 = time.time()
    done_rows = 0
    for i, e in enumerate(todo, 1):
        p = IDX / e["file"]
        con.execute(f"""
            INSERT INTO raw_contacts ({cols})
            SELECT {cols} FROM read_parquet('{sql_path(p)}', union_by_name=true)
        """)
        con.execute("INSERT INTO merged_shards VALUES (?, ?, now())", [e["file"], e["rows"]])
        done_rows += e["rows"]
        elapsed = time.time() - t0
        eta = elapsed / done_rows * (target_rows - done_rows) if done_rows else 0
        log(f"  {bar(done_rows, target_rows)} {i}/{len(todo)} {e['file']}  "
            f"{done_rows:,}/{target_rows:,} rows  elapsed {hms(elapsed)}  ETA {hms(eta)}")

    if overlaps:
        log("  deduplicating rows on (file_path, row_number) ...")
        con.execute(f"""
            CREATE OR REPLACE TABLE raw_contacts AS
            SELECT * EXCLUDE (_rn) FROM (
                SELECT {cols},
                       ROW_NUMBER() OVER (PARTITION BY file_path, row_number) AS _rn
                FROM raw_contacts
            ) WHERE _rn = 1
        """)


def build_contacts(con, args):
    """Deduped contacts table, as a bucketed hash aggregation.

    One GROUP BY per hash bucket: bounded memory, and progress after each
    bucket instead of one silent multi-hour sort.
    """
    struct_fields = ", ".join(f"'{c}': {c}" for c in CONTACT_COLS)
    col_defs = ", ".join(f"{c} VARCHAR" for c in CONTACT_COLS)
    con.execute(f"CREATE OR REPLACE TABLE contacts ({col_defs}, source_count BIGINT)")

    n = max(1, args.buckets)
    log(f"Building deduped contacts table in {n} bucket(s) ...")
    t0 = time.time()
    total = 0

    for b in range(n):
        bucket_filter = "" if n == 1 else f"AND hash(dedup_key) % {n} = {b}"
        con.execute(f"""
            INSERT INTO contacts
            SELECT best.*, source_count FROM (
                SELECT
                    COUNT(*) AS source_count,
                    min_by({{{struct_fields}}},
                           (CASE WHEN NULLIF(email, '') IS NOT NULL THEN 0 ELSE 2 END) +
                           (CASE WHEN NULLIF(name, '')  IS NOT NULL THEN 0 ELSE 1 END)
                    ) AS best
                FROM (
                    SELECT *, COALESCE(NULLIF(email, ''), NULLIF(domain, '')) AS dedup_key
                    FROM raw_contacts
                )
                WHERE dedup_key IS NOT NULL {bucket_filter}
                GROUP BY dedup_key
            )
        """)
        total = con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        elapsed = time.time() - t0
        eta = elapsed / (b + 1) * (n - b - 1)
        log(f"  {bar(b + 1, n)} bucket {b + 1}/{n}  {total:,} contacts so far  "
            f"elapsed {hms(elapsed)}  ETA {hms(eta)}")
    return total


def build(args):
    if args.skip_merge:
        log("Skipping merge - reusing raw_contacts already in leads.duckdb")
        ok, overlaps = [], []
    else:
        ok, overlaps = verify(args)
        if not ok:
            log("Nothing healthy to merge - aborting.")
            return 1

    (IDX / "_tmp").mkdir(parents=True, exist_ok=True)
    con = connect(args, DB)

    if not args.skip_merge:
        merge_shards(con, ok, overlaps, args)

    raw_rows = con.execute("SELECT COUNT(*) FROM raw_contacts").fetchone()[0]
    log(f"raw_contacts: {raw_rows:,} rows")

    deduped = build_contacts(con, args)

    log(f"Exporting deduped contacts to {LEADS} ...")
    t0 = time.time()
    tmp_leads = IDX / "leads_new.parquet"
    con.execute(f"COPY (SELECT * FROM contacts) TO '{sql_path(tmp_leads)}' "
                f"(FORMAT parquet, COMPRESSION zstd)")
    log(f"  done ({hms(time.time() - t0)})")

    with_email = con.execute("SELECT COUNT(*) FROM contacts WHERE email IS NOT NULL").fetchone()[0]
    with_phone = con.execute("SELECT COUNT(*) FROM contacts WHERE phone IS NOT NULL").fetchone()[0]
    with_company = con.execute("SELECT COUNT(*) FROM contacts WHERE company IS NOT NULL").fetchone()[0]

    tmp_raw = IDX / "raw_contacts_new.parquet"
    if not args.no_raw_rewrite:
        log("Rewriting consolidated raw_contacts.parquet ...")
        t0 = time.time()
        con.execute(f"COPY (SELECT * FROM raw_contacts) TO '{sql_path(tmp_raw)}' "
                    f"(FORMAT parquet, COMPRESSION zstd)")
        log(f"  done ({hms(time.time() - t0)})")
    con.close()

    os.replace(tmp_leads, LEADS)
    if not args.no_raw_rewrite:
        os.replace(tmp_raw, IDX / "raw_contacts.parquet")

    if not args.keep_batches and not args.no_raw_rewrite and not args.skip_merge:
        merged_dir = IDX / "_merged"
        merged_dir.mkdir(exist_ok=True)
        moved = 0
        for e in ok:
            p = IDX / e["file"]
            if p.name.startswith("batch_") and p.exists():
                shutil.move(str(p), str(merged_dir / p.name))
                moved += 1
        log(f"Moved {moved} merged batch files to {merged_dir}")

    log("")
    log("=== Finalize complete ===")
    log(f"  Raw rows:          {raw_rows:,}")
    log(f"  Deduped contacts:  {deduped:,}")
    log(f"  With email:        {with_email:,}")
    log(f"  With phone:        {with_phone:,}")
    log(f"  With company:      {with_company:,}")
    log(f"  Database:          {DB}")
    log(f"  Export:            {LEADS}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "build"])
    ap.add_argument("--memory-limit", default="6GB")
    ap.add_argument("--buckets", type=int, default=8)
    ap.add_argument("--skip-merge", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--include-snapshots", action="store_true")
    ap.add_argument("--keep-batches", action="store_true")
    ap.add_argument("--no-raw-rewrite", action="store_true")
    args = ap.parse_args()

    if args.cmd == "verify":
        verify(args)
        return 0
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
