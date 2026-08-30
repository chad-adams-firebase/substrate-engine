# Vendored frontend libraries

Pinned copies, served from the engine's static directory so the demo
runs on a machine without open egress (Brief §10.5 ruling: no CDN).
Upstream banner comments are stripped; each library's attribution and
license text is the adjacent `*.LICENSE` file, retained verbatim as
the licenses require.

| file | project | version | license |
|---|---|---|---|
| `marked.min.js` | markedjs/marked | 15.0.12 | MIT (`marked.LICENSE`) |
| `highlight.min.js` | highlightjs/highlight.js — the common-languages build (Python, SQL, JSON, YAML, Bash, …) | 11.11.1 | BSD-3-Clause (`highlight.LICENSE`) |
| `highlight-github.min.css` | highlightjs/highlight.js `styles/github` | 11.11.1 | BSD-3-Clause |

To upgrade: replace the file at the new pinned version, update this
table, and re-run `tests/test_web_static.py`.
