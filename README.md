# Conceiva

**Decision support for family building with genetics in the loop.**

Conceiva helps couples compare carrier-screening context with sperm-bank donor records, shortlist candidates for a genetic counselor visit, and give that counselor a cited briefing before the session starts.

It is an intermediary on records users supply — **not** a diagnosis, a lab test, or a substitute for a genetic counselor.

**Live demo:** [conceiva.vercel.app](https://conceiva.vercel.app)

---

## Who it serves

| Role | What they do |
| --- | --- |
| **Couples** | Enter carrier and preference context, review donors with hard-stop conflicts visible, shortlist candidates, and book a counselor who already sees that shortlist. |
| **Sperm banks** | Maintain donor catalog records and optionally run motility analysis on microscopy video. |
| **Genetic counselors** | Post availability, open booked visits with the couple’s shortlist, and generate field-cited AI briefings per candidate. |

Public signup is for **couples only**. Bank and counselor accounts come from the demo seed (or your own provisioning).

---

## Product highlights

- **Carrier-aware matching** — hard stops and soft preference weights over a confirmed donor catalog
- **Shortlist → visit** — one booked counselor visit per couple, with the shortlist snapshotted onto the appointment
- **Cited counselor reports** — plain-language summaries grounded in case fields (decision support only)
- **Motility first pass** — YOLO tracking on VISEM-style video; progressive / non-progressive / immotile metrics for bank review
- **Optional Stripe unlock** — couples can pay once to open matches when Stripe env vars are set; leave them blank for a free demo

---

## Stack

- **App:** FastAPI, Jinja templates, SQLite, session auth
- **LLM:** OpenAI-compatible API (Gemini or OpenAI) for counselor briefings
- **Motility:** Separate FastAPI service (`backend/`) with Ultralytics YOLO + ByteTrack
- **Deploy:** Vercel for the web app; optional EC2 host for long-running motility analysis

---

## Quick start

```bash
uv sync
uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Copy `.env.example` to `.env` and set at least `SESSION_SECRET`. See that file for LLM, motility, and Stripe options.

```bash
uv run pytest
```

After pulling schema changes, delete `data/app.db` (or point `DATABASE_PATH` at a fresh file) so SQLite recreates tables, or rely on startup column adds when possible.

### Demo accounts

| Role | Email | Password |
| --- | --- | --- |
| Couple | `couple@demo.local` | `demo-couple` |
| Bank | `bank@demo.local` | `demo-bank` |
| Counselor | `counselor@demo.local` | `demo-counselor` |

---

## Configuration

### Vercel (shared demo)

| Variable | Required | Notes |
| --- | --- | --- |
| `SESSION_SECRET` | **Yes** | Non-default value. App refuses to start without it. |
| `SEED_ON_EMPTY` | Optional | Default `true`. Demo accounts upsert if missing. |
| `STRIPE_*` | Leave unset | Matches stay free for the demo. |
| `MOTILITY_UPLOADS_ENABLED` | Optional | Default `true`. Set `false` to hide the bank upload form. |
| `MOTILITY_SERVICE_URL` / `MOTILITY_SERVICE_API_KEY` | For live analyze | **Required on Vercel** when uploads are on. Must be a public URL (not localhost). |

SQLite on Vercel lives under `/tmp`. Data can reset when instances recycle. Demo logins are shared across judges.

Vercel `maxDuration` is **300s** so the app can wait for YOLO (1–2 min). That needs a plan that allows 300s function duration.

### Stripe (optional)

Couples pay once to see donor matches (`/match`, donor details, explanations) when Stripe env vars are set. Intake forms and all bank features stay free.

1. Create a one-time Price in the Stripe Dashboard and set `STRIPE_PRICE_ID`.
2. Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `APP_BASE_URL`.
3. Point a webhook at `/webhooks/stripe` for `checkout.session.completed` (or forward with Stripe CLI locally).

---

## Motility analysis

Bank uploads POST video to a separate FastAPI service in `backend/`. That service is **not** part of the Vercel deploy.

### Local

```bash
uv sync --group motility
uv run --group motility python -m backend
```

Or:

```bash
$env:MOTILITY_SERVICE_DEV="1"
uv run --group motility uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

In `.env` for the main app (same machine):

```
MOTILITY_SERVICE_URL=http://127.0.0.1:8010
MOTILITY_SERVICE_DEV=1
```

Or set the same `MOTILITY_SERVICE_API_KEY` on both processes. Check `GET http://127.0.0.1:8010/health` before uploading. Analysis takes 1–2 minutes on CPU.

### AWS (cheap public host)

Remote bank uploads need a public motility service. This repo ships a **single t3.small EC2** stack (no ALB; stop it between demos).

From the repo root (AWS CLI required; **no local Docker** — EC2 installs CPU torch from an S3 source pack):

```powershell
.\infra\motility\deploy.ps1
```

Copy the printed `MOTILITY_SERVICE_URL` and `MOTILITY_SERVICE_API_KEY` into the Vercel project env, then redeploy. First boot takes ~5–15 minutes while torch installs.

```powershell
.\infra\motility\deploy.ps1 -Stop   # after demos
.\infra\motility\deploy.ps1 -Start  # before the next demo
```

Smoke: `GET http://<eip>:8010/health`

---

## Disclaimer

Conceiva is decision support on the records you enter. It does not diagnose disease, predict a child’s traits or health, or replace clinical genetic counseling. Always use it with a qualified genetic counselor.
