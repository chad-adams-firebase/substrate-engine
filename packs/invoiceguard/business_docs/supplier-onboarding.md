---
title: Supplier Onboarding Note — The First-Year Review Flag
author: R. Calloway, Audit Policy Lead
date: 2025-08-22
status: adopted
source_repo: chad-adams-firebase/invoice-guard
source_path: docs/business-context/supplier-onboarding.md
source_commit_sha: "761a18e9b9253870d930f1b13b3a852ce516d603"
copied_date: 2026-08-17
---

# Supplier Onboarding Note — The First-Year Review Flag

Every invoice from a supplier inside its first contracted year carries
a flat review flag (`new_supplier`, no dollar amount). This note
records why the flag exists and why it is deliberately toothless.

New suppliers are not worse actors — they are *unlearned* ones, in
both directions. Their billing teams have not internalized our
contract structure, so honest mistakes cluster: the wrong rate card,
list price instead of contract price, freight billed the way their
previous customer allowed. And our own baselines for them are thin —
the quantity-spike rule needs history to have a trailing average at
all. The first-year flag tells the auditor: *the statistical rules
have less footing here; look with your eyes.*

The flag carries no amount and adds no priority weight on its own.
That is intentional. A new supplier with clean invoices should not
outrank real recovery opportunity just for being new; the flag is
context for the auditor who already has the invoice open, not a
reason to open it.

The window is **365 days** from `first_contracted_at`. One full year
covers a complete seasonal cycle of the supplier's billing — annual
true-ups, holiday rush fees, year-end adjustments — so the flag
retires only after we have seen each kind of invoice they send at
least once. Suppliers without a recorded first-contract date do not
trip the flag; the onboarding checklist owns getting that date filed.
