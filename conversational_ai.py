"""
conversational_ai.py - Conversational AI outreach system.

This module handles:
- Multi-turn AI conversations with leads
- Context-aware responses based on lead data
- Natural language campaign builder
- Conversation history tracking
"""
import os, sys, json, time, uuid, datetime
from typing import List, Dict, Optional
from lead_studio_schema import connect, get_api_key


def create_conversation(lead_id: str, channel: str = "email") -> str:
    """Create a new conversation with a lead."""
    con = connect()
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"

    con.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id VARCHAR PRIMARY KEY,
            lead_id VARCHAR,
            channel VARCHAR,
            status VARCHAR DEFAULT 'active',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP
        )
    """)

    con.execute("""
        INSERT INTO conversations (id, lead_id, channel)
        VALUES (?, ?, ?)
    """, [conv_id, lead_id, channel])

    con.close()
    return conv_id


def add_message(conversation_id: str, role: str, content: str, metadata: Dict = None) -> str:
    """Add a message to a conversation."""
    con = connect()

    con.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id VARCHAR PRIMARY KEY,
            conversation_id VARCHAR,
            role VARCHAR,
            content TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    msg_id = f"msg_{uuid.uuid4().hex[:12]}"

    con.execute("""
        INSERT INTO conversation_messages (id, conversation_id, role, content, metadata)
        VALUES (?, ?, ?, ?, ?)
    """, [msg_id, conversation_id, role, content, json.dumps(metadata or {})])

    con.close()
    return msg_id


def get_conversation_history(conversation_id: str, limit: int = 20) -> List[Dict]:
    """Get message history for a conversation."""
    con = connect()

    messages = con.execute("""
        SELECT * FROM conversation_messages
        WHERE conversation_id = ?
        ORDER BY created_at
        LIMIT ?
    """, [conversation_id, limit]).fetchall()

    con.close()

    return [
        {
            "id": m[0],
            "conversation_id": m[1],
            "role": m[2],
            "content": m[3],
            "metadata": json.loads(m[4]) if m[4] else {},
            "created_at": m[5]
        }
        for m in messages
    ]


def get_lead_context(lead_id: str) -> Dict:
    """Get full context for a lead for AI generation."""
    con = connect()

    lead = con.execute("""
        SELECT * FROM crm_leads WHERE id = ?
    """, [lead_id]).fetchone()

    if not lead:
        con.close()
        return {}

    custom_fields = json.loads(lead[7]) if lead[7] else {}

    con.close()

    return {
        "email": lead[1],
        "name": lead[2] or "there",
        "company": lead[3] or "your company",
        "title": lead[4] or "",
        "stage": lead[5],
        "custom_fields": custom_fields
    }


def generate_ai_response(conversation_id: str, user_message: str,
                        prompt_name: str = "outreach/cold_email") -> str:
    """Generate an AI response based on conversation history and lead context."""
    con = connect()

    # Get conversation info
    conv = con.execute("""
        SELECT lead_id, channel FROM conversations WHERE id = ?
    """, [conversation_id]).fetchone()

    if not conv:
        con.close()
        return None

    lead_id, channel = conv[0], conv[1]

    # Get conversation history
    history = get_conversation_history(conversation_id)

    # Get lead context
    lead_context = get_lead_context(lead_id)

    # Get prompt
    prompt = con.execute("""
        SELECT content FROM prompts WHERE name = ? AND is_production = true
    """, [prompt_name]).fetchone()

    con.close()

    if not prompt:
        # Fallback to simple template
        response = f"Hi {lead_context['name']},\n\nThanks for your message. We'll get back to you shortly."
    else:
        # Build prompt with context
        prompt_template = prompt[0]

        # Replace variables
        context_vars = {
            "lead_name": lead_context["name"],
            "lead_company": lead_context["company"],
            "lead_title": lead_context["title"],
            "lead_email": lead_context["email"],
            "conversation_history": "\n".join([f"{m['role']}: {m['content']}" for m in history[-5:]]),
            "user_message": user_message
        }

        for var, value in context_vars.items():
            prompt_template = prompt_template.replace(f"{{{{{var}}}}}", str(value))

        # TODO: Call Ollama/ai_enrich.py for actual AI generation
        # For now, return the filled template
        response = prompt_template

    # Add AI response to conversation
    add_message(conversation_id, "assistant", response)

    return response


def build_campaign_from_natural_language(description: str) -> Dict:
    """Parse natural language description into campaign structure."""
    # This would use AI to parse the description
    # For now, return a simple structure

    campaign = {
        "name": "Campaign from description",
        "description": description,
        "channels": ["email"],
        "target_criteria": {},
        "sequence": [
            {
                "order": 1,
                "wait_days": 0,
                "subject": "Introduction",
                "body": "Hi {{lead_name}}, I noticed your work at {{lead_company}}..."
            }
        ]
    }

    # Simple keyword extraction
    if "whatsapp" in description.lower():
        campaign["channels"].append("whatsapp")
    if "linkedin" in description.lower():
        campaign["channels"].append("linkedin")

    return campaign


def classify_reply(email_content: str, sender_email: str, subject: str = "") -> str:
    """Classify an incoming email reply."""
    con = connect()

    # Get classification prompt
    prompt = con.execute("""
        SELECT content FROM prompts WHERE name = 'classification/reply' AND is_production = true
    """, []).fetchone()

    con.close()

    if not prompt:
        # Simple keyword-based classification
        content_lower = email_content.lower()

        if any(word in content_lower for word in ["interested", "yes", "let's", "sounds good", "tell me more"]):
            return "interested"
        elif any(word in content_lower for word in ["not interested", "no thanks", "remove", "unsubscribe"]):
            return "not_interested"
        elif any(word in content_lower for word in ["out of office", "vacation", "away"]):
            return "out_of_office"
        elif any(word in content_lower for word in ["spam", "junk"]):
            return "spam"
        else:
            return "need_info"
    else:
        # TODO: Use AI for classification
        return "need_info"


def classify_reply_and_update_lead(lead_id: str, email_content: str, sender_email: str):
    """Classify a reply and update lead stage accordingly."""
    classification = classify_reply(email_content, sender_email)

    con = connect()

    stage_mapping = {
        "interested": "negotiating",
        "not_interested": "lost",
        "out_of_office": "contacted",  # Keep same stage, just note it
        "spam": "lost",
        "need_info": "contacted"
    }

    new_stage = stage_mapping.get(classification, "contacted")

    con.execute("""
        UPDATE crm_leads
        SET stage_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, [new_stage, lead_id])

    con.close()

    return classification


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Conversational AI system")
    parser.add_argument("command", choices=["create-conv", "add-msg", "respond", "classify", "build-campaign"],
                        help="Command to run")
    parser.add_argument("--lead-id", help="Lead ID")
    parser.add_argument("--conv-id", help="Conversation ID")
    parser.add_argument("--role", help="Message role (user/assistant)")
    parser.add_argument("--content", help="Message content")
    parser.add_argument("--channel", default="email", help="Channel")
    parser.add_argument("--description", help="Campaign description")

    args = parser.parse_args()

    if args.command == "create-conv":
        conv_id = create_conversation(args.lead_id, args.channel)
        print(f"Conversation created: {conv_id}")
    elif args.command == "add-msg":
        msg_id = add_message(args.conv_id, args.role, args.content)
        print(f"Message added: {msg_id}")
    elif args.command == "respond":
        response = generate_ai_response(args.conv_id, args.content)
        print(f"AI Response: {response}")
    elif args.command == "classify":
        classification = classify_reply(args.content, "sender@example.com")
        print(f"Classification: {classification}")
    elif args.command == "build-campaign":
        campaign = build_campaign_from_natural_language(args.description)
        print(f"Campaign: {json.dumps(campaign, indent=2)}")
