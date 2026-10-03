# SpermMatch

Decision support for comparing a couple's carrier report with sperm-bank donor records,
shortlisting candidates for a genetic counselor visit, and cited counselor reports.
It is not a diagnosis and not a prediction of a child.

## Run locally

```bash
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
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
| `MOTILITY_UPLOADS_ENABLED` | Leave unset | Defaults off on Vercel (analyze cannot fit in 60s). |
| `MOTILITY_SERVICE_URL` / `MOTILITY_SERVICE_API_KEY` | Local only | Motility service is a separate long-lived process. |

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

## Motility (local / long-lived host)

Bank video analyze needs the separate motility service (`backend/`) and is not supported on the Vercel function. Run both locally when you need that demo path.
