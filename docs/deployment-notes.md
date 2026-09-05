# Deployment notes — for the team that productionises the demo

Nothing here is built; each is a design decision already reasoned through,
written down so it is not reasoned again. The ports are the contract
(`src/engine/ports/`); every note is one real adapter behind one port.

- **SqlPort, live.** The same Statement Execution client the pull uses
  (`packtools/pull_databricks.py`, `_Warehouse.execute`) behind `run_sql`,
  with the user's forwarded token in place of the PAT — `run_sql(query,
  identity)` already carries the identity. The inline disposition caps a
  result at 25 MiB; `run_sql` already caps rows (`max_result_rows`), so a
  live adapter stays inline and never needs cloud fetch, which would require
  egress to object storage.
- **SubstrateStorePort on Delta.** One table per substrate, one `SELECT *`
  per getter; the JSONL rows are the schema. Provenance columns travel as
  they are; `manifest_id` stays the join to the manifest table.
- **WorkStorePort on Delta or Postgres.** The LangGraph checkpointer swaps
  behind `checkpointer()` (a Postgres saver is the reference pattern). The
  list of engine models registered with the serializer
  (`adapters/work_store_sqlite.py`, `checkpoint_serde`) must move with it.
- **ExecutionLogPort on Splunk.** REST search jobs (port 8089) with the SPL
  templates in the adapter's config, one per component, as the logfmt
  adapter's templates are today; the model never writes SPL. The port's
  two questions (`did_run`, `recent_errors`) are the whole surface.
- **LLMPort auth.** A service principal's OAuth token in place of the
  personal access token; the adapter changes only where the token comes
  from. Endpoint names stay pack config.
- **Identity.** A forwarded-token identity adapter behind `IdentityPort`
  (`current_user`, `acls`); the fake adapter shows the shape. Peer
  visibility of published units is pack config, not code (Brief §18).
- **Hosting.** Databricks Apps, gated on the Unity Catalog migration; the
  demo runs locally by design. The web layer takes resolved objects only
  (`web/app.py`), so hosting changes the composition root, not the app.
- **Worlds.** A pulled world is pinned by `schema|table@version`
  (`source_snapshot` in its manifest); a live adapter has no snapshot, and
  the eval preflight's world-manifest comparison would need a different pin
  (a Delta version per table at run start).
