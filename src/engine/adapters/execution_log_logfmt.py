"""Logfmt-file adapter for ExecutionLogPort.

Parses the structured log files real target-app runs emit:

    ts=<ISO8601, aware> level=<L> logger=<name> event=<name> k=v ...

with values double-quoted (inner quotes backslash-escaped) when they
contain a space or '='. A malformed line raises: these files are
machine-emitted, so a bad line means a bug upstream, not noise to
skip — the same policy jsonl.read_rows applies to blank lines.

The component→(logger, event, key_field, error_levels) templates live
in pack config settings. They are this adapter's analog of the Splunk
adapter's SPL templates: the query shapes are configuration, and the
LLM never writes one.

Timezone discipline: log timestamps are aware; windows must be aware
too. Naive-vs-aware comparison is the bug this boundary exists to
stop (the target DB stores naive UTC — normalization happens here,
nowhere else).
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from engine.ports.execution_log import ExecutionLogError
from engine.ports.types import LogEvent, RunStatus, TimeWindow


class ComponentTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logger: str
    ran_event: str
    # Which log field narrows a run check to one unit of work (an
    # invoice id, a file name). None: the component has no per-unit
    # runs and asking with a key is an error.
    key_field: str | None = None
    error_levels: list[str] = ["ERROR"]


class LogfmtSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Log file path, relative to the pack directory (resolved by the
    # container) or absolute.
    path: str
    components: dict[str, ComponentTemplate]
    # Cap on RunStatus.matched_lines — receipts, not the count, which
    # always reflects the true total.
    max_evidence_lines: int = 50


def _parse_line(line: str, number: int, path: Path) -> dict[str, str]:
    """One logfmt line to a name-keyed dict. Strict: every token must
    be key=value, quotes must close, quoted values must end a token."""

    def fail(reason: str) -> ExecutionLogError:
        return ExecutionLogError(f"{path}:{number}: malformed logfmt line ({reason})")

    fields: dict[str, str] = {}
    i, n = 0, len(line)
    while i < n:
        eq = line.find("=", i)
        if eq == -1:
            raise fail(f"token without '=': {line[i:].split(' ', 1)[0]!r}")
        key = line[i:eq]
        if not key or " " in key:
            raise fail(f"bad key {key!r}")
        i = eq + 1
        if i < n and line[i] == '"':
            j = i + 1
            parts: list[str] = []
            while j < n and line[j] != '"':
                if line[j] == "\\" and j + 1 < n:
                    parts.append(line[j + 1])
                    j += 2
                else:
                    parts.append(line[j])
                    j += 1
            if j >= n:
                raise fail(f"unterminated quote in value of {key!r}")
            value = "".join(parts)
            i = j + 1
            if i < n and line[i] != " ":
                raise fail(f"garbage after quoted value of {key!r}")
        else:
            space = line.find(" ", i)
            end = space if space != -1 else n
            value = line[i:end]
            i = end
        fields[key] = value
        while i < n and line[i] == " ":
            i += 1
    return fields


def _to_event(line: str, number: int, path: Path) -> LogEvent:
    fields = _parse_line(line, number, path)
    for required in ("ts", "level", "logger", "event"):
        if required not in fields:
            raise ExecutionLogError(
                f"{path}:{number}: malformed logfmt line (no {required}= field)"
            )
    ts = datetime.fromisoformat(fields.pop("ts"))
    if ts.tzinfo is None:
        raise ExecutionLogError(
            f"{path}:{number}: naive timestamp — the log contract is aware ISO8601"
        )
    return LogEvent(
        ts=ts,
        level=fields.pop("level"),
        logger=fields.pop("logger"),
        event=fields.pop("event"),
        fields=fields,
        raw=line,
    )


class LogfmtExecutionLog:
    def __init__(self, settings: LogfmtSettings) -> None:
        self._settings = settings
        self._lazy_events: list[LogEvent] | None = None

    @property
    def settings(self) -> LogfmtSettings:
        return self._settings

    @property
    def _events(self) -> list[LogEvent]:
        # Lazy: constructing the adapter (`engine info`) must not read
        # the log. Cached: the file is a finished run's artifact.
        if self._lazy_events is None:
            path = Path(self._settings.path)
            if not path.is_file():
                raise ExecutionLogError(
                    f"Log file not found: {path} — is the target app's "
                    f"simulation output present?"
                )
            self._lazy_events = [
                _to_event(line, number, path)
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1
                )
                if line.strip()
            ]
        return self._lazy_events

    def _template(self, component: str) -> ComponentTemplate:
        template = self._settings.components.get(component)
        if template is None:
            known = ", ".join(sorted(self._settings.components)) or "(none)"
            raise ExecutionLogError(
                f"Unknown component {component!r}. Known components: {known}."
            )
        return template

    @staticmethod
    def _require_aware(window: TimeWindow) -> None:
        if window.start.tzinfo is None or window.end.tzinfo is None:
            raise ExecutionLogError(
                "TimeWindow must be timezone-aware — log timestamps are aware, "
                "and naive comparisons answer the wrong question."
            )

    def _in_window(self, event: LogEvent, window: TimeWindow) -> bool:
        return window.start <= event.ts < window.end

    def did_run(self, component: str, key: str, window: TimeWindow) -> RunStatus:
        template = self._template(component)
        self._require_aware(window)
        if key and template.key_field is None:
            raise ExecutionLogError(
                f"Component {component!r} has no key_field configured — a "
                f"keyed run check ({key!r}) cannot be answered; ask without "
                f"a key for 'did it run at all'."
            )
        matched = [
            event
            for event in self._events
            if event.logger == template.logger
            and event.event == template.ran_event
            and self._in_window(event, window)
            and (not key or event.fields.get(template.key_field) == key)
        ]
        count = len(matched)
        subject = f"{template.logger}/{template.ran_event}"
        scope = f" for {template.key_field}={key}" if key else ""
        span = f"[{window.start.isoformat()}, {window.end.isoformat()})"
        return RunStatus(
            ran=count > 0,
            count=count,
            detail=f"{count} {subject} event(s){scope} in {span}",
            matched_lines=[
                event.raw for event in matched[: self._settings.max_evidence_lines]
            ],
        )

    def recent_errors(self, component: str, window: TimeWindow) -> list[LogEvent]:
        template = self._template(component)
        self._require_aware(window)
        return [
            event
            for event in self._events
            if event.logger == template.logger
            and event.level in template.error_levels
            and self._in_window(event, window)
        ]
