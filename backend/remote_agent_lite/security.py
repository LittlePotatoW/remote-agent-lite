from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .db import Database
from .store import SettingsStore
from .utils import iso, new_id, parse_iso, random_token, token_hash, utcnow


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(password) > 200:
        raise ValueError("password is too long")
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            salt.hex(),
            digest.hex(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n_raw, r_raw, p_raw, salt_hex, digest_hex = stored.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@dataclass
class AuthSession:
    id: str
    expires_at: datetime
    last_seen_at: datetime


class AuthService:
    PASSWORD_KEY = "admin_password_hash"

    def __init__(self, db: Database, *, ttl_days: int, login_window_seconds: int,
                 login_max_failures: int, login_lock_seconds: int):
        self.db = db
        self.store = SettingsStore(db)
        self.ttl_days = ttl_days
        self.login_window_seconds = login_window_seconds
        self.login_max_failures = login_max_failures
        self.login_lock_seconds = login_lock_seconds

    async def has_password(self) -> bool:
        return bool(await self.store.get(self.PASSWORD_KEY))

    async def set_password(self, password: str) -> None:
        await self.store.set(self.PASSWORD_KEY, hash_password(password))

    async def verify_password(self, password: str) -> bool:
        stored = await self.store.get(self.PASSWORD_KEY)
        if not stored:
            return False
        return verify_password(password, stored)

    async def login(self, password: str, ip: str) -> str | None:
        if await self.is_locked(ip):
            raise PermissionError("too many login failures")
        if not await self.verify_password(password):
            await self.record_failure(ip)
            return None
        await self.clear_failures(ip)
        token = random_token()
        now = utcnow()
        expires = now + timedelta(days=self.ttl_days)
        await self.db.execute(
            """
            INSERT INTO auth_sessions(id, token_hash, created_at, last_seen_at, expires_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (new_id(), token_hash(token), iso(now), iso(now), iso(expires)),
        )
        return token

    async def validate(self, token: str | None) -> AuthSession | None:
        if not token:
            return None
        row = await self.db.fetchone(
            "SELECT id, expires_at, last_seen_at FROM auth_sessions WHERE token_hash = ?",
            (token_hash(token),),
        )
        if not row:
            return None
        expires = parse_iso(row["expires_at"])
        if not expires or expires <= utcnow():
            await self.db.execute("DELETE FROM auth_sessions WHERE id = ?", (row["id"],))
            return None
        now = iso()
        await self.db.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"])
        )
        return AuthSession(
            id=row["id"],
            expires_at=expires,
            last_seen_at=parse_iso(row["last_seen_at"]) or utcnow(),
        )

    async def logout(self, token: str | None) -> None:
        if token:
            await self.db.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(token),)
            )

    async def cleanup_expired(self) -> None:
        await self.db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (iso(),))

    async def is_locked(self, ip: str) -> bool:
        row = await self.db.fetchone(
            "SELECT locked_until FROM login_attempts WHERE ip = ?", (ip,)
        )
        if not row or not row["locked_until"]:
            return False
        locked_until = parse_iso(row["locked_until"])
        if not locked_until:
            return False
        if locked_until <= utcnow():
            await self.db.execute(
                "UPDATE login_attempts SET failures = 0, locked_until = NULL WHERE ip = ?",
                (ip,),
            )
            return False
        return True

    async def record_failure(self, ip: str) -> None:
        now = utcnow()
        row = await self.db.fetchone(
            "SELECT failures, window_start, locked_until FROM login_attempts WHERE ip = ?",
            (ip,),
        )
        if not row:
            await self.db.execute(
                """
                INSERT INTO login_attempts(ip, failures, window_start, locked_until)
                VALUES(?, 1, ?, NULL)
                """,
                (ip, iso(now)),
            )
            return
        window_start = parse_iso(row["window_start"]) or now
        failures = int(row["failures"]) + 1
        locked_until = None
        if failures >= self.login_max_failures:
            locked_until = iso(now + timedelta(seconds=self.login_lock_seconds))
        if (now - window_start).total_seconds() > self.login_window_seconds:
            failures = 1
            window_start = now
        await self.db.execute(
            """
            UPDATE login_attempts
            SET failures = ?, window_start = ?, locked_until = ?
            WHERE ip = ?
            """,
            (failures, iso(window_start), locked_until, ip),
        )

    async def clear_failures(self, ip: str) -> None:
        await self.db.execute(
            "UPDATE login_attempts SET failures = 0, locked_until = NULL WHERE ip = ?",
            (ip,),
        )

