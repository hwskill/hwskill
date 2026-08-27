from __future__ import annotations

import re

from .models import EffectiveCatalog, SearchResult


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def search_skills(catalog: EffectiveCatalog, query: str, limit: int = 10) -> list[SearchResult]:
    query_tokens = _tokens(query)
    results = []
    for skill in catalog.skills:
        name_tokens = _tokens(skill.skill_id + " " + skill.name)
        description_tokens = _tokens(skill.description)
        name_matches = sum(
            1
            for query_token in query_tokens
            if any(
                name_token == query_token
                or name_token.startswith(query_token)
                or query_token.startswith(name_token)
                for name_token in name_tokens
            )
        )
        score = 10 * name_matches + len(query_tokens & description_tokens)
        if score:
            results.append(SearchResult(
                skill.skill_id, skill.name, skill.description, score, skill.content_digest
            ))
    return sorted(results, key=lambda item: (-item.score, item.skill_id))[:limit]
