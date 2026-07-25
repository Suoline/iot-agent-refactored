from __future__ import annotations

import re


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    latin_tokens = re.findall(r"[a-z0-9_]+", lowered)
    cjk_chars = [char for char in lowered if "\u4e00" <= char <= "\u9fff"]
    cjk_bigrams = [
        "".join(cjk_chars[index : index + 2])
        for index in range(len(cjk_chars) - 1)
    ]
    return {token for token in [*latin_tokens, *cjk_chars, *cjk_bigrams] if token}


class VectorIndex:
    """Dependency-free retrieval fallback with the same role as the Chroma adapter.

    Chroma can be added behind this interface later. The experiment runner remains
    runnable in the current environment where chromadb is not installed.
    """

    def search(self, query: str, records, top_k: int = 10):
        query_tokens = _tokens(query)
        ranked = []
        for record in records:
            combined = f"{record.subject} {record.object} {record.natural_text}"
            text_tokens = _tokens(combined)
            union = query_tokens | text_tokens
            overlap = len(query_tokens & text_tokens) / len(union or {"_"})
            if overlap > 0:
                ranked.append((overlap, record))
        ranked.sort(key=lambda item: (-item[0], item[1].memory_id))
        return ranked[:top_k]
