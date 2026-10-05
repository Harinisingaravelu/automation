# SalesFlow — Sales & Profit Automation

A zero-cost local prototype for a WhatsApp-style sales automation system.

## Features
- Chat-style sales entry
- Simple natural-language parsing: `sold 5 t-shirts`, `sold 3 shirts for 2700`
- SQLite database (no external database cost)
- Automatic revenue and profit calculation
- Product master and stock tracking
- Today / month summary commands
- Product performance and last-7-days dashboard
- CSV export
- Responsive frontend
- Resettable demo data

## Run locally
1. Install Python 3.10+.
2. Open a terminal in this folder.
3. Run: `python -m venv .venv`
4. Windows: `.venv\\Scripts\\activate`  | macOS/Linux: `source .venv/bin/activate`
5. Run: `pip install -r requirements.txt`
6. Run: `python app.py`
7. Open: http://127.0.0.1:5000

## Demo messages
- `sold 5 t-shirts`
- `sold 3 shirts`
- `sold 2 jeans`
- `sold 3 shirts for 2700`
- `today sales`
- `today profit`
- `monthly sales`

## Architecture
Frontend: HTML/CSS/JavaScript
Backend: Python Flask
Database: SQLite
Analytics: SQL + JavaScript dashboard

## Real WhatsApp upgrade
The current version is intentionally local and zero-cost. A later production version can replace the chat UI with a WhatsApp Business webhook and keep the same backend/database logic. Real WhatsApp API access can have account/usage requirements and is not included in this zero-cost prototype.


## Public deployment
This Flask app is deployment-ready for a service such as Render. GitHub stores the source code; a web host runs the Flask backend and provides the public HTTPS URL.

**Build command:** `pip install -r requirements.txt`

**Start command:** `gunicorn app:app`

> Note: SQLite is local file storage. On free/ephemeral hosting, database data may reset after a redeploy or instance replacement. Use managed PostgreSQL for persistent production data.
