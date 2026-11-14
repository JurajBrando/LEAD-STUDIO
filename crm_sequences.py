"""
crm_sequences.py - CRM Sequences and Auto-Follow-ups engine.

This module handles:
- Creating email sequences for CRM stages
- Adding steps to sequences
- Generating AI-powered email drafts
- Auto-sending follow-ups based on sending rules
"""
import os, sys, json, time, uuid, datetime
from typing import List, Dict, Optional

# Import schema
from lead_studio_schema import connect, get_channel_settings, get_api_key


def create_sequence(name: str, stage_id: str, organization_id: str = "default",
                   auto_send_follow_ups: bool = False, follow_up_gap_minutes: int = 30,
                   description: str = "") -> str:
    """Create a new CRM sequence for a stage."""
    con = connect()
    seq_id = f"seq_{uuid.uuid4().hex[:12]}"

    con.execute("""
        INSERT INTO crm_sequences (id, name, stage_id, organization_id, auto_send_follow_ups, follow_up_gap_minutes, description)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [seq_id, name, stage_id, organization_id, auto_send_follow_ups, follow_up_gap_minutes, description])

    con.close()
    return seq_id


def add_sequence_step(sequence_id: str, order_index: int, wait_days: int = 1,
                     subject_template: str = "", body_template: str = "",
                     instructions: str = "") -> str:
    """Add a step to a sequence."""
    con = connect()
    step_id = f"step_{uuid.uuid4().hex[:12]}"

    con.execute("""
        INSERT INTO crm_sequence_steps (id, sequence_id, order_index, wait_days, subject_template, body_template, instructions)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [step_id, sequence_id, order_index, wait_days, subject_template, body_template, instructions])

    con.close()
    return step_id


def get_sequence(sequence_id: str) -> Optional[Dict]:
    """Get a sequence with its steps."""
    con = connect()

    seq = con.execute("""
        SELECT * FROM crm_sequences WHERE id = ?
    """, [sequence_id]).fetchone()

    if not seq:
        con.close()
        return None

    steps = con.execute("""
        SELECT * FROM crm_sequence_steps WHERE sequence_id = ? ORDER BY order_index
    """, [sequence_id]).fetchall()

    con.close()

    return {
        "id": seq[0],
        "name": seq[1],
        "stage_id": seq[2],
        "organization_id": seq[3],
        "auto_send_follow_ups": seq[4],
        "follow_up_gap_minutes": seq[5],
        "description": seq[6],
        "steps": [
            {
                "id": s[0],
                "sequence_id": s[1],
                "order_index": s[2],
                "wait_days": s[3],
                "subject_template": s[4],
                "body_template": s[5],
                "instructions": s[6]
            }
            for s in steps
        ]
    }


def create_lead(email: str, name: str = "", company: str = "", title: str = "",
                stage_id: str = "new", source: str = "", custom_fields: Dict = None) -> str:
    """Create or update a CRM lead."""
    con = connect()

    # Check if lead exists
    existing = con.execute("""
        SELECT id FROM crm_leads WHERE email = ?
    """, [email]).fetchone()

    if existing:
        lead_id = existing[0]
        con.execute("""
            UPDATE crm_leads
            SET name = COALESCE(?, name),
                company = COALESCE(?, company),
                title = COALESCE(?, title),
                stage_id = COALESCE(?, stage_id),
                custom_fields = COALESCE(?, custom_fields),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [name, company, title, stage_id, json.dumps(custom_fields or {}), lead_id])
    else:
        lead_id = f"lead_{uuid.uuid4().hex[:12]}"
        con.execute("""
            INSERT INTO crm_leads (id, email, name, company, title, stage_id, source, custom_fields)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [lead_id, email, name, company, title, stage_id, source, json.dumps(custom_fields or {})])

    con.close()
    return lead_id


def generate_draft(lead_id: str, sequence_step_id: str, sender: str,
                  prompt_name: str = "crm/draft_reply") -> str:
    """Generate an AI email draft for a lead."""
    con = connect()

    # Get lead data
    lead = con.execute("""
        SELECT * FROM crm_leads WHERE id = ?
    """, [lead_id]).fetchone()

    if not lead:
        con.close()
        return None

    # Get sequence step
    step = con.execute("""
        SELECT * FROM crm_sequence_steps WHERE id = ?
    """, [sequence_step_id]).fetchone()

    if not step:
        con.close()
        return None

    # Get prompt
    prompt = con.execute("""
        SELECT content FROM prompts WHERE name = ? AND is_production = true
    """, [prompt_name]).fetchone()

    con.close()

    if not prompt:
        # Use fallback instructions
        instructions = step[6] or "Write a professional email following the template."
    else:
        instructions = prompt[0]

    # Build template variables
    lead_data = {
        "lead_name": lead[2] or "there",
        "lead_company": lead[3] or "your company",
        "lead_title": lead[4] or "",
        "sender": sender,
        "subject_template": step[4] or "",
        "body_template": step[5] or "",
        "instructions": instructions
    }

    # For now, return a simple template fill
    # TODO: Integrate with Ollama/ai_enrich.py for actual AI generation
    subject = step[4].replace("{{lead_name}}", lead_data["lead_name"])
    subject = subject.replace("{{lead_company}}", lead_data["lead_company"])

    body = step[5].replace("{{lead_name}}", lead_data["lead_name"])
    body = body.replace("{{lead_company}}", lead_data["lead_company"])
    body = body.replace("{{sender}}", lead_data["sender"])

    # Create draft record
    con = connect()
    draft_id = f"draft_{uuid.uuid4().hex[:12]}"

    con.execute("""
        INSERT INTO crm_drafts (id, lead_id, sequence_step_id, sender, subject, body, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, [draft_id, lead_id, sequence_step_id, sender, subject, body])

    con.close()
    return draft_id


def schedule_follow_ups(lead_id: str, sequence_id: str, sender: str):
    """Schedule all follow-up drafts for a lead based on a sequence."""
    con = connect()

    # Get sequence steps
    steps = con.execute("""
        SELECT * FROM crm_sequence_steps WHERE sequence_id = ? ORDER BY order_index
    """, [sequence_id]).fetchall()

    # Get follow-up gap from sequence
    seq = con.execute("""
        SELECT follow_up_gap_minutes FROM crm_sequences WHERE id = ?
    """, [sequence_id]).fetchone()

    gap_minutes = seq[0] if seq else 30

    # Schedule each step
    base_time = datetime.datetime.now()
    for i, step in enumerate(steps):
        wait_minutes = (step[3] * 24 * 60) + (i * gap_minutes)  # wait_days + gap between steps
        send_time = base_time + datetime.timedelta(minutes=wait_minutes)

        draft_id = generate_draft(lead_id, step[0], sender)

        if draft_id:
            con.execute("""
                UPDATE crm_drafts SET auto_send_at = ? WHERE id = ?
            """, [send_time, draft_id])

    con.close()


def get_pending_drafts(limit: int = 100) -> List[Dict]:
    """Get drafts that are ready to be sent."""
    con = connect()

    now = datetime.datetime.now()
    drafts = con.execute("""
        SELECT d.*, l.email, l.name, l.company
        FROM crm_drafts d
        JOIN crm_leads l ON d.lead_id = l.id
        WHERE d.status = 'pending'
          AND d.auto_send_at IS NOT NULL
          AND d.auto_send_at <= ?
        ORDER BY d.auto_send_at
        LIMIT ?
    """, [now, limit]).fetchall()

    con.close()

    return [
        {
            "id": d[0],
            "lead_id": d[1],
            "sequence_step_id": d[2],
            "sender": d[3],
            "subject": d[4],
            "body": d[5],
            "auto_send_at": d[7],
            "lead_email": d[10],
            "lead_name": d[11],
            "lead_company": d[12]
        }
        for d in drafts
    ]


def check_sending_rules(channel: str = "email") -> Dict:
    """Check if sending is allowed based on channel rules."""
    settings = get_channel_settings(channel)

    if not settings:
        return {"allowed": True, "reason": "No rules configured"}

    now = datetime.datetime.now()
    current_hour = now.hour

    # Check sending hours
    if current_hour < settings["sending_hours_start"] or current_hour >= settings["sending_hours_end"]:
        return {
            "allowed": False,
            "reason": f"Outside sending hours ({settings['sending_hours_start']}:00-{settings['sending_hours_end']}:00)"
        }

    # TODO: Check daily limit count from email_health table
    # For now, allow
    return {"allowed": True, "reason": "All checks passed"}


def mark_draft_sent(draft_id: str, status: str = "sent", error_message: str = ""):
    """Mark a draft as sent or failed."""
    con = connect()

    if status == "sent":
        con.execute("""
            UPDATE crm_drafts
            SET status = ?, sent_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [status, draft_id])
    else:
        con.execute("""
            UPDATE crm_drafts
            SET status = ?, error_message = ?
            WHERE id = ?
        """, [status, error_message, draft_id])

    con.close()


def move_lead_stage(lead_id: str, new_stage_id: str):
    """Move a lead to a new stage and trigger follow-ups."""
    con = connect()

    # Update lead stage
    con.execute("""
        UPDATE crm_leads
        SET stage_id = ?, updated_at = CURRENT_TIMESTAMP, last_contacted_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, [new_stage_id, lead_id])

    # Get sequence for this stage
    seq = con.execute("""
        SELECT id, auto_send_follow_ups FROM crm_sequences WHERE stage_id = ?
    """, [new_stage_id]).fetchone()

    if seq and seq[1]:  # auto_send_follow_ups is true
        # Get lead's default sender (from custom fields or settings)
        lead = con.execute("""
            SELECT custom_fields FROM crm_leads WHERE id = ?
        """, [lead_id]).fetchone()

        custom_fields = json.loads(lead[0]) if lead and lead[0] else {}
        sender = custom_fields.get("sender", "default")

        # Schedule follow-ups
        schedule_follow_ups(lead_id, seq[0], sender)

    con.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CRM Sequences management")
    parser.add_argument("command", choices=["create-seq", "add-step", "create-lead", "schedule", "pending", "send-check"],
                        help="Command to run")
    parser.add_argument("--name", help="Sequence or lead name")
    parser.add_argument("--stage", default="new", help="Stage ID")
    parser.add_argument("--email", help="Lead email")
    parser.add_argument("--company", help="Lead company")
    parser.add_argument("--title", help="Lead title")
    parser.add_argument("--seq-id", help="Sequence ID")
    parser.add_argument("--order", type=int, help="Step order")
    parser.add_argument("--wait-days", type=int, default=1, help="Days to wait")
    parser.add_argument("--subject", help="Subject template")
    parser.add_argument("--body", help="Body template")
    parser.add_argument("--instructions", help="AI instructions")
    parser.add_argument("--sender", default="default", help="Sender name")
    parser.add_argument("--limit", type=int, default=100, help="Limit for pending")

    args = parser.parse_args()

    if args.command == "create-seq":
        seq_id = create_sequence(args.name, args.stage, auto_send_follow_ups=True)
        print(f"Sequence created: {seq_id}")
    elif args.command == "add-step":
        step_id = add_sequence_step(args.seq_id, args.order, args.wait_days, args.subject, args.body, args.instructions)
        print(f"Step added: {step_id}")
    elif args.command == "create-lead":
        lead_id = create_lead(args.email, args.name, args.company, args.title, args.stage)
        print(f"Lead created: {lead_id}")
    elif args.command == "schedule":
        schedule_follow_ups(args.seq_id, args.sender)
        print("Follow-ups scheduled")
    elif args.command == "pending":
        drafts = get_pending_drafts(args.limit)
        print(f"Pending drafts: {len(drafts)}")
        for d in drafts:
            print(f"  {d['id']}: {d['lead_email']} - {d['subject'][:50]}")
    elif args.command == "send-check":
        check = check_sending_rules()
        print(f"Sending allowed: {check['allowed']}")
        print(f"Reason: {check['reason']}")
