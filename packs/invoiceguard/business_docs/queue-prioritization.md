---
title: Queue Prioritization — The Weight Formula in Plain Language
author: D. Okafor, Audit Operations Manager
date: 2026-01-19
status: adopted
source_repo: chad-adams-firebase/invoice-guard
source_path: docs/business-context/queue-prioritization.md
source_commit_sha: "761a18e9b9253870d930f1b13b3a852ce516d603"
copied_date: 2026-08-17
---

# Queue Prioritization — The Weight Formula in Plain Language

Eight auditors cannot read two thousand invoices. The queue exists to
answer one question per pull: *which unworked invoice returns the most
if an auditor opens it right now?* This memo explains the priority
weight in business terms; the exact formula lives in the roll-up
component and its factors in the config table.

## Start with dollars on the table

The base of the weight is the recovery opportunity itself: the sum of
the dollar findings the audit rules wrote (material, service, and fee
buckets), plus the benchmark signal when it is the stronger story —
if the peer comparison says the service hours on an invoice are worth
more than our own service findings, we trust the larger number. A
finding an auditor has already excepted contributes nothing; judged
issues do not keep ringing the bell.

## Multiply for behavior, not just dollars

Two patterns multiply the weight because they are about supplier
*conduct*, and conduct repeats:

- **Creep-back (×2).** A supplier who re-raised a price we already
  corrected once is testing whether we watch. Double weight until the
  pattern is confronted.
- **Ignored corrections (×5).** A supplier who resubmitted without
  applying requested corrections is openly declining the audit
  outcome. That is the strongest signal we track and it outranks any
  dollar figure of similar size.

## Add a flat boost for compliance-critical reports

A compliance report at the bureau's critical line adds a large flat
boost (config `compliance_flat_weight`). Flat, not proportional: the
point is to force review of contract exposure even when the dollar
findings are small.

## Divide by invoice size

Finally the weight is divided by the invoice total. Two invoices with
the same opportunity are not the same job: the smaller one is a
sharper anomaly and a faster review. Dividing by size ranks *density
of opportunity per hour of auditor attention*, which is the resource
we are actually rationing.

## The tie-breakers auditors notice

An invoice whose revision chain an auditor touched within the last
business day is boosted into that auditor's queue — context is
expensive to rebuild, so recent history stays with its auditor.
Credit memos, disputed holds, and excluded suppliers never surface at
all; those are handled outside the audit lane.
