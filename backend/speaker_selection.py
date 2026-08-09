"""Resolve voice speaker requests to labels already exposed by the browser."""
from __future__ import annotations

import re
import unicodedata


PERSONAL_SPEAKER_ALIASES = {
    "personal_headset": (
        "personal_headset", "my_headset", "meu headset", "meu fone", "meu fone de ouvido", "headset",
        "fone", "fone de ouvido", "hyperx", "hyperx cloud",
        "hyperx cloud 3", "hyperx cloud iii",
    ),
    "pc_speakers": (
        "pc_speakers", "personal_speakers", "computer_speakers",
        "speakers", "caixa de som", "minha caixa de som", "alto-falante",
        "alto falante", "alto-falantes", "alto falantes", "realtek",
        "realtek hd", "realtek hd audio",
    ),
}

TARGET_TOKENS = {
    "personal_headset": ("hyperx", "cloud"),
    "pc_speakers": ("realtek",),
}

ROLE_PREFIX_RE = re.compile(r"^(?:default|communications)\s+-\s+", re.IGNORECASE)
BROWSER_DEVICE_ID_RE = re.compile(r"\s*\([0-9a-f]{4}:[0-9a-f]{4}\)\s*$", re.IGNORECASE)


def normalize_speaker_text(value):
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text.lower()))


def logical_speaker_identity(label):
    """Normalize browser roles/IDs only for comparing logical hardware."""
    without_role = ROLE_PREFIX_RE.sub("", str(label or "").strip())
    without_browser_id = BROWSER_DEVICE_ID_RE.sub("", without_role)
    return normalize_speaker_text(without_browser_id)


def _browser_role_priority(label):
    normalized = str(label or "").strip().lower()
    if normalized.startswith("default -"):
        return 0
    if normalized.startswith("communications -"):
        return 2
    return 1


def resolve_voice_output(target, available_labels):
    """Return one existing browser label, never a PortAudio endpoint or index."""
    labels = list(dict.fromkeys(
        str(label).strip() for label in (available_labels or []) if str(label).strip()
    ))

    # A browser label is already a complete, valid identity. Do not reinterpret it.
    exact_match = next((label for label in labels if label == str(target or "").strip()), None)
    if exact_match is not None:
        return {
            "status": "success",
            "target": target,
            "output_device_name": exact_match,
            "applies": "next_session",
        }

    normalized_target = normalize_speaker_text(target)
    logical_targets = [
        logical for logical, aliases in PERSONAL_SPEAKER_ALIASES.items()
        if normalized_target in {normalize_speaker_text(alias) for alias in aliases}
    ]
    if len(logical_targets) != 1:
        return {"status": "unavailable", "target": target}

    logical_target = logical_targets[0]
    required_tokens = TARGET_TOKENS[logical_target]
    candidates = [
        label for label in labels
        if all(token in normalize_speaker_text(label).split() for token in required_tokens)
    ]
    if not candidates:
        return {"status": "unavailable", "target": target}

    logical_groups = {}
    for candidate in candidates:
        logical_groups.setdefault(logical_speaker_identity(candidate), []).append(candidate)
    if len(logical_groups) > 1:
        return {"status": "ambiguous", "target": target, "candidates": candidates}

    equivalent_candidates = next(iter(logical_groups.values()))
    selected = min(
        enumerate(equivalent_candidates),
        key=lambda item: (_browser_role_priority(item[1]), item[0]),
    )[1]
    if len(equivalent_candidates) > 1:
        logical_name = "HyperX Cloud III" if logical_target == "personal_headset" else logical_target
        print(
            f"[SPEAKER_SELECTION] target={target!r} logical_device={logical_name!r} "
            f"candidates={len(equivalent_candidates)} selected={selected!r} "
            "reason='equivalent_browser_roles'"
        )
    return {
        "status": "success",
        "target": logical_target,
        "output_device_name": selected,
        "applies": "next_session",
    }
