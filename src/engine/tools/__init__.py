"""The closed tool surface (Brief §6).

Each tool is a registered plugin: name, description, input schema,
run(). Capabilities are tools, never ad-hoc LLM freedom; the registry
built from pack config is the complete list of what the engine can do
for that pack.
"""
