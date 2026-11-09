"""Bounded, content-keyed BM25 statistics for repeated library searches."""

from collections import Counter, OrderedDict
from dataclasses import dataclass
import math
import threading
from types import MappingProxyType
from typing import Callable, Mapping


@dataclass(frozen=True)
class DocumentTerms:
    frequencies: Mapping[str, int]
    length: int


@dataclass(frozen=True)
class CorpusTerms:
    documents: tuple[DocumentTerms, ...]
    frequencies: Mapping[str, int]
    average_length: float


class LexicalIndex:
    """Reuse tokenization and scoped statistics without caching source metadata.

    Exact ordered (text, language) keys detect edits, deletion, and rechunking,
    even when callers mutate the library in place. Tokenizer versions partition
    both caches. Snapshot construction is serialized; scoring uses immutable
    statistics outside the lock. The current candidate rows supply citations.
    """

    def __init__(
        self, tokenize: Callable, *, max_documents: int = 8192, max_scopes: int = 8
    ):
        if max_documents < 1 or max_scopes < 1:
            raise ValueError("cache limits must be positive")
        self._tokenize = tokenize
        self._max_documents = max_documents
        self._max_scopes = max_scopes
        self._documents = OrderedDict()
        self._scopes = OrderedDict()
        self._lock = threading.Lock()

    def clear(self):
        """Drop cached statistics, for cold-run measurements or explicit reset."""
        with self._lock:
            self._documents.clear()
            self._scopes.clear()

    def _prepare(self, candidates: list[dict], token_version: str) -> CorpusTerms:
        signature = tuple((row["text"], row.get("language")) for row in candidates)
        key = (token_version, signature)
        with self._lock:
            cached = self._scopes.get(key)
            if cached is not None:
                self._scopes.move_to_end(key)
                return cached

            documents = []
            frequencies = Counter()
            for text, language in signature:
                document_key = (token_version, text, language)
                document = self._documents.get(document_key)
                if document is None:
                    tokens = self._tokenize(text, language=language)
                    document = DocumentTerms(
                        MappingProxyType(dict(Counter(tokens))), len(tokens)
                    )
                    self._documents[document_key] = document
                    if len(self._documents) > self._max_documents:
                        self._documents.popitem(last=False)
                else:
                    self._documents.move_to_end(document_key)
                documents.append(document)
                frequencies.update(document.frequencies.keys())

            corpus = CorpusTerms(
                tuple(documents),
                MappingProxyType(dict(frequencies)),
                sum(doc.length for doc in documents) / len(documents)
                if documents
                else 1.0,
            )
            self._scopes[key] = corpus
            if len(self._scopes) > self._max_scopes:
                self._scopes.popitem(last=False)
            return corpus

    def search(
        self,
        candidates: list[dict],
        query_tokens: list[str],
        *,
        k: int,
        token_version: str,
    ) -> list[dict]:
        """Score current rows using the original scoped BM25 formula and tie order."""
        if not candidates or not query_tokens or k <= 0:
            return []
        corpus = self._prepare(candidates, token_version)
        query_tf = Counter(query_tokens)
        total_docs = len(candidates)
        scored = []
        for candidate, document in zip(candidates, corpus.documents):
            if not document.length:
                continue
            score = 0.0
            for term, weight in query_tf.items():
                frequency = document.frequencies.get(term, 0)
                if not frequency:
                    continue
                document_frequency = corpus.frequencies.get(term, 0)
                idf = math.log(
                    1.0
                    + (total_docs - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                denominator = frequency + 1.5 * (
                    1.0
                    - 0.75
                    + 0.75
                    * (max(1, document.length) / max(corpus.average_length, 1e-9))
                )
                score += (
                    idf
                    * ((frequency * (1.5 + 1.0)) / denominator)
                    * (1.0 + 0.2 * (weight - 1))
                )
            if score > 0.0:
                scored.append(
                    dict(candidate, lexical_score=float(score), score=float(score))
                )

        scored.sort(key=lambda row: row["lexical_score"], reverse=True)
        for rank, row in enumerate(scored, start=1):
            row["rank"] = rank
        return scored[:k]
