# SpermMatch — Executive Summary

**Product:** SpermMatch (web app: Donor Match)  
**Audience:** Couples seeking donor sperm, and sperm banks managing inventory  
**Scope:** Decision support on user-supplied records — not a diagnosis, lab test, or prediction of a child

## Problem

Choosing a sperm donor today forces couples and clinics to stitch together carrier screens, blood-type notes, bank policies, and preference surveys by hand. Shared recessive carrier status is easy to miss. Soft constraints such as CMV, ID-release policy, and family limits are hard to weigh consistently. Banks and couples also pull in opposite directions: inventory utilization versus fewer medical surprises. Existing catalogs rarely explain *why* a match is safe or risky in plain language tied to the source record.

## Goal

Give couples and sperm banks one place to compare confirmed carrier results and survey preferences, surface hard medical conflicts first, and explain each ranked result from the fields on file — so matching decisions are clearer, more consistent, and less likely to overlook a shared recessive risk.

## What this project does

- **Carrier-aware matching:** Ranks donors for a couple. A shared confirmed recessive gene (for example both heterozygous for CFTR) is a hard stop and stays visible. Soft weights cover ID release, family limit, CMV, and quarantine. Rh mismatch is an informational flag, not a block.
- **Two roles, one site:** Couples complete history, a carrier form, and a two-step survey. Banks maintain per-donor surveys and can confirm fields parsed from a public catalog URL (with a human confirm step; images are not kept).
- **Explainable results:** Match explanations cite retrieved database fields. Missing facts are stated as not in the record. Clinical free text is redacted or kept out of model context. An optional LLM only sees those retrieved fields.
- **Ancestry as context only:** Overlap may note allele-frequency context. It does not change the medical rank. There is no race filter.
- **Consent and privacy:** Genetic fields and scores sit behind sign-in and consent. Account close deletes genotypes, surveys, portrait keys, and explanation logs. Portraits are open-source stand-ins, not donor or child photographs and not phenotype predictions.
- **Demo-ready seed data:** Synthetic donors (including clear matches, carrier conflicts, soft-preference misses, and incomplete panels) so the product can be walked through without real genomes or real photos.

## What this project deliberately does not do

- Predict or generate a future child’s face or appearance  
- Rank donors by IQ, personality, attractiveness, or other polygenic “optimization” traits  
- Claim to diagnose disease or replace clinical genetic counseling  
- Use ancestry or race as a match-quality score  

## Success looks like

A couple can enter history and carrier results, complete preferences, and see a ranked list where medical conflicts appear before preference fit, with each explanation expandable to the stored field. A bank can maintain inventory and confirm catalog-derived fields without inventing genetic claims. Judges and users can follow `/start` with demo accounts and see the hard-stop and soft-weight behavior without real patient data.
