"""Version-aware hybrid policy retrieval with citations (S3).

Deterministic ingestion + chunking + embeddings, PostgreSQL full-text and pgvector
semantic search fused by Reciprocal Rank Fusion, with strict source-trust boundaries.
Retrieval returns evidence and citations; it never generates a customer answer.
"""
