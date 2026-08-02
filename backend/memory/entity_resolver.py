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

    def resolve_entities(self, query: str) -> list[dict]:
        normalized = normalize_text(query)
        if self.is_nonsemantic_greeting(query):
            return []
        candidates = {}
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
                    if not alias:
                        continue
                    match = re.search(rf"(?:^|\s){re.escape(alias)}(?:$|\s)", normalized)
                    if match:
                        candidate = (match.start(), -len(alias.split()), -len(alias), key, category)
                        if key not in candidates or candidate < candidates[key]:
                            candidates[key] = candidate
        for key, aliases in self.STATIC_ALIASES.items():
            if key in known:
                continue
            for alias in aliases:
                normalized_alias = normalize_text(alias)
                match = re.search(rf"(?:^|\s){re.escape(normalized_alias)}(?:$|\s)", normalized)
                if match:
                    candidate = (match.start(), -len(normalized_alias.split()), -len(normalized_alias), key, "facts")
                    if key not in candidates or candidate < candidates[key]:
                        candidates[key] = candidate
        if re.search(r"\beu\s+e\b|\be\s+eu\b", normalized):
            candidates["Marcelo"] = (-1, -1, -7, "Marcelo", "profile")
        vocative = bool(re.match(r"^(?:(?:bom dia|boa tarde|boa noite|oi|ola) )?veronica(?: |$)", normalized))
        if vocative and any(name != "Veronica" for name in candidates):
            candidates.pop("Veronica", None)
        return [
            {"name": name, "category": category}
            for _, _, _, name, category in sorted(candidates.values())
        ]

    def resolve_entity(self, query: str):
        candidates = self.resolve_entities(query)
        if not candidates:
            return None
        return dict(candidates[0])

    @staticmethod
    def resolve_intent(query: str) -> str:
        normalized = normalize_text(query)
        patterns = (
            ("session_resume", ("o que a gente tava conversando", "o que a gente estava conversando",
                "o que estavamos conversando", "onde a gente parou", "onde paramos",
                "o que eu estava te contando", "do que eu estava falando", "qual era o assunto",
                "voltando ao assunto", "continua de onde paramos", "lembra do que estavamos falando")),
            ("decision", ("o que decidimos", "tinha decidido", "tinham decidido", "havia decidido", "depois mudei", "decisao", "decisoes", "decidimos", "foi decidido", "definimos")),
            ("plan", ("o que eu ia fazer", "o que eu tinha planejado", "o que planejei", "planejado", "meu plano", "meus planos", "planos ativos", "planos estao ativos")),
            ("event", ("o que aconteceu", "aconteceu recentemente", "acontecendo", "como esta", "como ele esta", "como ela esta", "ele ficou bem", "ela ficou bem", "o que houve", "depois")),
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
    def is_nonsemantic_greeting(query: str) -> bool:
        normalized = normalize_text(query)
        if normalized in {"veronica", "oi", "ola", "bom dia", "boa tarde", "boa noite",
                          "tudo bem", "esta me ouvindo", "voce esta me ouvindo"}:
            return True
        greeting = re.match(r"^(?:oi|ola|bom dia|boa tarde|boa noite) veronica(?: (.*))?$", normalized)
        if not greeting:
            return False
        remainder = greeting.group(1) or ""
        return not remainder or any(marker in remainder for marker in ("esta me ouvindo", "tudo bem", "me escuta"))

    @staticmethod
    def is_continuation(query: str) -> bool:
        normalized = normalize_text(query)
        markers = (
            "mais detalhes", "nela", "nele", "nessa empresa", "nesse projeto",
            "e como", "como ele", "como ela", "e ele", "e ela", "e isso", "e aquilo",
            "esse projeto", "essa empresa", "o assunto", "voltando", "onde a gente parou",
            "nas duas", "nos dois", "as duas", "os dois", "ambos", "ambas",
            "e qual", "qual a meta", "o que a gente faria", "e o",
        )
        return any(marker in normalized for marker in markers)
