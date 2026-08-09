from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Iterable


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


@dataclass(frozen=True)
class AudioOutput:
    device_index: int
    name: str
    is_default: bool = False
    host_api: str = "unknown"
    output_channels: int = 2
    endpoint_key: str | None = None
    display_name: str | None = None
    is_accessible: bool = True

    @property
    def id(self) -> str:
        identity = self.endpoint_key or f"{self.host_api}\0{_normalize(self.name)}"
        digest = hashlib.sha256(f"local-output\0{identity}".encode()).hexdigest()[:12]
        return f"output_{digest}"

    @property
    def public_name(self) -> str:
        return self.display_name or self.name


class AudioOutputService:
    HOST_API_PRIORITY = ("windows wasapi", "windows directsound", "mme", "windows wdm-ks")
    GENERIC_OUTPUTS = {
        "mapeador de som da microsoft output", "microsoft sound mapper output",
        "driver de som primario", "primary sound driver", "default", "output",
    }
    def __init__(
        self,
        provider,
        *,
        selected_id: str | None = None,
        selected_name: str | None = None,
        aliases: dict[str, str] | None = None,
        persist: Callable[[dict], None] | None = None,
        on_change: Callable[[dict], None] | None = None,
    ):
        self.provider = provider
        self.selected_id = selected_id
        self.selected_name = selected_name
        self.aliases = self._normalize_aliases(aliases or {})
        self.persist = persist
        self.on_change = on_change
        self.revision = 0

    @staticmethod
    def _normalize_aliases(config: dict) -> dict[str, str]:
        """Flatten persisted alias groups while retaining legacy alias -> target maps."""
        normalized: dict[str, str] = {}
        for key, value in config.items():
            if isinstance(value, str):
                normalized[_normalize(key)] = value
                continue
            if not isinstance(value, dict) or not isinstance(value.get("target"), str):
                continue
            target = value["target"].strip()
            for alias in value.get("aliases", []):
                if isinstance(alias, str) and _normalize(alias):
                    normalized[_normalize(alias)] = target
        return normalized

    def _available(self) -> list[AudioOutput]:
        raw_outputs = list(self.provider.list_outputs())
        eligible = [
            item for item in raw_outputs
            if item.output_channels > 0 and item.is_accessible and self._useful_name(item.name)
        ]
        host_names = {_normalize(item.host_api) for item in eligible}
        preferred_host = next((host for host in self.HOST_API_PRIORITY if host in host_names), None)
        if preferred_host:
            eligible = [item for item in eligible if _normalize(item.host_api) == preferred_host]
        eligible = [item for item in eligible if not self._is_generic_output(item.name)]
        unique = []
        seen = set()
        for item in eligible:
            key = item.endpoint_key or (
                _normalize(item.host_api), _normalize(item.public_name), item.output_channels
            )
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    @classmethod
    def _useful_name(cls, name: str) -> bool:
        normalized = _normalize(name)
        if not normalized or normalized in {"output", "fones de ouvido", "headphones", "speakers"}:
            return False
        if re.search(r"\(\s*\)\s*$", name or ""):
            return False
        return not any(marker in (name or "").lower() for marker in ("@system32\\drivers", "%bthhfenum", "guid"))

    @classmethod
    def _is_generic_output(cls, name: str) -> bool:
        normalized = _normalize(name)
        return (
            normalized in cls.GENERIC_OUTPUTS
            or normalized.startswith("mapeador de som da microsoft")
            or normalized.startswith("microsoft sound mapper")
            or normalized.startswith("driver de som prim")
            or normalized.startswith("primary sound driver")
        )

    def _current_from(self, outputs: Iterable[AudioOutput]) -> AudioOutput | None:
        available = list(outputs)
        current = next((item for item in available if item.id == self.selected_id), None)
        if current is None and self.selected_name:
            saved = _normalize(self.selected_name)
            current = next((item for item in available if saved in {_normalize(item.name), _normalize(item.public_name)}), None)
            if current is None:
                saved_core = self._identity_tokens(self.selected_name)
                matches = [item for item in available if saved_core and saved_core == self._identity_tokens(item.name)]
                current = matches[0] if len(matches) == 1 else None
        if current is None:
            current = next((item for item in available if item.is_default), available[0] if available else None)
            if current is not None:
                self._store(current, notify=False)
        return current

    @staticmethod
    def _public(item: AudioOutput, current: AudioOutput | None) -> dict:
        return {
            "id": item.id,
            "name": item.public_name,
            "is_default": item.is_default,
            "is_current": current is not None and item.id == current.id,
        }

    def list_outputs(self) -> dict:
        try:
            outputs = self._available()
            current = self._current_from(outputs)
            return {"success": True, "outputs": [self._public(item, current) for item in outputs]}
        except Exception:
            return {"success": False, "error": "audio_outputs_unavailable", "outputs": []}

    def get_current_output(self) -> dict:
        listed = self.list_outputs()
        if not listed["success"]:
            return listed
        current = next((item for item in listed["outputs"] if item["is_current"]), None)
        return {"success": current is not None, "output": current, **({} if current else {"error": "no_audio_output"})}

    def resolve(self, reference: str) -> tuple[AudioOutput | None, list[AudioOutput]]:
        outputs = self._available()
        exact_id = next((item for item in outputs if item.id == reference), None)
        if exact_id:
            return exact_id, []
        normalized = _normalize(reference)
        alias_target = self.aliases.get(normalized)
        if alias_target:
            normalized = _normalize(alias_target)
        exact_name = [item for item in outputs if normalized in {_normalize(item.name), _normalize(item.public_name)}]
        if len(exact_name) == 1:
            return exact_name[0], []
        category_terms = {
            "headphones": {"headset", "headphone", "headphones", "fone", "fones"},
            "speakers": {"speaker", "speakers", "caixa", "alto falante", "altofalante"},
            "monitor": {"monitor", "display"},
        }
        requested_categories = {
            category for category, terms in category_terms.items()
            if any(term in normalized for term in terms)
        }
        if requested_categories:
            category_candidates = []
            for item in outputs:
                item_name = _normalize(item.public_name)
                if any(
                    any(term in item_name for term in category_terms[category])
                    for category in requested_categories
                ):
                    category_candidates.append(item)
            if category_candidates:
                return (
                    (category_candidates[0], [])
                    if len(category_candidates) == 1 else (None, category_candidates)
                )
        tokens = set(normalized.split())
        generic = {
            "meu", "minha", "do", "da", "de", "o", "a", "para", "no", "na",
            "audio", "som", "voz", "dispositivo", "computador", "pc", "alto", "falante",
        }
        tokens -= generic
        candidates = [item for item in outputs if tokens and tokens <= set(_normalize(item.public_name).split())]
        return (candidates[0], []) if len(candidates) == 1 else (None, candidates)

    @staticmethod
    def _identity_tokens(name: str) -> frozenset[str]:
        generic = {
            "fones", "fone", "ouvido", "headphones", "headphone", "speakers", "speaker",
            "audio", "output", "saida", "de", "do", "da", "r",
        }
        return frozenset(_normalize(name).split()) - generic

    def set_output(self, reference: str) -> dict:
        if not isinstance(reference, str) or not reference.strip():
            return {"success": False, "error": "invalid_audio_output"}
        try:
            alias_target = self.aliases.get(_normalize(reference))
            selected, candidates = self.resolve(reference.strip())
            if selected is None:
                if alias_target:
                    return {
                        "success": False,
                        "error": "audio_output_unavailable",
                        "target": alias_target,
                    }
                if candidates:
                    return {
                        "success": False,
                        "error": "ambiguous_audio_output",
                        "candidates": [{"id": item.id, "name": item.public_name} for item in candidates],
                    }
                return {"success": False, "error": "audio_output_not_found"}
            self.provider.validate_output(selected.device_index)
            self._store(selected, notify=True)
            self._log_selection(reference, selected, reason="explicit")
            return {"success": True, "output": {"id": selected.id, "name": selected.public_name}}
        except Exception:
            return {"success": False, "error": "audio_output_activation_failed"}

    def _store(self, selected: AudioOutput, *, notify: bool) -> None:
        changed = self.selected_id != selected.id or self.selected_name != selected.name
        self.selected_id = selected.id
        self.selected_name = selected.name
        if changed:
            self.revision += 1
        payload = {"audio_output_id": selected.id, "audio_output_name": selected.name}
        if self.persist:
            self.persist(payload)
        if notify and self.on_change:
            self.on_change(payload)

    def current_device_index(self) -> int | None:
        current = self.current_endpoint()
        return current.device_index if current else None

    def current_endpoint(self) -> AudioOutput | None:
        """Return the current raw endpoint from the latest PortAudio enumeration."""
        return self._current_from(self._available())

    def log_restored_selection(self) -> None:
        endpoint = self.current_endpoint()
        if endpoint is None:
            print(
                "[SPEAKER_SELECTION] reason=restore requested_value=None error=no_audio_output",
                flush=True,
            )
            return
        self._log_selection(self.selected_id or self.selected_name, endpoint, reason="restore")

    def _log_selection(self, requested, endpoint: AudioOutput, *, reason: str) -> None:
        print(
            "[SPEAKER_SELECTION] "
            f"reason={reason} requested_value={requested!r} "
            f"display_name={endpoint.public_name!r} opaque_id={endpoint.id!r} "
            f"canonical_identity={endpoint.endpoint_key!r} raw_name={endpoint.name!r} "
            f"raw_index={endpoint.device_index} host_api={endpoint.host_api!r} "
            f"is_default={endpoint.is_default} is_current={endpoint.id == self.selected_id}",
            flush=True,
        )
