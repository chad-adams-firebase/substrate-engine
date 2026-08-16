---
source: machine
needs_validation: true
confidence: 0.5
note: Adapted from invoice-guard docs/functional-spec.md at commit 761a18e9; to be human-validated through use.
---

# InvoiceGuard — what this application is

InvoiceGuard is a supplier-invoice audit application. A mid-size buyer
receives thousands of invoices from roughly forty contracted suppliers,
and a team of about eight auditors can review only a fraction of them
before payment. InvoiceGuard's job is to find the invoices with the most
**recovery opportunity** — a supplier overcharging, billing outside
contract, or quietly undoing an auditor's prior correction — and float
those to the top of each auditor's queue. It fires discrete audit rules
over every invoice line, folds in an external contract-compliance report
and a benchmark score from a peer-comparison service, and rolls
everything into a dollar **opportunity** figure and a priority
**weight** per invoice.

## How an invoice moves through the pipeline

Everything starts when a file lands in the drop directory. The intake
poller (ig.spine.intake) routes files by name: invoice XML to the
parser, compliance reports and prior-review reports to their intakes;
nothing enters the system any other way. The invoice parser
(ig.spine.invoice-parse) extracts the identifiers, refuses to duplicate
a redelivered revision, and creates the invoice row — born `RECEIVED`
for network suppliers, or `LAPSED` (out of audit scope) for
non-network ones. Line mapping (ig.spine.line-mapping) flattens the
parsed payload into queryable columns and one row per line, links the
invoice into its revision chain, and — if the prior revision is
mid-review — parks itself behind a hold-recheck task instead of racing
the auditor.

Several independent branches then look for opportunity, each writing
**findings** against the invoice. The rules engine
(ig.spine.rules-engine) applies the twelve audit rules — rate variance
over contract, unapproved items, quantity spikes, duplicate lines,
freight overcharges, and the rest — each tripping rule recording a
dollar amount. Prior-audit compliance
(ig.spine.prior-audit-compliance) compares a revision against what the
auditor demanded last time; every ignored correction becomes a finding.
Creep-back detection (ig.spine.creepback) walks the revision chain for
prices quietly raised again after an accepted reduction. Compliance
intake (ig.spine.compliance-intake) stores the external compliance
report and turns its rule lines into findings alongside InvoiceGuard's
own. Benchmark scoring (ig.spine.benchmark-scoring) is the convergence
point: it asks a separate tiny HTTP service how the invoice's service
hours and alternate-sourcing percentage compare to peers, and proceeds
gracefully without a benchmark if the service is down.

Roll-up (ig.spine.rollup) reduces all of it to the two queue-driving
numbers — opportunity and weight — honoring auditor feedback (an
excepted finding contributes zero on the next recalculation) and flips
the invoice from `RECEIVED` to `READY`, which is what makes it visible
to queues. Queue surfacing (ig.spine.queue) filters the scored backlog
to each team's categories, excludes credit memos, disputed holds, and
excluded suppliers, and orders by weight; GetNext claims the single
highest-priority eligible invoice, and an authorized direct claim can
deliberately reactivate a `LAPSED` invoice when no newer revision
exists. While working an invoice, the auditor's judgments — valid
exception, rule misfire, notes — are recorded by finding feedback
(ig.spine.feedback) and carried forward to matching findings on later
revisions, so the same issue is never re-judged. Invoices that sit
unworked too long are lapsed by the daily stale sweep
(ig.spine.lapse-lifecycle), which deliberately protects
compliance-critical and in-flight work.

## The platform underneath

Tunable thresholds, weight factors, and the team-to-categories mapping
live in one versioned config table (ig.platform.config); any change
schedules the next nightly recalculation. Auditor records and the
header-based role stub live in ig.platform.users — there is no real
auth by design. All delayed behavior — hold rechecks, compliance
fallbacks, the nightly recalc, the stale sweep — flows through the
scheduled-tasks table and its tick executor (ig.platform.scheduler),
exactly once, in due-at order. Processed and failed drop files are
relocated by ig.platform.file-lifecycle so the drop directory holds
only unprocessed work. Read access for humans and dashboards — queue
health, auditor performance, the 30-day production rollup — is the
Flask API layer (ig.platform.api). The application factory, logging,
clock wiring, and the CLI entry points are ig.platform.bootstrap.

## Status lifecycle, in one line

`RECEIVED → READY → CLAIMED → IN_REVIEW → CLOSED`, with terminals
`NO_REVIEW_NEEDED` and `LAPSED`; every transition writes the history
log through a single helper, and `LAPSED` has exactly one sanctioned
exit — the authorized direct claim.
