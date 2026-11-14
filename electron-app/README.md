# Lead Studio - Electron Desktop Application

## Overview

This is the Electron version of Lead Studio - a professional desktop application (like VS Code, Discord, Slack) that wraps the existing HTML/CSS/JS UI with a Node.js backend instead of Python.

## Features

- **Native Desktop Window** - Opens in its own window (not a browser tab)
- **Node.js Backend** - Express server for API endpoints
- **DuckDB Database** - Same database schema as Python version
- **CRM Pipeline** - Kanban board, sequences, auto-follow-ups
- **Multi-Channel Campaigns** - Email, WhatsApp, LinkedIn, social
- **Email Health Monitoring** - Reputation tracking and warm-up
- **Data Visualization** - Ready for Metabase integration
- **Cross-Platform** - Builds for Windows (Linux/Mac possible with config changes)

## Project Structure

```
electron-app/
├── main.js              # Electron main process
├── preload.js           # Context bridge for renderer
├── server.js            # Express API server
├── database.js          # DuckDB database operations
├── package.json         # Dependencies and build config
├── ui/                  # HTML/CSS/JS UI (copied from Python version)
└── resources/           # Icons and assets
```

## Installation

### Prerequisites

- Node.js 18+ (https://nodejs.org/)
- npm or yarn

### Setup

```bash
cd electron-app
npm install
```

## Development

### Run in Development Mode

```bash
npm run dev
```

This opens the app with DevTools enabled for debugging.

### Run in Production Mode

```bash
npm start
```

## Building

### Build for Windows

```bash
npm run build
```

This creates:
- `dist/Lead Studio Setup x.x.x.exe` - Installer (NSIS)
- `dist/Lead Studio x.x.x.exe` - Portable executable

### Build Portable Only

```bash
npm run build:portable
```

## Architecture

### Main Process (main.js)
- Creates Electron window
- Manages window lifecycle
- Spawns Express server
- Handles IPC communication

### Renderer Process (ui/)
- HTML/CSS/JS UI (same as Python version)
- Communicates with main process via `preload.js`
- Makes API calls to Express server

### Backend (server.js + database.js)
- Express server on port 8765
- DuckDB database operations
- API endpoints for CRM, campaigns, health

## Database

The database is stored at:
```
../LEAD_INDEX/lead_studio.db
```

Same schema as Python version - 14 tables for CRM, campaigns, email health, etc.

## API Endpoints

### Settings
- `GET /api/state` - Get app state
- `POST /api/settings` - Save settings

### Files
- `GET /api/files` - List workspace files

### CRM
- `GET /api/crm/leads` - Get leads
- `POST /api/crm/leads` - Create lead
- `PUT /api/crm/leads/:id` - Update lead

### Health
- `GET /api/health` - System health check

## Migration from Python Version

### What's Ported
- ✅ UI (HTML/CSS/JS) - 100% compatible
- ✅ Database schema - DuckDB same structure
- ✅ HTTP server - Express instead of Python HTTP
- ✅ Database operations - DuckDB Node.js bindings
- ✅ CRM features - Leads, stages, sequences
- ✅ Email health - Reputation tracking

### What Needs Work
- ⚠️ Python scripts - Need Node.js equivalents:
  - `cc_hf_scrape.py` - Common Crawl scraper
  - `scan_sites.py` - Site scanner
  - `verify_emails.py` - Email verifier
  - `ai_enrich.py` - AI enrichment (needs Ollama integration)
  - `mailer.py` - Email sending (needs SMTP library)
  - `connect.py` - WhatsApp/social integrations

### Recommended Approach

1. **Hybrid Approach** - Keep Python scripts for data collection, use Electron for UI/CRM
   - Electron app spawns Python processes for scraping jobs
   - Results stored in shared DuckDB database
   - Electron UI reads from database

2. **Full Node.js Rewrite** - Rewrite all scripts to Node.js:
   - Use `axios` or `node-fetch` for HTTP requests
   - Use `nodemailer` for email sending
   - Use `whatsapp-web.js` for WhatsApp
   - Use `ollama-node` for AI

## Configuration

### Port
Default: 8765 (hardcoded, can be changed in `server.js`)

### Workspace
Automatically detected as parent directory of `electron-app/`

### Database
Located at `../LEAD_INDEX/lead_studio.db`

## Next Steps

### Phase 1 - Testing
1. Install dependencies: `npm install`
2. Run dev mode: `npm run dev`
3. Test UI loads correctly
4. Test database operations
5. Test API endpoints

### Phase 2 - Python Integration
1. Add job runner that spawns Python scripts
2. Update tool registration to support Python jobs
3. Add log tailing for Python processes
4. Test Common Crawl scraper integration

### Phase 3 - Build
1. Create icon (256x256 PNG, convert to .ico)
2. Configure installer settings
3. Build test version
4. Test installer
5. Build production version

### Phase 4 - Full Node.js Migration
1. Port `scan_sites.py` to Node.js
2. Port `verify_emails.py` to Node.js
3. Port `ai_enrich.py` to Node.js (Ollama integration)
4. Port `mailer.py` to Node.js (nodemailer)
5. Remove Python dependency

## Security Notes

- Database is stored locally (no cloud by default)
- API keys stored in `settings.json` (consider encryption)
- Server runs on localhost only (127.0.0.1)
- No external connections except configured integrations

## Troubleshooting

### "Module not found" errors
```bash
npm install
```

### "Port already in use"
Check if Python Lead Studio is running, or change port in `server.js`

### Database errors
Ensure `LEAD_INDEX` directory exists and is writable

### Build fails
Check Electron Builder logs, ensure all files are included in `package.json` build config

## Resources

- Electron Documentation: https://www.electronjs.org/docs
- Electron Builder: https://www.electron.build/
- DuckDB Node.js: https://www.npmjs.com/package/duckdb
- Express: https://expressjs.com/

## License

Same as Lead Studio - MIT
