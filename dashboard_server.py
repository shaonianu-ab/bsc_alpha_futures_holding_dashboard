#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, time as wall_clock_time, timezone
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import list_bsc_futures_by_fdv as source


PROJECT_ROOT = Path(__file__).resolve().parent
WEB_DIRECTORY = PROJECT_ROOT / "web"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "dashboard.sqlite3"
MAX_JSON_BODY_BYTES = 1_000_000
SINGAPORE_TIME_ZONE = ZoneInfo("Asia/Singapore")
AUTH_SESSION_COOKIE = "bsc_holdings_session"
AUTH_SESSION_LIFETIME = timedelta(hours=12)
LOGIN_ATTEMPT_WINDOW = timedelta(minutes=15)
MAX_LOGIN_ATTEMPTS = 5
PASSWORD_HASH_ITERATIONS = 600_000
AUTH_SECRET_SETTING_KEYS = frozenset({"auth_password_salt", "auth_password_hash"})

DEFAULT_SETTINGS = {
    "wallet_addresses": "",
    "default_initial_purchase_usd": "30",
    "default_target_value_usd": "30",
    "low_fdv_limit_usd": "200000000",
    "price_gap_alert_pct": "20",
    "scheduled_refresh_time": "09:00",
    "auth_enabled": "0",
    "auth_username": "",
}
EDITABLE_SETTINGS = frozenset(DEFAULT_SETTINGS)


@dataclass(frozen=True)
class AuthenticationConfiguration:
    enabled: bool
    username: str
    password_salt: str
    password_hash: str

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.password_salt and self.password_hash)


@dataclass(frozen=True)
class AuthenticatedSession:
    token: str
    username: str
    expires_at: datetime


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def singapore_today() -> str:
    return datetime.now(SINGAPORE_TIME_ZONE).date().isoformat()


def parse_scheduled_refresh_time(value: Any) -> wall_clock_time:
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except (TypeError, ValueError) as error:
        raise ValueError("scheduled_refresh_time must use HH:MM format") from error


def decimal_from(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def nonnegative_decimal(value: Any, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a number") from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field_name} must be a non-negative number")
    return result


def normalized_contract_address(value: Any, *, required: bool = False) -> str:
    address = str(value or "").strip().lower()
    if not address:
        if required:
            raise ValueError("contract_address is required")
        return ""
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError("contract_address must be a BSC contract address")
    try:
        int(address[2:], 16)
    except ValueError as error:
        raise ValueError("contract_address must be a BSC contract address") from error
    return address


def enabled_setting(value: Any, field_name: str) -> bool:
    if value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if value is False or str(value).strip().lower() in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{field_name} must be enabled or disabled")


def normalized_auth_username(value: Any, *, required: bool) -> str:
    username = str(value or "").strip()
    if not username and not required:
        return ""
    if not 1 <= len(username) <= 64:
        raise ValueError("auth_username must contain 1 to 64 characters")
    if any(character.isspace() or ord(character) < 32 for character in username):
        raise ValueError("auth_username cannot contain whitespace or control characters")
    return username


def normalized_auth_password(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("auth_password must be text")
    if not 8 <= len(value) <= 256:
        raise ValueError("auth_password must contain 8 to 256 characters")
    return value


def derive_password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS
    ).hex()


def password_record(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    return base64.b64encode(salt).decode("ascii"), derive_password_hash(password, salt)


def password_matches(password: str, configuration: AuthenticationConfiguration) -> bool:
    try:
        salt = base64.b64decode(configuration.password_salt.encode("ascii"), validate=True)
        expected_hash = derive_password_hash(password, salt)
    except (ValueError, UnicodeEncodeError):
        return False
    return hmac.compare_digest(expected_hash, configuration.password_hash)


class AuthenticationManager:
    def __init__(self, configuration: AuthenticationConfiguration) -> None:
        self._configuration = configuration
        self._sessions: dict[str, AuthenticatedSession] = {}
        self._failed_attempts: dict[str, list[datetime]] = defaultdict(list)
        self._lock = threading.Lock()

    @property
    def authentication_enabled(self) -> bool:
        with self._lock:
            return self._configuration.enabled

    def replace_configuration(self, configuration: AuthenticationConfiguration) -> bool:
        with self._lock:
            changed = configuration != self._configuration
            self._configuration = configuration
            if changed:
                self._sessions.clear()
                self._failed_attempts.clear()
            return changed

    def session(self, token: str) -> AuthenticatedSession | None:
        if not token:
            return None
        with self._lock:
            now = datetime.now(timezone.utc)
            self._discard_expired_sessions(now)
            return self._sessions.get(token)

    def login(
        self, remote_address: str, username: str, password: str
    ) -> tuple[AuthenticatedSession | None, int]:
        with self._lock:
            now = datetime.now(timezone.utc)
            self._discard_expired_sessions(now)
            attempts = self._recent_attempts(remote_address, now)
            if len(attempts) >= MAX_LOGIN_ATTEMPTS:
                retry_at = attempts[0] + LOGIN_ATTEMPT_WINDOW
                retry_after = max(1, int((retry_at - now).total_seconds()) + 1)
                return None, retry_after

            configuration = self._configuration
            valid_username = hmac.compare_digest(
                username.encode("utf-8"), configuration.username.encode("utf-8")
            )
            valid_password = configuration.is_configured and password_matches(password, configuration)
            if not configuration.enabled or not valid_username or not valid_password:
                attempts.append(now)
                return None, 0

            self._failed_attempts.pop(remote_address, None)
            return self._create_session(now), 0

    def create_session(self) -> AuthenticatedSession | None:
        with self._lock:
            if not self._configuration.enabled or not self._configuration.is_configured:
                return None
            now = datetime.now(timezone.utc)
            self._discard_expired_sessions(now)
            return self._create_session(now)

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def _recent_attempts(self, remote_address: str, now: datetime) -> list[datetime]:
        attempts = self._failed_attempts[remote_address]
        cutoff = now - LOGIN_ATTEMPT_WINDOW
        attempts[:] = [attempt for attempt in attempts if attempt > cutoff]
        return attempts

    def _discard_expired_sessions(self, now: datetime) -> None:
        expired_tokens = [
            token for token, session in self._sessions.items() if session.expires_at <= now
        ]
        for token in expired_tokens:
            del self._sessions[token]

    def _create_session(self, now: datetime) -> AuthenticatedSession:
        session = AuthenticatedSession(
            token=secrets.token_urlsafe(32),
            username=self._configuration.username,
            expires_at=now + AUTH_SESSION_LIFETIME,
        )
        self._sessions[session.token] = session
        return session


def token_candidates_by_symbol(tokens: list[sqlite3.Row]) -> dict[str, list[dict[str, str]]]:
    candidates: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen_contracts: set[str] = set()
    for token in tokens:
        symbol = str(token["symbol"]).strip().upper()
        contract_address = normalized_contract_address(token["contract_address"], required=True)
        candidate_key = f"{symbol}:{contract_address}"
        if candidate_key in seen_contracts:
            continue
        seen_contracts.add(candidate_key)
        candidates[symbol].append(
            {
                "contract_address": contract_address,
                "name": str(token["name"]),
                "symbol": symbol,
            }
        )
    for entries in candidates.values():
        entries.sort(key=lambda entry: (entry["name"], entry["contract_address"]))
    return candidates


def parse_wallet_addresses(value: str) -> list[str]:
    addresses: list[str] = []
    seen: set[str] = set()
    for candidate in value.replace(",", "\n").splitlines():
        candidate = candidate.strip()
        if not candidate:
            continue
        address = source.validate_wallet_address(candidate)
        if address not in seen:
            seen.add(address)
            addresses.append(address)
    return addresses


def parse_settings_payload(payload: dict[str, Any]) -> dict[str, str]:
    unexpected = set(payload) - EDITABLE_SETTINGS
    if unexpected:
        raise ValueError(f"Unsupported settings: {', '.join(sorted(unexpected))}")

    parsed = dict(DEFAULT_SETTINGS)
    parsed.update({key: str(value) for key, value in payload.items()})
    wallets = parse_wallet_addresses(parsed["wallet_addresses"])
    initial_purchase = nonnegative_decimal(
        parsed["default_initial_purchase_usd"], "default_initial_purchase_usd"
    )
    target_value = nonnegative_decimal(parsed["default_target_value_usd"], "default_target_value_usd")
    low_fdv_limit = nonnegative_decimal(parsed["low_fdv_limit_usd"], "low_fdv_limit_usd")
    price_gap_alert = nonnegative_decimal(parsed["price_gap_alert_pct"], "price_gap_alert_pct")
    scheduled_refresh_time = parse_scheduled_refresh_time(parsed["scheduled_refresh_time"])
    authentication_enabled = enabled_setting(parsed["auth_enabled"], "auth_enabled")
    authentication_username = normalized_auth_username(
        parsed["auth_username"], required=authentication_enabled
    )

    return {
        "wallet_addresses": "\n".join(wallets),
        "default_initial_purchase_usd": decimal_text(initial_purchase),
        "default_target_value_usd": decimal_text(target_value),
        "low_fdv_limit_usd": decimal_text(low_fdv_limit),
        "price_gap_alert_pct": decimal_text(price_gap_alert),
        "scheduled_refresh_time": scheduled_refresh_time.strftime("%H:%M"),
        "auth_enabled": "1" if authentication_enabled else "0",
        "auth_username": authentication_username,
    }


class DashboardStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY,
                    fetched_at TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    wallet_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS token_snapshots (
                    id INTEGER PRIMARY KEY,
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                    contract_address TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    futures_symbol TEXT NOT NULL,
                    fdv_usd TEXT NOT NULL,
                    token_price TEXT NOT NULL,
                    market_price TEXT,
                    price_gap_pct TEXT,
                    holder_count INTEGER,
                    match_method TEXT NOT NULL,
                    onchain_amount TEXT NOT NULL,
                    onchain_value_usd TEXT NOT NULL,
                    UNIQUE(snapshot_id, contract_address)
                );

                CREATE INDEX IF NOT EXISTS token_snapshots_snapshot_id
                    ON token_snapshots(snapshot_id);

                CREATE TABLE IF NOT EXISTS token_preferences (
                    contract_address TEXT PRIMARY KEY,
                    initial_purchase_usd TEXT,
                    target_value_usd TEXT,
                    replenish_enabled INTEGER NOT NULL DEFAULT 1,
                    ignored INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS manual_holdings (
                    id INTEGER PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    asset_symbol TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    contract_address TEXT NOT NULL DEFAULT '',
                    mapping_status TEXT NOT NULL CHECK(mapping_status IN ('confirmed', 'pending')),
                    updated_at TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS manual_holdings_contract
                    ON manual_holdings(contract_address, mapping_status);

                CREATE TABLE IF NOT EXISTS scheduled_refreshes (
                    snapshot_date TEXT PRIMARY KEY,
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                    completed_at TEXT NOT NULL
                );
                """
            )
            snapshot_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(snapshots)").fetchall()
            }
            if "snapshot_date" not in snapshot_columns:
                connection.execute("ALTER TABLE snapshots ADD COLUMN snapshot_date TEXT")
                connection.execute(
                    "UPDATE snapshots SET snapshot_date = substr(fetched_at, 1, 10)"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS snapshots_snapshot_date
                ON snapshots(snapshot_date, id DESC)
                """
            )
            for key, value in DEFAULT_SETTINGS.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, value),
                )

    def get_settings(self) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        settings = dict(DEFAULT_SETTINGS)
        settings.update(
            {row["key"]: row["value"] for row in rows if row["key"] in DEFAULT_SETTINGS}
        )
        return settings

    def authentication_configuration(self) -> AuthenticationConfiguration:
        settings = self.get_settings()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?)",
                tuple(AUTH_SECRET_SETTING_KEYS),
            ).fetchall()
        secrets_by_key = {row["key"]: row["value"] for row in rows}
        return AuthenticationConfiguration(
            enabled=settings["auth_enabled"] == "1",
            username=settings["auth_username"],
            password_salt=secrets_by_key.get("auth_password_salt", ""),
            password_hash=secrets_by_key.get("auth_password_hash", ""),
        )

    def update_settings(self, payload: dict[str, Any]) -> dict[str, str]:
        unexpected = set(payload) - EDITABLE_SETTINGS - {"auth_password"}
        if unexpected:
            raise ValueError(f"Unsupported settings: {', '.join(sorted(unexpected))}")
        current = self.get_settings()
        current.update({key: value for key, value in payload.items() if key != "auth_password"})
        parsed = parse_settings_payload(current)
        previous_authentication = self.authentication_configuration()
        password_value = payload.get("auth_password")
        password = "" if password_value in (None, "") else normalized_auth_password(password_value)

        password_salt = ""
        password_hash = ""
        if parsed["auth_enabled"] == "1":
            if password:
                password_salt, password_hash = password_record(password)
            elif not previous_authentication.is_configured:
                raise ValueError("auth_password is required when enabling authentication")

        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                parsed.items(),
            )
            if parsed["auth_enabled"] == "0":
                connection.executemany(
                    "DELETE FROM settings WHERE key = ?",
                    ((key,) for key in AUTH_SECRET_SETTING_KEYS),
                )
            elif password:
                connection.executemany(
                    """
                    INSERT INTO settings(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (
                        ("auth_password_salt", password_salt),
                        ("auth_password_hash", password_hash),
                    ),
                )
        return parsed

    def save_snapshot(
        self,
        matches: list[source.TokenMatch],
        wallet_count: int,
        snapshot_date: str | None = None,
    ) -> int:
        fetched_at = utc_now()
        target_date = snapshot_date or singapore_today()
        try:
            date.fromisoformat(target_date)
        except ValueError as error:
            raise ValueError("snapshot_date must use YYYY-MM-DD format") from error
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots(fetched_at, snapshot_date, wallet_count) VALUES (?, ?, ?)",
                (fetched_at, target_date, wallet_count),
            )
            snapshot_id = int(cursor.lastrowid)
            rows = []
            for match in matches:
                contract_address = normalized_contract_address(match.contract_address, required=True)
                onchain_value = source.calculate_holding_value(match.holding_amount, match.token_price)
                rows.append(
                    (
                        snapshot_id,
                        contract_address,
                        match.symbol,
                        match.name,
                        match.futures_symbol,
                        decimal_text(match.fdv),
                        decimal_text(match.token_price),
                        decimal_text(match.market_price) if match.market_price is not None else None,
                        decimal_text(match.price_gap_pct)
                        if match.price_gap_pct is not None
                        else None,
                        match.holder_count,
                        match.match_method,
                        decimal_text(match.holding_amount),
                        decimal_text(onchain_value),
                    )
                )
            connection.executemany(
                """
                INSERT INTO token_snapshots(
                    snapshot_id, contract_address, symbol, name, futures_symbol, fdv_usd,
                    token_price, market_price, price_gap_pct, holder_count, match_method,
                    onchain_amount, onchain_value_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return snapshot_id

    def current_token_candidates(self, asset_symbol: str) -> list[dict[str, str]]:
        snapshot, tokens = self.latest_snapshot()
        if snapshot is None:
            return []
        return token_candidates_by_symbol(tokens).get(asset_symbol, [])

    def auto_confirm_pending_holdings(self) -> int:
        snapshot, tokens = self.latest_snapshot()
        if snapshot is None:
            return 0

        candidates_by_symbol = token_candidates_by_symbol(tokens)
        updates: list[tuple[str, str, str, int]] = []
        for holding in self.manual_holdings():
            if holding["mapping_status"] != "pending":
                continue
            candidates = candidates_by_symbol.get(holding["asset_symbol"], [])
            if len(candidates) == 1:
                updates.append(
                    (
                        candidates[0]["contract_address"],
                        "confirmed",
                        utc_now(),
                        holding["id"],
                    )
                )
        if not updates:
            return 0

        with self.connect() as connection:
            connection.executemany(
                """
                UPDATE manual_holdings
                SET contract_address = ?, mapping_status = ?, updated_at = ?
                WHERE id = ?
                """,
                updates,
            )
        return len(updates)

    def latest_snapshot(self) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
        with self.connect() as connection:
            snapshot = connection.execute(
                """
                SELECT id, fetched_at, snapshot_date, wallet_count
                FROM snapshots
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if snapshot is None:
                return None, []
            rows = connection.execute(
                """
                SELECT contract_address, symbol, name, futures_symbol, fdv_usd, token_price,
                       market_price, price_gap_pct, holder_count, match_method, onchain_amount,
                       onchain_value_usd
                FROM token_snapshots
                WHERE snapshot_id = ?
                ORDER BY CAST(fdv_usd AS REAL) DESC, symbol
                """,
                (snapshot["id"],),
            ).fetchall()
        return snapshot, rows

    def snapshot_dates(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshots.id, snapshots.snapshot_date, snapshots.fetched_at,
                       snapshots.wallet_count, COUNT(token_snapshots.id) AS token_count
                FROM snapshots
                INNER JOIN (
                    SELECT snapshot_date, MAX(id) AS id
                    FROM snapshots
                    GROUP BY snapshot_date
                ) latest_by_date ON latest_by_date.id = snapshots.id
                LEFT JOIN token_snapshots ON token_snapshots.snapshot_id = snapshots.id
                GROUP BY snapshots.id
                ORDER BY snapshots.snapshot_date DESC, snapshots.id DESC
                """
            ).fetchall()
        return [
            {
                "snapshot_date": row["snapshot_date"],
                "snapshot_id": row["id"],
                "fetched_at": row["fetched_at"],
                "wallet_count": row["wallet_count"],
                "token_count": row["token_count"],
            }
            for row in rows
        ]

    def snapshot_for_date(self, snapshot_date: str) -> sqlite3.Row:
        try:
            date.fromisoformat(snapshot_date)
        except ValueError as error:
            raise ValueError("Snapshot date must use YYYY-MM-DD format") from error
        with self.connect() as connection:
            snapshot = connection.execute(
                """
                SELECT id, fetched_at, snapshot_date, wallet_count
                FROM snapshots
                WHERE snapshot_date = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (snapshot_date,),
            ).fetchone()
        if snapshot is None:
            raise ValueError(f"No snapshot exists for {snapshot_date}")
        return snapshot

    def holder_comparison(self, from_date: str, to_date: str) -> dict[str, Any]:
        if from_date == to_date:
            raise ValueError("Choose two different snapshot dates")
        from_snapshot = self.snapshot_for_date(from_date)
        to_snapshot = self.snapshot_for_date(to_date)
        with self.connect() as connection:
            from_tokens = connection.execute(
                """
                SELECT contract_address, symbol, name, holder_count
                FROM token_snapshots
                WHERE snapshot_id = ?
                """,
                (from_snapshot["id"],),
            ).fetchall()
            to_tokens = connection.execute(
                """
                SELECT contract_address, symbol, name, holder_count, fdv_usd, token_price
                FROM token_snapshots
                WHERE snapshot_id = ?
                """,
                (to_snapshot["id"],),
            ).fetchall()

        from_by_contract = {row["contract_address"]: row for row in from_tokens}
        to_by_contract = {row["contract_address"]: row for row in to_tokens}
        rows: list[dict[str, Any]] = []
        unavailable_count = 0
        for contract_address in from_by_contract.keys() & to_by_contract.keys():
            before = from_by_contract[contract_address]
            after = to_by_contract[contract_address]
            if before["holder_count"] is None or after["holder_count"] is None:
                unavailable_count += 1
                continue
            holder_change = after["holder_count"] - before["holder_count"]
            rows.append(
                {
                    "contract_address": contract_address,
                    "symbol": after["symbol"],
                    "name": after["name"],
                    "from_holder_count": before["holder_count"],
                    "to_holder_count": after["holder_count"],
                    "holder_change": holder_change,
                    "holder_change_pct": (
                        holder_change / before["holder_count"] * 100
                        if before["holder_count"] > 0
                        else None
                    ),
                    "fdv_usd": float(decimal_from(after["fdv_usd"])),
                    "token_price": after["token_price"],
                }
            )
        rows.sort(
            key=lambda row: (
                -abs(row["holder_change"]),
                -row["holder_change"],
                row["symbol"],
            )
        )
        return {
            "from_snapshot": {
                "snapshot_date": from_snapshot["snapshot_date"],
                "fetched_at": from_snapshot["fetched_at"],
            },
            "to_snapshot": {
                "snapshot_date": to_snapshot["snapshot_date"],
                "fetched_at": to_snapshot["fetched_at"],
            },
            "rows": rows,
            "comparable_count": len(rows),
            "unavailable_count": unavailable_count,
            "missing_token_count": len(from_by_contract.keys() ^ to_by_contract.keys()),
        }

    def has_scheduled_refresh(self, snapshot_date: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM scheduled_refreshes WHERE snapshot_date = ?",
                (snapshot_date,),
            ).fetchone()
        return row is not None

    def record_scheduled_refresh(self, snapshot_date: str, snapshot_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_refreshes(snapshot_date, snapshot_id, completed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(snapshot_date) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    completed_at = excluded.completed_at
                """,
                (snapshot_date, snapshot_id, utc_now()),
            )

    def latest_scheduled_refresh(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_date, snapshot_id, completed_at
                FROM scheduled_refreshes
                ORDER BY snapshot_date DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "snapshot_date": row["snapshot_date"],
            "snapshot_id": row["snapshot_id"],
            "completed_at": row["completed_at"],
        }

    def preferences(self) -> dict[str, sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM token_preferences").fetchall()
        return {row["contract_address"]: row for row in rows}

    def update_preference(self, payload: dict[str, Any]) -> None:
        contract_address = normalized_contract_address(payload.get("contract_address"), required=True)
        initial_purchase = payload.get("initial_purchase_usd")
        target_value = payload.get("target_value_usd")
        initial_purchase_text = (
            decimal_text(nonnegative_decimal(initial_purchase, "initial_purchase_usd"))
            if str(initial_purchase or "").strip()
            else None
        )
        target_value_text = (
            decimal_text(nonnegative_decimal(target_value, "target_value_usd"))
            if str(target_value or "").strip()
            else None
        )
        replenish_enabled = 1 if payload.get("replenish_enabled", True) else 0
        ignored = 1 if payload.get("ignored", False) else 0
        note = str(payload.get("note", "")).strip()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO token_preferences(
                    contract_address, initial_purchase_usd, target_value_usd, replenish_enabled,
                    ignored, note, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contract_address) DO UPDATE SET
                    initial_purchase_usd = excluded.initial_purchase_usd,
                    target_value_usd = excluded.target_value_usd,
                    replenish_enabled = excluded.replenish_enabled,
                    ignored = excluded.ignored,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    contract_address,
                    initial_purchase_text,
                    target_value_text,
                    replenish_enabled,
                    ignored,
                    note,
                    utc_now(),
                ),
            )

    def manual_holdings(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT id, source_name, asset_symbol, amount, contract_address, mapping_status,
                       updated_at, note
                FROM manual_holdings
                ORDER BY mapping_status, source_name, asset_symbol, id
                """
            ).fetchall()

    def save_manual_holding(
        self,
        payload: dict[str, Any],
        holding_id: int | None = None,
        candidates: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        source_name = str(payload.get("source_name", "")).strip()
        asset_symbol = str(payload.get("asset_symbol", "")).strip().upper()
        amount = decimal_text(nonnegative_decimal(payload.get("amount"), "amount"))
        contract_address = normalized_contract_address(payload.get("contract_address"))
        note = str(payload.get("note", "")).strip()

        if not source_name:
            raise ValueError("source_name is required")
        if not asset_symbol:
            raise ValueError("asset_symbol is required")
        auto_matched = False
        candidate_count = 0
        if contract_address:
            mapping_status = "confirmed"
        else:
            current_candidates = (
                candidates if candidates is not None else self.current_token_candidates(asset_symbol)
            )
            candidate_count = len(current_candidates)
            if candidate_count == 1:
                contract_address = current_candidates[0]["contract_address"]
                mapping_status = "confirmed"
                auto_matched = True
            else:
                mapping_status = "pending"

        values = (
            source_name,
            asset_symbol,
            amount,
            contract_address,
            mapping_status,
            utc_now(),
            note,
        )
        with self.connect() as connection:
            if holding_id is None:
                existing = connection.execute(
                    """
                    SELECT id FROM manual_holdings
                    WHERE source_name = ? AND asset_symbol = ? AND contract_address = ?
                      AND mapping_status = ?
                    """,
                    (source_name, asset_symbol, contract_address, mapping_status),
                ).fetchone()
                if existing is not None:
                    holding_id = int(existing["id"])
            if holding_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO manual_holdings(
                        source_name, asset_symbol, amount, contract_address, mapping_status,
                        updated_at, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                holding_id = int(cursor.lastrowid)
            else:
                cursor = connection.execute(
                    """
                    UPDATE manual_holdings
                    SET source_name = ?, asset_symbol = ?, amount = ?, contract_address = ?,
                        mapping_status = ?, updated_at = ?, note = ?
                    WHERE id = ?
                    """,
                    (*values, holding_id),
                )
                if cursor.rowcount == 0:
                    raise ValueError("Manual holding not found")
        return {
            "id": holding_id,
            "mapping_status": mapping_status,
            "contract_address": contract_address,
            "candidate_count": candidate_count,
            "auto_matched": auto_matched,
        }

    def delete_manual_holding(self, holding_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM manual_holdings WHERE id = ?", (holding_id,))
            if cursor.rowcount == 0:
                raise ValueError("Manual holding not found")

    def import_manual_holdings(self, csv_text: str) -> int:
        reader = csv.DictReader(StringIO(csv_text.strip()))
        required_columns = {"source_name", "asset_symbol", "amount"}
        if not reader.fieldnames or not required_columns.issubset(set(reader.fieldnames)):
            raise ValueError("CSV requires source_name, asset_symbol, and amount columns")

        snapshot, tokens = self.latest_snapshot()
        candidates_by_symbol = token_candidates_by_symbol(tokens) if snapshot is not None else {}
        imported = 0
        for row in reader:
            if not any(str(value or "").strip() for value in row.values()):
                continue
            self.save_manual_holding(
                {
                    "source_name": row.get("source_name"),
                    "asset_symbol": row.get("asset_symbol"),
                    "amount": row.get("amount"),
                    "contract_address": row.get("contract_address"),
                    "note": row.get("note", ""),
                },
                candidates=candidates_by_symbol.get(
                    str(row.get("asset_symbol") or "").strip().upper(), []
                ),
            )
            imported += 1
        return imported


def aggregate_wallet_balances(
    matches: list[source.TokenMatch], wallet_addresses: list[str]
) -> list[source.TokenMatch]:
    amounts_by_contract: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for wallet_address in wallet_addresses:
        for match in source.fetch_wallet_balances(matches, wallet_address):
            contract_address = normalized_contract_address(match.contract_address, required=True)
            amounts_by_contract[contract_address] += match.holding_amount

    return [
        replace(
            match,
            holding_amount=amounts_by_contract[
                normalized_contract_address(match.contract_address, required=True)
            ],
        )
        for match in matches
    ]


def refresh_snapshot(store: DashboardStore) -> dict[str, Any]:
    settings = store.get_settings()
    wallet_addresses = parse_wallet_addresses(settings["wallet_addresses"])
    matches = source.build_matches()
    matches = aggregate_wallet_balances(matches, wallet_addresses)
    snapshot_id = store.save_snapshot(matches, len(wallet_addresses))
    auto_confirmed_count = store.auto_confirm_pending_holdings()
    return {
        "snapshot_id": snapshot_id,
        "total_count": len(matches),
        "auto_confirmed_count": auto_confirmed_count,
    }


class DailyRefreshScheduler(threading.Thread):
    def __init__(self, store: DashboardStore, refresh_lock: threading.Lock) -> None:
        super().__init__(name="daily-refresh-scheduler", daemon=True)
        self.store = store
        self.refresh_lock = refresh_lock
        self.stop_event = threading.Event()
        self.last_error: str | None = None
        self.last_attempt_at: str | None = None

    def stop(self) -> None:
        self.stop_event.set()

    def status(self) -> dict[str, Any]:
        return {
            "last_attempt_at": self.last_attempt_at,
            "last_error": self.last_error,
        }

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                settings = self.store.get_settings()
                scheduled_time = parse_scheduled_refresh_time(settings["scheduled_refresh_time"])
                now = datetime.now(SINGAPORE_TIME_ZONE)
                today = now.date().isoformat()
                if now.time() >= scheduled_time and not self.store.has_scheduled_refresh(today):
                    if self.refresh_lock.acquire(blocking=False):
                        self.last_attempt_at = utc_now()
                        try:
                            result = refresh_snapshot(self.store)
                            self.store.record_scheduled_refresh(today, result["snapshot_id"])
                            self.last_error = None
                        except Exception as error:  # noqa: BLE001
                            self.last_error = str(error)
                        finally:
                            self.refresh_lock.release()
            except Exception as error:  # noqa: BLE001
                self.last_error = str(error)
            self.stop_event.wait(30)


def match_quality(match_method: str) -> str:
    if match_method == "unique_symbol":
        return "unique_symbol"
    if match_method.startswith("duplicate_symbol_nearest_price:"):
        return "duplicate_symbol_nearest_price"
    return "fallback"


def candidate_tokens_by_manual_holding(
    manual_holdings: list[sqlite3.Row], tokens: list[sqlite3.Row]
) -> dict[int, list[dict[str, str]]]:
    candidates_by_symbol = token_candidates_by_symbol(tokens)

    candidates: dict[int, list[dict[str, str]]] = {}
    for holding in manual_holdings:
        if holding["mapping_status"] == "pending":
            candidates[holding["id"]] = candidates_by_symbol.get(holding["asset_symbol"], [])
    return candidates


def build_dashboard(store: DashboardStore) -> dict[str, Any]:
    settings = store.get_settings()
    snapshot, token_rows = store.latest_snapshot()
    snapshot_dates = store.snapshot_dates()
    scheduled_refresh = {
        "time": settings["scheduled_refresh_time"],
        "timezone": "Asia/Singapore",
        "last_success": store.latest_scheduled_refresh(),
    }
    wallet_addresses = parse_wallet_addresses(settings["wallet_addresses"])
    manual_holdings = store.manual_holdings()
    if snapshot is None:
        manual_payload = [
            {
                "id": row["id"],
                "source_name": row["source_name"],
                "asset_symbol": row["asset_symbol"],
                "amount": row["amount"],
                "contract_address": row["contract_address"],
                "mapping_status": row["mapping_status"],
                "updated_at": row["updated_at"],
                "note": row["note"],
            }
            for row in manual_holdings
        ]
        return {
            "has_snapshot": False,
            "settings": settings,
            "snapshot_dates": snapshot_dates,
            "scheduled_refresh": scheduled_refresh,
            "wallet_addresses": wallet_addresses,
            "manual_holdings": manual_payload,
            "manual_holding_candidates": {},
            "tokens": [],
            "unheld_tokens": [],
            "opportunities": [],
            "replenishments": [],
            "holdings": [],
            "metrics": {
                "token_count": 0,
                "unheld_token_count": 0,
                "recorded_unheld_low_fdv_count": 0,
                "pending_confirmation_count": 0,
                "replenishment_count": 0,
                "replenishment_shortfall_usd": 0,
                "held_token_count": 0,
                "total_holding_value_usd": 0,
            },
            "fdv_distribution": [],
        }

    preferences = store.preferences()
    candidates_by_manual_id = candidate_tokens_by_manual_holding(manual_holdings, token_rows)
    confirmed_amount_by_contract: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    confirmed_sources_by_contract: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pending_count_by_symbol: dict[str, int] = defaultdict(int)

    for holding in manual_holdings:
        amount = decimal_from(holding["amount"])
        if holding["mapping_status"] == "confirmed" and holding["contract_address"]:
            confirmed_amount_by_contract[holding["contract_address"]] += amount
            confirmed_sources_by_contract[holding["contract_address"]].append(
                {
                    "id": holding["id"],
                    "source_name": holding["source_name"],
                    "asset_symbol": holding["asset_symbol"],
                    "amount": decimal_text(amount),
                    "updated_at": holding["updated_at"],
                }
            )
        else:
            pending_count_by_symbol[holding["asset_symbol"]] += 1

    default_initial = decimal_from(settings["default_initial_purchase_usd"])
    default_target = decimal_from(settings["default_target_value_usd"])
    low_fdv_limit = decimal_from(settings["low_fdv_limit_usd"])
    price_gap_alert = decimal_from(settings["price_gap_alert_pct"])

    tokens: list[dict[str, Any]] = []
    for token in token_rows:
        contract_address = token["contract_address"]
        preference = preferences.get(contract_address)
        onchain_amount = decimal_from(token["onchain_amount"])
        manual_amount = confirmed_amount_by_contract[contract_address]
        total_amount = onchain_amount + manual_amount
        token_price = decimal_from(token["token_price"])
        total_value = source.calculate_holding_value(total_amount, token_price)
        fdv = decimal_from(token["fdv_usd"])
        initial_purchase = (
            decimal_from(preference["initial_purchase_usd"])
            if preference and preference["initial_purchase_usd"] is not None
            else default_initial
        )
        target_value = (
            decimal_from(preference["target_value_usd"])
            if preference and preference["target_value_usd"] is not None
            else default_target
        )
        replenish_enabled = bool(preference["replenish_enabled"]) if preference else True
        ignored = bool(preference["ignored"]) if preference else False
        pending_manual_count = pending_count_by_symbol[token["symbol"]]
        has_recorded_holding = total_amount > 0
        has_pending_manual_holding = pending_manual_count > 0
        is_low_fdv = fdv <= low_fdv_limit
        shortfall = max(target_value - total_value, Decimal("0"))
        quality = match_quality(token["match_method"])
        price_gap = decimal_from(token["price_gap_pct"]) if token["price_gap_pct"] else None
        state = (
            "held"
            if has_recorded_holding
            else "pending_confirmation"
            if has_pending_manual_holding
            else "not_recorded"
        )
        is_opportunity = is_low_fdv and state == "not_recorded" and not ignored
        is_replenishment = (
            is_low_fdv
            and state == "held"
            and total_value > 0
            and shortfall > 0
            and replenish_enabled
            and not ignored
        )
        tokens.append(
            {
                "contract_address": contract_address,
                "symbol": token["symbol"],
                "name": token["name"],
                "futures_symbol": token["futures_symbol"],
                "fdv_usd": float(fdv),
                "token_price": decimal_text(token_price),
                "market_price": token["market_price"],
                "price_gap_pct": float(price_gap) if price_gap is not None else None,
                "holder_count": token["holder_count"],
                "match_method": token["match_method"],
                "match_quality": quality,
                "price_gap_alert": price_gap is not None and price_gap >= price_gap_alert,
                "onchain_amount": decimal_text(onchain_amount),
                "onchain_value_usd": float(decimal_from(token["onchain_value_usd"])),
                "manual_amount": decimal_text(manual_amount),
                "manual_value_usd": float(source.calculate_holding_value(manual_amount, token_price)),
                "total_amount": decimal_text(total_amount),
                "total_value_usd": float(total_value),
                "initial_purchase_usd": float(initial_purchase),
                "target_value_usd": float(target_value),
                "shortfall_usd": float(shortfall),
                "replenish_enabled": replenish_enabled,
                "ignored": ignored,
                "note": preference["note"] if preference else "",
                "pending_manual_count": pending_manual_count,
                "manual_sources": confirmed_sources_by_contract[contract_address],
                "holding_state": state,
                "is_low_fdv": is_low_fdv,
                "is_opportunity": is_opportunity,
                "is_replenishment": is_replenishment,
            }
        )

    unheld_tokens = sorted(
        (token for token in tokens if token["holding_state"] == "not_recorded"),
        key=lambda token: (token["fdv_usd"], token["symbol"]),
    )
    opportunities = sorted(
        (token for token in tokens if token["is_opportunity"]),
        key=lambda token: (token["fdv_usd"], token["symbol"]),
    )
    replenishments = sorted(
        (token for token in tokens if token["is_replenishment"]),
        key=lambda token: (token["fdv_usd"], -token["shortfall_usd"], token["symbol"]),
    )
    holdings = sorted(
        (token for token in tokens if token["holding_state"] == "held"),
        key=lambda token: (-token["total_value_usd"], token["symbol"]),
    )

    fdv_boundaries = sorted(
        {Decimal("0"), Decimal("10000000"), Decimal("100000000"), low_fdv_limit}
    )
    distribution = []
    for index, lower in enumerate(fdv_boundaries):
        upper = fdv_boundaries[index + 1] if index + 1 < len(fdv_boundaries) else None
        lower_label = f"{decimal_text(lower / source.MILLION)}M"
        upper_label = f"{decimal_text(upper / source.MILLION)}M" if upper is not None else ""
        label = f"< {upper_label}" if lower == 0 and upper is not None else (
            f"≥ {lower_label}" if upper is None else f"{lower_label} - {upper_label}"
        )
        bucket_tokens = [
            token
            for token in tokens
            if Decimal(str(token["fdv_usd"])) >= lower
            and (upper is None or Decimal(str(token["fdv_usd"])) < upper)
        ]
        distribution.append(
            {
                "label": label,
                "token_count": len(bucket_tokens),
                "opportunity_count": sum(token["is_opportunity"] for token in bucket_tokens),
                "replenishment_shortfall_usd": round(
                    sum(token["shortfall_usd"] for token in bucket_tokens if token["is_replenishment"]),
                    2,
                ),
            }
        )

    manual_payload = [
        {
            "id": row["id"],
            "source_name": row["source_name"],
            "asset_symbol": row["asset_symbol"],
            "amount": row["amount"],
            "contract_address": row["contract_address"],
            "mapping_status": row["mapping_status"],
            "updated_at": row["updated_at"],
            "note": row["note"],
        }
        for row in manual_holdings
    ]
    candidate_payload = {
        str(holding_id): contracts for holding_id, contracts in candidates_by_manual_id.items()
    }
    return {
        "has_snapshot": True,
        "snapshot": {
            "id": snapshot["id"],
            "fetched_at": snapshot["fetched_at"],
            "snapshot_date": snapshot["snapshot_date"],
            "wallet_count": snapshot["wallet_count"],
        },
        "settings": settings,
        "snapshot_dates": snapshot_dates,
        "scheduled_refresh": scheduled_refresh,
        "wallet_addresses": wallet_addresses,
        "tokens": tokens,
        "unheld_tokens": unheld_tokens,
        "opportunities": opportunities,
        "replenishments": replenishments,
        "holdings": holdings,
        "manual_holdings": manual_payload,
        "manual_holding_candidates": candidate_payload,
        "metrics": {
            "token_count": len(tokens),
            "unheld_token_count": len(unheld_tokens),
            "recorded_unheld_low_fdv_count": len(opportunities),
            "pending_confirmation_count": sum(
                1 for token in tokens if token["holding_state"] == "pending_confirmation"
            ),
            "replenishment_count": len(replenishments),
            "replenishment_shortfall_usd": round(
                sum(token["shortfall_usd"] for token in replenishments), 2
            ),
            "held_token_count": len(holdings),
            "total_holding_value_usd": round(
                sum(token["total_value_usd"] for token in holdings), 2
            ),
        },
        "fdv_distribution": distribution,
    }


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: DashboardStore) -> None:
        super().__init__(address, DashboardRequestHandler)
        self.store = store
        self.refresh_lock = threading.Lock()
        self.authentication = AuthenticationManager(store.authentication_configuration())
        self.scheduler: DailyRefreshScheduler | None = None


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    server: DashboardHTTPServer

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIRECTORY), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self) -> None:
        # The dashboard is a local, frequently updated single-page application.
        # Avoid serving an earlier JavaScript bundle after a backend restart.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        if content_length > MAX_JSON_BODY_BYTES:
            raise ValueError("Request body is too large")
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise ValueError("Request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def handle_api_error(self, error: Exception) -> None:
        status = HTTPStatus.BAD_REQUEST if isinstance(error, ValueError) else HTTPStatus.INTERNAL_SERVER_ERROR
        self.send_json({"error": str(error)}, status)

    def session_token(self) -> str:
        raw_cookie = self.headers.get("Cookie", "")
        try:
            cookie = SimpleCookie()
            cookie.load(raw_cookie)
        except (ValueError, TypeError):
            return ""
        morsel = cookie.get(AUTH_SESSION_COOKIE)
        return morsel.value if morsel is not None else ""

    def is_secure_request(self) -> bool:
        forwarded_protocol = self.headers.get("X-Forwarded-Proto", "")
        return forwarded_protocol.split(",", 1)[0].strip().lower() == "https"

    def session_cookie(self, session: AuthenticatedSession | None = None) -> str:
        cookie = SimpleCookie()
        cookie[AUTH_SESSION_COOKIE] = session.token if session is not None else ""
        morsel = cookie[AUTH_SESSION_COOKIE]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Strict"
        if session is None:
            morsel["max-age"] = 0
        else:
            morsel["max-age"] = int(AUTH_SESSION_LIFETIME.total_seconds())
        if self.is_secure_request():
            morsel["secure"] = True
        return morsel.OutputString()

    def authentication_payload(self, session: AuthenticatedSession | None = None) -> dict[str, Any]:
        authentication_enabled = self.server.authentication.authentication_enabled
        authenticated = not authentication_enabled or session is not None
        return {
            "enabled": authentication_enabled,
            "authenticated": authenticated,
            "username": session.username if session is not None else "",
        }

    def require_authentication(self) -> bool:
        if not self.server.authentication.authentication_enabled:
            return True
        if self.server.authentication.session(self.session_token()) is not None:
            return True
        self.send_json(
            {"error": "请先登录后再访问看板数据", "code": "authentication_required"},
            HTTPStatus.UNAUTHORIZED,
        )
        return False

    def login(self, payload: dict[str, Any]) -> None:
        unexpected = set(payload) - {"username", "password"}
        if unexpected:
            raise ValueError(f"Unsupported login fields: {', '.join(sorted(unexpected))}")
        username = str(payload.get("username", ""))
        password = payload.get("password")
        if not isinstance(password, str):
            password = ""
        session, retry_after = self.server.authentication.login(
            self.client_address[0], username, password
        )
        if session is None:
            if retry_after:
                self.send_json(
                    {
                        "error": "登录失败次数过多，请稍后再试",
                        "code": "login_rate_limited",
                        "retry_after_seconds": retry_after,
                    },
                    HTTPStatus.TOO_MANY_REQUESTS,
                )
            else:
                self.send_json(
                    {"error": "用户名或密码不正确", "code": "invalid_credentials"},
                    HTTPStatus.UNAUTHORIZED,
                )
            return
        self.send_json(
            {"authentication": self.authentication_payload(session)},
            headers={"Set-Cookie": self.session_cookie(session)},
        )

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/api/auth/status":
            session = self.server.authentication.session(self.session_token())
            self.send_json({"authentication": self.authentication_payload(session)})
            return
        if path == "/api/health":
            self.send_json({"status": "ok"})
            return
        if path.startswith("/api/") and not self.require_authentication():
            return
        if path == "/api/dashboard":
            try:
                payload = build_dashboard(self.server.store)
                if self.server.scheduler is not None:
                    payload["scheduled_refresh"]["runtime"] = self.server.scheduler.status()
                self.send_json(payload)
            except Exception as error:  # noqa: BLE001
                self.handle_api_error(error)
            return
        if path == "/api/holder-comparison":
            try:
                query = parse_qs(parsed_url.query)
                from_date = query.get("from_date", [""])[0]
                to_date = query.get("to_date", [""])[0]
                self.send_json(self.server.store.holder_comparison(from_date, to_date))
            except Exception as error:  # noqa: BLE001
                self.handle_api_error(error)
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/auth/login":
                self.login(self.read_json())
                return
            if path == "/api/auth/logout":
                self.server.authentication.logout(self.session_token())
                self.send_json(
                    {"authentication": self.authentication_payload()},
                    headers={"Set-Cookie": self.session_cookie()},
                )
                return
            if not self.require_authentication():
                return
            payload = self.read_json()
            if path == "/api/refresh":
                if not self.server.refresh_lock.acquire(blocking=False):
                    self.send_json({"error": "A refresh is already running"}, HTTPStatus.CONFLICT)
                    return
                try:
                    result = refresh_snapshot(self.server.store)
                finally:
                    self.server.refresh_lock.release()
                self.send_json(result, HTTPStatus.CREATED)
                return
            if path == "/api/settings":
                settings = self.server.store.update_settings(payload)
                configuration_changed = self.server.authentication.replace_configuration(
                    self.server.store.authentication_configuration()
                )
                session = None
                headers: dict[str, str] | None = None
                if configuration_changed:
                    if self.server.authentication.authentication_enabled:
                        session = self.server.authentication.create_session()
                        if session is None:
                            raise RuntimeError("Authentication is enabled without valid credentials")
                        headers = {"Set-Cookie": self.session_cookie(session)}
                    else:
                        headers = {"Set-Cookie": self.session_cookie()}
                else:
                    session = self.server.authentication.session(self.session_token())
                self.send_json(
                    {
                        "settings": settings,
                        "authentication": self.authentication_payload(session),
                    },
                    headers=headers,
                )
                return
            if path == "/api/preferences":
                self.server.store.update_preference(payload)
                self.send_json({"status": "saved"})
                return
            if path == "/api/manual-holdings":
                result = self.server.store.save_manual_holding(payload)
                self.send_json(result, HTTPStatus.CREATED)
                return
            if path == "/api/manual-holdings/import":
                csv_text = str(payload.get("csv_text", ""))
                imported = self.server.store.import_manual_holdings(csv_text)
                self.send_json({"imported": imported}, HTTPStatus.CREATED)
                return
            self.send_json({"error": "Unknown API endpoint"}, HTTPStatus.NOT_FOUND)
        except Exception as error:  # noqa: BLE001
            self.handle_api_error(error)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/manual-holdings/"
        if not path.startswith(prefix):
            self.send_json({"error": "Unknown API endpoint"}, HTTPStatus.NOT_FOUND)
            return
        if not self.require_authentication():
            return
        try:
            holding_id = int(path.removeprefix(prefix))
            result = self.server.store.save_manual_holding(self.read_json(), holding_id)
            self.send_json(result)
        except Exception as error:  # noqa: BLE001
            self.handle_api_error(error)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/manual-holdings/"
        if not path.startswith(prefix):
            self.send_json({"error": "Unknown API endpoint"}, HTTPStatus.NOT_FOUND)
            return
        if not self.require_authentication():
            return
        try:
            holding_id = int(path.removeprefix(prefix))
            self.server.store.delete_manual_holding(holding_id)
            self.send_json({"id": holding_id})
        except Exception as error:  # noqa: BLE001
            self.handle_api_error(error)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local BSC futures dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--open", action="store_true", help="Open the dashboard in the default browser")
    args = parser.parse_args()

    store = DashboardStore(args.database)
    server = DashboardHTTPServer((args.host, args.port), store)
    scheduler = DailyRefreshScheduler(store, server.refresh_lock)
    server.scheduler = scheduler
    url = f"http://{args.host}:{args.port}"
    print(f"Dashboard available at {url}")
    print("The server listens locally and performs daily scheduled refreshes. Press Ctrl+C to stop it.")
    if args.open:
        webbrowser.open(url)
    scheduler.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        scheduler.stop()
        scheduler.join(timeout=1)
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
