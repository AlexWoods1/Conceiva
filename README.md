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
