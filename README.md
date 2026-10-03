# SpermMatch

Decision support for comparing a couple's carrier report with sperm-bank donor records,
shortlisting candidates for a genetic counselor visit, and cited counselor reports.
It is not a diagnosis and not a prediction of a child.

## Run locally

```bash
uv sync
uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Demo couple: `couple@demo.local` / `demo-couple`

Demo bank: `bank@demo.local` / `demo-bank`

Demo counselor: `counselor@demo.local` / `demo-counselor`

The walkthrough is on `/start`.

Public signup is for **couples only**. Bank and counselor accounts come from the demo seed.

After pulling schema changes, delete `data/app.db` (or set a fresh `DATABASE_PATH`) so SQLite recreates tables, or rely on startup column adds when possible.

```bash
uv run pytest
```

## Shared Vercel (hackathon)

Set these in the Vercel project environment:

| Variable | Required | Notes |
|---|---|---|
| `SESSION_SECRET` | **Yes** | Non-default value. App refuses to start without it. |
| `SEED_ON_EMPTY` | Optional | Default `true`. Demo accounts upsert if missing. |
| `STRIPE_*` | Leave unset | Matches stay free for the demo. |
| `MOTILITY_UPLOADS_ENABLED` | Optional | Default `true`. Set `false` to hide the bank upload form. |
| `MOTILITY_SERVICE_URL` / `MOTILITY_SERVICE_API_KEY` | For live analyze | Point at the motility backend (`backend/`). Local default `http://127.0.0.1:8010`. |

SQLite on Vercel lives under `/tmp`. Data can reset when instances recycle. Demo logins are shared across judges.

## Stripe (optional couple match unlock)

Couples pay once to see donor matches (`/match`, donor details, explanations) when Stripe env vars are set. Intake forms and all bank features stay free. Matches stay open when Stripe env vars are unset.

1. Create a one-time Price in the Stripe Dashboard and set `STRIPE_PRICE_ID`.
2. Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `APP_BASE_URL`.
3. Point a webhook at `/webhooks/stripe` for `checkout.session.completed` (or use Stripe CLI to forward locally).

See `.env.example` for the full list. The app reads environment variables only, so start it with the file loaded:

```bash
uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Motility backend (local)

Bank uploads POST the video to a separate FastAPI service in `backend/` that runs YOLO tracking. That service is not part of the Vercel deploy.

```bash
uv sync --group motility
uv run --group motility python -m backend
```

Or explicitly:

```bash
$env:MOTILITY_SERVICE_DEV="1"
uv run --group motility uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

In `.env` for the main app (same machine):

```
MOTILITY_SERVICE_URL=http://127.0.0.1:8010
MOTILITY_SERVICE_DEV=1
```

Or set the same `MOTILITY_SERVICE_API_KEY` on both processes. Check `GET http://127.0.0.1:8010/health` before uploading from the bank donor page. Analysis takes 1-2 minutes on CPU.
