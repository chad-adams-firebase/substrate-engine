"""IdentityPort — who is asking, and what they may see.

Local adapter: fake configured user. Real adapter (later phase):
OBO/forwarded-token identity.
"""

from typing import Protocol

from engine.ports.types import User


class IdentityPort(Protocol):
    def current_user(self) -> User: ...

    def acls(self) -> list[str]: ...
