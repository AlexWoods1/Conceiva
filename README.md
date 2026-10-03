# SpermMatch

Decision support for comparing a couple's carrier report with sperm-bank donor records,
shortlisting candidates for a genetic counselor visit, and cited counselor reports.
It is not a diagnosis and not a prediction of a child.

## Run

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Demo couple: `couple@demo.local` / `demo-couple`

Demo bank: `bank@demo.local` / `demo-bank`

Demo counselor: `counselor@demo.local` / `demo-counselor`

The walkthrough is on `/start`.

After pulling schema changes, delete `data/app.db` (or set a fresh `DATABASE_PATH`) so SQLite recreates tables.

## Stripe (couple match unlock)

Couples pay once to see donor matches (`/match`, donor details, explanations). Intake forms and all bank features are free. Matches stay open when Stripe env vars are unset. To require the payment:

1. Create a one-time Price in the Stripe Dashboard and set `STRIPE_PRICE_ID`.
2. Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `APP_BASE_URL`.
3. Point a webhook at `/webhooks/stripe` for `checkout.session.completed` (or use Stripe CLI to forward locally).

See `.env.example` for the full list. The app reads environment variables only, so start it with the file loaded:

```bash
uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000
```
