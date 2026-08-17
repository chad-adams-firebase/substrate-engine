---
title: Audit Policy — Why the Thresholds Sit Where They Sit
author: R. Calloway, Audit Policy Lead
date: 2025-11-04
status: adopted
source_repo: chad-adams-firebase/invoice-guard
source_path: docs/business-context/audit-policy-thresholds.md
source_commit_sha: "761a18e9b9253870d930f1b13b3a852ce516d603"
copied_date: 2026-08-17
---

# Audit Policy — Why the Thresholds Sit Where They Sit

This memo records the reasoning behind the four numbers auditors ask
about most. The numbers themselves live in the InvoiceGuard config
table (the tunable ones) or as named constants in the rules; this is
the policy story behind them. Recalibration happens at the annual
policy review, not ad hoc.

## Rate variance: 15% over contract

Contracted rates drift legitimately: surcharge pass-throughs, quarterly
indexation, negotiated substitutions that outrun the paperwork. Our
recovery history says drift under about 12% almost always evaporates
under review — the supplier produces a signed change order and the
auditor's hour is wasted. Above 15%, recoveries stick. We set the trip
line at **15%** to spend audit hours where the yield is, and we
deliberately claim the *full* delta back to contract once tripped: a
supplier past the line does not get to keep the drift under it.

## Freight and handling: 8% of the material subtotal

Freight is the classic soft spot — no catalog rate to compare, so it
absorbs padding. Benchmarking across our lanes puts honest freight and
handling between 3% and 6% of the material value moved. The cap is
**8%**: one and a third times the top of the honest band, so partial
shipments and remote deliveries clear it, and padding does not.
Everything above the cap counts as recovery, not just the excess over
honest freight — the padding hid inside the whole fee.

## Compliance-critical: a report score of 1,500

The contract-compliance bureau scores on its own scale; their guidance
is that anything holding 1,500 or more after their internal netting
has contract exposure worth escalating regardless of invoice size.
We honor that line twice. A report at or above **1,500** grants a
large flat priority boost (the invoice must be looked at even if the
dollar findings are modest), and it *protects the invoice from the
stale sweep* — compliance exposure is not allowed to age out of the
queue quietly. Auditors can except individual compliance findings,
which removes the priority boost, but the sweep protection rides on
the report score itself and stays.

## The lapse window: 6 days plus 1 grace

Payment terms drive this one. Once an invoice is deeper into the
payment cycle than about a week, the recovery levers weaken: the
payment run has usually gone out, and we are negotiating a clawback
instead of a short-pay. Six days is the working window our recovery
rates support; the seventh day is deliberate grace so an invoice
received late Friday is not swept before Monday's queue pull. The
window (**6 days**, config `lapse_after_days`) is tunable per audit
season; the **1-day grace** is fixed policy and not a knob.
