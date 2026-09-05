"""Runbook step 0: does the Databricks FM endpoint answer from this
machine, and does it call a tool? One request, one tool, no engine.

    set DATABRICKS_HOST=https://<workspace host>
    set DATABRICKS_TOKEN=<personal access token>
    uv run python scripts/fm_smoke.py <endpoint name>

Prints the finish reason, the tool calls, the content and the usage.
Exit 0 only when a tool call came back — a "hello" that answers in
prose proves nothing the engine needs (Brief §3: the smoke test is a
tool-using turn). Photograph the output. Failure modes are listed in
src/engine/adapters/llm_databricks_fm.py's docstring.
"""

import os
import sys

from openai import OpenAI

host = os.environ["DATABRICKS_HOST"].rstrip("/")
if "://" not in host:
    host = "https://" + host
client = OpenAI(
    api_key=os.environ["DATABRICKS_TOKEN"],
    base_url=f"{host}/serving-endpoints",
    max_retries=0,
    timeout=60,
)
ping = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Echo a number back.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    },
}
response = client.chat.completions.create(
    model=sys.argv[1],
    messages=[{"role": "user", "content": "Call the ping tool with value 7."}],
    tools=[ping],
    temperature=0,
)
choice = response.choices[0]
calls = [(c.function.name, c.function.arguments) for c in (choice.message.tool_calls or [])]
print("endpoint:", sys.argv[1], "->", response.model)
print("finish_reason:", choice.finish_reason)
print("tool_calls:", calls)
print("content:", choice.message.content)
print("usage:", response.usage)
sys.exit(0 if calls else 1)
