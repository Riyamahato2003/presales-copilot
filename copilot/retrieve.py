"""Hybrid retrieval: embedding cosine + lexical overlap (numbers/units matter in specs),
plus always-included pinned chunks (exclusion lists)."""
from __future__ import annotations

import numpy as np

from .models import Chunk
from .textutil import tokens


class Index:
    def __init__(self, chunks: list[Chunk], llm, product: str):
        self.chunks, self.llm, self.product = chunks, llm, product
        docs = [f"{c.section}\n{c.text}" for c in chunks]
        self.vecs = llm.embed(docs, "doc") if docs else np.zeros((0, 1))
        self.tok = [set(tokens(d)) for d in docs]

    def search(self, query: str, k: int = 6, max_pinned: int = 3) -> list[Chunk]:
        if not self.chunks:
            return []
        q = self.llm.embed([query], "query")[0]
        cos = self.vecs @ q
        qt = set(tokens(query))
        weights = {t: (2.0 if any(ch.isdigit() for ch in t) else 1.0) for t in qt}
        total = sum(weights.values()) or 1.0
        lex = np.array([sum(w for t, w in weights.items() if t in ct) / total for ct in self.tok])
        score = 0.65 * cos + 0.35 * lex
        order = [i for i in np.argsort(-score)]
        picked: list[Chunk] = []
        for i in order:
            c = self.chunks[i]
            if not c.pinned and len(picked) < k:
                picked.append(c)
        pinned = [c for c in self.chunks if c.pinned][:max_pinned]
        seen, out = set(), []
        for c in picked + pinned:
            if c.id not in seen:
                seen.add(c.id)
                out.append(c)
        return out
