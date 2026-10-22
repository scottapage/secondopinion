# SecondOpinion

Business mobile and broadband bill analysis for New Zealand, from any provider. Proof of concept.

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
| Background worker (mail, 3 parallel extractions, summaries, catalogue, meter, 7am digest) | `app/worker.py` |
| SQLite schema | `app/db.py` |
| Model access: Claude or any OpenAI-compatible API | `app/ai.py` |
| Click any number, see it on the bill (page renders + highlights) | `app/evidence.py`, `templates/bill.html` |
| NZ overpay meter (front page, anonymised, from real bills) | `app/meter.py` |
| Shareable anonymised finding cards with preview images | `app/share.py`, `templates/share.html` |
| Visitor stories, daily digest, instant alerts | `app/visitors.py`, `app/notify.py` |

### How the analyser gets smarter
1. **Knowledge base** - every bill whose lines reconcile to the invoice total adds the carrier's plan names,
   prices and charge categorisations to `knowledge`, which is fed into the next extraction for that carrier.
2. **Verified references** - bills the superadmin corrects and marks verified (Admin > Bills) are used as
   reference extractions for that carrier.
3. **Self-correction** - if a bill doesn't reconcile, it's re-extracted at higher effort with the discrepancy pointed out.

Admin > Learning shows (and lets you edit) everything it has learned.

## V2 and switching back to V1
`main` is V2 (the current site). Branch `v1` is the frozen first release. `deploy/update.sh` deploys whichever
branch is named in `/var/lib/secondopinion/deploy-branch` (default `main`); Admin > Settings > "Site version"
writes that file, and the server redeploys within a minute. Database changes in V2 are additive, so V1 runs
against the same database.

## Deployment
Droplet `secondopinion-syd1` (DigitalOcean, Sydney). `deploy/update.sh` runs every minute via `so-update.timer`,
pulls the deployed branch from GitHub and restarts `so-web` / `so-worker` when it changes. Caddy terminates TLS.
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

## Providers and bill types
Any NZ provider is read the same way (`CARRIERS` in `app/config.py`; unknown ones arrive as `other` with the name from
the bill). Broadband-only bills are analysed, and energy bills that carry broadband (Mercury, Contact, Electric Kiwi...)
have their power/gas charges kept apart as `non_telco`. Plan catalogues and benefits research cover the big three.

## Other AI providers
Admin > Settings lets the bill reader and the learning passes run on any OpenAI-compatible chat API (OpenAI, Gemini,
OpenRouter, Groq, xAI, Ollama, LM Studio) with JSON output; vision models also read scanned bills from page images.
The research agents and Ask AI use Claude's tool-use loop and stay on Claude.
