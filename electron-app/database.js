const Database = require('duckdb');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(path.join(__dirname, '..'));
const IDX = path.join(ROOT, 'LEAD_INDEX');
const DB_PATH = path.join(IDX, 'lead_studio.db');

// Ensure directory exists
if (!fs.existsSync(IDX)) {
  fs.mkdirSync(IDX, { recursive: true });
}

// Database connection
let db;

function connect() {
  if (!db) {
    db = new Database(DB_PATH);
    db.exec('SET preserve_insertion_order=false');
  }
  return db;
}

// ------------------------------------------------------------------ Schema Initialization

function initSchema() {
  const con = connect();
  
  console.log('Initializing Lead Studio schema...');
  
  // API Keys
  con.exec(`
    CREATE TABLE IF NOT EXISTS api_keys (
      id VARCHAR PRIMARY KEY,
      name VARCHAR NOT NULL,
      provider VARCHAR NOT NULL,
      key_hash VARCHAR NOT NULL,
      key_encrypted TEXT,
      description TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      last_used_at TIMESTAMP,
      is_active BOOLEAN DEFAULT true
    )
  `);
  
  // Channel Settings
  con.exec(`
    CREATE TABLE IF NOT EXISTS channel_settings (
      id VARCHAR PRIMARY KEY,
      channel VARCHAR NOT NULL,
      daily_limit INTEGER DEFAULT 50,
      daily_limit_min INTEGER DEFAULT 10,
      daily_limit_max INTEGER DEFAULT 500,
      send_gap_minutes INTEGER DEFAULT 30,
      send_gap_minutes_min INTEGER DEFAULT 5,
      send_gap_minutes_max INTEGER DEFAULT 300,
      sending_hours_start INTEGER DEFAULT 9,
      sending_hours_end INTEGER DEFAULT 17,
      timezone VARCHAR DEFAULT 'UTC',
      warmup_days INTEGER DEFAULT 0,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // Insert default settings
  con.run(`
    INSERT INTO channel_settings (id, channel, daily_limit, send_gap_minutes)
    VALUES
      ('default-email', 'email', 50, 30),
      ('default-whatsapp', 'whatsapp', 25, 60),
      ('default-linkedin', 'linkedin', 25, 60),
      ('default-social', 'social', 10, 120)
    ON CONFLICT (id) DO NOTHING
  `);
  
  // Organizations
  con.exec(`
    CREATE TABLE IF NOT EXISTS organizations (
      id VARCHAR PRIMARY KEY,
      name VARCHAR NOT NULL,
      slug VARCHAR UNIQUE NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      is_active BOOLEAN DEFAULT true
    )
  `);
  
  con.run(`
    INSERT INTO organizations (id, name, slug)
    VALUES ('default', 'Default Workspace', 'default')
    ON CONFLICT (id) DO NOTHING
  `);
  
  // Users
  con.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id VARCHAR PRIMARY KEY,
      email VARCHAR UNIQUE NOT NULL,
      name VARCHAR,
      password_hash VARCHAR,
      role VARCHAR DEFAULT 'member',
      organization_id VARCHAR,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      last_login_at TIMESTAMP
    )
  `);
  
  // CRM Stages
  con.exec(`
    CREATE TABLE IF NOT EXISTS crm_stages (
      id VARCHAR PRIMARY KEY,
      name VARCHAR NOT NULL,
      order_index INTEGER NOT NULL,
      color VARCHAR DEFAULT '#6366f1',
      is_default BOOLEAN DEFAULT false,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  con.run(`
    INSERT INTO crm_stages (id, name, order_index, color, is_default)
    VALUES
      ('new', 'New', 1, '#6366f1', true),
      ('contacted', 'Contacted', 2, '#8b5cf6', true),
      ('replied', 'Replied', 3, '#ec4899', true),
      ('negotiating', 'Negotiating', 4, '#f59e0b', true),
      ('closed', 'Closed', 5, '#10b981', true),
      ('lost', 'Lost', 6, '#ef4444', true)
    ON CONFLICT (id) DO NOTHING
  `);
  
  // CRM Leads
  con.exec(`
    CREATE TABLE IF NOT EXISTS crm_leads (
      id VARCHAR PRIMARY KEY,
      email VARCHAR NOT NULL,
      name VARCHAR,
      company VARCHAR,
      title VARCHAR,
      stage_id VARCHAR,
      organization_id VARCHAR,
      source VARCHAR,
      custom_fields TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      last_contacted_at TIMESTAMP
    )
  `);
  
  // CRM Sequences
  con.exec(`
    CREATE TABLE IF NOT EXISTS crm_sequences (
      id VARCHAR PRIMARY KEY,
      name VARCHAR NOT NULL,
      stage_id VARCHAR,
      organization_id VARCHAR,
      auto_send_follow_ups BOOLEAN DEFAULT false,
      follow_up_gap_minutes INTEGER DEFAULT 30,
      description TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // CRM Sequence Steps
  con.exec(`
    CREATE TABLE IF NOT EXISTS crm_sequence_steps (
      id VARCHAR PRIMARY KEY,
      sequence_id VARCHAR,
      order_index INTEGER NOT NULL,
      wait_days INTEGER DEFAULT 1,
      subject_template TEXT,
      body_template TEXT,
      instructions TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // CRM Drafts
  con.exec(`
    CREATE TABLE IF NOT EXISTS crm_drafts (
      id VARCHAR PRIMARY KEY,
      lead_id VARCHAR,
      sequence_step_id VARCHAR,
      sender VARCHAR,
      subject TEXT,
      body TEXT,
      status VARCHAR DEFAULT 'pending',
      auto_send_at TIMESTAMP,
      sent_at TIMESTAMP,
      error_message TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // Campaigns
  con.exec(`
    CREATE TABLE IF NOT EXISTS campaigns (
      id VARCHAR PRIMARY KEY,
      name VARCHAR NOT NULL,
      description TEXT,
      organization_id VARCHAR,
      status VARCHAR DEFAULT 'draft',
      start_date DATE,
      end_date DATE,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // Campaign Channels
  con.exec(`
    CREATE TABLE IF NOT EXISTS campaign_channels (
      id VARCHAR PRIMARY KEY,
      campaign_id VARCHAR,
      channel VARCHAR NOT NULL,
      configuration TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // Campaign Sends
  con.exec(`
    CREATE TABLE IF NOT EXISTS campaign_sends (
      id VARCHAR PRIMARY KEY,
      campaign_id VARCHAR,
      campaign_channel_id VARCHAR,
      lead_email VARCHAR NOT NULL,
      channel VARCHAR NOT NULL,
      status VARCHAR DEFAULT 'pending',
      sent_at TIMESTAMP,
      delivered_at TIMESTAMP,
      opened_at TIMESTAMP,
      clicked_at TIMESTAMP,
      replied_at TIMESTAMP,
      error_message TEXT,
      metadata TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // Email Health
  con.exec(`
    CREATE TABLE IF NOT EXISTS email_health (
      id VARCHAR PRIMARY KEY,
      mailbox_name VARCHAR NOT NULL,
      smtp_host VARCHAR,
      domain VARCHAR,
      daily_sent_count INTEGER DEFAULT 0,
      daily_bounce_count INTEGER DEFAULT 0,
      daily_spam_count INTEGER DEFAULT 0,
      reputation_score INTEGER DEFAULT 100,
      warmup_day INTEGER DEFAULT 0,
      warmup_limit INTEGER DEFAULT 10,
      last_check_at TIMESTAMP,
      health_status VARCHAR DEFAULT 'good',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // Prompts
  con.exec(`
    CREATE TABLE IF NOT EXISTS prompts (
      id VARCHAR PRIMARY KEY,
      name VARCHAR NOT NULL UNIQUE,
      area VARCHAR NOT NULL,
      version VARCHAR NOT NULL,
      content TEXT NOT NULL,
      variables TEXT,
      is_production BOOLEAN DEFAULT false,
      langfuse_prompt_id VARCHAR,
      organization_id VARCHAR,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // Insert default prompts
  const defaultPrompts = [
    {
      id: 'enrich_company_profile',
      name: 'enrich/company_profile',
      area: 'enrichment',
      version: '1.0',
      content: 'Analyze this company based on its website and available data. Return a JSON with: industry, size, key technologies, business description, and lead_score.',
      variables: JSON.stringify(['company', 'website', 'description']),
      is_production: true
    },
    {
      id: 'crm_draft_reply',
      name: 'crm/draft_reply',
      area: 'crm',
      version: '1.0',
      content: 'Write a reply to this lead as {{sender}}. Be professional, concise, and helpful.',
      variables: JSON.stringify(['sender', 'lead_name', 'lead_company', 'lead_message']),
      is_production: true
    },
    {
      id: 'outreach_cold_email',
      name: 'outreach/cold_email',
      area: 'outreach',
      version: '1.0',
      content: 'Write a cold email to {{lead_name}} at {{lead_company}}. Keep it under 150 words.',
      variables: JSON.stringify(['lead_name', 'lead_company', 'lead_title', 'sender']),
      is_production: true
    },
    {
      id: 'classification_reply',
      name: 'classification/reply',
      area: 'classification',
      version: '1.0',
      content: 'Classify this email reply into: interested, not_interested, need_info, out_of_office, spam, other.',
      variables: JSON.stringify(['sender_email', 'subject', 'body']),
      is_production: true
    }
  ];
  
  const stmt = con.prepare('INSERT INTO prompts (id, name, area, version, content, variables, is_production) VALUES (?, ?, ?, ?, ?, ?, ?)');
  defaultPrompts.forEach(p => {
    try {
      stmt.run(p.id, p.name, p.area, p.version, p.content, p.variables, p.is_production);
    } catch (e) {
      // Already exists
    }
  });
  
  console.log('Schema initialized successfully.');
}

// ------------------------------------------------------------------ CRM Operations

function createLead(email, name = '', company = '', title = '', stageId = 'new', source = '', customFields = {}) {
  const con = connect();
  const id = `lead_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  
  // Check if exists
  const existing = con.prepare('SELECT id FROM crm_leads WHERE email = ?').get(email);
  
  if (existing) {
    con.prepare('UPDATE crm_leads SET name = ?, company = ?, title = ?, stage_id = ?, custom_fields = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?')
      .run(name || null, company || null, title || null, stageId, JSON.stringify(customFields), existing.id);
    con.close();
    return existing.id;
  }
 else {
    con.prepare('INSERT INTO crm_leads (id, email, name, company, title, stage_id, source, custom_fields) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
      .run(id, email, name || null, company || null, title || null, stageId, source || null, JSON.stringify(customFields));
    con.close();
    return id;
  }
}

function getLeads(stageId = null, limit = 100) {
  const con = connect();
  
  let query = 'SELECT * FROM crm_leads';
  const params = [];
  
  if (stageId) {
    query += ' WHERE stage_id = ?';
    params.push(stageId);
  }
  
  query += ' ORDER BY created_at DESC LIMIT ?';
  params.push(limit);
  
  const stmt = con.prepare(query);
  const leads = stmt.all(...params);
  
  con.close();
  
  return leads.map(l => ({
    ...l,
    customFields: l.custom_fields ? JSON.parse(l.custom_fields) : {}
  }));
}

function updateLeadStage(leadId, newStageId) {
  const con = connect();
  
  con.prepare('UPDATE crm_leads SET stage_id = ?, updated_at = CURRENT_TIMESTAMP, last_contacted_at = CURRENT_TIMESTAMP WHERE id = ?')
    .run(newStageId, leadId);
  
  con.close();
}

function getStages() {
  const con = connect();
  const stages = con.prepare('SELECT * FROM crm_stages ORDER BY order_index').all();
  con.close();
  return stages;
}

// ------------------------------------------------------------------ Channel Settings

function getChannelSettings(channel = 'email') {
  const con = connect();
  const settings = con.prepare('SELECT * FROM channel_settings WHERE channel = ?').get(channel);
  con.close();
  return settings;
}

function updateChannelSettings(channel, settings) {
  const con = connect();
  
  con.prepare(`UPDATE channel_settings SET 
    daily_limit = COALESCE(?, daily_limit),
    send_gap_minutes = COALESCE(?, send_gap_minutes),
    sending_hours_start = COALESCE(?, sending_hours_start),
    sending_hours_end = COALESCE(?, sending_hours_end),
    updated_at = CURRENT_TIMESTAMP
  WHERE channel = ?`).run(
    settings.daily_limit,
    settings.send_gap_minutes,
    settings.sending_hours_start,
    settings.sending_hours_end,
    channel
  );
  
  con.close();
}

// ------------------------------------------------------------------ Email Health

function registerMailbox(mailboxName, smtpHost, domain = '', warmupLimit = 10) {
  const con = connect();
  const id = `health_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  
  con.prepare('INSERT INTO email_health (id, mailbox_name, smtp_host, domain, warmup_limit, warmup_day) VALUES (?, ?, ?, ?, ?, 0)')
    .run(id, mailboxName, smtpHost, domain, warmupLimit);
  
  con.close();
  return id;
}

function getEmailHealthSummary() {
  const con = connect();
  
  const total = con.prepare('SELECT COUNT(*) as count FROM email_health').get().count;
  const good = con.prepare("SELECT COUNT(*) as count FROM email_health WHERE health_status = 'good'").get().count;
  const warning = con.prepare("SELECT COUNT(*) as count FROM email_health WHERE health_status = 'warning'").get().count;
  const critical = con.prepare("SELECT COUNT(*) as count FROM email_health WHERE health_status = 'critical'").get().count;
  
  con.close();
  
  return { total, good, warning, critical };
}

// ------------------------------------------------------------------ Utility

function generateId(prefix = '') {
  return `${prefix}${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
}

module.exports = {
  connect,
  initSchema,
  createLead,
  getLeads,
  updateLeadStage,
  getStages,
  getChannelSettings,
  updateChannelSettings,
  registerMailbox,
  getEmailHealthSummary,
  generateId
};
