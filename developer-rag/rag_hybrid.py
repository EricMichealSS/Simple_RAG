"""
Hybrid Retrieval Module
========================

Supports TWO modes:
1. UNFILTERED (where=None): Dense + BM25 → RRF Fusion → Cross-Encoder Rerank
2. FILTERED (where!=None): Dense (with filter) → Cross-Encoder Rerank

BM25 doesn't support metadata filters, so filtered queries use dense + rerank only.
"""

import chromadb
import numpy as np
from typing import List, Dict, Optional

from sentence_transformers import SentenceTransformer, CrossEncoder

from config import (
    CHROMA_PATH,
    COLLECTION_NAME,
    TOP_K,
    RELEVANCE_THRESHOLD,
)


# ============================================================
# Shared Resources (loaded once at module import)
# ============================================================

chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_collection(name=COLLECTION_NAME)

# Load all chunks for BM25 index (only for unfiltered queries)
_all_data = collection.get(include=["documents", "metadatas"])
_all_docs = _all_data["documents"]
_all_metas = _all_data["metadatas"]
_all_ids = _all_data["ids"]

# Dense embedding model
_embedding_model = SentenceTransformer("BAAI/bge-small-en-v1.5")

# Cross-encoder for reranking (loaded lazily)
_cross_encoder = None

def _get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _cross_encoder


# ============================================================
# BM25 Implementation (for unfiltered queries only)
# ============================================================

class BM25:
    """Simple BM25 implementation for keyword search."""
    
    def __init__(self, corpus, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_lens = [len(doc.split()) for doc in corpus]
        self.avgdl = np.mean(self.doc_lens) if self.doc_lens else 1
        self.ndocs = len(corpus)
        
        # Build term frequency index
        self.tf = []
        self.df = {}
        
        for doc in corpus:
            terms = doc.lower().split()
            tf_doc = {}
            for term in terms:
                tf_doc[term] = tf_doc.get(term, 0) + 1
            self.tf.append(tf_doc)
            for term in set(terms):
                self.df[term] = self.df.get(term, 0) + 1
        
        # IDF
        self.idf = {}
        for term, freq in self.df.items():
            self.idf[term] = np.log((self.ndocs - freq + 0.5) / (freq + 0.5) + 1)
    
    def score(self, query, doc_idx):
        """Compute BM25 score for query against document at doc_idx."""
        query_terms = query.lower().split()
        score = 0.0
        doc_len = self.doc_lens[doc_idx]
        tf_doc = self.tf[doc_idx]
        
        for term in query_terms:
            if term not in tf_doc:
                continue
            tf = tf_doc[term]
            idf = self.idf.get(term, 0)
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
            score += idf * numerator / denominator
        return score
    
    def top_k(self, query, k=60):
        """Return top-k document indices by BM25 score."""
        scores = [(i, self.score(query, i)) for i in range(self.ndocs)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in scores[:k]]


# Build BM25 index once (for unfiltered queries)
_bm25_index = BM25(_all_docs)


# ============================================================
# RRF Fusion
# ============================================================

def reciprocal_rank_fusion(rank_lists: List[List[str]], k: int = 60, top_k: int = 5) -> List[str]:
    """Fuse multiple ranked lists using Reciprocal Rank Fusion."""
    scores = {}
    for rank_list in rank_lists:
        for rank, chunk_id in enumerate(rank_list):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [chunk_id for chunk_id, _ in fused[:top_k]]


# ============================================================
# Cross-Encoder Reranking
# ============================================================

def rerank_with_cross_encoder(question: str, candidates: List[Dict], top_k: int = 5) -> List[Dict]:
    """
    Rerank candidates using cross-encoder.
    Returns top-k reranked results with updated scores.
    """
    if not candidates:
        return candidates
    
    cross_encoder = _get_cross_encoder()
    
    # Prepare pairs: [question, chunk_text]
    pairs = [(question, c["text"]) for c in candidates]
    
    # Get relevance scores
    scores = cross_encoder.predict(pairs)
    
    # Attach scores and sort
    for candidate, score in zip(candidates, scores):
        candidate["rerank_score"] = float(score)
    
    # Sort by rerank score descending
    candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
    
    return candidates[:top_k]


# ============================================================
# Result Formatting (shared)
# ============================================================

def _format_results(ids: List[str], documents: List[str], metadatas: List[Dict], distances: List[float]) -> List[Dict]:
    """Standardize result format."""
    retrieved = []
    for chunk_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
        retrieved.append({
            "chunk_id": chunk_id,
            "text": document,
            "source": metadata.get("source", ""),
            "source_file": metadata.get("source_file", ""),
            "page_id": metadata.get("page_id", ""),
            "page": metadata.get("page", 1),
            "chunk": metadata.get("chunk", 0),
            "sdk_version": metadata.get("sdk_version", ""),
            "page_type": metadata.get("page_type", ""),
            "anchor": metadata.get("anchor", ""),
            "section": metadata.get("section", ""),
            "distance": distance,
        })
    return retrieved


# ============================================================
# MAIN RETRIEVE FUNCTION — Single Entry Point
# ============================================================

def retrieve(
    question: str,
    top_k: int = TOP_K,
    collection_name: str = COLLECTION_NAME,
    where: Optional[Dict] = None,
    use_hybrid: bool = True,
    use_rerank: bool = True,
) -> List[Dict]:
    """
    Main retrieval function with automatic strategy selection.
    
    Args:
        question: User query
        top_k: Final number of results
        collection_name: ChromaDB collection
        where: Metadata filter (e.g., {"sdk_version": "v2"})
        use_hybrid: If True and unfiltered, use BM25+Dense+RRF
        use_rerank: If True, apply cross-encoder reranking
    
    Returns:
        List of result dicts with chunk_id, text, metadata, distance, rerank_score
    """
    
    # Get target collection
    target = (
        collection
        if collection_name == COLLECTION_NAME
        else chroma_client.get_collection(name=collection_name)
    )
    
    # ========================================================
    # PATH 1: FILTERED QUERY → Dense (with filter) + Rerank
    # ========================================================
    if where is not None:
        # Dense search WITH metadata filter
        query_embedding = _embedding_model.encode([question], normalize_embeddings=True)[0].tolist()
        
        results = target.query(
            query_embeddings=[query_embedding],
            n_results=min(25, top_k * 5),  # Get more for reranking
            where=where,
            include=["documents", "metadatas", "distances"]
        )
        
        candidates = _format_results(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        )
        
        # Rerank if enabled
        if use_rerank and candidates:
            candidates = rerank_with_cross_encoder(question, candidates, top_k)
        
        return candidates[:top_k]
    
    # ========================================================
    # PATH 2: UNFILTERED QUERY → Hybrid (Dense + BM25) + Rerank
    # ========================================================
    else:
        if not use_hybrid:
            # Dense only (fallback)
            query_embedding = _embedding_model.encode([question], normalize_embeddings=True)[0].tolist()
            results = target.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"]
            )
            candidates = _format_results(
                results["ids"][0], results["documents"][0],
                results["metadatas"][0], results["distances"][0]
            )
        else:
            # ---- Dense vector search (top 25 for fusion pool) ----
            query_embedding = _embedding_model.encode([question], normalize_embeddings=True)[0].tolist()
            dense_results = target.query(
                query_embeddings=[query_embedding],
                n_results=25,
                include=["documents", "metadatas", "distances"]
            )
            dense_ids = dense_results["ids"][0]
            dense_docs = dense_results["documents"][0]
            dense_metas = dense_results["metadatas"][0]
            dense_distances = dense_results["distances"][0]
            dense_candidates = _format_results(dense_ids, dense_docs, dense_metas, dense_distances)
            
            # ---- BM25 keyword search (top 60 for fusion pool) ----
            bm25_top_indices = _bm25_index.top_k(question, k=60)
            bm25_ids = [_all_ids[i] for i in bm25_top_indices]
            bm25_candidates = []
            for idx in bm25_top_indices:
                bm25_candidates.append({
                    "chunk_id": _all_ids[idx],
                    "text": _all_docs[idx],
                    "source_file": _all_metas[idx].get("source_file", ""),
                    "page_id": _all_metas[idx].get("page_id", ""),
                    "sdk_version": _all_metas[idx].get("sdk_version", ""),
                    "page_type": _all_metas[idx].get("page_type", ""),
                    "anchor": _all_metas[idx].get("anchor", ""),
                    "section": _all_metas[idx].get("section", ""),
                    "page": _all_metas[idx].get("page", 1),
                    "chunk": _all_metas[idx].get("chunk", 0),
                    "source": _all_metas[idx].get("source", ""),
                    "distance": 1.0,  # placeholder
                })
            
            # ---- RRF Fusion ----
            fused_ids = reciprocal_rank_fusion([dense_ids, bm25_ids], k=60, top_k=top_k * 3)
            
            # Build candidates from fused IDs
            id_to_candidate = {c["chunk_id"]: c for c in dense_candidates}
            id_to_candidate.update({c["chunk_id"]: c for c in bm25_candidates})
            
            candidates = [id_to_candidate[cid] for cid in fused_ids if cid in id_to_candidate]
        
        # Rerank if enabled
        if use_rerank and candidates:
            candidates = rerank_with_cross_encoder(question, candidates, top_k)
        
        return candidates[:top_k]


# ============================================================
# Convenience function for app.py
# ============================================================

def is_relevant(results: List[Dict]) -> bool:
    """Check if top result is relevant.
    Uses rerank_score if available (hybrid), otherwise falls back to distance.
    """
    if not results:
        return False
    
    # Prefer rerank_score (higher = more relevant)
    if "rerank_score" in results[0]:
        return results[0]["rerank_score"] > 0.0
    
    # Fallback to distance
    best_distance = results[0].get("distance", 1.0)
    return best_distance <= RELEVANCE_THRESHOLD