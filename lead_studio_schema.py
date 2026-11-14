"""
lead_studio_schema.py - Extended schema for Lead Studio CRM features.

This file defines the DuckDB schema for new CRM, campaigns, and automation features.
The main lead database (leads.parquet) remains unchanged - this adds relational tables
for workflow management.

New tables:
  - api_keys: API key management (ManyReach, Ollama, Langfuse, etc.)
  - channel_settings: Sending rules per channel (emails/day, gaps, hours)
  - organizations: Multi-tenancy support
  - users: User accounts with roles
  - crm_stages: Pipeline stages (new, contacted, replied, etc.)
  - crm_leads: Lead records with stage and history
  - crm_sequences: Email sequences per stage
  - crm_sequence_steps: Individual email steps in a sequence
  - crm_drafts: AI-generated email drafts
  - campaigns: Multi-channel campaigns
  - campaign_channels: Campaign channel assignments
  - campaign_sends: Individual send records
  - email_health: Mailbox health and reputation tracking
  - prompts: Langfuse prompt management
"""
import os, sys, json, time
try:
    import duckdb
except ImportError:
    subprocess = __import__('subprocess')
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "duckdb"])
    import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(HERE))
DB = os.path.join(ROOT, "LEAD_INDEX", "lead_studio.db")


def connect():
    """Connect to the Lead Studio DuckDB database."""
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order=false")
    return con


def init_schema(con=None):
    """Initialize all new schema tables."""
    if con is None:
        con = connect()
        should_close = True
    else:
        should_close = False

    print("Initializing Lead Studio schema...")

    # ------------------------------------------------------------------ API Keys
    con.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            provider VARCHAR NOT NULL,  -- 'manyreach', 'ollama', 'langfuse', 'openrouter', etc.
            key_hash VARCHAR NOT NULL,  -- SHA-256 hash of the key
            key_encrypted TEXT,         -- Encrypted key value
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP,
            is_active BOOLEAN DEFAULT true
        )
    """)

    # ------------------------------------------------------------------ Channel Settings (Sending Rules)
    con.execute("""
        CREATE TABLE IF NOT EXISTS channel_settings (
            id VARCHAR PRIMARY KEY,
            channel VARCHAR NOT NULL,  -- 'email', 'whatsapp', 'linkedin', 'social'
            daily_limit INTEGER DEFAULT 50,
            daily_limit_min INTEGER DEFAULT 10,
            daily_limit_max INTEGER DEFAULT 500,
            send_gap_minutes INTEGER DEFAULT 30,
            send_gap_minutes_min INTEGER DEFAULT 5,
            send_gap_minutes_max INTEGER DEFAULT 300,
            sending_hours_start INTEGER DEFAULT 9,  -- 9 AM
            sending_hours_end INTEGER DEFAULT 17,   -- 5 PM
            timezone VARCHAR DEFAULT 'UTC',
            warmup_days INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert default sending rules
    con.execute("""
        INSERT INTO channel_settings (id, channel, daily_limit, send_gap_minutes)
        VALUES
            ('default-email', 'email', 50, 30),
            ('default-whatsapp', 'whatsapp', 25, 60),
            ('default-linkedin', 'linkedin', 25, 60),
            ('default-social', 'social', 10, 120)
        ON CONFLICT (id) DO NOTHING
    """)

    # ------------------------------------------------------------------ Organizations (Multi-tenancy)
    con.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            slug VARCHAR UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT true
        )
    """)

    # Insert default organization
    con.execute("""
        INSERT INTO organizations (id, name, slug)
        VALUES ('default', 'Default Workspace', 'default')
        ON CONFLICT (id) DO NOTHING
    """)

    # ------------------------------------------------------------------ Users
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR PRIMARY KEY,
            email VARCHAR UNIQUE NOT NULL,
            name VARCHAR,
            password_hash VARCHAR,
            role VARCHAR DEFAULT 'member',  -- 'owner', 'admin', 'member'
            organization_id VARCHAR REFERENCES organizations(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------ CRM Stages
    con.execute("""
        CREATE TABLE IF NOT EXISTS crm_stages (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            order_index INTEGER NOT NULL,
            color VARCHAR DEFAULT '#6366f1',
            is_default BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert default CRM stages
    con.execute("""
        INSERT INTO crm_stages (id, name, order_index, color, is_default)
        VALUES
            ('new', 'New', 1, '#6366f1', true),
            ('contacted', 'Contacted', 2, '#8b5cf6', true),
            ('replied', 'Replied', 3, '#ec4899', true),
            ('negotiating', 'Negotiating', 4, '#f59e0b', true),
            ('closed', 'Closed', 5, '#10b981', true),
            ('lost', 'Lost', 6, '#ef4444', true)
        ON CONFLICT (id) DO NOTHING
    """)

    # ------------------------------------------------------------------ CRM Leads
    con.execute("""
        CREATE TABLE IF NOT EXISTS crm_leads (
            id VARCHAR PRIMARY KEY,
            email VARCHAR NOT NULL,
            name VARCHAR,
            company VARCHAR,
            title VARCHAR,
            stage_id VARCHAR REFERENCES crm_stages(id),
            organization_id VARCHAR REFERENCES organizations(id),
            source VARCHAR,  -- Which CSV/source this came from
            custom_fields JSON,  -- Flexible additional fields
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_contacted_at TIMESTAMP
        )
    """)

    con.execute("CREATE INDEX IF NOT EXISTS idx_crm_leads_email ON crm_leads(email)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_crm_leads_stage ON crm_leads(stage_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_crm_leads_org ON crm_leads(organization_id)")

    # ------------------------------------------------------------------ CRM Sequences
    con.execute("""
        CREATE TABLE IF NOT EXISTS crm_sequences (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            stage_id VARCHAR REFERENCES crm_stages(id),
            organization_id VARCHAR REFERENCES organizations(id),
            auto_send_follow_ups BOOLEAN DEFAULT false,
            follow_up_gap_minutes INTEGER DEFAULT 30,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------ CRM Sequence Steps
    con.execute("""
        CREATE TABLE IF NOT EXISTS crm_sequence_steps (
            id VARCHAR PRIMARY KEY,
            sequence_id VARCHAR REFERENCES crm_sequences(id),
            order_index INTEGER NOT NULL,
            wait_days INTEGER DEFAULT 1,
            subject_template TEXT,
            body_template TEXT,
            instructions TEXT,  -- AI instructions for this step
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------ CRM Drafts
    con.execute("""
        CREATE TABLE IF NOT EXISTS crm_drafts (
            id VARCHAR PRIMARY KEY,
            lead_id VARCHAR REFERENCES crm_leads(id),
            sequence_step_id VARCHAR REFERENCES crm_sequence_steps(id),
            sender VARCHAR,  -- Mailbox name or LinkedIn account name
            subject TEXT,
            body TEXT,
            status VARCHAR DEFAULT 'pending',  -- 'pending', 'sent', 'skipped', 'failed'
            auto_send_at TIMESTAMP,
            sent_at TIMESTAMP,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("CREATE INDEX IF NOT EXISTS idx_crm_drafts_lead ON crm_drafts(lead_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_crm_drafts_status ON crm_drafts(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_crm_drafts_auto_send ON crm_drafts(auto_send_at)")

    # ------------------------------------------------------------------ Campaigns
    con.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            description TEXT,
            organization_id VARCHAR REFERENCES organizations(id),
            status VARCHAR DEFAULT 'draft',  -- 'draft', 'active', 'paused', 'completed'
            start_date DATE,
            end_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------ Campaign Channels
    con.execute("""
        CREATE TABLE IF NOT EXISTS campaign_channels (
            id VARCHAR PRIMARY KEY,
            campaign_id VARCHAR REFERENCES campaigns(id),
            channel VARCHAR NOT NULL,  -- 'email', 'whatsapp', 'linkedin', 'social'
            configuration JSON,  -- Channel-specific settings
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------ Campaign Sends
    con.execute("""
        CREATE TABLE IF NOT EXISTS campaign_sends (
            id VARCHAR PRIMARY KEY,
            campaign_id VARCHAR REFERENCES campaigns(id),
            campaign_channel_id VARCHAR REFERENCES campaign_channels(id),
            lead_email VARCHAR NOT NULL,
            channel VARCHAR NOT NULL,
            status VARCHAR DEFAULT 'pending',  -- 'pending', 'sent', 'delivered', 'opened', 'clicked', 'replied', 'bounced', 'failed'
            sent_at TIMESTAMP,
            delivered_at TIMESTAMP,
            opened_at TIMESTAMP,
            clicked_at TIMESTAMP,
            replied_at TIMESTAMP,
            error_message TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("CREATE INDEX IF NOT EXISTS idx_campaign_sends_campaign ON campaign_sends(campaign_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_campaign_sends_status ON campaign_sends(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_campaign_sends_email ON campaign_sends(lead_email)")

    # ------------------------------------------------------------------ Email Health
    con.execute("""
        CREATE TABLE IF NOT EXISTS email_health (
            id VARCHAR PRIMARY KEY,
            mailbox_name VARCHAR NOT NULL,
            smtp_host VARCHAR,
            domain VARCHAR,
            daily_sent_count INTEGER DEFAULT 0,
            daily_bounce_count INTEGER DEFAULT 0,
            daily_spam_count INTEGER DEFAULT 0,
            reputation_score INTEGER DEFAULT 100,  -- 0-100
            warmup_day INTEGER DEFAULT 0,
            warmup_limit INTEGER DEFAULT 10,
            last_check_at TIMESTAMP,
            health_status VARCHAR DEFAULT 'good',  -- 'good', 'warning', 'critical'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("CREATE INDEX IF NOT EXISTS idx_email_health_mailbox ON email_health(mailbox_name)")

    # ------------------------------------------------------------------ Prompts (Langfuse Integration)
    con.execute("""
        CREATE TABLE IF NOT EXISTS prompts (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL UNIQUE,
            area VARCHAR NOT NULL,  -- 'enrichment', 'crm', 'outreach', 'classification'
            version VARCHAR NOT NULL,
            content TEXT NOT NULL,
            variables TEXT,  -- JSON string of variables used in the template
            is_production BOOLEAN DEFAULT false,
            langfuse_prompt_id VARCHAR,  -- Reference to Langfuse
            organization_id VARCHAR REFERENCES organizations(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_prompts_area ON prompts(area)")

    # Insert default prompts
    default_prompts = [
        {
            "name": "enrich/company_profile",
            "area": "enrichment",
            "version": "1.0",
            "content": "Analyze this company based on its website and available data. Return a JSON with: industry, size (1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5000+), key technologies, business description (max 200 chars), and a lead_score (0-100).\n\nCompany: {{company}}\nWebsite: {{website}}\nDescription: {{description}}",
            "variables": ["company", "website", "description"],
            "is_production": True
        },
        {
            "name": "crm/draft_reply",
            "area": "crm",
            "version": "1.0",
            "content": "Write a reply to this lead as {{sender}}. Be professional, concise, and helpful. Address their concerns. Don't make promises you can't keep.\n\nLead: {{lead_name}} at {{lead_company}}\nTheir message: {{lead_message}}\nConversation history: {{conversation_history}}\nStage context: {{stage_context}}",
            "variables": ["sender", "lead_name", "lead_company", "lead_message", "conversation_history", "stage_context"],
            "is_production": True
        },
        {
            "name": "outreach/cold_email",
            "area": "outreach",
            "version": "1.0",
            "content": "Write a cold email to {{lead_name}} at {{lead_company}} ({{lead_title}}). You are {{sender}} from {{your_company}}. Personalize based on: {{personalization_data}}. Keep it under 150 words. Focus on value, not features. End with a clear call to action.",
            "variables": ["lead_name", "lead_company", "lead_title", "sender", "your_company", "personalization_data"],
            "is_production": True
        },
        {
            "name": "classification/reply",
            "area": "classification",
            "version": "1.0",
            "content": "Classify this email reply into one of: interested, not_interested, need_info, out_of_office, spam, other. Return just the category.\n\nFrom: {{sender_email}}\nSubject: {{subject}}\nBody: {{body}}",
            "variables": ["sender_email", "subject", "body"],
            "is_production": True
        }
    ]

    for prompt in default_prompts:
        prompt_id = prompt["name"].replace("/", "_")
        try:
            con.execute("""
                INSERT INTO prompts (id, name, area, version, content, variables, is_production)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                prompt_id,
                prompt["name"],
                prompt["area"],
                prompt["version"],
                prompt["content"],
                json.dumps(prompt["variables"]),
                prompt["is_production"]
            ])
        except Exception:
            # Already exists, skip
            pass

    print("Schema initialized successfully.")

    if should_close:
        con.close()


def get_channel_settings(con=None, channel='email'):
    """Get sending rules for a channel."""
    if con is None:
        con = connect()
        should_close = True
    else:
        should_close = False

    result = con.execute("""
        SELECT * FROM channel_settings WHERE channel = ?
    """, [channel]).fetchone()

    if should_close:
        con.close()

    if result:
        return {
            "id": result[0],
            "channel": result[1],
            "daily_limit": result[2],
            "daily_limit_min": result[3],
            "daily_limit_max": result[4],
            "send_gap_minutes": result[5],
            "send_gap_minutes_min": result[6],
            "send_gap_minutes_max": result[7],
            "sending_hours_start": result[8],
            "sending_hours_end": result[9],
            "timezone": result[10],
            "warmup_days": result[11]
        }
    return None


def update_channel_settings(channel, settings, con=None):
    """Update sending rules for a channel."""
    if con is None:
        con = connect()
        should_close = True
    else:
        should_close = False

    con.execute("""
        UPDATE channel_settings
        SET daily_limit = COALESCE(?, daily_limit),
            send_gap_minutes = COALESCE(?, send_gap_minutes),
            sending_hours_start = COALESCE(?, sending_hours_start),
            sending_hours_end = COALESCE(?, sending_hours_end),
            timezone = COALESCE(?, timezone),
            warmup_days = COALESCE(?, warmup_days),
            updated_at = CURRENT_TIMESTAMP
        WHERE channel = ?
    """, [
        settings.get("daily_limit"),
        settings.get("send_gap_minutes"),
        settings.get("sending_hours_start"),
        settings.get("sending_hours_end"),
        settings.get("timezone"),
        settings.get("warmup_days"),
        channel
    ])

    if should_close:
        con.close()


def add_api_key(name, provider, key_value, description="", con=None):
    """Add an API key (stores hash, returns key ID)."""
    import hashlib

    if con is None:
        con = connect()
        should_close = True
    else:
        should_close = False

    key_id = f"{provider}_{name.lower().replace(' ', '_')}_{int(time.time())}"
    key_hash = hashlib.sha256(key_value.encode()).hexdigest()

    # In production, you'd encrypt the key. For now, store as-is (TODO: add encryption)
    con.execute("""
        INSERT INTO api_keys (id, name, provider, key_hash, key_encrypted, description)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            name = excluded.name,
            key_encrypted = excluded.key_encrypted,
            description = excluded.description,
            last_used_at = CURRENT_TIMESTAMP
    """, [key_id, name, provider, key_hash, key_value, description])

    if should_close:
        con.close()

    return key_id


def get_api_key(provider, con=None):
    """Get the active API key for a provider."""
    if con is None:
        con = connect()
        should_close = True
    else:
        should_close = False

    result = con.execute("""
        SELECT key_encrypted FROM api_keys
        WHERE provider = ? AND is_active = true
        ORDER BY created_at DESC
        LIMIT 1
    """, [provider]).fetchone()

    if should_close:
        con.close()

    return result[0] if result else None


def list_api_keys(con=None):
    """List all API keys (without the actual keys)."""
    if con is None:
        con = connect()
        should_close = True
    else:
        should_close = False

    results = con.execute("""
        SELECT id, name, provider, description, created_at, last_used_at, is_active
        FROM api_keys
        ORDER BY created_at DESC
    """).fetchall()

    if should_close:
        con.close()

    return [
        {
            "id": r[0],
            "name": r[1],
            "provider": r[2],
            "description": r[3],
            "created_at": r[4],
            "last_used_at": r[5],
            "is_active": r[6]
        }
        for r in results
    ]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lead Studio schema management")
    parser.add_argument("command", choices=["init", "status", "add-key", "list-keys"],
                        help="Command to run")
    parser.add_argument("--name", help="Key name (for add-key)")
    parser.add_argument("--provider", help="Provider (manyreach, ollama, langfuse, etc.)")
    parser.add_argument("--key", help="API key value (for add-key)")
    parser.add_argument("--description", default="", help="Key description")

    args = parser.parse_args()

    if args.command == "init":
        init_schema()
    elif args.command == "status":
        con = connect()
        print("\n=== Schema Status ===")
        tables = con.execute("SHOW TABLES").fetchall()
        print(f"Tables: {len(tables)}")
        for t in tables:
            count = con.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
            print(f"  {t[0]}: {count} rows")
        con.close()
    elif args.command == "add-key":
        if not args.name or not args.provider or not args.key:
            print("Error: --name, --provider, and --key are required for add-key")
            sys.exit(1)
        key_id = add_api_key(args.name, args.provider, args.key, args.description)
        print(f"API key added: {key_id}")
    elif args.command == "list-keys":
        keys = list_api_keys()
        print("\n=== API Keys ===")
        for k in keys:
            print(f"  {k['name']} ({k['provider']}): {k['id']} - Active: {k['is_active']}")
