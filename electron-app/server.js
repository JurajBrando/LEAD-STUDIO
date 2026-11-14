const express = require('express');
const cors = require('cors');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const os = require('os');

const app = express();
const PORT = 8765;

// Middleware
app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));

// Serve static UI files
app.use(express.static(path.join(__dirname, 'ui')));

// Configuration
const ROOT = path.resolve(path.join(__dirname, '..'));
const LEAD_INDEX = path.join(ROOT, 'LEAD_INDEX');
const LOGS = path.join(__dirname, 'logs');

// Create logs directory
if (!fs.existsSync(LOGS)) {
  fs.mkdirSync(LOGS, { recursive: true });
}

// Jobs tracking
const jobs = new Map();
const jobMutex = new Map();

// ------------------------------------------------------------------ Settings
const SETTINGS_FILE = path.join(__dirname, 'settings.json');

function loadSettings() {
  try {
    if (fs.existsSync(SETTINGS_FILE)) {
      return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8'));
    }
  } catch (e) {
    console.error('Error loading settings:', e);
  }
  return {
    ollama_key: '',
    ollama_base: 'https://ollama.com',
    ollama_model: '',
    mcp_token: generateToken(),
    mailboxes: []
  };
}

function saveSettings(settings) {
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2));
}

function generateToken() {
  return require('crypto').randomBytes(16).toString('hex');
}

// ------------------------------------------------------------------ API Routes

// Get initial state
app.get('/api/state', (req, res) => {
  const settings = loadSettings();
  res.json({
    tools: [],
    jobs: Array.from(jobs.values()).map(j => ({
      id: j.id,
      tool: j.tool,
      name: j.name,
      running: j.running,
      code: j.code,
      minutes: j.minutes
    })),
    root: ROOT,
    app: __dirname,
    python: true, // We'll need Node.js equivalents
    hot: [],
    settings: publicSettings(settings),
    version: '2.0',
    port: PORT
  });
});

function publicSettings(settings) {
  return {
    ollama_has_key: !!settings.ollama_key,
    ollama_key_hint: settings.ollama_key ? '...' + settings.ollama_key.slice(-4) : '',
    ollama_base: settings.ollama_base,
    ollama_model: settings.ollama_model,
    mcp_token: settings.mcp_token,
    mailbox_count: settings.mailboxes?.length || 0
  };
}

// Save settings
app.post('/api/settings', (req, res) => {
  const settings = loadSettings();
  
  if (req.body.ollama_key) settings.ollama_key = req.body.ollama_key;
  if (req.body.ollama_base) settings.ollama_base = req.body.ollama_base;
  if (req.body.ollama_model) settings.ollama_model = req.body.ollama_model;
  if (req.body.new_mcp_token) settings.mcp_token = generateToken();
  
  saveSettings(settings);
  res.json({ ok: true, settings: publicSettings(settings) });
});

// ------------------------------------------------------------------ File Operations

app.get('/api/files', (req, res) => {
  const relPath = req.query.path || '';
  const fullPath = path.join(ROOT, relPath);
  
  if (!fullPath.startsWith(ROOT)) {
    return res.status(403).json({ error: 'Access denied' });
  }
  
  if (!fs.existsSync(fullPath)) {
    return res.json({ items: [], path: relPath });
  }
  
  const items = [];
  const files = fs.readdirSync(fullPath);
  
  for (const file of files) {
    const filePath = path.join(fullPath, file);
    const stats = fs.statSync(filePath);
    
    items.push({
      name: file,
      dir: stats.isDirectory(),
      table: !stats.isDirectory() && (file.endsWith('.csv') || file.endsWith('.csv.gz') || file.endsWith('.tsv')),
      kb: Math.floor(stats.size / 1024),
      t: stats.mtime,
      when: new Date(stats.mtime).toLocaleString('sk-SK')
    });
  }
  
  items.sort((a, b) => (a.dir === b.dir) ? 0 : a.dir ? -1 : 1);
  
  res.json({ items: items.slice(0, 400), path: relPath });
});

// ------------------------------------------------------------------ Table Operations

app.get('/api/table', (req, res) => {
  const relPath = req.query.path;
  const limit = parseInt(req.query.limit) || 200;
  const offset = parseInt(req.query.offset) || 0;
  const query = (req.query.q || '').toLowerCase();
  
  const fullPath = path.join(ROOT, relPath);
  
  if (!fullPath.startsWith(ROOT) || !fs.existsSync(fullPath)) {
    return res.json({ cols: [], rows: [], total: 0 });
  }
  
  // For now, return placeholder - would need CSV parsing library
  res.json({ 
    cols: ['col1', 'col2'], 
    rows: [['data1', 'data2']], 
    total: 0,
    more: false
  });
});

// ------------------------------------------------------------------ Job Management

app.post('/api/run', (req, res) => {
  const tool = req.body.tool;
  const args = req.body.args || '';
  
  const jobId = generateJobId(tool);
  
  // In the full version, this would spawn a Node.js process
  // For now, create a placeholder job
  jobs.set(jobId, {
    id: jobId,
    tool: tool,
    name: tool,
    running: true,
    code: null,
    started: Date.now(),
    minutes: 0
  });
  
  res.json({ job: jobId, error: null });
});

app.post('/api/stop', (req, res) => {
  const jobId = req.body.id;
  const job = jobs.get(jobId);
  
  if (job && job.running) {
    job.running = false;
    job.code = -1;
    res.json({ ok: true });
  } else {
    res.json({ ok: false });
  }
});

function generateJobId(tool) {
  const date = new Date();
  const dateStr = date.toISOString().slice(0, 10).replace(/-/g, '');
  const timeStr = date.toTimeString().slice(0, 8).replace(/:/g, '');
  const cleanTool = tool.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 60);
  return `${dateStr}_${timeStr}_${cleanTool}`;
}

// ------------------------------------------------------------------ CRM Features (from new schema)

app.get('/api/crm/leads', (req, res) => {
  // This would query the DuckDB database
  // For now, return placeholder
  res.json({
    cols: ['id', 'email', 'name', 'company', 'title', 'stage', 'source'],
    rows: [],
    total: 0
  });
});

app.post('/api/crm/leads', (req, res) => {
  const { email, name, company, title, stage, source } = req.body;
  
  // This would insert into crm_leads table
  res.json({ ok: true, id: `lead_${Date.now()}` });
});

app.put('/api/crm/leads/:id', (req, res) => {
  const { id } = req.params;
  const { stage } = req.body;
  
  // This would update crm_leads table
  res.json({ ok: true });
});

// ------------------------------------------------------------------ Health Check

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    uptime: process.uptime(),
    memory: process.memoryUsage(),
    platform: os.platform(),
    nodeVersion: process.version
  });
});

// ------------------------------------------------------------------ Start Server

app.listen(PORT, '127.0.0.1', () => {
  console.log(`Lead Studio server running on http://127.0.0.1:${PORT}`);
  console.log(`Workspace: ${ROOT}`);
  console.log('Server ready for Electron window');
});
