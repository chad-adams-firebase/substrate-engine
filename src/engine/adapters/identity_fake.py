"""Fake-user adapter for IdentityPort.

Local development identity: a user configured in the pack, with
optional ACL strings. The real adapter (later phase) derives identity
from an OBO/forwarded token instead.
"""

from pydantic import BaseModel, ConfigDict

from engine.ports.types import User


class FakeUserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    display_name: str
    acls: list[str] = []


class FakeUserIdentity:
    def __init__(self, settings: FakeUserSettings) -> None:
        self._settings = settings

    @property
    def settings(self) -> FakeUserSettings:
        return self._settings

    def current_user(self) -> User:
        return User(
            username=self._settings.username,
            display_name=self._settings.display_name,
        )

    def acls(self) -> list[str]:
        return list(self._settings.acls)
