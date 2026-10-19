# GotTime?

**Trade Hours, Learn Skills.**

GotTime? is a full-stack time-banking platform where one hour of teaching earns one credit to spend learning. The current UI is a responsive React application backed by Flask and MySQL.

## Stack

- React 18 + Vite
- Flask JSON API
- MySQL / PyMySQL
- Session authentication, Google OAuth, Flask-Mail
- Custom responsive design system and gamification UI

## Local setup

1. Create the MySQL database:

   ```sql
   CREATE DATABASE gottime;
   ```

2. Copy `.env.example` to `.env` and fill in your own credentials, or set them in the terminal:

   ```powershell
   $env:MYSQL_HOST="localhost"
   $env:MYSQL_USER="root"
   $env:MYSQL_PASSWORD="your-mysql-password"
   $env:MYSQL_DB="gottime"
   $env:FRONTEND_URL="http://localhost:5173"
   ```

3. Install and run Flask:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   flask --app app run --debug
   ```

4. In another terminal, install and run React:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

5. Open `http://localhost:5173`.

Local editor settings, uploads, database backups, and `.env` credentials are excluded from Git.

## Accounts

Create a real account from the app. New users receive 10 welcome time credits.

## Production UI

Run `npm run build` inside `frontend`. Vite writes the optimized bundle to `static/react`, and Flask serves it at `http://localhost:5000/`.

Google sign-in and email delivery require the corresponding `GOOGLE_*` and `MAIL_*` environment variables.

## Multiple users and production serving

Use the built React UI and Waitress for concurrent use. Flask's `app.py` runner is for development.
Install `requirements.txt`, choose a private random `SECRET_KEY`, then run from the project root:

```powershell
python manage.py init
cd frontend
npm run build
cd ..
python serve.py
```

`serve.py` defaults to 8 worker threads on `127.0.0.1:5000`. Set `HOST=0.0.0.0` for a trusted LAN,
or put a TLS reverse proxy in front for public hosting. Set `WEB_THREADS` only after measuring
the target server and MySQL limits. Multiple processes must share the secret, database and upload storage.
Run schema initialization once before starting workers; `serve.py` disables import-time migrations.
Schedule `python manage.py jobs` once a minute for reminders and missed-session updates when no users
are browsing. Requests also process the signed-in user's due work. Jobs and ledger changes use transactions.

The API checks overlap and counts credits committed to pending/active bookings. Completion and
missed-session deductions are atomic, and payment retries cannot credit the same order twice.
Chat scheduling remains flexible; bookings from mentor cards retain the mentor's time range.

Tests use an automatically created disposable `gottime_audit_*` database and never reset app data:

```powershell
python -m unittest discover -s tests -p test_flows.py -v
node --test frontend/src/sessionRules.test.js frontend/src/chatCrypto.test.js
python tests/load_probe.py --users 50 --requests 500
```

See [the verification report](docs/verification-2026-10-18.md) for results and remaining service limitations.
