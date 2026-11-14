"""
cross_channel_campaigns.py - Multi-channel campaign system.

This module handles:
- Unified campaign builder for email, WhatsApp, LinkedIn, social
- Cross-channel follow-up rules
- Unified reporting across channels
- Campaign scheduling and execution
"""
import os, sys, json, uuid, datetime
from typing import List, Dict, Optional
from lead_studio_schema import connect, get_channel_settings


def create_campaign(name: str, description: str = "", organization_id: str = "default",
                    start_date: str = None, end_date: str = None) -> str:
    """Create a new multi-channel campaign."""
    con = connect()
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"

    con.execute("""
        INSERT INTO campaigns (id, name, description, organization_id, start_date, end_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [campaign_id, name, description, organization_id, start_date, end_date])

    con.close()
    return campaign_id


def add_campaign_channel(campaign_id: str, channel: str, configuration: Dict = None) -> str:
    """Add a channel to a campaign."""
    con = connect()
    channel_id = f"ch_{uuid.uuid4().hex[:12]}"

    con.execute("""
        INSERT INTO campaign_channels (id, campaign_id, channel, configuration)
        VALUES (?, ?, ?, ?)
    """, [channel_id, campaign_id, channel, json.dumps(configuration or {})])

    con.close()
    return channel_id


def add_leads_to_campaign(campaign_id: str, lead_emails: List[str], channel: str = "email") -> int:
    """Add leads to a campaign for a specific channel."""
    con = connect()

    # Get campaign channel
    campaign_channel = con.execute("""
        SELECT id FROM campaign_channels WHERE campaign_id = ? AND channel = ?
    """, [campaign_id, channel]).fetchone()

    if not campaign_channel:
        con.close()
        raise ValueError(f"No {channel} channel found for campaign {campaign_id}")

    campaign_channel_id = campaign_channel[0]

    # Add leads
    added = 0
    for email in lead_emails:
        send_id = f"send_{uuid.uuid4().hex[:12]}"
        con.execute("""
            INSERT INTO campaign_sends (id, campaign_id, campaign_channel_id, lead_email, channel, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, [send_id, campaign_id, campaign_channel_id, email, channel])
        added += 1

    con.close()
    return added


def get_campaign(campaign_id: str) -> Optional[Dict]:
    """Get campaign details with channels and send statistics."""
    con = connect()

    campaign = con.execute("""
        SELECT * FROM campaigns WHERE id = ?
    """, [campaign_id]).fetchone()

    if not campaign:
        con.close()
        return None

    # Get channels
    channels = con.execute("""
        SELECT * FROM campaign_channels WHERE campaign_id = ?
    """, [campaign_id]).fetchall()

    # Get send statistics
    stats = con.execute("""
        SELECT channel, status, COUNT(*)
        FROM campaign_sends
        WHERE campaign_id = ?
        GROUP BY channel, status
    """, [campaign_id]).fetchall()

    con.close()

    return {
        "id": campaign[0],
        "name": campaign[1],
        "description": campaign[2],
        "organization_id": campaign[3],
        "status": campaign[4],
        "start_date": campaign[5],
        "end_date": campaign[6],
        "channels": [
            {
                "id": ch[0],
                "campaign_id": ch[1],
                "channel": ch[2],
                "configuration": json.loads(ch[3]) if ch[3] else {}
            }
            for ch in channels
        ],
        "stats": [
            {"channel": s[0], "status": s[1], "count": s[2]}
            for s in stats
        ]
    }


def execute_campaign_send(campaign_id: str, channel: str, limit: int = 100) -> int:
    """Execute pending sends for a campaign on a channel."""
    con = connect()

    # Check sending rules
    rules = get_channel_settings(channel)
    if not rules:
        con.close()
        return 0

    # Get pending sends
    pending = con.execute("""
        SELECT id, lead_email
        FROM campaign_sends
        WHERE campaign_id = ? AND channel = ? AND status = 'pending'
        LIMIT ?
    """, [campaign_id, channel, limit]).fetchall()

    sent = 0
    for send_id, email in pending:
        # TODO: Actual send logic via mailer.py / connect.py
        # For now, mark as sent
        con.execute("""
            UPDATE campaign_sends
            SET status = 'sent', sent_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [send_id])
        sent += 1

    con.close()
    return sent


def get_cross_channel_report(campaign_id: str) -> Dict:
    """Get unified report across all channels for a campaign."""
    con = connect()

    # Overall stats
    overall = con.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) as delivered,
            SUM(CASE WHEN status = 'opened' THEN 1 ELSE 0 END) as opened,
            SUM(CASE WHEN status = 'clicked' THEN 1 ELSE 0 END) as clicked,
            SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END) as replied,
            SUM(CASE WHEN status = 'bounced' THEN 1 ELSE 0 END) as bounced
        FROM campaign_sends
        WHERE campaign_id = ?
    """, [campaign_id]).fetchone()

    # By channel
    by_channel = con.execute("""
        SELECT
            channel,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END) as replied
        FROM campaign_sends
        WHERE campaign_id = ?
        GROUP BY channel
    """, [campaign_id]).fetchall()

    # Timeline
    timeline = con.execute("""
        SELECT
            DATE(sent_at) as date,
            channel,
            COUNT(*) as count
        FROM campaign_sends
        WHERE campaign_id = ? AND sent_at IS NOT NULL
        GROUP BY DATE(sent_at), channel
        ORDER BY date DESC
        LIMIT 30
    """, [campaign_id]).fetchall()

    con.close()

    return {
        "overall": {
            "total": overall[0],
            "sent": overall[1],
            "delivered": overall[2],
            "opened": overall[3],
            "clicked": overall[4],
            "replied": overall[5],
            "bounced": overall[6]
        },
        "by_channel": [
            {"channel": ch[0], "total": ch[1], "sent": ch[2], "replied": ch[3]}
            for ch in by_channel
        ],
        "timeline": [
            {"date": str(t[0]), "channel": t[1], "count": t[2]}
            for t in timeline
        ]
    }


def schedule_cross_channel_follow_up(campaign_id: str, lead_email: str,
                                     channel_sequence: List[str],
                                     delays_hours: List[int]):
    """Schedule follow-ups across multiple channels."""
    con = connect()

    base_time = datetime.datetime.now()

    for i, (channel, delay) in enumerate(zip(channel_sequence, delays_hours)):
        send_time = base_time + datetime.timedelta(hours=delay)

        # Get campaign channel
        campaign_channel = con.execute("""
            SELECT id FROM campaign_channels WHERE campaign_id = ? AND channel = ?
        """, [campaign_id, channel]).fetchone()

        if not campaign_channel:
            continue

        send_id = f"send_{uuid.uuid4().hex[:12]}"

        con.execute("""
            INSERT INTO campaign_sends (id, campaign_id, campaign_channel_id, lead_email, channel, status, sent_at)
            VALUES (?, ?, ?, ?, ?, 'scheduled', ?)
        """, [send_id, campaign_id, campaign_channel[0], lead_email, channel, send_time])

    con.close()


def unify_campaign_reporting(campaign_ids: List[str]) -> Dict:
    """Combine reporting across multiple campaigns."""
    con = connect()

    placeholders = ",".join(["?" for _ in campaign_ids])

    combined = con.execute(f"""
        SELECT
            c.name as campaign,
            cs.channel,
            cs.status,
            COUNT(*) as count
        FROM campaign_sends cs
        JOIN campaigns c ON cs.campaign_id = c.id
        WHERE cs.campaign_id IN ({placeholders})
        GROUP BY c.name, cs.channel, cs.status
    """, campaign_ids).fetchall()

    con.close()

    return [
        {"campaign": r[0], "channel": r[1], "status": r[2], "count": r[3]}
        for r in combined
    ]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cross-channel campaigns")
    parser.add_argument("command", choices=["create", "add-channel", "add-leads", "execute", "report", "follow-up"],
                        help="Command to run")
    parser.add_argument("--name", help="Campaign name")
    parser.add_argument("--id", help="Campaign ID")
    parser.add_argument("--channel", help="Channel (email, whatsapp, linkedin, social)")
    parser.add_argument("--config", help="Channel config as JSON")
    parser.add_argument("--leads", help="Comma-separated lead emails")
    parser.add_argument("--limit", type=int, default=100, help="Send limit")
    parser.add_argument("--sequence", help="Channel sequence (comma-separated)")
    parser.add_argument("--delays", help="Delays in hours (comma-separated)")

    args = parser.parse_args()

    if args.command == "create":
        camp_id = create_campaign(args.name)
        print(f"Campaign created: {camp_id}")
    elif args.command == "add-channel":
        config = json.loads(args.config) if args.config else {}
        ch_id = add_campaign_channel(args.id, args.channel, config)
        print(f"Channel added: {ch_id}")
    elif args.command == "add-leads":
        leads = args.leads.split(",") if args.leads else []
        added = add_leads_to_campaign(args.id, leads, args.channel)
        print(f"Added {added} leads")
    elif args.command == "execute":
        sent = execute_campaign_send(args.id, args.channel, args.limit)
        print(f"Sent {sent} messages")
    elif args.command == "report":
        report = get_cross_channel_report(args.id)
        print(f"Report: {json.dumps(report, indent=2)}")
    elif args.command == "follow-up":
        sequence = args.sequence.split(",") if args.sequence else []
        delays = [int(d) for d in args.delays.split(",")] if args.delays else []
        schedule_cross_channel_follow_up(args.id, "test@example.com", sequence, delays)
        print("Follow-ups scheduled")
