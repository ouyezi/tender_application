from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol, Sequence

import jieba
import numpy as np

from app.config import EMBEDDING_DIM, EMBEDDING_MODEL_PATH

_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
_DIGIT_NORMALIZE = str.maketrans(
    {
        "零": "0",
        "一": "1",
        "二": "2",
        "两": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
    }
)


class EmbeddingModel(Protocol):
    def embed(self, text: str) -> np.ndarray: ...

    def embed_many(self, texts: Sequence[str]) -> list[np.ndarray]: ...


def _normalize_text(text: str) -> str:
    return text.translate(_DIGIT_NORMALIZE)


def _cjk_ngrams(text: str, n: int) -> list[str]:
    grams: list[str] = []
    for run in _CJK_PATTERN.findall(text):
        if len(run) < n:
            grams.append(run)
            continue
        grams.extend(run[i : i + n] for i in range(len(run) - n + 1))
    return grams


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    normalized = _normalize_text(text)
    tokens = [token.strip() for token in jieba.cut(normalized) if token.strip()]
    if not tokens:
        tokens = _TOKEN_PATTERN.findall(normalized)
    features = list(tokens)
    features.extend(_cjk_ngrams(normalized, 2))
    features.extend(_cjk_ngrams(normalized, 3))
    return features


def _stable_bucket(token: str, dim: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        return vector
    return vector / norm


class HashEmbeddingModel:
    """Deterministic bag-of-token hash vectors for tests and fallback."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        for token in _tokenize(text):
            vector[_stable_bucket(token, self.dim)] += 1.0
        return _normalize(vector)

    def embed_many(self, texts: Sequence[str]) -> list[np.ndarray]:
        return [self.embed(text) for text in texts]


def get_embedding_model() -> EmbeddingModel:
    if EMBEDDING_MODEL_PATH:
        raise NotImplementedError(
            "Local embedding model loading is not implemented; "
            "configure HashEmbeddingModel fallback by leaving EMBEDDING_MODEL_PATH empty."
        )
    return HashEmbeddingModel(dim=EMBEDDING_DIM)


class VectorIndex:
    """Per-file cosine index persisted as ``{chunk_ids, matrix}`` npz."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        if self._path.suffix != ".npz":
            self._path = self._path.with_suffix(".npz")
        self._chunk_ids: list[str] = []
        self._matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        if self._path.is_file():
            self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        with np.load(self._path, allow_pickle=True) as data:
            chunk_ids = data["chunk_ids"]
            self._chunk_ids = [str(chunk_id) for chunk_id in chunk_ids.tolist()]
            self._matrix = np.asarray(data["matrix"], dtype=np.float32)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            self._path,
            chunk_ids=np.asarray(self._chunk_ids, dtype=object),
            matrix=self._matrix,
        )

    def upsert(self, pairs: Sequence[tuple[str, np.ndarray]]) -> None:
        if not pairs:
            self._chunk_ids = []
            self._matrix = np.zeros((0, 0), dtype=np.float32)
            self._save()
            return

        by_id: dict[str, np.ndarray] = {
            chunk_id: vector for chunk_id, vector in zip(self._chunk_ids, self._matrix)
        }
        for chunk_id, vector in pairs:
            by_id[chunk_id] = np.asarray(vector, dtype=np.float32)

        self._chunk_ids = list(by_id.keys())
        self._matrix = np.stack([by_id[chunk_id] for chunk_id in self._chunk_ids])
        self._save()

    def search(
        self,
        query_vec: np.ndarray,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        if self._matrix.size == 0 or top_k <= 0:
            return []

        query = np.asarray(query_vec, dtype=np.float32)
        scores = self._matrix @ query
        order = np.argsort(scores)[::-1]
        limit = min(top_k, len(order))
        return [
            (self._chunk_ids[idx], float(scores[idx]))
            for idx in order[:limit]
        ]
