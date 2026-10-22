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

## Render with hosted MySQL

Use a Python web service with the repository root as its working directory.

- Build command: `pip install -r requirements.txt && npm --prefix frontend ci --include=dev && npm --prefix frontend run build`
- Start command: `python serve.py`
- Set `HOST=0.0.0.0`; Render provides `PORT` automatically.
- Set a private, generated `SECRET_KEY` and keep it stable between deployments.
- Set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, and `MYSQL_DB`
  from your database provider. Do not use your laptop's localhost credentials.
- Add the provider's CA certificate as a Render Secret File named `ca.pem`, and set
  `MYSQL_SSL_CA=/etc/secrets/ca.pem`. Connections then verify both the certificate
  and hostname. An invalid or missing configured certificate fails closed.
- Keep `MYSQL_TIME_ZONE=+05:30`. Without a custom port or CA, local development
  continues to use port 3306 and the existing local connection behavior.

Initialize a new hosted database with `python manage.py init` before serving requests.
For an initial single-instance deployment without a separate migration runner, temporarily
use `python manage.py init && python serve.py` as the start command. Once initialization
succeeds, return to `python serve.py`. This creates schema and badge definitions;
it does not copy local accounts or history. Do not run parallel schema initializations.

Keep Google OAuth credentials in Render's environment and register the exact HTTPS
callback URL in Google Cloud. Database connectivity is required to complete login.
Never commit passwords, private environment files, or database backups.

For real payments, follow [Razorpay live setup](docs/razorpay-live-setup.md).
Live checkouts require `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and the separate
`RAZORPAY_WEBHOOK_SECRET`. Configure the dashboard webhook and automatic capture
before accepting payments. Refund reconciliation remains manual.

The direct UPI donation card is configured separately from Razorpay. Set
`DONATION_UPI_ID` to your actual recipient UPI ID and `DONATION_UPI_NAME` to the
recipient display name. A local `.env` value is not pushed to GitHub or Render.
In Render, open the service's **Environment** settings, add or update just these
two variables, and choose **Save, rebuild, and deploy**. Do not upload the entire
local `.env`, which may contain local database settings and other credentials.
See [Render's environment variable instructions](https://render.com/docs/configure-environment-variables).
After deployment, sign in and open Donations to check the QR code. The payment
area shows only the QR code once ready, with a retry option if loading fails.
Direct UPI transfers are not verified by the Razorpay checkout flow.

Connection configuration tests (no database required):
`python -m unittest discover -s tests -p test_db_connection.py -v`
