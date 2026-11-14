"""
metabase_setup.py - Metabase integration for data visualization.

This module provides:
- Metabase connection configuration
- Dashboard generation for lead analytics
- SQL queries for common visualizations
- Export of data to Metabase-compatible format
"""
import os, sys, json, uuid
from typing import List, Dict, Optional
from lead_studio_schema import connect


# Metabase-compatible SQL queries for common visualizations
ANALYTICS_QUERIES = {
    "lead_source_breakdown": """
        SELECT
            source,
            COUNT(*) as count,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) as percentage
        FROM crm_leads
        WHERE source IS NOT NULL
        GROUP BY source
        ORDER BY count DESC
    """,

    "conversion_funnel": """
        SELECT
            s.name as stage,
            COUNT(*) as leads,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) as percentage
        FROM crm_leads l
        JOIN crm_stages s ON l.stage_id = s.id
        GROUP BY s.name, s.order_index
        ORDER BY s.order_index
    """,

    "geographic_distribution": """
        SELECT
            country,
            COUNT(*) as leads
        FROM crm_leads
        WHERE country IS NOT NULL
        GROUP BY country
        ORDER BY leads DESC
        LIMIT 20
    """,

    "technology_adoption": """
        SELECT
            json_each.key as technology,
            COUNT(*) as leads
        FROM crm_leads l,
             json_each(l.custom_fields)
        WHERE json_each.key LIKE '%tech%'
        GROUP BY json_each.key
        ORDER BY leads DESC
        LIMIT 20
    """,

    "email_performance": """
        SELECT
            DATE(sent_at) as date,
            status,
            COUNT(*) as count
        FROM campaign_sends
        WHERE sent_at IS NOT NULL
        GROUP BY DATE(sent_at), status
        ORDER BY date DESC
    """,

    "campaign_performance": """
        SELECT
            c.name as campaign,
            cs.status,
            COUNT(*) as sends,
            ROUND(AVG(
                CASE
                    WHEN cs.status = 'replied' THEN 1
                    WHEN cs.status = 'clicked' THEN 0.5
                    ELSE 0
                END
            ) * 100, 2) as engagement_rate
        FROM campaigns c
        JOIN campaign_sends cs ON c.id = cs.campaign_id
        GROUP BY c.name, cs.status
        ORDER BY sends DESC
    """,

    "stage_velocity": """
        SELECT
            s.name as stage,
            AVG(EXTRACT(EPOCH FROM (l.updated_at - l.created_at)) / 86400) as avg_days_in_stage
        FROM crm_leads l
        JOIN crm_stages s ON l.stage_id = s.id
        WHERE l.created_at IS NOT NULL
        GROUP BY s.name, s.order_index
        ORDER BY s.order_index
    """,

    "daily_activity": """
        SELECT
            DATE(created_at) as date,
            COUNT(*) as new_leads
        FROM crm_leads
        WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
        GROUP BY DATE(created_at)
        ORDER BY date DESC
    """
}


def export_analytics_data(output_dir: str = "LEAD_INDEX/metabase_exports"):
    """Export all analytics data to CSV files for Metabase."""
    con = connect()
    os.makedirs(output_dir, exist_ok=True)

    exported = {}

    for name, query in ANALYTICS_QUERIES.items():
        try:
            results = con.execute(query).fetchall()

            # Get column names
            col_names = [desc[0] for desc in con.description]

            # Write CSV
            csv_path = os.path.join(output_dir, f"{name}.csv")
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                import csv
                writer = csv.writer(f)
                writer.writerow(col_names)
                writer.writerows(results)

            exported[name] = csv_path
            print(f"Exported {name}: {len(results)} rows")
        except Exception as e:
            print(f"Error exporting {name}: {e}")

    con.close()
    return exported


def generate_metabase_config(db_name: str = "Lead Studio", host: str = "localhost",
                            port: int = 5432, user: str = "admin", password: str = "") -> Dict:
    """Generate Metabase database configuration."""
    config = {
        "name": db_name,
        "engine": "postgres",
        "details": {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "dbname": "lead_studio",
            "ssl": False
        },
        "auto_run_queries": True,
        "is_sample": False,
        "cache_field_values_ttl": None
    }
    return config


def create_dashboard_definition(name: str = "Lead Studio Dashboard") -> Dict:
    """Create a Metabase dashboard definition."""
    dashboard = {
        "name": name,
        "description": "Lead generation and CRM analytics",
        "cards": [
            {
                "name": "Total Leads",
                "visualization": "scalar",
                "query": "SELECT COUNT(*) FROM crm_leads"
            },
            {
                "name": "Leads by Stage",
                "visualization": "bar",
                "query": ANALYTICS_QUERIES["conversion_funnel"]
            },
            {
                "name": "Lead Sources",
                "visualization": "pie",
                "query": ANALYTICS_QUERIES["lead_source_breakdown"]
            },
            {
                "name": "Geographic Distribution",
                "visualization": "map",
                "query": ANALYTICS_QUERIES["geographic_distribution"]
            },
            {
                "name": "Daily New Leads",
                "visualization": "line",
                "query": ANALYTICS_QUERIES["daily_activity"]
            },
            {
                "name": "Email Performance",
                "visualization": "bar",
                "query": ANALYTICS_QUERIES["email_performance"]
            },
            {
                "name": "Campaign Performance",
                "visualization": "table",
                "query": ANALYTICS_QUERIES["campaign_performance"]
            },
            {
                "name": "Stage Velocity",
                "visualization": "bar",
                "query": ANALYTICS_QUERIES["stage_velocity"]
            }
        ],
        "parameters": [
            {
                "name": "Date Range",
                "type": "date/daterange",
                "slug": "date_range"
            },
            {
                "name": "Stage",
                "type": "category",
                "slug": "stage"
            }
        ],
        "ordering": [
            ["scalar", 0, 0],
            ["bar", 1, 0],
            ["pie", 0, 1],
            ["map", 1, 1],
            ["line", 0, 2],
            ["bar", 1, 2],
            ["table", 0, 3],
            ["bar", 1, 3]
        ]
    }
    return dashboard


def save_dashboard_definition(dashboard: Dict, output_path: str):
    """Save dashboard definition to JSON file."""
    with open(output_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
    return output_path


def get_quick_stats() -> Dict:
    """Get quick statistics for the dashboard."""
    con = connect()

    stats = {}

    # Total leads
    stats["total_leads"] = con.execute("SELECT COUNT(*) FROM crm_leads").fetchone()[0]

    # Leads by stage
    stats["by_stage"] = dict(con.execute("""
        SELECT s.name, COUNT(*)
        FROM crm_leads l
        JOIN crm_stages s ON l.stage_id = s.id
        GROUP BY s.name
    """).fetchall())

    # New leads today
    stats["new_today"] = con.execute("""
        SELECT COUNT(*) FROM crm_leads
        WHERE DATE(created_at) = CURRENT_DATE
    """).fetchone()[0]

    # Active campaigns
    stats["active_campaigns"] = con.execute("""
        SELECT COUNT(*) FROM campaigns WHERE status = 'active'
    """).fetchone()[0]

    # Email health
    stats["email_health"] = dict(con.execute("""
        SELECT health_status, COUNT(*)
        FROM email_health
        GROUP BY health_status
    """).fetchall())

    con.close()
    return stats


def generate_metabase_sql_snippet(query_name: str) -> str:
    """Generate a Metabase SQL snippet for a query."""
    if query_name not in ANALYTICS_QUERIES:
        raise ValueError(f"Unknown query: {query_name}")

    return ANALYTICS_QUERIES[query_name]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Metabase setup for data visualization")
    parser.add_argument("command", choices=["export", "dashboard", "stats", "snippet"],
                        help="Command to run")
    parser.add_argument("--output", default="LEAD_INDEX/metabase_exports", help="Output directory")
    parser.add_argument("--name", default="Lead Studio Dashboard", help="Dashboard name")
    parser.add_argument("--query", help="Query name for snippet")

    args = parser.parse_args()

    if args.command == "export":
        exported = export_analytics_data(args.output)
        print(f"\nExported {len(exported)} analytics files to {args.output}")
    elif args.command == "dashboard":
        dashboard = create_dashboard_definition(args.name)
        output_path = os.path.join(args.output, f"{args.name.replace(' ', '_')}.json")
        os.makedirs(args.output, exist_ok=True)
        save_dashboard_definition(dashboard, output_path)
        print(f"Dashboard definition saved to: {output_path}")
    elif args.command == "stats":
        stats = get_quick_stats()
        print("\n=== Quick Statistics ===")
        print(f"Total Leads: {stats['total_leads']}")
        print(f"New Today: {stats['new_today']}")
        print(f"Active Campaigns: {stats['active_campaigns']}")
        print(f"\nLeads by Stage:")
        for stage, count in stats['by_stage'].items():
            print(f"  {stage}: {count}")
        print(f"\nEmail Health:")
        for status, count in stats['email_health'].items():
            print(f"  {status}: {count}")
    elif args.command == "snippet":
        if not args.query:
            print("Error: --query required for snippet")
            sys.exit(1)
        snippet = generate_metabase_sql_snippet(args.query)
        print(f"\nSQL for {args.query}:")
        print(snippet)
