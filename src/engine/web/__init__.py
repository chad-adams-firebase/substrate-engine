"""The Phase 5 web layer: a thin Flask shell over AskSession and
WorkStorePort (Brief §10). No engine logic in routes; routes receive
the container's resolved objects exactly as the CLI does and never
import adapters. Every answer path is session.ask() — through the
Verifier, like everywhere else."""
