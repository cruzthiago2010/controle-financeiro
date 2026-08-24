<p align="center">
  <img src="static/logo-financerto.svg" width="96" alt="FinanCerto">
</p>

<p align="center">
  <a href="README.md">Português</a> · <b>English</b>
</p>

# FinanCerto — Self-hosted

Monthly personal finance app (income, expenses, cards), built to run on your own
server (a home lab, or any Docker host). All data is stored locally in SQLite,
inside the `data/` folder — nothing leaves your server.

> Built for Brazil: amounts in R$, PIX, credit-card bills, payslips and Open
> Finance Brasil. The interface is available in Portuguese and English.

## Features

- **Dashboard** — the month's balance, money available, income and expenses, a
  cash-flow chart (monthly summary and day-by-day), a 6-month trend chart, bills
  due in the next 7 days, upcoming instalments and a per-account summary. The
  dashboard cards are clickable and show how each number was worked out.
- **Money Map** — a Sankey showing where the money came from and where it went,
  over 30 days, 3, 6 or 12 months. Transfers between your own accounts are left
  out: they move balance, but are neither income nor spending.
- **Income & Expenses** — one-off or recurring entries (rent, salary), with
  automatic instalments (enter the total or the instalment amount) and a receipt
  or photo attached per entry. A button copies last month's recurring entries so
  you don't retype everything.
- **Scan receipt** — from the floating button, photograph a receipt with your
  phone camera and the app reads the store, the amount and the items bought
  (OCR), always with a review screen before it becomes an expense.
- **Investments** — a full portfolio: stocks, REITs (FIIs), ETFs, BDRs, crypto,
  US assets (stocks, REITs and ETFs), fixed income (CDB/LCI/LCA/debenture/
  Treasury, with issuer and liquidity) and funds. Search by ticker, the current
  price comes filled in, and **quotes refresh on their own** every hour.
  **Dividends are imported automatically**: the app knows how many shares you
  held on each ex-date and posts the right amount, already net of JCP tax. Five
  tabs — Summary, Holdings, Income, Net Worth and Return — show your net worth
  growing month by month since the first purchase, how it did against the CDI
  benchmark, everything already received and what is still coming. Set your
  target mix (e.g. 40% stocks, 25% REITs) and each asset gets a **Buy?** column
  saying whether it is below or above target.
- **Budget by category** — set a monthly cap per expense category and follow how
  much is already spent on a progress bar.
- **Savings goals** — create goals with a target amount and an optional
  deadline, and record how much you have put aside.
- **Payslip** — import the PDF and the app reads the earnings, deductions, the
  net paid at month end and the mid-month advance, posting the matching income
  automatically. It shows an earnings/deductions chart by month — click a bar to
  see exactly which payslip lines add up to that number.
- **Loans** _(optional)_ — track loans (payroll-deducted or debited from an
  account), with current/total instalment and progress. Hidden by default; only
  the administrator (the first user of the installation) can turn the tab on for
  everyone, from the Backup tab.
- **Accounts** — several accounts per user, with the balance worked out
  automatically, and transfers between accounts (including between different
  users' accounts, e.g. from yours to your partner's) without counting as real
  income or spending.
- **Credit cards** — limit, current bill, due day, the account linked for
  automatic payment, and a warning when the bill is near (or past) the limit.
- **Categories** — your own income/expense categories, with colors.
- **Automatic rules** — rules that read the entry description and classify it,
  so "UBER *TRIP 8829" becomes Transport. Nine rules ship ready; the
  **Sort out** tab groups whatever is left uncategorized so you handle a whole
  group at once and turn it into a rule for next time.
- **Calendar** — a monthly view of due dates, marking paid, pending and
  received.
- **Multiple users per household** — everyone in your household has their own
  login; you can see and move money between everyone's accounts inside the same
  household. Each user can be marked **read-only** (sees everything, edits
  nothing).
- **Public sign-up (`/registro`)** — anyone can create their own household,
  fully isolated from yours — they see none of your categories, accounts or
  entries, and vice versa. Whoever creates a household starts as its
  administrator. Full database backup is disabled automatically when the server
  has more than one household (so one household's data can't leak into
  another's).
- **Backup** — download a `.zip` with entries, receipts, payslips and photos at
  any time, keep scheduled backups on the server, or restore from a file.
- **Demo mode** — turns on a separate, made-up database so you can show the app
  to someone without exposing your real data.
- **Installable as an app (PWA)** — opens full screen on the phone, like a
  native app.
- **Light/dark theme**, **hide amounts** (handy in public), and
  **Portuguese/English**.

## Screenshots

_Taken with **demo mode** on — made-up data._

| | |
|---|---|
| **Sign in** | ![Sign in](docs/screenshots/login.png) |
| **Create household** | ![Create household](docs/screenshots/registro.png) |
| **Dashboard** | ![Dashboard](docs/screenshots/dashboard.png) |
| **Income & Expenses** | ![Income and expenses](docs/screenshots/lancamentos.png) |
| **Investments** | ![Investments](docs/screenshots/investimentos.png) |
| **Dividends received and expected** | ![Dividends](docs/screenshots/investimentos-proventos.png) |
| **Return against the CDI** | ![Return](docs/screenshots/investimentos-rentabilidade.png) |
| **Calendar** | ![Calendar](docs/screenshots/calendario.png) |
| **Credit cards** | ![Cards](docs/screenshots/cartoes.png) |
| **Automatic backup** | ![Backup](docs/screenshots/backup.png) |

## Running it (easiest — terminal/SSH)

1. Connect over SSH (or open the terminal).
2. Clone the repository and start the container:
   ```bash
   git clone https://github.com/cruzthiago2010/controle-financeiro.git
   cd controle-financeiro
   docker compose up -d --build
   ```
3. Open `http://localhost:8420` in the browser (your machine's IP).

The `data/` folder is created on first start and holds the database, the
receipts and the photos. It lives outside the container, so updating the code
erases nothing.

## Login

The app requires a login. On first start:

- If you set `ADMIN_USERNAME`/`ADMIN_PASSWORD` in `docker-compose.yml`, use those.
- If you didn't, a random password is generated. To see it, run
  `docker compose logs` right after the first `docker compose up -d --build` and
  look for the `USUÁRIO INICIAL CRIADO` block.
- Once inside, change the password from your name in the user menu.
- To add another user **from your household** (a partner, for instance), use
  "Add user" in the same menu.
- To give access to someone **outside your household** (a friend, another
  family), send them the `/registro` link — they create their own household,
  separate from yours.

### Sign in with Google (optional)

Besides username/password, you can enable a "Sign in with Google" button —
a first-time user also gets their own household automatically, the same as via
`/registro`. To turn it on:

1. Create an OAuth client ID (type **Web application**) at
   [console.cloud.google.com](https://console.cloud.google.com) → APIs and
   Services → Credentials.
2. Under "Authorised redirect URIs", register
   `https://YOUR-DOMAIN/api/auth/google/callback`.
3. Copy `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` into your `.env`.
4. While your OAuth consent screen is in **Testing**, only the e-mails you add
   there can sign in. To let anyone use it, publish the app (status
   "In production").

Without those variables the button simply doesn't appear — username/password
keeps working normally.

## Installing on the phone

### Android app (recommended)

Download the APK from the [releases page](../../releases/latest) and install it.
Since it doesn't come from the Play Store, Android asks you to allow installing
from "unknown sources" the first time.

On first launch the app asks for the address of **your** server — it doesn't
point anywhere by default. A domain or a local address both work:

```
finance.yourdomain.com
192.168.1.10:8420
```

It tests the connection before saving, so a typo shows up right away instead of
turning into a blank screen. To change it later, use "Trocar servidor" on the
connection error screen.

What the app adds beyond opening the site:

- **Fingerprint or PIN lock** on open, using the phone's own unlock. If the
  phone has no lock set, it opens straight through — there is no point guarding
  the app when the phone screen is open.
- **Bill reminders**, checked in the background.
- **Balance widget** for the home screen.
- Sending files (receipts and invoices) straight from the gallery or camera.
- **Self-update**: when a newer version is published, the app downloads it and
  opens the installer. Android always asks you to confirm the install — no
  ordinary app installs silently, and that is a protection.

### Or as a PWA

Without installing anything: open it in Chrome and use "Add to Home screen". It
opens full screen, but without fingerprint, widget or notifications.

## Open Finance: pulling your bank statement automatically

FinanCerto connects to your banks through [Pluggy](https://pluggy.ai), a payment
institution authorised by the Brazilian Central Bank. Once connected, the
statement becomes entries on its own and the app balance matches the bank's.

**It is optional.** With nothing configured the app works normally with manual
entry — and that remains the most private mode, because no data leaves your
server.

### What it costs

Pluggy's commercial plan starts at R$ 2,500/month and is not meant for personal
use. The path here is **Meu Pluggy**, free indefinitely for you to reach **your
own data**, on accounts in your name.

The Dashboard opens with a 15-day trial, but that applies only to commercial
features: **after the 15 days you keep pulling your own data for free**, through
the MeuPluggy connector.

### Before you start, understand what you are authorising

- Your bank password **never passes through FinanCerto or Pluggy**: you sign in
  on the bank's own page, through Open Finance.
- The consent is **read-only** — statement and balance. Moving money is a
  separate consent you would have to authorise on its own.
- It lasts at most **12 months** and you can **revoke it whenever you want**,
  from your bank's app, effective immediately.
- In exchange, your transaction history also comes to exist on Pluggy's servers.
  That is a real trade-off: weigh it before deciding.

### Step by step

**1. Connect your banks on Meu Pluggy**

Create an account at [meu.pluggy.ai](https://meu.pluggy.ai) and connect each
bank. Check that the flow takes you to **your bank's own page** to sign in —
that is the sign you are on the regulated Open Finance connector.

**2. Get your developer credentials**

Create an account at [dashboard.pluggy.ai](https://dashboard.pluggy.ai). Under
**Financial Data → Customization → Connectors**, search for "MeuPluggy" and
leave connector **(200) MeuPluggy** enabled. Save.

Under **Applications**, create an application and copy the **Client ID** and
**Client Secret** — the secret is usually shown only once.

> Skip the *due diligence* step that asks for company details: that is the paid
> commercial path and is not for personal use.

**3. Link your Meu Pluggy account to the application**

Still in the Dashboard, open the widget and pick the **MeuPluggy** connector. It
redirects to `meu.pluggy.ai` and you authorise — **no password is asked**, it is
OAuth.

Repeat **once per connected bank**. Each bank becomes a connection with its own
**Item ID** (a UUID). Copy the Item IDs; Pluggy has no endpoint that lists them,
so you are the one who keeps them.

**4. Configure it in FinanCerto**

In the **Accounts** tab, **Open Finance** section, paste the Client ID and the
Client Secret. Then paste each **Item ID** into the "Connect bank" field.

**5. Link each account**

For every account that shows up, use the selector to say whether it is a
FinanCerto **account**, a **card**, or should be **ignored**. Nothing is
imported while there is no link — that is deliberate.

### What happens next

- A daily sync brings in what arrived and keeps the balance equal to the bank's.
- Every entry from the bank carries an **Open Finance** badge, so you can tell it
  apart from what you typed.
- A transaction that already exists as one of your entries is **recognised, not
  duplicated** (same amount, date within three days).
- On a synced account the **starting balance stops being editable**: it becomes
  calculated from the statement, and changing it would make the balance stop
  matching the bank.

### Instant alerts (webhook, optional)

Without it, news shows up within 24 hours. With the webhook, Pluggy tells your
server as soon as a transaction arrives or a connection breaks.

It requires your server to have a **public HTTPS address** (Pluggy rejects
`localhost`). Set in `.env`:

```
PLUGGY_WEBHOOK_URL=https://your-domain.com
```

Restart and, in the Accounts tab, use **Turn on instant alerts**. The endpoint
protects itself with a secret generated on its own in `data/.pluggy_webhook` —
it is embedded in the URL registered with Pluggy and never has to be typed
anywhere.

### If something goes wrong

| Symptom | What it usually is |
|---|---|
| "Pluggy rejected the credentials" | Client ID/Secret swapped, or the MeuPluggy connector wasn't enabled under Customization |
| "Pluggy could not find that Item ID" | The Item belongs to another application, or step 3 wasn't done for that bank |
| Connected but no account appears | The account still needs to be linked to its FinanCerto counterpart |
| The sync date isn't moving | The consent may have expired — reconnect via `meu.pluggy.ai` |

Your imported data lives in **your** SQLite database. If you ever turn Open
Finance off, nothing that already came in is lost.

## Layout

- `app.py` — Flask backend (REST API + serves the site + authentication)
- `static/index.html` — the app frontend (a single page)
- `static/login.html` — sign-in screen
- `static/registro.html` — public sign-up screen (creates a new household)
- `static/manifest.json`, `static/sw.js`, `static/icon*.svg` — PWA files
- `Dockerfile` / `docker-compose.yml` — packaging (includes `tesseract-ocr` for
  receipt scanning)
- `.env.example` — a template of the environment variables accepted; copy it to
  `.env` and fill it in (the real `.env` is never committed)
- `migrations/` — database schema changes, applied in order and recorded in a
  control table (`schema_migrations`) every time the app starts
- `data/` — where the SQLite database, the session key, the receipts, the
  payslips and the photos are kept (outside the container, so nothing is lost on
  update)
- `android/` — the Android app source (WebView with fingerprint, bill
  notifications and a balance widget). To build your own:
  `docker build -t android-build-env -f android/Dockerfile.build android/` then
  `gradle assembleRelease` inside it. The APK comes out unsigned; sign it with
  your own key.
- `umbrel-app.yml` — optional manifest, in case you want to publish it as a
  formal app in an Umbrel community app store

**The code and the comments are in Portuguese.** Only the interface is
translated, through a lookup table keyed by the Portuguese text.

## Changing the port

If `8420` is taken, edit `docker-compose.yml` and change `"8420:5000"` to
another free port, e.g. `"8421:5000"`.

## Licence

FinanCerto is free software, under the **GNU Affero General Public License,
version 3 (AGPL-3.0)**. The full text is in [`LICENSE`](LICENSE).

In practice, what that guarantees:

- **Use it freely.** Run it on your server, for you or your family, without
  asking anyone and without paying anything.
- **Modify it freely.** The code is yours to adapt.
- **If you distribute a modified version, publish its source.** That also
  applies to anyone who *hosts* a modified version and lets other people use it
  over a network — that is the difference between the AGPL and the plain GPL.
  Nobody can take FinanCerto, close the source and resell it as a service.

Choosing the AGPL is consistent with why the app exists: your financial data
stays on your server, and the code that handles it stays open for anyone to
audit.
