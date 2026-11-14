"""
email_health.py - Email health monitoring and warm-up automation.

This module handles:
- Mailbox health and reputation tracking
- Warm-up schedules for new mailboxes
- Bounce and spam rate monitoring
- Automatic throttling when reputation drops
"""
import os, sys, json, uuid, datetime
from typing import List, Dict, Optional
from lead_studio_schema import connect, get_channel_settings


def register_mailbox(mailbox_name: str, smtp_host: str, domain: str = "",
                    warmup_limit: int = 10) -> str:
    """Register a mailbox for health monitoring."""
    con = connect()
    health_id = f"health_{uuid.uuid4().hex[:12]}"

    con.execute("""
        INSERT INTO email_health (id, mailbox_name, smtp_host, domain, warmup_limit, warmup_day)
        VALUES (?, ?, ?, ?, ?, 0)
    """, [health_id, mailbox_name, smtp_host, domain, warmup_limit])

    con.close()
    return health_id


def update_daily_stats(mailbox_name: str, sent: int = 0, bounced: int = 0,
                      spam: int = 0) -> Dict:
    """Update daily statistics for a mailbox."""
    con = connect()

    # Get current stats
    current = con.execute("""
        SELECT * FROM email_health WHERE mailbox_name = ?
    """, [mailbox_name]).fetchone()

    if not current:
        con.close()
        return {"error": "Mailbox not found"}

    # Update stats
    new_sent = current[3] + sent
    new_bounce = current[4] + bounced
    new_spam = current[5] + spam

    # Calculate reputation score
    total = new_sent
    if total > 0:
        failure_rate = (new_bounce + new_spam) / total
        # Reputation: 100 - (failure_rate * 100)
        reputation = max(0, min(100, int(100 - (failure_rate * 100))))
    else:
        reputation = 100

    # Determine health status
    if reputation >= 90:
        status = "good"
    elif reputation >= 70:
        status = "warning"
    else:
        status = "critical"

    con.execute("""
        UPDATE email_health
        SET daily_sent_count = ?,
            daily_bounce_count = ?,
            daily_spam_count = ?,
            reputation_score = ?,
            health_status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE mailbox_name = ?
    """, [new_sent, new_bounce, new_spam, reputation, status, mailbox_name])

    con.close()

    return {
        "mailbox_name": mailbox_name,
        "daily_sent": new_sent,
        "daily_bounce": new_bounce,
        "daily_spam": new_spam,
        "reputation": reputation,
        "status": status
    }


def advance_warmup(mailbox_name: str) -> Dict:
    """Advance warm-up to the next day."""
    con = connect()

    # Get current warmup state
    current = con.execute("""
        SELECT warmup_day, warmup_limit FROM email_health WHERE mailbox_name = ?
    """, [mailbox_name]).fetchone()

    if not current:
        con.close()
        return {"error": "Mailbox not found"}

    warmup_day, warmup_limit = current[0], current[1]

    # Warm-up schedule: exponential growth
    # Day 0: 10, Day 1: 20, Day 2: 40, Day 3: 80, Day 4: 150, Day 5: 250, Day 6: 400, Day 7+: 500
    warmup_schedule = [10, 20, 40, 80, 150, 250, 400, 500]
    new_day = warmup_day + 1
    new_limit = warmup_schedule[min(new_day, len(warmup_schedule) - 1)]

    con.execute("""
        UPDATE email_health
        SET warmup_day = ?, warmup_limit = ?, updated_at = CURRENT_TIMESTAMP
        WHERE mailbox_name = ?
    """, [new_day, new_limit, mailbox_name])

    con.close()

    return {
        "mailbox_name": mailbox_name,
        "warmup_day": new_day,
        "warmup_limit": new_limit,
        "completed": new_day >= len(warmup_schedule)
    }


def check_send_permission(mailbox_name: str) -> Dict:
    """Check if a mailbox is allowed to send based on health and warm-up."""
    con = connect()

    health = con.execute("""
        SELECT * FROM email_health WHERE mailbox_name = ?
    """, [mailbox_name]).fetchone()

    if not health:
        con.close()
        return {"allowed": False, "reason": "Mailbox not registered"}

    health_status = health[9]
    daily_sent = health[3]
    warmup_limit = health[7]

    # Check health status
    if health_status == "critical":
        return {"allowed": False, "reason": "Mailbox health is critical", "reputation": health[8]}

    # Check warm-up limit
    if daily_sent >= warmup_limit:
        return {"allowed": False, "reason": f"Warm-up limit reached ({warmup_limit})", "sent_today": daily_sent}

    # Check channel settings
    settings = get_channel_settings("email")
    if settings and daily_sent >= settings["daily_limit"]:
        return {"allowed": False, "reason": f"Daily limit reached ({settings['daily_limit']})", "sent_today": daily_sent}

    con.close()

    return {
        "allowed": True,
        "reason": "All checks passed",
        "sent_today": daily_sent,
        "warmup_limit": warmup_limit,
        "reputation": health[8]
    }


def get_all_mailbox_health() -> List[Dict]:
    """Get health status for all mailboxes."""
    con = connect()

    results = con.execute("""
        SELECT * FROM email_health ORDER BY reputation_score DESC
    """).fetchall()

    con.close()

    return [
        {
            "id": r[0],
            "mailbox_name": r[1],
            "smtp_host": r[2],
            "domain": r[3],
            "daily_sent": r[3],
            "daily_bounce": r[4],
            "daily_spam": r[5],
            "reputation": r[6],
            "warmup_day": r[7],
            "warmup_limit": r[8],
            "health_status": r[9]
        }
        for r in results
    ]


def reset_daily_stats(mailbox_name: str = None):
    """Reset daily statistics (run at end of day)."""
    con = connect()

    if mailbox_name:
        con.execute("""
            UPDATE email_health
            SET daily_sent_count = 0, daily_bounce_count = 0, daily_spam_count = 0
            WHERE mailbox_name = ?
        """, [mailbox_name])
    else:
        con.execute("""
            UPDATE email_health
            SET daily_sent_count = 0, daily_bounce_count = 0, daily_spam_count = 0
        """)

    con.close()


def get_health_summary() -> Dict:
    """Get overall health summary."""
    con = connect()

    total = con.execute("SELECT COUNT(*) FROM email_health").fetchone()[0]
    good = con.execute("SELECT COUNT(*) FROM email_health WHERE health_status = 'good'").fetchone()[0]
    warning = con.execute("SELECT COUNT(*) FROM email_health WHERE health_status = 'warning'").fetchone()[0]
    critical = con.execute("SELECT COUNT(*) FROM email_health WHERE health_status = 'critical'").fetchone()[0]

    avg_reputation = con.execute("SELECT AVG(reputation_score) FROM email_health").fetchone()[0] or 0

    con.close()

    return {
        "total_mailboxes": total,
        "good": good,
        "warning": warning,
        "critical": critical,
        "average_reputation": round(avg_reputation, 1)
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Email health monitoring")
    parser.add_argument("command", choices=["register", "update", "warmup", "check", "list", "reset", "summary"],
                        help="Command to run")
    parser.add_argument("--mailbox", help="Mailbox name")
    parser.add_argument("--smtp", help="SMTP host")
    parser.add_argument("--domain", help="Domain")
    parser.add_argument("--sent", type=int, default=0, help="Emails sent")
    parser.add_argument("--bounced", type=int, default=0, help="Emails bounced")
    parser.add_argument("--spam", type=int, default=0, help="Emails marked spam")
    parser.add_argument("--limit", type=int, default=10, help="Warm-up limit")

    args = parser.parse_args()

    if args.command == "register":
        if not args.mailbox or not args.smtp:
            print("Error: --mailbox and --smtp required for register")
            sys.exit(1)
        health_id = register_mailbox(args.mailbox, args.smtp, args.domain, args.limit)
        print(f"Mailbox registered: {health_id}")
    elif args.command == "update":
        if not args.mailbox:
            print("Error: --mailbox required for update")
            sys.exit(1)
        stats = update_daily_stats(args.mailbox, args.sent, args.bounced, args.spam)
        print(f"Updated: {json.dumps(stats, indent=2)}")
    elif args.command == "warmup":
        if not args.mailbox:
            print("Error: --mailbox required for warmup")
            sys.exit(1)
        result = advance_warmup(args.mailbox)
        print(f"Warm-up advanced: {json.dumps(result, indent=2)}")
    elif args.command == "check":
        if not args.mailbox:
            print("Error: --mailbox required for check")
            sys.exit(1)
        permission = check_send_permission(args.mailbox)
        print(f"Send permission: {json.dumps(permission, indent=2)}")
    elif args.command == "list":
        health = get_all_mailbox_health()
        print(f"\n=== Mailbox Health ({len(health)} mailboxes) ===")
        for h in health:
            print(f"  {h['mailbox_name']}: {h['health_status']} (reputation: {h['reputation']}, sent: {h['daily_sent']})")
    elif args.command == "reset":
        if args.mailbox:
            reset_daily_stats(args.mailbox)
            print(f"Reset stats for {args.mailbox}")
        else:
            reset_daily_stats()
            print("Reset stats for all mailboxes")
    elif args.command == "summary":
        summary = get_health_summary()
        print(f"\n=== Health Summary ===")
        print(f"Total: {summary['total_mailboxes']}")
        print(f"Good: {summary['good']}")
        print(f"Warning: {summary['warning']}")
        print(f"Critical: {summary['critical']}")
        print(f"Average Reputation: {summary['average_reputation']}")
