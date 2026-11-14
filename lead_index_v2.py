"""
lead_index_v2.py - rewritten lead indexer with better error handling and progress reporting

  python lead_index_v2.py scan              # scan folder for contact files
  python lead_index_v2.py build              # build the unified leads database
  python lead_index_v2.py info               # show database stats
  python lead_index_v2.py search "keyword"  # search leads
  python lead_index_v2.py export leads.csv   # export all leads
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
    print(line)
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

def read_csv_sample(file_path, max_rows=10):
    """Read sample rows from CSV to detect columns"""
    try:
        if file_path.suffix == ".gz":
            import gzip
            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                rows = [next(reader, None) for _ in range(max_rows)]
        else:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                rows = [next(reader, None) for _ in range(max_rows)]
        return header, rows
    except Exception as e:
        error(f"Error reading CSV sample from {file_path}: {e}")
        return None, None

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

def build_index():
    """Build the unified leads database"""
    log("Starting lead index build...")
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
    
    # Initialize database
    log("Initializing DuckDB database...")
    con = duckdb.connect(str(DB))
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
            source_file VARCHAR,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    con.execute("""
        CREATE TABLE IF NOT EXISTS build_status (
            file_path VARCHAR PRIMARY KEY,
            status VARCHAR,
            rows_processed BIGINT,
            error_message VARCHAR,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Process files
    log(f"Processing {len(files)} files...")
    total_processed = 0
    errors = 0
    
    for i, file_path in enumerate(files, 1):
        if i % 100 == 0:
            log(f"Progress: {i}/{len(files)} files ({total_processed} rows processed)")
        
        # Check if already processed
        rel_path = str(file_path.relative_to(ROOT))
        existing = con.execute(
            "SELECT status FROM build_status WHERE file_path = ?",
            [rel_path]
        ).fetchone()
        
        if existing and existing[0] == "done":
            continue
        
        try:
            # Detect format
            fmt = detect_format(file_path)
            
            if fmt == "csv":
                # Read CSV with DuckDB
                header, sample = read_csv_sample(file_path)
                if not header or not has_contact_columns(header):
                    log(f"Skipping {rel_path}: no contact columns")
                    con.execute("""
                        INSERT INTO build_status (file_path, status, rows_processed, error_message)
                        VALUES (?, 'skipped', 0, 'no contact columns')
                    """, [rel_path])
                    continue
                
                # Load CSV
                log(f"Loading CSV: {rel_path}")
                try:
                    con.execute(f"""
                        INSERT INTO raw_contacts (file_path, row_number, email, name, first_name, last_name, 
                        title, company, domain, website, phone, country, city, industry, technologies, source_file)
                        SELECT 
                            '{rel_path}' as file_path,
                            row_number() OVER () as row_number,
                            COALESCE(NULLIF(email, ''), NULLIF(e_mail, ''), NULLIF(e_mail_1, '')) as email,
                            COALESCE(NULLIF(name, ''), CONCAT(NULLIF(first_name, ''), ' ', NULLIF(last_name, ''))) as name,
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
                            '{rel_path}' as source_file
                        FROM read_csv('{file_path}', header=true, all_varchar=true, ignore_errors=true, null_padding=true)
                    """)
                    rows = con.execute("SELECT COUNT(*) FROM raw_contacts WHERE file_path = ?", [rel_path]).fetchone()[0]
                    con.execute("""
                        INSERT INTO build_status (file_path, status, rows_processed)
                        VALUES (?, 'done', ?)
                        ON CONFLICT (file_path) DO UPDATE SET status='done', rows_processed=?, last_updated=CURRENT_TIMESTAMP
                    """, [rel_path, rows, rows])
                    total_processed += rows
                    log(f"  Loaded {rows} rows")
                except Exception as e:
                    error(f"Error loading CSV {rel_path}: {e}")
                    con.execute("""
                        INSERT INTO build_status (file_path, status, error_message)
                        VALUES (?, 'error', ?)
                        ON CONFLICT (file_path) DO UPDATE SET status='error', error_message=?, last_updated=CURRENT_TIMESTAMP
                    """, [rel_path, str(e)[:500], str(e)[:500]])
                    errors += 1
            
            elif fmt == "parquet":
                log(f"Loading Parquet: {rel_path}")
                try:
                    con.execute(f"""
                        INSERT INTO raw_contacts (file_path, row_number, email, name, first_name, last_name, 
                        title, company, domain, website, phone, country, city, industry, technologies, source_file)
                        SELECT 
                            '{rel_path}' as file_path,
                            row_number() OVER () as row_number,
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
                            '{rel_path}' as source_file
                        FROM read_parquet('{file_path}')
                    """)
                    rows = con.execute("SELECT COUNT(*) FROM raw_contacts WHERE file_path = ?", [rel_path]).fetchone()[0]
                    con.execute("""
                        INSERT INTO build_status (file_path, status, rows_processed)
                        VALUES (?, 'done', ?)
                        ON CONFLICT (file_path) DO UPDATE SET status='done', rows_processed=?, last_updated=CURRENT_TIMESTAMP
                    """, [rel_path, rows, rows])
                    total_processed += rows
                    log(f"  Loaded {rows} rows")
                except Exception as e:
                    error(f"Error loading Parquet {rel_path}: {e}")
                    con.execute("""
                        INSERT INTO build_status (file_path, status, error_message)
                        VALUES (?, 'error', ?)
                        ON CONFLICT (file_path) DO UPDATE SET status='error', error_message=?, last_updated=CURRENT_TIMESTAMP
                    """, [rel_path, str(e)[:500], str(e)[:500]])
                    errors += 1
            
            else:
                log(f"Skipping {rel_path}: unsupported format {fmt}")
                con.execute("""
                    INSERT INTO build_status (file_path, status, error_message)
                    VALUES (?, 'skipped', 'unsupported format')
                    ON CONFLICT (file_path) DO UPDATE SET status='skipped', error_message='unsupported format', last_updated=CURRENT_TIMESTAMP
                """, [rel_path])
        
        except Exception as e:
            error(f"Unexpected error processing {rel_path}: {e}")
            errors += 1
    
    # Create deduped contacts table
    log("Creating deduped contacts table...")
    con.execute("""
        CREATE OR REPLACE TABLE contacts AS
        SELECT DISTINCT ON (COALESCE(NULLIF(email, ''), domain))
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
            COUNT(*) as source_count
        FROM raw_contacts
        WHERE email IS NOT NULL OR domain IS NOT NULL
        ORDER BY
            CASE WHEN email IS NOT NULL THEN 0 ELSE 1 END,
            source_count DESC
    """)
    
    # Export to Parquet
    log(f"Exporting to {LEADS}...")
    con.execute(f"COPY (SELECT * FROM contacts) TO '{LEADS}' (FORMAT parquet, COMPRESSION zstd)")
    
    # Stats
    total_raw = con.execute("SELECT COUNT(*) FROM raw_contacts").fetchone()[0]
    total_deduped = con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    with_email = con.execute("SELECT COUNT(*) FROM contacts WHERE email IS NOT NULL").fetchone()[0]
    with_phone = con.execute("SELECT COUNT(*) FROM contacts WHERE phone IS NOT NULL").fetchone()[0]
    
    log(f"Build complete!")
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
    if not DB.exists():
        print(json.dumps({"error": "no_database"}))
        return
    
    con = duckdb.connect(str(DB))
    
    try:
        total_raw = con.execute("SELECT COUNT(*) FROM raw_contacts").fetchone()[0]
        total_deduped = con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        
        with_email = con.execute("SELECT COUNT(*) FROM contacts WHERE email IS NOT NULL").fetchone()[0]
        with_phone = con.execute("SELECT COUNT(*) FROM contacts WHERE phone IS NOT NULL").fetchone()[0]
        with_company = con.execute("SELECT COUNT(*) FROM contacts WHERE company IS NOT NULL").fetchone()[0]
        
        status = con.execute("SELECT status, COUNT(*) FROM build_status GROUP BY status").fetchall()
        
        info = {
            "raw_contacts": total_raw,
            "deduped_contacts": total_deduped,
            "with_email": with_email,
            "with_phone": with_phone,
            "with_company": with_company,
            "build_status": {s: c for s, c in status},
            "database": str(DB),
            "export": str(LEADS) if LEADS.exists() else None
        }
        
        print(json.dumps(info, indent=2))
    finally:
        con.close()

def search(query):
    """Search leads"""
    if not DB.exists():
        print(json.dumps({"error": "no_database"}))
        return
    
    con = duckdb.connect(str(DB))
    
    try:
        # Simple text search across key fields
        results = con.execute("""
            SELECT email, name, company, domain, phone, country, city, industry
            FROM contacts
            WHERE 
                email ILIKE ? OR
                name ILIKE ? OR
                company ILIKE ? OR
                domain ILIKE ? OR
                industry ILIKE ?
            LIMIT 100
        """, [f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"]).fetchall()
        
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
    if not DB.exists():
        print(json.dumps({"error": "no_database"}))
        return
    
    con = duckdb.connect(str(DB))
    
    try:
        total = con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        log(f"Exporting {total:,} contacts to {output_path}...")
        
        con.execute(f"COPY (SELECT * FROM contacts) TO '{output_path}' (HEADER, DELIMITER ',')")
        
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
