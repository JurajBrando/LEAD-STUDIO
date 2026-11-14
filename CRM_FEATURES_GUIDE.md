# Lead Studio CRM Features - Implementation Guide

## Overview

This document describes the new CRM, automation, and analytics features added to Lead Studio, inspired by AgentSDR, OpenOutreach, and other tools from the Inspiration folder.

## New Files Created

### Core Schema
- **lead_studio_schema.py** - Database schema for all new features (14 tables)
  - API Keys management
  - Channel Settings (sending rules)
  - Organizations & Users (multi-tenancy)
  - CRM Stages, Leads, Sequences, Drafts
  - Campaigns, Campaign Channels, Campaign Sends
  - Email Health monitoring
  - Prompts (Langfuse integration)

### CRM & Automation
- **crm_sequences.py** - Sequences & Auto-Follow-ups engine
- **conversational_ai.py** - Conversational AI outreach system
- **cross_channel_campaigns.py** - Multi-channel campaigns
- **email_health.py** - Email health monitoring & warm-up

### Data & Analytics
- **openrefine_bridge.py** - OpenRefine integration for data cleaning
- **metabase_setup.py** - Metabase integration for data visualization

### UI & Team
- **crm_kanban.html** - Kanban board UI for CRM (drag-and-drop)
- **multi_tenancy.py** - Multi-tenancy and team support

---

## Quick Start

### 1. Initialize the Schema

```bash
cd C:\Users\admin\Desktop\Modulario\LEAD_STUDIO
python lead_studio_schema.py init
python lead_studio_schema.py status
```

This creates 14 tables in `LEAD_INDEX/lead_studio.db`:
- api_keys, channel_settings, organizations, users
- crm_stages, crm_leads, crm_sequences, crm_sequence_steps, crm_drafts
- campaigns, campaign_channels, campaign_sends
- email_health, prompts

### 2. Set Up API Keys

Replace environment variables with the API keys table:

```bash
python lead_studio_schema.py add-key --name "ManyReach" --provider "manyreach" --key "your-api-key" --description "Main ManyReach account"
python lead_studio_schema.py add-key --name "Ollama" --provider "ollama" --key "your-ollama-key" --description "Ollama Cloud"
python lead_studio_schema.py add-key --name "Langfuse" --provider "langfuse" --key "your-langfuse-key" --description "Prompt management"
python lead_studio_schema.py list-keys
```

### 3. Configure Sending Rules

The default rules are already set up. To customize:

```python
from lead_studio_schema import update_channel_settings

update_channel_settings("email", {
    "daily_limit": 100,
    "send_gap_minutes": 30,
    "sending_hours_start": 9,
    "sending_hours_end": 17,
    "timezone": "UTC"
})
```

### 4. Open the CRM Kanban Board

Open `crm_kanban.html` in your browser:

```bash
start crm_kanban.html  # Windows
# or open it directly in your browser
```

The Kanban board has 6 stages: New → Contacted → Replied → Negotiating → Closed → Lost

---

## Feature Deep Dives

### 1. API Keys Management

**Purpose:** Store API keys securely (ManyReach, Ollama, Langfuse, etc.) instead of environment variables.

**Usage:**
```python
from lead_studio_schema import add_api_key, get_api_key

# Add a key
key_id = add_api_key("ManyReach Production", "manyreach", "sk-12345", "Main account")

# Get a key when needed
key = get_api_key("manyreach")
```

**Tables:** `api_keys` (id, name, provider, key_hash, key_encrypted, description, created_at, last_used_at, is_active)

---

### 2. Sending Rules System

**Purpose:** Centralized sending rules per channel (emails/day, gaps, hours, warm-up).

**Default Rules:**
- Email: 50/day, 30 min gap, 9 AM - 5 PM
- WhatsApp: 25/day, 60 min gap
- LinkedIn: 25/day, 60 min gap
- Social: 10/day, 120 min gap

**Usage:**
```python
from lead_studio_schema import get_channel_settings, update_channel_settings

# Get current rules
rules = get_channel_settings("email")
print(f"Daily limit: {rules['daily_limit']}")

# Update rules
update_channel_settings("email", {"daily_limit": 100, "send_gap_minutes": 15})
```

**Tables:** `channel_settings` (id, channel, daily_limit, send_gap_minutes, sending_hours_start, sending_hours_end, timezone, warmup_days)

---

### 3. CRM Pipeline Stages

**Purpose:** Track leads through a sales pipeline with visual Kanban board.

**Default Stages:**
1. New (purple)
2. Contacted (violet)
3. Replied (pink)
4. Negotiating (orange)
5. Closed (green)
6. Lost (red)

**Usage:**
```python
from crm_sequences import create_lead, move_lead_stage

# Create a lead
lead_id = create_lead(
    email="john@example.com",
    name="John Smith",
    company="Acme Corp",
    title="CEO",
    stage_id="new",
    source="CC_BULK"
)

# Move to next stage
move_lead_stage(lead_id, "contacted")
```

**Tables:** `crm_stages`, `crm_leads`

**UI:** Open `crm_kanban.html` for drag-and-drop stage management.

---

### 4. Sequences & Auto-Follow-ups

**Purpose:** Create email sequences per stage with automatic follow-up scheduling.

**Usage:**
```python
from crm_sequences import create_sequence, add_sequence_step, schedule_follow_ups

# Create a sequence for the "contacted" stage
seq_id = create_sequence(
    name="Contacted Follow-up",
    stage_id="contacted",
    auto_send_follow_ups=True,
    follow_up_gap_minutes=30
)

# Add steps
add_sequence_step(seq_id, 1, wait_days=0, subject="Introduction", body="Hi {{lead_name}}...")
add_sequence_step(seq_id, 2, wait_days=3, subject="Checking in", body="Just following up...")
add_sequence_step(seq_id, 3, wait_days=7, subject="Final attempt", body="Last chance...")

# Schedule follow-ups for a lead
schedule_follow_ups(lead_id, seq_id, sender="sales@adtracker.fun")
```

**Auto-Send Engine:**
```python
from crm_sequences import get_pending_drafts, check_sending_rules, mark_draft_sent

# Get drafts ready to send
drafts = get_pending_drafts(limit=100)

for draft in drafts:
    # Check if sending is allowed
    check = check_sending_rules("email")
    if check["allowed"]:
        # Send the email (via mailer.py)
        # mark_draft_sent(draft["id"], "sent")
        pass
```

**Tables:** `crm_sequences`, `crm_sequence_steps`, `crm_drafts`

---

### 5. Conversational AI Outreach

**Purpose:** Multi-turn AI conversations with context-aware responses.

**Usage:**
```python
from conversational_ai import create_conversation, add_message, generate_ai_response

# Start a conversation
conv_id = create_conversation(lead_id, channel="email")

# Add user message
add_message(conv_id, "user", "I'm interested in your services")

# Generate AI response
response = generate_ai_response(conv_id, "I'm interested in your services",
                               prompt_name="outreach/cold_email")
print(response)

# Classify incoming replies
from conversational_ai import classify_reply_and_update_lead
classification = classify_reply_and_update_lead(lead_id, "Yes, let's proceed", "lead@example.com")
# Returns: "interested", "not_interested", "need_info", "out_of_office", "spam"
```

**Tables:** `conversations`, `conversation_messages`

---

### 6. OpenRefine Data Cleaning

**Purpose:** Visual data cleaning with transform rules, deduplication, standardization.

**Usage:**
```python
from openrefine_bridge import export_to_openrefine, deduplicate_csv, standardize_fields

# Export data for OpenRefine
export_to_openrefine("crm_leads", "LEAD_INDEX/metabase_exports/leads.csv")

# Apply transform rules
from openrefine_bridge import apply_transform_rule
apply_transform_rule("leads.csv", "email", "standardize_email", "email_transform.json")

# Deduplicate by email
deduplicate_csv("leads.csv", ["email"], "leads_dedup.csv")

# Standardize fields
mapping = {"First Name": "name", "Company Name": "company", "Email Address": "email"}
standardize_fields("leads.csv", mapping, "leads_clean.csv")
```

**Available Transform Rules:**
- `standardize_email` - Lowercase and trim
- `standardize_phone` - Remove non-numeric characters
- `standardize_company` - Title case, remove suffixes
- `extract_domain` - Extract domain from email
- `remove_freemail` - Filter out freemail domains
- `deduplicate_email` - Mark duplicates

**Tables:** Uses CSV export/import

---

### 7. Metabase Data Visualization

**Purpose:** Visual analytics dashboard for lead data.

**Usage:**
```python
from metabase_setup import export_analytics_data, create_dashboard_definition, get_quick_stats

# Export all analytics data
exported = export_analytics_data("LEAD_INDEX/metabase_exports")

# Generate dashboard definition
dashboard = create_dashboard_definition("Lead Studio Dashboard")
save_dashboard_definition(dashboard, "LEAD_INDEX/metabase_exports/dashboard.json")

# Get quick stats
stats = get_quick_stats()
print(f"Total leads: {stats['total_leads']}")
print(f"New today: {stats['new_today']}")
print(f"Active campaigns: {stats['active_campaigns']}")
```

**Pre-built Queries:**
- `lead_source_breakdown` - Leads by source
- `conversion_funnel` - Pipeline conversion
- `geographic_distribution` - Leads by country
- `technology_adoption` - Technologies used
- `email_performance` - Email stats over time
- `campaign_performance` - Campaign metrics
- `stage_velocity` - Average time in each stage
- `daily_activity` - New leads per day

**Setup Metabase:**
1. Download Metabase from https://www.metabase.com/start/
2. Connect to DuckDB (or export to PostgreSQL)
3. Import the dashboard JSON
4. Use the pre-built SQL queries

---

### 8. Cross-Channel Campaigns

**Purpose:** Unified campaigns across email, WhatsApp, LinkedIn, and social.

**Usage:**
```python
from cross_channel_campaigns import create_campaign, add_campaign_channel, add_leads_to_campaign

# Create campaign
camp_id = create_campaign("Q4 Outreach", "Contact all high-value leads")

# Add channels
add_campaign_channel(camp_id, "email", {"template": "cold_email_v1"})
add_campaign_channel(camp_id, "whatsapp", {"template": "whatsapp_intro"})

# Add leads
leads = ["john@example.com", "jane@example.com"]
add_leads_to_campaign(camp_id, leads, channel="email")

# Execute sends
from cross_channel_campaigns import execute_campaign_send
sent = execute_campaign_send(camp_id, "email", limit=50)

# Get unified report
from cross_channel_campaigns import get_cross_channel_report
report = get_cross_channel_report(camp_id)
print(f"Sent: {report['overall']['sent']}, Replied: {report['overall']['replied']}")

# Schedule cross-channel follow-ups
from cross_channel_campaigns import schedule_cross_channel_follow_up
schedule_cross_channel_follow_up(camp_id, "lead@example.com",
                                   ["email", "whatsapp", "linkedin"],
                                   [0, 24, 72])  # Email now, WhatsApp in 24h, LinkedIn in 72h
```

**Tables:** `campaigns`, `campaign_channels`, `campaign_sends`

---

### 9. Email Health Monitoring

**Purpose:** Track mailbox reputation, warm-up schedules, and automatic throttling.

**Usage:**
```python
from email_health import register_mailbox, update_daily_stats, check_send_permission

# Register a mailbox
register_mailbox("sales@adtracker.fun", "smtp.m1.websupport.sk", "adtracker.fun", warmup_limit=10)

# Update daily stats
update_daily_stats("sales@adtracker.fun", sent=50, bounced=2, spam=0)

# Check if sending is allowed
permission = check_send_permission("sales@adtracker.fun")
if permission["allowed"]:
    print(f"Can send. Sent today: {permission['sent_today']}, Limit: {permission['warmup_limit']}")
else:
    print(f"Cannot send: {permission['reason']}")

# Advance warm-up
from email_health import advance_warmup
result = advance_warmup("sales@adtracker.fun")
print(f"Warm-up day {result['warmup_day']}, limit: {result['warmup_limit']}")

# Get all mailbox health
from email_health import get_all_mailbox_health, get_health_summary
health = get_all_mailbox_health()
summary = get_health_summary()
print(f"Good: {summary['good']}, Warning: {summary['warning']}, Critical: {summary['critical']}")
```

**Warm-up Schedule:**
- Day 0: 10 emails
- Day 1: 20 emails
- Day 2: 40 emails
- Day 3: 80 emails
- Day 4: 150 emails
- Day 5: 250 emails
- Day 6: 400 emails
- Day 7+: 500 emails

**Tables:** `email_health`

---

### 10. Multi-Tenancy & Teams

**Purpose:** Support multiple organizations with role-based access control.

**Usage:**
```python
from multi_tenancy import create_organization, create_user, verify_login, check_permission

# Create organization
org_id = create_organization("Acme Corp", "acme-corp")

# Create users
admin_id = create_user("admin@acme.com", "Admin User", "password123", role="admin", organization_id=org_id)
member_id = create_user("sales@acme.com", "Sales User", "password123", role="member", organization_id=org_id)

# Verify login
user = verify_login("admin@acme.com", "password123")
if user:
    print(f"Logged in as {user['name']} ({user['role']})")

# Check permissions
if check_permission(user["id"], "manage_campaigns"):
    print("Can manage campaigns")

# List users in org
from multi_tenancy import get_users_by_organization
users = get_users_by_organization(org_id)
for u in users:
    print(f"{u['email']} - {u['role']}")
```

**Roles:**
- **owner** - Full access, can manage users
- **admin** - Can manage campaigns, view analytics, send emails
- **member** - Can view leads and analytics

**Tables:** `organizations`, `users`

---

## Integration with Existing Lead Studio

### Adding to lead_studio.py

To integrate these features into the main Lead Studio UI, add API endpoints:

```python
# In lead_studio.py, add to do_POST:

if self.path == "/api/crm/leads":
    from crm_sequences import create_lead, get_conversation_history
    # Handle lead CRUD

if self.path == "/api/crm/kanban":
    # Return kanban data

if self.path == "/api/campaigns":
    from cross_channel_campaigns import create_campaign, get_campaign
    # Handle campaign CRUD

if self.path == "/api/health":
    from email_health import get_all_mailbox_health
    # Return health data

if self.path == "/api/analytics":
    from metabase_setup import get_quick_stats
    # Return analytics
```

### Adding to lead_studio_tools.json

Register the new tools:

```json
{
  "id": "crm_kanban",
  "group": "CRM",
  "name": "CRM Kanban Board",
  "desc": "Visual pipeline management with drag-and-drop",
  "dir": "LEAD_STUDIO",
  "script": "crm_kanban.html",
  "args": ""
},
{
  "id": "email_health",
  "group": "CRM",
  "name": "Email Health Monitor",
  "desc": "Track mailbox reputation and warm-up schedules",
  "dir": "LEAD_STUDIO",
  "script": "email_health.py",
  "args": "summary"
}
```

---

## MCP Server Integration

Add MCP tools to lead_studio.py:

```python
MCP_TOOLS.extend([
    {"name": "create_crm_lead", "description": "Create a lead in the CRM pipeline", ...},
    {"name": "move_lead_stage", "description": "Move a lead to a new stage", ...},
    {"name": "create_campaign", "description": "Create a multi-channel campaign", ...},
    {"name": "check_email_health", "description": "Check mailbox health status", ...},
    {"name": "get_analytics", "description": "Get lead analytics", ...}
])
```

---

## Next Steps

### Phase 1 - Integration (Week 1-2)
1. Integrate schema into main lead_studio.py
2. Add UI pages for CRM, Campaigns, Health
3. Connect to existing mailer.py for actual sending
4. Add API key management UI

### Phase 2 - AI Integration (Week 3-4)
1. Connect to ai_enrich.py for actual AI generation
2. Integrate Langfuse for prompt versioning
3. Add conversation tracking to leads
4. Implement reply classification

### Phase 3 - Automation (Week 5-6)
1. Set up cron jobs for auto-follow-ups
2. Implement email health checks
3. Add daily stats reset
4. Build campaign scheduler

### Phase 4 - Analytics (Week 7-8)
1. Deploy Metabase
2. Create dashboards
3. Set up real-time updates
4. Add export capabilities

---

## File Structure

```
LEAD_STUDIO/
├── lead_studio_schema.py      # Database schema
├── crm_sequences.py            # Sequences & auto-follow-ups
├── conversational_ai.py        # AI conversations
├── cross_channel_campaigns.py  # Multi-channel campaigns
├── email_health.py             # Health monitoring
├── openrefine_bridge.py        # Data cleaning
├── metabase_setup.py           # Analytics
├── crm_kanban.html             # Kanban UI
├── multi_tenancy.py            # Teams & organizations
└── CRM_FEATURES_GUIDE.md       # This file
```

---

## Database Schema Summary

**14 Tables:**
1. `api_keys` - Secure API key storage
2. `channel_settings` - Sending rules per channel
3. `organizations` - Multi-tenancy
4. `users` - User accounts with roles
5. `crm_stages` - Pipeline stages
6. `crm_leads` - Lead records
7. `crm_sequences` - Email sequences
8. `crm_sequence_steps` - Sequence steps
9. `crm_drafts` - AI-generated drafts
10. `campaigns` - Multi-channel campaigns
11. `campaign_channels` - Campaign channel assignments
12. `campaign_sends` - Individual send records
13. `email_health` - Mailbox health tracking
14. `prompts` - Langfuse prompt management

---

## Security Notes

1. **API Keys:** Currently stored as-is. Add encryption before production.
2. **Passwords:** SHA-256 hashed. Consider bcrypt or Argon2 for production.
3. **Permissions:** Basic role-based. Add fine-grained permissions.
4. **Organization Isolation:** Implemented in schema. Add enforcement in queries.

---

## Performance Considerations

1. **DuckDB:** Fast for analytics. Consider PostgreSQL for production multi-tenancy.
2. **Indexes:** Added on key columns (email, stage_id, organization_id).
3. **Batch Operations:** All operations support batch processing.
4. **Caching:** Add Redis for session data and API key caching.

---

## Support

For issues or questions:
1. Check this guide first
2. Review AgentSDR's patterns in `Inspiration/AgentSDR-main/CLAUDE.md`
3. Refer to existing Lead Studio code in `lead_studio.py` and `lead_index.py`

---

## License

These additions follow the same license as Lead Studio.
