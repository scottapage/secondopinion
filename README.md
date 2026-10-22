# SecondOpinion

Business mobile bill analysis for New Zealand (Spark, One NZ, 2degrees). Proof of concept.

Visitors upload PDF bills at www.secondopinion.co.nz, or email them to mobile@secondopinion.co.nz.
Each bill is read by Claude into structured records (account, lines, plans, usage, charges, devices),
analysed per person/plan/month, and summarised on the site and by email.

## How it fits together

| Piece | File |
|---|---|
| Web app (FastAPI, server-rendered Jinja) | `app/main.py`, `app/templates/`, `app/static/` |
| Bill extraction + self-learning | `app/extract.py` |
| Analysis, insights, admin stats | `app/analysis.py` |
| Ask AI (Claude + read-only SQL over the user's own data) | `app/chat.py` |
| Mailbox polling (IMAP, every 60s) and sending (SMTP) | `app/mailer.py`, `app/notify.py`, `app/templates/email/` |
| Background worker (mail + 3 parallel extractions + summaries) | `app/worker.py` |
| SQLite schema | `app/db.py` |

### How the analyser gets smarter
1. **Knowledge base** - every bill whose lines reconcile to the invoice total adds the carrier's plan names,
   prices and charge categorisations to `knowledge`, which is fed into the next extraction for that carrier.
2. **Verified references** - bills the superadmin corrects and marks verified (Admin > Bills) are used as
   reference extractions for that carrier.
3. **Self-correction** - if a bill doesn't reconcile, it's re-extracted at higher effort with the discrepancy pointed out.

Admin > Learning shows (and lets you edit) everything it has learned.

## Deployment
Droplet `secondopinion-syd1` (DigitalOcean, Sydney). `deploy/update.sh` runs every minute via `so-update.timer`,
pulls `main` from GitHub and restarts `so-web` / `so-worker` when it changes. Caddy terminates TLS.
Secrets (Anthropic key, mail password) are entered at `/setup?token=...` or Admin > Settings and stored in
the server's database - never in this repo. Data lives in `/var/lib/secondopinion`.

## Local development
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ANTHROPIC_API_KEY=... DATA_DIR=./data .venv/bin/uvicorn app.main:app --reload
DATA_DIR=./data .venv/bin/python -m app.worker
```

## Switch scenario, sales dossier and porting
`app/recommend.py` maps every current service to a 2degrees business plan (from the plan catalogue) and scores
the estate's "mobile health". `app/dossier.py` builds the sales pack: an XLSX (one row per service with provider,
LSP account number/name, MSISDN, user, current plan and spend, recommended 2degrees plan and price) plus the
customer's bill PDFs, emailed to the sales address when a customer asks for an offer, or from Admin.
See `docs/PORTING.md` for why the bill alone is enough to port a number.
