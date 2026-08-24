<p align="center">
  <a href="SECURITY.md">Português</a> · <b>English</b>
</p>

# Security policy

FinanCerto stores financial data — bank statements, payslips, receipts,
investment balances. A flaw here exposes the financial life of whoever installed
it. That is why security reports are handled separately from ordinary bugs.

## Supported versions

The project ships as source, and the recommended install is always the latest
commit on `main`. Security fixes land there only — there are no backports. If
your instance is behind, updating is part of the fix:

```bash
cd controle-financeiro
git pull
docker compose up -d --build
```

| Version | Support |
|---|---|
| latest on `main` | ✅ receives fixes |
| any earlier commit | ❌ update before reporting |

## Reporting a vulnerability

**Do not open a public issue.** Issues are visible to everyone, including anyone
who would rather use the flaw than see it fixed.

Use GitHub's private channel:

1. Go to **[Security → Report a vulnerability](https://github.com/financerto/controle-financeiro/security/advisories/new)**.
2. Describe the problem. The report is visible only to you and the maintainer.

It helps a lot to include:

- what the flaw lets someone do (read another household's data? log in without a
  password? run commands on the server?);
- reproduction steps, as short as you can make them;
- the version you tested (`git rev-parse --short HEAD`) and how the instance is
  exposed (LAN only, behind a tunnel, open to the internet);
- the code involved, if you already know.

## What to expect

- **Acknowledgement within 5 days.** This is a one-person project maintained in
  spare time — that is an honest timeline, not a corporate SLA.
- **A reply saying whether the flaw reproduced**, as soon as there's a diagnosis,
  with a rough estimate for the fix.
- **Credit in the published advisory**, under whatever name or handle you
  prefer, unless you ask to stay anonymous.
- **Disclosure after the fix.** The advisory is published alongside the fixing
  commit so that self-hosters know they need to update.

There is no paid bounty. This is free software with no revenue.

## What counts as a security flaw

Among others:

- reaching data belonging to a **household** other than your own (isolation
  between households is the central guarantee of public registration);
- bypassing the login screen, escalating from read-only to editor, or from
  ordinary user to administrator;
- SQL injection, XSS, CSRF, path traversal in attachments and receipts;
- code execution on the server through an upload (receipt, payslip, photo);
- leaking `SECRET_KEY`, a password, or a Pluggy/Google token through logs, API
  responses or backups.

**Not** project flaws:

- an instance exposed straight to the internet with no HTTPS and no reverse
  proxy — the README recommends the opposite;
- a weak password in `.env`, or a blank `SECRET_KEY` in production;
- an attack that already requires administrator access to your own household;
- automated scanner output with no demonstrated impact.

## What the project does for your instance

- **Nothing leaves your server.** No telemetry; the database is a SQLite file
  inside `data/`.
- **`.env`, `data/` and the session key are never committed** — they are in
  `.gitignore`, and the repository has secret scanning enabled.
- **Dependencies watched by Dependabot**, with vulnerability alerts and
  automatic updates for vulnerable versions.
- **Static analysis (CodeQL) on every commit and weekly.**

If you host FinanCerto, two recommendations worth more than any code: keep the
app behind HTTPS (Cloudflare Tunnel, Tailscale, or a reverse proxy with a
certificate), and don't expose the port directly to the internet.
