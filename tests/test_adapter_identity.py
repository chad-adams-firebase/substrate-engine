"""Fake-user IdentityPort adapter."""

from engine.adapters.identity_fake import FakeUserIdentity, FakeUserSettings
from engine.ports.types import User


def _adapter(acls: list[str] | None = None) -> FakeUserIdentity:
    return FakeUserIdentity(
        FakeUserSettings(
            username="tester", display_name="Test User", acls=acls or []
        )
    )


def test_current_user_matches_settings():
    assert _adapter().current_user() == User(
        username="tester", display_name="Test User"
    )


def test_acls_default_empty():
    assert _adapter().acls() == []


def test_acls_returned_as_configured_and_copied():
    adapter = _adapter(acls=["reviewer_stats"])

    acls = adapter.acls()
    acls.append("mutated")  # caller mutation must not leak back

    assert adapter.acls() == ["reviewer_stats"]
