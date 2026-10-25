"""Local deterministic embeddings (feature hashing) so /v1/embeddings works
with zero external calls; when an upstream backend is configured the service
layer may proxy instead. Deterministic: same text → same vector, always."""
from __future__ import annotations

import hashlib
import math
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _hash_token(token: str) -> tuple[int, float]:
    digest = hashlib.sha256(token.encode()).digest()
    bucket = int.from_bytes(digest[:4], "little")
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    return bucket, sign


def embed_text(text: str, dim: int = 384) -> list[float]:
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return vec
    for tok in tokens:
        bucket, sign = _hash_token(tok)
        vec[bucket % dim] += sign
    # Add character-bigram features so near-miss words still land close.
    for tok in tokens:
        for i in range(len(tok) - 1):
            bucket, sign = _hash_token("~" + tok[i:i + 2])
            vec[bucket % dim] += 0.5 * sign
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec
