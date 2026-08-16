---
source: machine
needs_validation: true
confidence: 0.5
---

# Snapshot primer (fixture)

Invoices are scored by the audit rules in ig.spine.rules-engine and
surfaced to auditors by ig.spine.queue. Invoices left unworked past
the cutoff are lapsed by the daily sweep in ig.spine.lapse-lifecycle.
