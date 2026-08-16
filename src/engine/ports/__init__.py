"""Port interfaces (hexagonal boundary).

Everything here is an interface or a contract type. Nothing in this
package may import a concrete adapter — enforced by tests/test_architecture.py.
"""

from engine.ports.execution_log import ExecutionLogPort
from engine.ports.identity import IdentityPort
from engine.ports.llm import LLMPort
from engine.ports.source_code import SourceCodePort
from engine.ports.sql import SqlPort
from engine.ports.substrate_store import SubstrateStorePort
from engine.ports.types import (
    LLMResponse,
    Message,
    RunStatus,
    TimeWindow,
    ToolCall,
    ToolSpec,
    User,
    Workspace,
)
from engine.ports.work_store import WorkStorePort

__all__ = [
    "ExecutionLogPort",
    "IdentityPort",
    "LLMPort",
    "LLMResponse",
    "Message",
    "RunStatus",
    "SourceCodePort",
    "SqlPort",
    "SubstrateStorePort",
    "TimeWindow",
    "ToolCall",
    "ToolSpec",
    "User",
    "Workspace",
    "WorkStorePort",
]
