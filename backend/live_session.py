"""Sanitized Gemini Live session state and configuration."""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field


DEFAULT_COMPRESSION_TRIGGER_TOKENS = 6000
DEFAULT_COMPRESSION_TARGET_TOKENS = 3000


def compression_limits() -> tuple[int, int]:
    trigger = int(os.getenv("GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS", DEFAULT_COMPRESSION_TRIGGER_TOKENS))
    target = int(os.getenv("GEMINI_LIVE_COMPRESSION_TARGET_TOKENS", DEFAULT_COMPRESSION_TARGET_TOKENS))
    if trigger <= 0 or target <= 0 or target >= trigger:
        raise ValueError("Gemini Live compression requires 0 < target_tokens < trigger_tokens")
    return trigger, target


def short_handle_hash(handle: str | None) -> str | None:
    if not handle:
        return None
    return hashlib.sha256(handle.encode("utf-8")).hexdigest()[:12]


@dataclass
class LiveSessionState:
    """Operational state only; identifiers never derive from conversation content."""

    logical_session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    connection_id: str | None = None
    turn_index: int = 0
    connection_turn_index: int = 0
    connection_count: int = 0
    cold_start_send_count: int = 0
    cold_start_in_flight: bool = False
    resumption_handle: str | None = field(default=None, repr=False)
    resumption_requested: bool = False
    resumption_accepted: bool = False
    fallback_manual_used: bool = False
    go_away_received: bool = False

    def begin_connection(self) -> None:
        self.connection_id = uuid.uuid4().hex
        self.connection_count += 1
        self.connection_turn_index = 0
        self.resumption_requested = bool(self.resumption_handle)
        self.resumption_accepted = False
        self.fallback_manual_used = False
        self.go_away_received = False

    @property
    def cold_start_sent(self) -> bool:
        return self.cold_start_send_count > 0

    def begin_cold_start(self) -> bool:
        if self.cold_start_sent or self.cold_start_in_flight or self.resumption_requested:
            return False
        self.cold_start_in_flight = True
        return True

    def complete_cold_start(self, success: bool) -> None:
        if self.cold_start_in_flight and success:
            self.cold_start_send_count += 1
        self.cold_start_in_flight = False

    def complete_turn(self) -> None:
        self.turn_index += 1
        self.connection_turn_index += 1

    def update_resumption(self, *, resumable: bool | None, new_handle: str | None) -> None:
        if resumable is True and new_handle:
            self.resumption_handle = new_handle
            self.resumption_accepted = self.resumption_requested
        elif resumable is False:
            self.resumption_handle = None
            self.resumption_accepted = False

    def sanitized(self) -> dict:
        return {
            "logical_session_id": self.logical_session_id,
            "connection_id": self.connection_id,
            "logical_session_new": self.connection_count == 1 and self.turn_index <= 1,
            "connection_new": self.connection_turn_index <= 1,
            "reconnected": self.connection_count > 1,
            "resumption_requested": self.resumption_requested,
            "resumption_accepted": self.resumption_accepted,
            "resumption_handle_hash": short_handle_hash(self.resumption_handle),
            "fallback_manual_used": self.fallback_manual_used,
            "turn_index": self.turn_index,
            "connection_turn_index": self.connection_turn_index,
            "cold_start_send_count": self.cold_start_send_count,
            "go_away_received": self.go_away_received,
        }
