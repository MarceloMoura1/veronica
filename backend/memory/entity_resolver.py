"""Deterministic entity and intent resolution for personal memory queries."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text))


class EntityResolver:
    STATIC_ALIASES = {
        "FaYerS": ("fayers", "fayer", "fires", "faiers"),
        "MegaDesk": ("megadesk", "mega desk", "mega deste"),
        "Christyan": ("christyan", "cristian", "christian"),
        "JosianeFrancaDeMoura": (
            "josiane", "josiane franca de moura", "minha mae", "mae do marcelo",
        ),
        "AntonioJocelioLacerdaDeMoura": (
            "antonio", "antonio jocelio", "meu pai", "pai do marcelo",
        ),
        "Veronica": ("veronica", "assistente veronica"),
        "Jarvis": ("jarvis",),
        "Pedro": ("pedro",),
    }

    def __init__(self, memory_manager):
        self.memory = memory_manager

    def resolve_entity(self, query: str):
        normalized = normalize_text(query)
        candidates = []
        known = {}
        facts = self.memory.get_category("facts")
        for category in ("projects", "people"):
            for key, value in self.memory.get_category(category).items():
                known[key] = category
                aliases = {normalize_text(key)}
                if isinstance(value, dict):
                    aliases.update(normalize_text(str(value.get(field, ""))) for field in ("name", "canonical_name"))
                for alias in self.STATIC_ALIASES.get(key, ()):
                    aliases.add(normalize_text(alias))
                entity_prefix = normalize_text(key).replace(" ", "")
                for fact_key, fact_value in facts.items():
                    normalized_key = normalize_text(fact_key).replace(" ", "")
                    if normalized_key.startswith(entity_prefix) and "alias" in normalized_key:
                        aliases.add(normalize_text(str(fact_value)))
                for alias in aliases:
                    if alias and re.search(rf"(?:^|\s){re.escape(alias)}(?:$|\s)", normalized):
                        candidates.append((len(alias.split()), len(alias), key, category))
        for key, aliases in self.STATIC_ALIASES.items():
            if key in known:
                continue
            for alias in aliases:
                normalized_alias = normalize_text(alias)
                if re.search(rf"(?:^|\s){re.escape(normalized_alias)}(?:$|\s)", normalized):
                    candidates.append((len(normalized_alias.split()), len(normalized_alias), key, "facts"))
        if not candidates:
            return None
        _, _, name, category = max(candidates)
        return {"name": name, "category": category}

    @staticmethod
    def resolve_intent(query: str) -> str:
        normalized = normalize_text(query)
        patterns = (
            ("operations", ("o que fazemos", "o que a gente faria", "o que fariamos", "fariamos", "como funciona", "operacao", "servicos", "faria nela")),
            ("detail", ("detalhes", "mais detalhes", "detalhadamente", "aprofundar", "conte mais")),
            ("goals", ("qual a meta", "qual e a meta", "objetivo", "primeiro ano", "meta")),
            ("relationship", ("meu socio", "minha socia", "melhores amigos", "relacao", "relacionamento")),
            ("preference", ("como deve me chamar", "como voce deve me chamar", "preferencia", "tratamento")),
            ("personal", ("minha mae", "meu pai", "irmaos", "familia", "sobre mim")),
            ("future", ("o que eu espero", "futuro", "visao", "vai ajudar", "como vai ajudar")),
            ("overview", ("o que e", "o que seria", "explique", "visao geral")),
            ("identity", ("quem e", "quem sao", "quem")),
        )
        for intent, phrases in patterns:
            if any(phrase in normalized for phrase in phrases):
                return intent
        return "standard"

    @staticmethod
    def is_continuation(query: str) -> bool:
        normalized = normalize_text(query)
        markers = (
            "mais detalhes", "nela", "nele", "nessa empresa", "nesse projeto",
            "e como", "e qual", "qual a meta", "o que a gente faria", "e o",
        )
        return any(marker in normalized for marker in markers)
