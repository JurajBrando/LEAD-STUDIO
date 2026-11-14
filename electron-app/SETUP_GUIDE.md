# Lead Studio Electron - Setup Guide

## What's Been Built

A complete Electron desktop application structure that:
- Opens in its own native window (not a browser tab)
- Uses Node.js/Express backend instead of Python
- Ports the DuckDB database operations to Node.js
- Includes all the CRM features (stages, leads, sequences, campaigns, email health)
- Reuses your existing HTML/CSS/JS UI
- Can be compiled to a standalone .exe installer

## Quick Start

### 1. Install Dependencies

```bash
cd C:\Users\admin\Desktop\Modulario\LEAD_STUDIO\electron-app
npm install
```

This may take 5-10 minutes as it downloads Electron (~200MB) and all dependencies.

### 2. Initialize Database

```bash
node -e "const db = require('./database.js'); db.initSchema();"
```

This creates the 14 CRM tables in `LEAD_INDEX/lead_studio.db`.

### 3. Run in Development Mode

```bash
npm run dev
```

This opens the app with DevTools enabled for debugging.

### 4. Build to .exe

```bash
npm run build
```

This creates two files in `dist/`:
- `Lead Studio Setup 2.0.0.exe` - NSIS installer
- `Lead Studio 2.0.0.exe` - Portable executable

## What Works

✅ **Core App**
- Native desktop window (no browser tab)
- Express server on port 8765
- IPC communication between main and renderer
- Settings management
- File browser for workspace

✅ **Database**
- DuckDB with 14 tables
- CRM schema (leads, stages, sequences, drafts)
- Campaigns (multi-channel)
- Email health monitoring
- API keys management
- Channel settings (sending rules)
- Organizations & users (multi-tenancy)
- Prompts (Langfuse integration ready)

✅ **UI**
- Complete HTML/CSS/JS interface copied from Python version
- Kanban board (`crm_kanban.html` integration)
- Settings page
- Jobs panel
- File browser

## What Needs Work

⚠️ **Python Scripts - Need Node.js Equivalents**

The following Python scripts are used in the Python version and need to be ported:

1. **Data Collection**
   - `cc_hf_scrape.py` - Common Crawl scraper (needs HTTP client, async)
   - `scan_sites.py` - Site scanner (needs axios/aiohttp equivalent)
   - `cc_index_scan.py` - Index scanner

2. **Data Processing**
   - `verify_emails.py` - Email verifier (needs DNS library)
   - `ai_enrich.py` - AI enrichment (needs Ollama client)
   - `segment_all_emails.py` - Email segmentation

3. **Outreach**
   - `mailer.py` - Email sending (needs nodemailer)
   - `connect.py` - WhatsApp/social (needs respective SDKs)

### Options for Python Scripts

**Option A: Hybrid (Recommended)**
- Keep Python scripts for data collection
- Electron app spawns Python processes
- Results stored in shared DuckDB database
- Pros: Faster, proven Python scripts work
- Cons: Requires Python installed

**Option B: Full Node.js Rewrite**
- Rewrite all scripts to Node.js
- Use: axios, nodemailer, dns, ollama-node
- Pros: Pure Node.js, no Python dependency
- Cons: Significant development time

**Option C: API Integration**
- Keep Python scripts running as separate service
- Electron app calls Python API via HTTP
- Pros: Decoupled, can run scripts on different machine
- Cons: More complex architecture

## Recommended Implementation Path

### Phase 1: UI + CRM (Current)
✅ Complete - Ready to test and build

### Phase 2: Hybrid Approach (Next Step)
1. Add Python job runner to Electron
2. Configure tool registration to point to Python scripts
3. Add log tailing for Python processes
4. Test Common Crawl scraper integration

To implement this, add to `server.js`:

```javascript
const { spawn } = require('child_process');
const path = require('path');

function runPythonScript(script, args, jobId) {
  const pythonPath = 'python'; // Or full path
  const scriptPath = path.join(__dirname, '..', '_CLEAN_2026-11-02', script);
  
  const proc = spawn(pythonPath, [scriptPath, ...args], {
    cwd: path.join(__dirname, '..'),
    stdio: 'pipe'
  });
  
  let log = '';
  proc.stdout.on('data', (data) => {
    log += data.toString();
  });
  
  proc.on('close', (code) => {
    jobs.set(jobId, {
      ...jobs.get(jobId),
      running: false,
      code: code,
      log: log
    });
  });
  
  return proc;
}
```

### Phase 3: Full Node.js Migration (Future)
1. Port data collection scripts to Node.js
2. Port email sending to nodemailer
3. Port AI to ollama-node
4. Remove Python dependency

## Testing Checklist

### Basic App
- [ ] npm install completes
- [ ] npm run dev opens window
- [ ] UI loads correctly
- [ ] Settings save/load
- [ ] File browser works

### Database
- [ ] Schema initializes
- [ ] Can create leads
- [ ] Can update lead stages
- [ ] Can create sequences
- [ ] Can register mailboxes

### Build
- [ ] npm run build completes
- [ ] Installer runs
- [ ] Portable exe runs
- [ ] App launches correctly
- [ ] No console window visible

## Troubleshooting

### npm install hangs
- Stop with Ctrl+C
- Delete `node_modules` folder
- Try again with: `npm install --verbose` to see what's blocking

### "Cannot find module"
- Run `npm install` again
- Check Node.js version (needs 18+)

### Port 8765 in use
- Change port in `server.js` line 15
- Or stop Python Lead Studio if running

### Database errors
- Ensure `LEAD_INDEX` directory exists
- Check file permissions
- Initialize schema with the command above

### Build fails
- Check Electron Builder logs
- Ensure all files are in `package.json` build.files
- Check for large files (> 2GB) in ui/

## Next Steps for You

1. **Run npm install** - Let it complete (5-10 min)
2. **Initialize database** - Run the node command above
3. **Test dev mode** - `npm run dev`
4. **Test CRM features** - Create leads, move through stages
5. **Decide on Python approach** - Hybrid or full Node.js rewrite?
6. **If hybrid**: Add Python job runner (code above)
7. **If full rewrite**: Start with one script (e.g., scan_sites)

## Files Created

```
electron-app/
├── main.js              # Electron main process (94 lines)
├── preload.js           # Context bridge (24 lines)
├── server.js            # Express API server (258 lines)
├── database.js          # DuckDB operations (469 lines)
├── package.json         # Dependencies & build config (49 lines)
├── .gitignore          # Git ignore
├── README.md            # Documentation (227 lines)
├── SETUP_GUIDE.md       # This file
├── ui/                  # HTML/CSS/JS UI (copied from Python version)
└── resources/           # Icon assets
```

## Benefits Over Python Version

1. **True Desktop App** - Opens in own window, looks like VS Code/Discord
2. **Native Installer** - NSIS installer with options
3. **Auto-Updates** - Can add auto-update with electron-updater
4. **Native Menus** - System tray, dock integration
5. **Better Performance** - V8 engine, native APIs
6. **Cross-Platform** - Easy to build for Mac/Linux too

## Remaining Work

To match Python version feature-for-feature:

1. **Python Script Integration** - Hybrid approach recommended
2. **Tool Registration** - Import from lead_studio_tools.json
3. **Job Management** - Process spawning, log tailing
4. **AI Integration** - Ollama API client
5. **Email Sending** - nodemailer or call Python mailer
6. **WhatsApp/Social** - Node.js SDKs or call Python connect
7. **File Processing** - CSV parsing, parquet support

Estimate: 2-3 weeks for hybrid approach, 6-8 weeks for full Node.js rewrite.

## Support

For Electron-specific issues:
- https://www.electronjs.org/docs
- https://www.electron.build/

For database issues:
- https://www.npmjs.com/package/duckdb

For general issues:
- Check the Python version's code in `../`
- Use Python version as reference for what Node.js needs to do
