# Project Story — Conceiva

## Inspiration

One of our team members ran into how hard it is to enter the fertility industry — and how wasteful the pipeline can be. Digging into sperm-bank operations, a stark number kept coming up: **about 90% of sperm samples are rejected** and never make it into usable inventory. That is not only a logistics problem for banks; it is a bottleneck for the families waiting on donors.

At the same time, couples who *do* find candidates still face a second gap. Extended carrier screening is long. Shared recessive risks are easy to miss in a spreadsheet. Genetic counselors often spend the first half of a visit reconstructing which donors are even in play. Couples, banks, and counselors sit in separate systems with no shared shortlist or briefing.

Conceiva started from those two pressures: help banks surface sample quality earlier (including motility from real microscopy video), and help couples move from “interesting profile” to “counselor-ready shortlist” without pretending the product is a diagnosis.

## What it does

**Conceiva** is decision support for family building with genetics in the loop.

- **Couples** enter carrier and preference context, swipe through donors (baby-photo–first cards), see hard carrier conflicts before attachment, shortlist candidates, and book a genetic counselor who can see that shortlist.
- **Sperm banks** manage donor catalog records and can run a **motility analysis** on an uploaded microscopy clip (YOLO tracking → progressive / non-progressive / immotile metrics vs WHO reference floors).
- **Genetic counselors** open visits already oriented to the couple’s shortlist, with AI-assisted plain-language summaries of carrier complications and gaps — so the session starts deciding, not catching up.

It is **not** a lab, a diagnosis, or a substitute for a genetic counselor. It is an intermediary on records the users supply: match → shortlist → prepared counseling visit.

## How we built it

- **Web app:** FastAPI + Jinja templates + SQLite, session auth, role-gated flows for couple / bank / counselor.
- **Matching:** Carrier hard-stops, blood-type flags, preference soft weights, and a swipe-style match UI over a seeded demo catalog.
- **Counseling path:** Shortlist, availability slots, booking, and LLM-assisted counselor reports with citations grounded in the case data.
- **Motility service:** Separate FastAPI service wrapping a Ultralytics YOLO + ByteTrack pipeline on public VISEM-style tracking video; H.264 annotated clips via `ffmpeg`; metrics attached back to the donor record.
- **Deploy:** Vercel for the main app (hackathon-friendly); a cheap single **EC2** host for the long-running motility analyzer (CPU torch), because serverless cannot carry multi-minute YOLO jobs. Stripe is wired for optional unlock / session charges but can stay off for a free demo.

## Challenges we ran into

- **Motility will not fit on Vercel.** Torch + OpenCV + 1–2 minute CPU analyze jobs forced a split architecture and a public `MOTILITY_SERVICE_URL`, not `localhost`.
- **GPU wheels on a CPU box.** First Docker builds tried to pull ~2.5 GB of CUDA packages; we switched to CPU torch and an S3 → EC2 bootstrap to keep cost near a single `t3.small`.
- **Missing `ffmpeg` in production.** Annotated clips failed until we installed a static `ffmpeg` on the host and added an `imageio-ffmpeg` fallback in code.
- **Ephemeral `/tmp` SQLite on Vercel.** Consent and demo state could “forget” between cold starts, which made the consent gate loop until we mirrored consent in the session and pre-consented demo accounts.
- **Shipping UI and infra on parallel branches.** A production deploy from the motility branch briefly overwrote a redesign; we had to merge main back before judges saw the right first impression.

## Accomplishments that we're proud of

- An end-to-end path: bank catalog → couple match/shortlist → counselor visit with AI briefing.
- A **real** motility pipeline (not a precomputed fake upload) reachable from the shared demo.
- Clear clinical humility in the product copy: decision support, hard stops for shared recessives, WHO reference comparison without claiming diagnosis.
- A deploy story that actually works for a weekend hackathon: Vercel for the site, one cheap EC2 for the heavy model.

## What we learned

- Fertility workflows are multi-stakeholder; the product only works if couples, banks, and counselors share the same shortlist context.
- “Put the model on the same host as the website” fails when inference is minutes long — architecture has to respect time and memory budgets.
- Demo reliability on serverless means designing for **resetting state** (seeds, session flags, fail-closed secrets), not assuming a durable laptop disk.
- Small ops details (`ffmpeg`, CPU vs CUDA wheels, ALB-less EC2) decide whether the live demo survives first contact with judges.

## What's next for Conceiva

- Durable storage (not `/tmp` SQLite) for shared multi-user demos and real pilots.
- Deeper bank integrations and calibrated motility (true microns/px, lab ground truth).
- Stronger counselor tooling: structured panel imports, clearer gap reports, and visit notes that stay cited.
- Privacy and compliance hardening if we move beyond synthetic demo genomes.
- Keep the promise of the inspiration: less wasted sample inventory upstream, and fewer couples arriving at counseling underprepared downstream.
