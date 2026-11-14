"""
lead_index_v3.py - simple, robust lead indexer with incremental processing

  python lead_index_v3.py scan              # scan folder for contact files
  python lead_index_v3.py build              # build the unified leads database (incremental)
  python lead_index_v3.py info               # show database stats
  python lead_index_v3.py search "keyword"  # search leads
  python lead_index_v3.py export leads.csv   # export all leads
"""
import os
import sys
import csv
import json
import glob
import time
import traceback
from pathlib import Path
from datetime import datetime

try:
    import duckdb
except ImportError:
    print("Installing duckdb...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "duckdb"])
    import duckdb

csv.field_size_limit(10 ** 9)

# Configuration
HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("LS_ROOT", HERE.parent))
IDX = ROOT / "LEAD_INDEX"
LEADS = IDX / "leads.parquet"
DB = IDX / "leads.duckdb"
LOG = IDX / "build.log"

# File patterns to scan
CONTACT_PATTERNS = [
    "**/*.csv",
    "**/*.csv.gz",
    "**/*.tsv",
    "**/*.json",
    "**/*.jsonl",
    "**/*.parquet",
]

# Skip these directories
SKIP_DIRS = {
    "__pycache__",
    "LEAD_STUDIO",
    "LEAD_INDEX",
    "_to_delete",
    "_TO_DELETE",
    "domain_output",
    "node_modules",
    ".git",
    "HF_DATASETS",
    "OSM_CACHE",
    "logs",
    "_build",
    "backup",
    ".pyc",
}

def log(msg):
    """Log to both console and file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    if LOG:
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except:
            pass

def error(msg):
    """Log error with traceback"""
    log(f"ERROR: {msg}")
    log(traceback.format_exc())

def scan_folder():
    """Scan the entire Modulario folder for contact files"""
    log(f"Scanning folder: {ROOT}")
    log(f"Skipping directories: {', '.join(SKIP_DIRS)}")
    
    files = []
    for pattern in CONTACT_PATTERNS:
        try:
            for file_path in ROOT.glob(pattern):
                # Skip directories
                if file_path.is_dir():
                    continue
                
                # Check if any parent directory is in SKIP_DIRS
                parts = file_path.relative_to(ROOT).parts
                if any(part in SKIP_DIRS for part in parts):
                    continue
                
                # Skip small files (< 100 bytes)
                if file_path.stat().st_size < 100:
                    continue
                
                files.append(file_path)
        except Exception as e:
            error(f"Error scanning pattern {pattern}: {e}")
    
    log(f"Found {len(files)} potential contact files")
    
    # Save file list
    os.makedirs(IDX, exist_ok=True)
    file_list = IDX / "files.json"
    with open(file_list, "w", encoding="utf-8") as f:
        json.dump([str(f.relative_to(ROOT)) for f in files], f, indent=1)
    
    log(f"File list saved to {file_list}")
    return files

def detect_format(file_path):
    """Detect file format based on extension"""
    suffix = file_path.suffix.lower()
    if suffix == ".gz":
        suffix = file_path.stem.split(".")[-1].lower()
    
    if suffix in [".csv", ".tsv"]:
        return "csv"
    elif suffix in [".json", ".jsonl"]:
        return "json"
    elif suffix == ".parquet":
        return "parquet"
    else:
        return "unknown"

def has_contact_columns(header):
    """Check if CSV has contact-related columns"""
    if not header:
        return False
    
    header_lower = [h.lower() for h in header]
    contact_keywords = [
        "email", "e-mail", "mail", "contact", "name", "first", "last",
        "company", "domain", "website", "phone", "phone", "fax"
    ]
    
    return any(keyword in " ".join(header_lower) for keyword in contact_keywords)

def build_select_parts(columns, rel_path):
    """Map a source file's actual columns onto the raw_contacts schema.

    Every target column is always present in the returned SELECT list; a
    column the source file does not have becomes NULL instead of raising
    a DuckDB binder error.
    """
    def pick(*names):
        wanted = [n.lower() for n in names]
        return next((c for c in columns if c.lower() in wanted), None)

    def q(col):
        return '"' + str(col).replace('"', '""') + '"'

    email_col    = pick("email", "e-mail", "e_mail", "mail")
    name_col     = pick("name", "full_name", "contact_name")
    first_col    = pick("first_name", "first", "fname")
    last_col     = pick("last_name", "last", "lname")
    title_col    = pick("title", "job_title", "position")
    company_col  = pick("company", "company_name", "organization")
    domain_col   = pick("domain", "website", "url")
    website_col  = pick("website", "url", "web")
    phone_col    = pick("phone", "phone_number", "telephone", "tel")
    country_col  = pick("country", "country_code")
    city_col     = pick("city", "location")
    industry_col = pick("industry", "sector", "category")
    tech_col     = pick("technology", "technologies", "tech")

    safe_rel = str(rel_path).replace("'", "''")
    parts = [f"'{safe_rel}' as file_path", "row_number() OVER () as row_number"]

    if email_col:
        parts.append(f"NULLIF(CAST({q(email_col)} AS VARCHAR), '') as email")
    else:
        parts.append("CAST(NULL AS VARCHAR) as email")

    if name_col:
        parts.append(f"NULLIF(CAST({q(name_col)} AS VARCHAR), '') as name")
    elif first_col or last_col:
        first_expr = f"COALESCE(CAST({q(first_col)} AS VARCHAR), '')" if first_col else "''"
        last_expr = f"COALESCE(CAST({q(last_col)} AS VARCHAR), '')" if last_col else "''"
        parts.append(f"NULLIF(TRIM({first_expr} || ' ' || {last_expr}), '') as name")
    else:
        parts.append("CAST(NULL AS VARCHAR) as name")

    for target, col in [
        ("first_name", first_col), ("last_name", last_col), ("title", title_col),
        ("company", company_col), ("domain", domain_col), ("website", website_col),
        ("phone", phone_col), ("country", country_col), ("city", city_col),
        ("industry", industry_col), ("technologies", tech_col),
    ]:
        if col:
            parts.append(f"NULLIF(CAST({q(col)} AS VARCHAR), '') as {target}")
        else:
            parts.append(f"CAST(NULL AS VARCHAR) as {target}")

    parts.append(f"'{safe_rel}' as source_file")
    return ", ".join(parts)


def build_index():
    """Build the unified leads database incrementally"""
    log("Starting lead index build (incremental)...")
    os.makedirs(IDX, exist_ok=True)
    
    # Load file list
    file_list_path = IDX / "files.json"
    if not file_list_path.exists():
        log("No file list found. Running scan first...")
        files = scan_folder()
    else:
        with open(file_list_path, encoding="utf-8") as f:
            file_paths = json.load(f)
        files = [ROOT / p for p in file_paths]
        log(f"Loaded {len(files)} files from previous scan")
    
    # Initialize database (use in-memory for speed, export to file at end)
    log("Initializing DuckDB (in-memory)...")
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET memory_limit='8GB'")
    
    # Create tables
    log("Creating tables...")
    con.execute("""
        CREATE TABLE IF NOT EXISTS raw_contacts (
            file_path VARCHAR,
            row_number BIGINT,
            email VARCHAR,
            name VARCHAR,
            first_name VARCHAR,
            last_name VARCHAR,
            title VARCHAR,
            company VARCHAR,
            domain VARCHAR,
            website VARCHAR,
            phone VARCHAR,
            country VARCHAR,
            city VARCHAR,
            industry VARCHAR,
            technologies VARCHAR,
            source_file VARCHAR
        )
    """)
    
    # Load existing raw_contacts if available
    raw_file = IDX / "raw_contacts.parquet"
    if raw_file.exists():
        log(f"Loading existing data from {raw_file}...")
        try:
            con.execute(f"INSERT INTO raw_contacts SELECT * FROM read_parquet('{raw_file}')")
            existing_count = con.execute("SELECT COUNT(*) FROM raw_contacts").fetchone()[0]
            log(f"Loaded {existing_count:,} existing rows")
            
            # Get list of already processed files
            processed_files = set(con.execute("SELECT DISTINCT file_path FROM raw_contacts").fetchall())
            processed_files = {row[0] for row in processed_files}
            log(f"Found {len(processed_files)} already processed files")
        except Exception as e:
            error(f"Failed to load existing data: {e}")
            processed_files = set()
    else:
        processed_files = set()
    
    # Filter out already processed files
    files_to_process = [f for f in files if str(f.relative_to(ROOT)) not in processed_files]
    log(f"Files to process: {len(files_to_process)} (skipped {len(files) - len(files_to_process)} already done)")
    
    # Process files in batches
    BATCH_SIZE = 50  # Reduced from 100 to avoid memory issues
    total_processed = 0
    errors = 0
    
    log(f"Processing {len(files_to_process)} files in batches of {BATCH_SIZE}...")
    
    for batch_start in range(0, len(files_to_process), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(files_to_process))
        batch_files = files_to_process[batch_start:batch_end]
        
        log(f"\n=== Batch {batch_start//BATCH_SIZE + 1}/{(len(files_to_process) + BATCH_SIZE - 1)//BATCH_SIZE} "
            f"(files {batch_start+1}-{batch_end}) ===")
        
        batch_files_set = {str(f.relative_to(ROOT)) for f in batch_files}
        batch_processed = 0
        batch_errors = 0
        
        for file_path in batch_files:
            rel_path = str(file_path.relative_to(ROOT))
            
            try:
                # Detect format
                fmt = detect_format(file_path)
                
                if fmt == "csv":
                    # Try to load CSV
                    try:
                        # Read first row to check columns
                        if file_path.suffix == ".gz":
                            import gzip
                            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as f:
                                reader = csv.reader(f)
                                header = next(reader, None)
                        else:
                            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                                reader = csv.reader(f)
                                header = next(reader, None)
                        
                        if not header or not has_contact_columns(header):
                            log(f"  Skip {rel_path}: no contact columns")
                            continue
                        
                        # Load CSV with DuckDB - use dynamic column detection
                        log(f"  Loading CSV: {rel_path}")
                        
                        # Get the actual columns from the CSV
                        con.execute(f"CREATE OR REPLACE TEMP TABLE temp_csv AS SELECT * FROM read_csv('{file_path}', header=true, all_varchar=true, ignore_errors=true, null_padding=true, parallel=false)")
                        columns = [row[0] for row in con.execute("DESCRIBE SELECT * FROM temp_csv").fetchall()]
                        
                        select_sql = build_select_parts(columns, rel_path)
                        
                        con.execute(f"""
                            INSERT INTO raw_contacts (file_path, row_number, email, name, first_name, last_name, 
                            title, company, domain, website, phone, country, city, industry, technologies, source_file)
                            SELECT {select_sql} FROM temp_csv
                        """)
                        con.execute("DROP TABLE temp_csv")
                        
                        rows = con.execute("SELECT COUNT(*) FROM raw_contacts WHERE file_path = ?", [rel_path]).fetchone()[0]
                        batch_processed += rows
                        log(f"    -> {rows} rows")
                    
                    except Exception as e:
                        error(f"  CSV error {rel_path}: {e}")
                        batch_errors += 1
                
                elif fmt == "parquet":
                    try:
                        log(f"  Loading Parquet: {rel_path}")
                        safe_file = str(file_path).replace("'", "''")

                        # Read the file's real schema first - many parquet files
                        # in the tree have no email/name columns at all.
                        columns = [row[0] for row in con.execute(
                            f"DESCRIBE SELECT * FROM read_parquet('{safe_file}')"
                        ).fetchall()]

                        if not has_contact_columns(columns):
                            log(f"  Skip {rel_path}: no contact columns")
                            continue

                        select_sql = build_select_parts(columns, rel_path)

                        con.execute(f"""
                            INSERT INTO raw_contacts (file_path, row_number, email, name, first_name, last_name,
                            title, company, domain, website, phone, country, city, industry, technologies, source_file)
                            SELECT {select_sql} FROM read_parquet('{safe_file}')
                        """)
                        rows = con.execute("SELECT COUNT(*) FROM raw_contacts WHERE file_path = ?", [rel_path]).fetchone()[0]
                        batch_processed += rows
                        log(f"    -> {rows} rows")

                    except Exception as e:
                        error(f"  Parquet error {rel_path}: {e}")
                        batch_errors += 1
                
                else:
                    log(f"  Skip {rel_path}: unsupported format {fmt}")
            
            except Exception as e:
                error(f"  Unexpected error {rel_path}: {e}")
                batch_errors += 1
        
        total_processed += batch_processed
        errors += batch_errors
        
        log(f"\nBatch complete: {batch_processed} rows, {batch_errors} errors")
        log(f"Total so far: {total_processed:,} rows")
        
        # Save only the current batch to a separate file (much faster)
        if batch_processed > 0:
            batch_num = batch_start // BATCH_SIZE + 1
            batch_file = IDX / f"batch_{batch_num:04d}.parquet"
            log(f"Saving batch {batch_num} to {batch_file.name}...")
            try:
                # Build WHERE clause for batch files
                file_list = ", ".join([f"'{f}'" for f in batch_files_set])
                con.execute(f"""
                    COPY (
                        SELECT * FROM raw_contacts 
                        WHERE file_path IN ({file_list})
                    ) TO '{batch_file}' (FORMAT parquet, COMPRESSION zstd)
                """)
                log(f"  Saved batch {batch_num} ({batch_processed:,} rows)")
            except Exception as e:
                error(f"Failed to save batch {batch_num}: {e}")
        
        # Clear memory periodically
        con.execute("VACUUM")
        
        # Check if we should stop (e.g., for testing)
        if os.environ.get("LS_LIMIT") and batch_start // BATCH_SIZE + 1 >= int(os.environ.get("LS_LIMIT")):
            log(f"Reached batch limit ({os.environ.get('LS_LIMIT')}), stopping early")
            break
    
    # Create deduped contacts table
    log("\nCreating deduped contacts table...")
    con.execute("""
        CREATE OR REPLACE TABLE contacts AS
        WITH keyed AS (
            SELECT *, COALESCE(NULLIF(email, ''), NULLIF(domain, '')) AS dedup_key
            FROM raw_contacts
            WHERE NULLIF(email, '') IS NOT NULL OR NULLIF(domain, '') IS NOT NULL
        ),
        counted AS (
            SELECT *, COUNT(*) OVER (PARTITION BY dedup_key) AS source_count
            FROM keyed
        )
        SELECT DISTINCT ON (dedup_key)
            email,
            name,
            first_name,
            last_name,
            title,
            company,
            domain,
            website,
            phone,
            country,
            city,
            industry,
            technologies,
            source_file,
            source_count
        FROM counted
        ORDER BY
            dedup_key,
            CASE WHEN NULLIF(email, '') IS NOT NULL THEN 0 ELSE 1 END,
            CASE WHEN NULLIF(name, '') IS NOT NULL THEN 0 ELSE 1 END,
            source_count DESC
    """)
    
    # Export final results - combine all batch files
    log(f"Exporting to {LEADS}...")
    
    # Get all batch files
    batch_files = sorted(IDX.glob("batch_*.parquet"))
    if batch_files:
        log(f"Combining {len(batch_files)} batch files...")
        # Combine all batch files plus existing data
        batch_list = ", ".join([f"'{f}'" for f in batch_files])
        con.execute(f"""
            COPY (
                SELECT * FROM read_parquet('{IDX}/raw_contacts.parquet')
                UNION ALL
                SELECT * FROM read_parquet([{batch_list}])
            ) TO '{LEADS}' (FORMAT parquet, COMPRESSION zstd)
        """)
    else:
        # No batch files, just export existing data
        con.execute(f"COPY (SELECT * FROM raw_contacts) TO '{LEADS}' (FORMAT parquet, COMPRESSION zstd)")
    
    # Stats
    total_raw = con.execute("SELECT COUNT(*) FROM raw_contacts").fetchone()[0]
    total_deduped = con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    with_email = con.execute("SELECT COUNT(*) FROM contacts WHERE email IS NOT NULL").fetchone()[0]
    with_phone = con.execute("SELECT COUNT(*) FROM contacts WHERE phone IS NOT NULL").fetchone()[0]
    
    log(f"\n=== Build Complete ===")
    log(f"  Raw rows: {total_raw:,}")
    log(f"  Deduped contacts: {total_deduped:,}")
    log(f"  With email: {with_email:,}")
    log(f"  With phone: {with_phone:,}")
    log(f"  Errors: {errors}")
    log(f"  Database: {DB}")
    log(f"  Export: {LEADS}")
    
    con.close()

def show_info():
    """Show database statistics"""
    if not DB.exists() and not LEADS.exists():
        print(json.dumps({"error": "no_database"}))
        return
    
    con = duckdb.connect()
    
    try:
        # Check if we have raw_contacts.parquet
        raw_file = IDX / "raw_contacts.parquet"
        if raw_file.exists():
            total_raw = con.execute(f"SELECT COUNT(*) FROM read_parquet('{raw_file}')").fetchone()[0]
        else:
            total_raw = 0
        
        total_deduped = 0
        with_email = 0
        with_phone = 0
        with_company = 0
        
        if LEADS.exists():
            total_deduped = con.execute(f"SELECT COUNT(*) FROM read_parquet('{LEADS}')").fetchone()[0]
            with_email = con.execute(f"SELECT COUNT(*) FROM read_parquet('{LEADS}') WHERE email IS NOT NULL").fetchone()[0]
            with_phone = con.execute(f"SELECT COUNT(*) FROM read_parquet('{LEADS}') WHERE phone IS NOT NULL").fetchone()[0]
            with_company = con.execute(f"SELECT COUNT(*) FROM read_parquet('{LEADS}') WHERE company IS NOT NULL").fetchone()[0]
        
        info = {
            "raw_contacts": total_raw,
            "deduped_contacts": total_deduped,
            "with_email": with_email,
            "with_phone": with_phone,
            "with_company": with_company,
            "database": str(DB) if DB.exists() else None,
            "export": str(LEADS) if LEADS.exists() else None
        }
        
        print(json.dumps(info, indent=2))
    finally:
        con.close()

def search(query):
    """Search leads"""
    if not LEADS.exists():
        print(json.dumps({"error": "no_leads_file"}))
        return
    
    con = duckdb.connect()
    
    try:
        # Simple text search across key fields
        results = con.execute("""
            SELECT email, name, company, domain, phone, country, city, industry
            FROM read_parquet(?)
            WHERE 
                email ILIKE ? OR
                name ILIKE ? OR
                company ILIKE ? OR
                domain ILIKE ? OR
                industry ILIKE ?
            LIMIT 100
        """, [str(LEADS), f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"]).fetchall()
        
        cols = ["email", "name", "company", "domain", "phone", "country", "city", "industry"]
        print(json.dumps({
            "query": query,
            "count": len(results),
            "results": [dict(zip(cols, r)) for r in results]
        }, indent=2))
    finally:
        con.close()

def export(output_path):
    """Export all contacts to CSV"""
    if not LEADS.exists():
        print(json.dumps({"error": "no_leads_file"}))
        return
    
    con = duckdb.connect()
    
    try:
        total = con.execute(f"SELECT COUNT(*) FROM read_parquet('{LEADS}')").fetchone()[0]
        log(f"Exporting {total:,} contacts to {output_path}...")
        
        con.execute(f"COPY (SELECT * FROM read_parquet('{LEADS}')) TO '{output_path}' (HEADER, DELIMITER ',')")
        
        log(f"Export complete: {output_path}")
        print(json.dumps({"status": "done", "output": str(output_path), "rows": total}))
    finally:
        con.close()

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "build", "info", "search", "export"])
    ap.add_argument("--query", help="search query")
    ap.add_argument("--output", help="output file for export")
    args = ap.parse_args()
    
    if args.cmd == "scan":
        scan_folder()
    elif args.cmd == "build":
        build_index()
    elif args.cmd == "info":
        show_info()
    elif args.cmd == "search":
        if not args.query:
            print("Error: --query required for search")
            sys.exit(1)
        search(args.query)
    elif args.cmd == "export":
        if not args.output:
            print("Error: --output required for export")
            sys.exit(1)
        export(args.output)

if __name__ == "__main__":
    main()
