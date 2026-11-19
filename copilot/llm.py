"""LLM + embedding backends behind one small interface.

GeminiLLM  : real calls (google-genai), with on-disk cache, rate limiting and backoff.
HashEmbedder: offline bag-of-words embeddings, no API calls (EMBED_BACKEND=local).
FakeLLM    : canned responses for tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from dotenv import load_dotenv

CACHE_PATH = Path(".cache/llm_cache.sqlite")


class Cache:
    def __init__(self, path: Path = CACHE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def get(self, k: str) -> Optional[Any]:
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, k: str, v: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))
        self.db.commit()


class HashEmbedder:
    """Deterministic offline embeddings (hashed bag of words + bigrams). Weaker than Gemini
    embeddings but free, instant and good enough for short spec documents."""
    DIM = 1024

    def embed(self, texts: list[str], task: str = "doc") -> np.ndarray:
        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = re.findall(r"[a-z0-9]+", t.lower())
            feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
            for f in feats:
                h = int(hashlib.md5(f.encode()).hexdigest(), 16)
                out[i, h % self.DIM] += 1.0 if "_" not in f else 0.5
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


class GeminiLLM:
    def __init__(self):
        load_dotenv()
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY missing. Copy .env.example to .env and add your key.")
        from google import genai  # imported lazily so tests run without the SDK configured
        self.genai = genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=key)
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.embed_model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
        self.embed_backend = os.getenv("EMBED_BACKEND", "gemini")
        self.min_interval = float(os.getenv("MIN_INTERVAL_SEC", "4"))
        self.cache = Cache()
        self._last_call = 0.0
        self._local = HashEmbedder()

    # ---- helpers -------------------------------------------------------
    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _with_backoff(self, fn: Callable[[], Any], tries: int = 6) -> Any:
        for attempt in range(tries):
            try:
                self._throttle()
                return fn()
            except Exception as e:  # SDK raises various error classes; match on message
                msg = str(e)
                transient = any(s in msg for s in ("429", "503", "500", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE"))
                if not transient or attempt == tries - 1:
                    raise
                sleep = min(60, 2 ** attempt * 3) + random.random()
                print(f"  [llm] transient error, retrying in {sleep:.0f}s: {msg[:90]}")
                time.sleep(sleep)

    # ---- public API ----------------------------------------------------
    def generate_json(self, prompt: str, system: str, schema: Any) -> Any:
        k = Cache.key("gen", self.model, system, prompt, repr(schema))
        hit = self.cache.get(k)
        if hit is not None:
            return hit
        cfg = self.types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=schema,
        )
        resp = self._with_backoff(lambda: self.client.models.generate_content(
            model=self.model, contents=prompt, config=cfg))
        text = (resp.text or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text).strip()
        data = json.loads(text)
        self.cache.set(k, data)
        return data

    def embed(self, texts: list[str], task: str = "doc") -> np.ndarray:
        if self.embed_backend == "local":
            return self._local.embed(texts, task)
        task_type = "RETRIEVAL_DOCUMENT" if task == "doc" else "RETRIEVAL_QUERY"
        vecs: list[Optional[list[float]]] = [None] * len(texts)
        todo: list[int] = []
        for i, t in enumerate(texts):
            hit = self.cache.get(Cache.key("emb", self.embed_model, task_type, t))
            if hit is not None:
                vecs[i] = hit
            else:
                todo.append(i)
        for s in range(0, len(todo), 50):
            batch = todo[s:s + 50]
            cfg = self.types.EmbedContentConfig(task_type=task_type)
            resp = self._with_backoff(lambda: self.client.models.embed_content(
                model=self.embed_model, contents=[texts[i] for i in batch], config=cfg))
            for i, e in zip(batch, resp.embeddings):
                vecs[i] = list(e.values)
                self.cache.set(Cache.key("emb", self.embed_model, task_type, texts[i]), vecs[i])
        arr = np.array(vecs, dtype=np.float32)
        return arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-9)


class FakeLLM:
    """Test double. `responder(prompt, system)` returns the JSON the model would return."""
    def __init__(self, responder: Callable[[str, str], Any]):
        self.responder = responder
        self._emb = HashEmbedder()
        self.calls = 0

    def generate_json(self, prompt: str, system: str, schema: Any) -> Any:
        self.calls += 1
        return self.responder(prompt, system)

    def embed(self, texts: list[str], task: str = "doc") -> np.ndarray:
        return self._emb.embed(texts, task)
