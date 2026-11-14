"""
openrefine_bridge.py - OpenRefine integration for data cleaning pipeline.

This module provides:
- CSV export to OpenRefine format
- Transform rules library
- Deduplication and standardization
- Import cleaned data back into Lead Studio
"""
import os, sys, csv, json, uuid, shutil
from typing import List, Dict, Optional
from lead_studio_schema import connect


def export_to_openrefine(table_name: str, output_path: str, filters: Dict = None) -> str:
    """Export data from Lead Studio to CSV for OpenRefine."""
    con = connect()

    # Get column info
    columns = con.execute(f"DESCRIBE {table_name}").fetchall()
    col_names = [c[0] for c in columns]

    # Build query
    query = f"SELECT * FROM {table_name}"
    if filters:
        where_clauses = []
        params = []
        for k, v in filters.items():
            where_clauses.append(f"{k} = ?")
            params.append(v)
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

    rows = con.execute(query, filters.values() if filters else []).fetchall()

    # Write CSV
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(col_names)
        writer.writerows(rows)

    con.close()
    return output_path


def import_from_openrefine(csv_path: str, target_table: str) -> int:
    """Import cleaned CSV back into Lead Studio."""
    con = connect()

    # Read CSV
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        rows = list(reader)

    # Create table if not exists
    col_defs = ", ".join([f"{h} VARCHAR" for h in headers])
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {target_table} ({col_defs})
    """)

    # Insert data
    placeholders = ", ".join(["?" for _ in headers])
    con.execute(f"INSERT INTO {target_table} VALUES ({placeholders})", rows)

    con.close()
    return len(rows)


TRANSFORM_RULES = {
    "standardize_email": {
        "description": "Convert emails to lowercase and trim whitespace",
        "function": "value.toString().toLowerCase().trim()"
    },
    "standardize_phone": {
        "description": "Remove non-numeric characters from phone numbers",
        "function": "value.replace(/[^0-9]/g, '')"
    },
    "standardize_company": {
        "description": "Title case company names and remove suffixes",
        "function": "value.toTitleCase().replace(/\\s+(Inc|LLC|Ltd|Corp|GmbH)\\.?$/gi, '')"
    },
    "extract_domain": {
        "description": "Extract domain from email address",
        "function": "value.split('@')[1]"
    },
    "remove_freemail": {
        "description": "Filter out freemail domains (gmail, yahoo, etc.)",
        "function": "!(['gmail.com','yahoo.com','hotmail.com','outlook.com'].includes(value.split('@')[1]))"
    },
    "deduplicate_email": {
        "description": "Mark duplicate emails for removal",
        "function": "row.record.cells['email'].value == row.record.cells['email'].value"
    }
}


def apply_transform_rule(csv_path: str, column: str, rule_name: str, output_path: str) -> str:
    """Generate OpenRefine transform JSON for a rule."""
    if rule_name not in TRANSFORM_RULES:
        raise ValueError(f"Unknown rule: {rule_name}")

    rule = TRANSFORM_RULES[rule_name]

    # Generate OpenRefine operation JSON
    operation = {
        "operation": {
            "op": "core/column-addition",
            "description": rule["description"],
            "engineConfig": {
                "mode": "row-based"
            },
            "baseColumnName": column,
            "expression": rule["function"],
            "newColumnName": f"{column}_{rule_name}",
            "columnInsertIndex": -1
        }
    }

    # Write operation JSON
    with open(output_path, 'w') as f:
        json.dump(operation, f, indent=2)

    return output_path


def deduplicate_csv(csv_path: str, key_columns: List[str], output_path: str) -> int:
    """Remove duplicate rows based on key columns."""
    seen = set()
    unique_rows = []

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames

        for row in reader:
            key = tuple(row[col] for col in key_columns)
            if key not in seen:
                seen.add(key)
                unique_rows.append(row)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(unique_rows)

    return len(unique_rows)


def standardize_fields(csv_path: str, mappings: Dict[str, str], output_path: str) -> int:
    """Standardize field names and values."""
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames

        # Map old headers to new headers
        new_headers = [mappings.get(h, h) for h in headers]

        rows = []
        for row in reader:
            new_row = {}
            for old_h, new_h in zip(headers, new_headers):
                value = row[old_h]
                # Apply standardization
                if new_h == "email":
                    value = value.lower().strip() if value else ""
                elif new_h == "phone":
                    value = ''.join(c for c in value if c.isdigit()) if value else ""
                elif new_h == "company":
                    value = value.strip().title() if value else ""
                new_row[new_h] = value
            rows.append(new_row)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=new_headers)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def generate_openrefine_project(csv_path: str, project_name: str = "Lead Studio Data") -> str:
    """Generate a OpenRefine project configuration."""
    project_config = {
        "projectName": project_name,
        "file": os.path.basename(csv_path),
        "created": time.time() * 1000,
        "modified": time.time() * 1000,
        "customMetadata": {
            "source": "Lead Studio",
            "imported_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    }

    config_path = csv_path.replace('.csv', '.project.json')
    with open(config_path, 'w') as f:
        json.dump(project_config, f, indent=2)

    return config_path


def list_transform_rules() -> Dict:
    """List all available transform rules."""
    return TRANSFORM_RULES


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OpenRefine bridge for data cleaning")
    parser.add_argument("command", choices=["export", "import", "transform", "dedup", "standardize", "rules"],
                        help="Command to run")
    parser.add_argument("--table", help="Table name (for export)")
    parser.add_argument("--csv", help="CSV file path")
    parser.add_argument("--output", help="Output CSV path")
    parser.add_argument("--column", help="Column to transform")
    rule_choices = list(TRANSFORM_RULES.keys())
    parser.add_argument("--rule", choices=rule_choices, help="Transform rule")
    parser.add_argument("--keys", nargs="+", help="Key columns for deduplication")
    parser.add_argument("--mapping", help="JSON mapping for field standardization")

    args = parser.parse_args()

    if args.command == "export":
        if not args.table or not args.output:
            print("Error: --table and --output required for export")
            sys.exit(1)
        path = export_to_openrefine(args.table, args.output)
        print(f"Exported to: {path}")
    elif args.command == "import":
        if not args.csv or not args.table:
            print("Error: --csv and --table required for import")
            sys.exit(1)
        count = import_from_openrefine(args.csv, args.table)
        print(f"Imported {count} rows to {args.table}")
    elif args.command == "transform":
        if not args.csv or not args.column or not args.rule or not args.output:
            print("Error: --csv, --column, --rule, and --output required for transform")
            sys.exit(1)
        op_path = apply_transform_rule(args.csv, args.column, args.rule, args.output)
        print(f"Transform operation saved to: {op_path}")
    elif args.command == "dedup":
        if not args.csv or not args.keys or not args.output:
            print("Error: --csv, --keys, and --output required for dedup")
            sys.exit(1)
        count = deduplicate_csv(args.csv, args.keys, args.output)
        print(f"Deduplicated to {count} unique rows")
    elif args.command == "standardize":
        if not args.csv or not args.mapping or not args.output:
            print("Error: --csv, --mapping, and --output required for standardize")
            sys.exit(1)
        mapping = json.loads(args.mapping)
        count = standardize_fields(args.csv, mapping, args.output)
        print(f"Standardized {count} rows")
    elif args.command == "rules":
        rules = list_transform_rules()
        print("Available transform rules:")
        for name, rule in rules.items():
            print(f"  {name}: {rule['description']}")
