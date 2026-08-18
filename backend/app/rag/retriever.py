"""Retrieval-Augmented Grounding for institutional policy evidence.

A dependency-free local retriever over the fabricated policy corpus in
``documents/``. It chunks each policy by section, builds TF-IDF vectors, and
ranks sections by cosine similarity to the user's request. This gives the
Policy Guardian a *real* retrieved policy with an id, version, source section
and a calibrated confidence score instead of a hardcoded lookup.

The implementation is pure Python so the demo runs offline with no model
downloads or API keys. It is structured so the ranking backend could later be
swapped for sentence-transformer embeddings or a Chroma/FAISS store without
changing the Policy Guardian.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DOCUMENTS_DIR = Path(__file__).parent / "documents"

# Canonical policy per governed intent. Used as a grounded fallback when the
# free-text query does not lexically overlap any section (e.g. a terse request).
CANONICAL_POLICY = {
    "MAINTENANCE": "FAC-MNT-001",
    "LAB_BOOKING": "LIB-BOOK-002",
    "CERTIFICATE": "ACA-CERT-003",
    "GRIEVANCE": "GRV-ESCAL-004",
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are",
    "be", "by", "with", "that", "this", "it", "as", "at", "from", "any", "may",
    "must", "not", "no", "i", "my", "me", "we", "you", "your", "please", "need",
    "want", "can", "will", "would", "should", "do", "does", "have", "has",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(token: str) -> str:
    """Very small plural normaliser so 'document'/'documents' and
    'certificate'/'certificates' match. Not a full stemmer — just enough to make
    lexical retrieval robust to singular/plural phrasing."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es") and not token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS]


@dataclass(frozen=True)
class PolicyChunk:
    policy_id: str
    name: str
    version: str
    effective_date: str
    intents: tuple[str, ...]
    section: str
    text: str
    tokens: tuple[str, ...]
    answer: str = ""


@dataclass(frozen=True)
class RetrievalMatch:
    policy_id: str
    name: str
    version: str
    effective_date: str
    section: str
    snippet: str
    score: float
    confidence: float
    answer: str = ""
    matched_terms: int = 0


def _parse_document(path: Path) -> list[PolicyChunk]:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    meta: dict[str, str] = {}
    cursor = 0
    for cursor, line in enumerate(lines):
        if line.strip() == "" or line.startswith("## "):
            break
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()

    intents = tuple(i.strip() for i in meta.get("intents", "GENERAL").split(",") if i.strip())
    keywords = meta.get("keywords", "")
    answer = meta.get("answer", "")

    chunks: list[PolicyChunk] = []
    section_title = "Overview"
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        body = " ".join(buffer).strip()
        if not body:
            return
        # Document name, section title and keyword line all enrich each chunk's
        # searchable text so a query naming a specific document (e.g. "bonafide")
        # ranks that document's sections above a generic one that merely mentions it.
        searchable = f"{meta.get('name', '')} {section_title} {keywords} {body}"
        chunks.append(PolicyChunk(
            policy_id=meta.get("id", path.stem),
            name=meta.get("name", path.stem),
            version=meta.get("version", ""),
            effective_date=meta.get("effective_date", ""),
            intents=intents,
            section=section_title,
            text=body,
            tokens=tuple(_tokenize(searchable)),
            answer=answer,
        ))

    for line in lines[cursor:]:
        if line.startswith("## "):
            flush()
            section_title = line[3:].strip()
            buffer = []
        else:
            buffer.append(line)
    flush()
    return chunks


@dataclass(frozen=True)
class _Corpus:
    chunks: tuple[PolicyChunk, ...]
    idf: dict[str, float]
    vectors: tuple[dict[str, float], ...]


def _build_vector(tokens: tuple[str, ...], idf: dict[str, float]) -> dict[str, float]:
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    vector = {term: (freq / total) * idf.get(term, 0.0) for term, freq in counts.items()}
    norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    if norm == 0:
        return {}
    return {term: weight / norm for term, weight in vector.items()}


@lru_cache(maxsize=1)
def _corpus() -> _Corpus:
    chunks: list[PolicyChunk] = []
    for path in sorted(DOCUMENTS_DIR.glob("*.md")):
        chunks.extend(_parse_document(path))

    document_frequency: Counter[str] = Counter()
    for chunk in chunks:
        document_frequency.update(set(chunk.tokens))
    total_docs = max(len(chunks), 1)
    idf = {
        term: math.log((total_docs + 1) / (freq + 1)) + 1.0
        for term, freq in document_frequency.items()
    }
    vectors = tuple(_build_vector(chunk.tokens, idf) for chunk in chunks)
    return _Corpus(chunks=tuple(chunks), idf=idf, vectors=vectors)


def reload_corpus() -> None:
    """Make newly published administrator policies searchable immediately."""
    _corpus.cache_clear()


def _cosine(query: dict[str, float], doc: dict[str, float]) -> float:
    if not query or not doc:
        return 0.0
    # Iterate over the smaller vector for efficiency.
    small, large = (query, doc) if len(query) <= len(doc) else (doc, query)
    return sum(weight * large.get(term, 0.0) for term, weight in small.items())


def _confidence(score: float) -> float:
    """Calibrate a raw cosine score (0..1) into a display confidence (0..0.99)."""
    if score <= 0:
        return 0.0
    return round(min(0.99, 0.55 + 0.5 * (score / (score + 0.12))), 2)


def search(query: str, k: int = 3, intent: str | None = None) -> list[RetrievalMatch]:
    """Return the top-k most relevant policy sections for a free-text query."""
    corpus = _corpus()
    query_terms = set(_tokenize(query))
    query_vector = _build_vector(tuple(query_terms), corpus.idf)
    scored: list[tuple[float, PolicyChunk]] = []
    for chunk, vector in zip(corpus.chunks, corpus.vectors):
        if intent and intent not in chunk.intents:
            continue
        scored.append((_cosine(query_vector, vector), chunk))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        RetrievalMatch(
            policy_id=chunk.policy_id,
            name=chunk.name,
            version=chunk.version,
            effective_date=chunk.effective_date,
            section=chunk.section,
            snippet=chunk.text,
            score=round(score, 4),
            confidence=_confidence(score),
            answer=chunk.answer,
            matched_terms=len(query_terms & set(chunk.tokens)),
        )
        for score, chunk in scored[:k]
    ]


def is_grounded(match: RetrievalMatch) -> bool:
    """Whether a match is relevant enough to answer from confidently.

    A raw similarity floor alone lets weak partial matches through (e.g. an
    off-domain question sharing one generic word). We additionally require real
    lexical overlap: at least two distinct query terms hit the policy, OR a
    single strong match. This is what stops the agent answering an unrelated
    question with a tangentially-similar policy."""
    if match.score <= 0.08:
        return False
    return match.matched_terms >= 2 or match.score >= 0.30


def get_policy(policy_id: str) -> dict | None:
    """Return a whole policy grouped by section, for composing a detailed answer.

    Unlike ``search`` (which returns the single best section), this returns every
    section of one document in order, plus its summary ``answer`` — used when a
    user explicitly asks for the full policy details."""
    name = version = answer = None
    sections: list[tuple[str, str]] = []
    seen: set[str] = set()
    for chunk in _corpus().chunks:
        if chunk.policy_id != policy_id:
            continue
        name, version, answer = chunk.name, chunk.version, chunk.answer
        if chunk.section not in seen:
            seen.add(chunk.section)
            sections.append((chunk.section, chunk.text))
    if name is None:
        return None
    return {"id": policy_id, "name": name, "version": version, "answer": answer or "", "sections": sections}


def list_policies() -> list[dict]:
    """Return corpus metadata once per policy, including admin-published files."""
    rows: dict[str, dict] = {}
    for chunk in _corpus().chunks:
        rows.setdefault(chunk.policy_id, {"id": chunk.policy_id, "name": chunk.name,
                                         "version": chunk.version, "effective_date": chunk.effective_date})
    return list(rows.values())


def retrieve_policy(intent: str, text: str) -> RetrievalMatch | None:
    """Retrieve the governing policy for an intent, grounded in the corpus.

    Returns the best-matching section among policies tagged with ``intent``.
    If the query has no lexical overlap, falls back to the canonical policy's
    overview section with a low confidence so callers can flag uncertainty.
    """
    matches = search(text, k=1, intent=intent)
    if matches and matches[0].score > 0:
        return matches[0]

    canonical_id = CANONICAL_POLICY.get(intent)
    if not canonical_id:
        return None
    for chunk in _corpus().chunks:
        if chunk.policy_id == canonical_id:
            return RetrievalMatch(
                policy_id=chunk.policy_id,
                name=chunk.name,
                version=chunk.version,
                effective_date=chunk.effective_date,
                section=chunk.section,
                snippet=chunk.text,
                score=0.0,
                confidence=0.6,
                answer=chunk.answer,
            )
    return None
