"""
agents/rag/retriever.py
------------------------
Minimal semantic retriever for the orchestrator's RAG layer (P3).

Embeds the curated corpus (agents/rag/corpus.py) with BAAI/bge-small-en-v1.5
via sentence-transformers, and answers retrieve(query) with the top-k most
similar chunks by cosine similarity.

Originally targeted farbodtavakkoli/OTel-Embedding-300M (the GSMA/OTel
telecom embedding model) per HANDOVER-2026-09-12.md, but that HF repo has a
publishing bug: its modules.json declares "2_Dense"/"3_Dense" projection
modules that don't actually exist in the repo (confirmed across
sentence-transformers 3.3.1/5.2.0/6.0.1 — same missing-file/KeyError failure
in all three, so it's the model repo, not a library version mismatch).
BAAI/bge is one of the 6 embedding families GSMA's own telco-retrieve-chunks
index is built with (see HANDOVER), so this stays in the same
GSMA-recommended family while actually loading. Swap back to OTel-Embedding
if/when that repo gets fixed upstream.

Unlike agents/nwdaf/main.py's RandomForestRegressor (retrained from scratch
on every /analytics request, because its training data — core_kpis — grows
over time), the corpus here is static: it doesn't change between requests,
so the model and the corpus embeddings are computed ONCE at import time and
reused for every call. Recomputing them per-request would just waste CPU.

MODULE_NAME is deliberately imported eagerly (not lazily on first call) so
that any startup failure (e.g. the embedding model isn't baked into the
image — see agents/Dockerfile) surfaces immediately when the orchestrator
boots, rather than on the first operator intent.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer, util

from .corpus import CHUNKS

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# Below this cosine similarity, a chunk is considered irrelevant noise and
# dropped rather than forced into the context — an empty retrieval is better
# than an unrelated one.
MIN_SIMILARITY = 0.35

_model = SentenceTransformer(EMBEDDING_MODEL)
_corpus_texts = [c["text"] for c in CHUNKS]
_corpus_embeddings = _model.encode(_corpus_texts, normalize_embeddings=True, convert_to_tensor=True)


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    """Return up to top_k corpus chunks most relevant to `query`, best first.
    Returns [] if nothing clears MIN_SIMILARITY (e.g. an intent with no
    overlap with the curated corpus) rather than forcing weak matches in."""
    query_embedding = _model.encode(query, normalize_embeddings=True, convert_to_tensor=True)
    scores = util.cos_sim(query_embedding, _corpus_embeddings)[0]

    ranked = sorted(zip(CHUNKS, scores.tolist()), key=lambda pair: pair[1], reverse=True)
    return [chunk for chunk, score in ranked[:top_k] if score >= MIN_SIMILARITY]


def format_context(chunks: list[dict]) -> str:
    """Render retrieved chunks as a context block to prepend to the operator
    intent. Returns "" when there's nothing to add (caller should skip
    prepending anything in that case)."""
    if not chunks:
        return ""
    lines = ["## Contexto recuperado (RAG)"]
    lines += [f"[{c['source']}] {c['text']}" for c in chunks]
    return "\n".join(lines)
