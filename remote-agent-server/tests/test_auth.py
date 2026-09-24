from __future__ import annotations

import pytest

from remote_agent_lite.db import Database
from remote_agent_lite.security import AuthService


@pytest.mark.asyncio
async def test_auth_lifecycle_and_rate_limit(settings) -> None:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.init()
    auth = AuthService(
        db,
        ttl_days=1,
        login_window_seconds=900,
        login_max_failures=3,
        login_lock_seconds=60,
    )
    assert not await auth.has_password()
    await auth.set_password("correct horse battery")
    assert await auth.verify_password("correct horse battery")
    token = await auth.login("correct horse battery", "127.0.0.1")
    assert token
    assert await auth.validate(token) is not None
    assert await auth.login("wrong", "10.0.0.1") is None
    assert await auth.login("wrong", "10.0.0.1") is None
    assert await auth.login("wrong", "10.0.0.1") is None
    with pytest.raises(PermissionError):
        await auth.login("wrong", "10.0.0.1")
    await auth.logout(token)
    assert await auth.validate(token) is None

