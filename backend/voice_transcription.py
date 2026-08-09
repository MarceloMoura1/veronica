"""Deterministic ownership and turn lifecycle for Gemini Live transcripts."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Callable


def normalize_transcript(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def merge_transcript(current: str, incoming: str, *, final: bool = False) -> str:
    current, incoming = normalize_transcript(current), normalize_transcript(incoming)
    if not incoming:
        return current
    if final:
        return incoming
    if not current or incoming.startswith(current):
        return incoming
    if current.startswith(incoming):
        return current
    current_fold, incoming_fold = current.casefold(), incoming.casefold()
    for size in range(min(len(current), len(incoming)), 0, -1):
        if current_fold[-size:] == incoming_fold[:size]:
            return normalize_transcript(current + incoming[size:])
    separator = "" if incoming[:1] in ",.;:!?" else " "
    return normalize_transcript(current + separator + incoming)


class VoiceTranscriptionTurns:
    ROLES = {"user", "assistant"}

    def __init__(self, emit: Callable[[dict], None] | None = None):
        self.emit = emit
        self._active: dict[str, dict] = {}
        self._sequence = 0

    def _new_turn(self, role: str) -> dict:
        turn_id = f"voice-{role}-{uuid.uuid4()}"
        return {"message_id": turn_id, "turn_id": turn_id, "role": role, "text": "",
                "timestamp": datetime.now(timezone.utc).isoformat()}

    def _publish(self, turn: dict, final: bool) -> dict:
        self._sequence += 1
        event = {**turn, "sender": "User" if turn["role"] == "user" else "VERÔNICA",
                 "source": "input_transcription" if turn["role"] == "user" else "output_transcription",
                 "kind": "final" if final else "partial", "final": final, "sequence": self._sequence}
        if self.emit:
            self.emit(event)
        return event

    def ingest(self, role: str, text: str, *, finished: bool = False) -> list[dict]:
        if role not in self.ROLES:
            raise ValueError("invalid transcript role")
        events = []
        if role == "user" and "assistant" in self._active:
            events.extend(self.finalize("assistant"))
        turn = self._active.get(role)
        if turn is None:
            turn = self._active[role] = self._new_turn(role)
        merged = merge_transcript(turn["text"], text, final=finished)
        if merged and (merged != turn["text"] or finished):
            turn["text"] = merged
            events.append(self._publish(turn, finished))
        if finished:
            self._active.pop(role, None)
        return events

    def finalize(self, role: str) -> list[dict]:
        turn = self._active.pop(role, None)
        return [self._publish(turn, True)] if turn and turn["text"] else []

    def finalize_all(self) -> list[dict]:
        return self.finalize("user") + self.finalize("assistant")
