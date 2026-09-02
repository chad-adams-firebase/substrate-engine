# CLAUDE.md — Working Conventions

Read this every session. The spec is `docs/technical-build-brief-v2.md`; the delivery plan is `docs/phasing.md`. When this file and improvisation disagree, this file wins.

## Toolchain

- Python 3.12 only: `requires-python = ">=3.12,<3.13"`. Never use the system 3.14.
- uv for everything: `uv add`, `uv run`, `uv sync`. Never pip, never hand-made venvs, never requirements.txt. Commit `pyproject.toml` AND `uv.lock`.
- Run tests with `uv run pytest`. A phase is not done with red tests.
- Dependencies must ship prebuilt wheels for cp312-win_amd64 (the work machine cannot compile). Check before adding.

## Architecture laws

- Ports never import adapters. The core never instantiates a concrete adapter; DI from config only.
- Config over code: no hostnames, endpoints, table names, thresholds, or branding in engine code. Pack config or env, always.
- The engine never knows which pack it runs. Pack-specific config and substrates live in the pack directory; the target application's source lives in its own repo and is reached only through SourceCodePort at a pinned SHA.
- Tool surface is closed: new capabilities are registered tools, never ad-hoc LLM freedom.
- The LLM never generates SPL, never types figures where code can inject them, and drafts number-bearing prose at temperature 0.
- Every answer path goes through the Verifier node. No bypasses, including "simple" answers.

## Data laws

- Name-based column access everywhere. Never positional. (A real production bug motivates this.)
- Join-key normalization (lowercase) happens once, at the adapter boundary — nowhere else.
- Every substrate row carries provenance: `source`, `confidence`, `last_confirmed_by`, `last_confirmed_date`, `needs_validation`.
- Regeneration overwrites only `source=machine` rows. Human rows are sacred. Write a test proving it.
- CKG node/edge IDs are content-addressed (hash of qualified name + kind). Never positional, never generation-ordered.
- Generators emit a manifest (commit SHA / source identifiers, timestamp, generator version). Substrate rows link to it.
- Published Units record the substrate versions that grounded them.

## Repo conventions

- Branch: work on main locally; the work machine pushes only to `report/YYYY-MM-DD` branches, never main.
- `.gitattributes` with `* text=auto eol=lf` is committed and stays.
- `--force-with-lease`, never `--force`. Prefer not rewriting pushed history at all.
- Small, logical commits with messages saying why, not what.

## Style

- pydantic models for every contract and config shape. No naked dicts across module boundaries.
- Keep the canonical schemas minimal: a field with no consumer does not exist.
- Frontend is vanilla JS + marked.js/Chart.js/highlight.js. No build pipeline, no framework, no localStorage.
- Write for a maintainer who is not the author: the developer will debug the real adapters at work with a chat assistant, not an agent. Clear names and small files beat cleverness.

## Testing discipline

- Generator fixtures have checked-in expected output; a changed extractor must change fixtures deliberately.
- The InvoiceGuard pack's machine-derived substrates are always produced by the generators — never hand-edited. If output looks wrong, fix the generator.
- Verifier tests include deliberately wrong results and corrupted summaries; catching them is the acceptance test.
- Model pin changes are isolated commits and trigger a full bank re-run before any other change lands.
