# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Work item labels from safe filename slugs."""

from pathlib import Path

from argus.conventions import phase_file_parts

_ACRONYMS = {
    "am": "AM",
    "api": "API",
    "cli": "CLI",
    "db": "DB",
    "ema": "EMA",
    "http": "HTTP",
    "https": "HTTPS",
    "json": "JSON",
    "mcp": "MCP",
    "ops": "OPS",
    "acl": "ACL",
    "ann": "ANN",
    "ast": "AST",
    "crud": "CRUD",
    "dag": "DAG",
    "etl": "ETL",
    "fts": "FTS",
    "hnsw": "HNSW",
    "idf": "IDF",
    "ir": "IR",
    "jwt": "JWT",
    "lww": "LWW",
    "nlp": "NLP",
    "rag": "RAG",
    "rbac": "RBAC",
    "rest": "REST",
    "rpc": "RPC",
    "sse": "SSE",
    "ttl": "TTL",
    "uuid": "UUID",
}


def parse_phase_file(phase_file: str | Path) -> tuple[str, str, str]:
    """Return the phase number, title slug, and normalized file name."""
    name = Path(phase_file).name
    parts = phase_file_parts(name)
    if parts is None:
        raise ValueError(f"not a phase file: {name}")
    number, title = parts
    return f"{number:06d}", title, name


def display_title(title: str) -> str:
    """Turn a phase slug into readable words while preserving common acronyms."""
    return " ".join(
        _ACRONYMS.get(word.lower(), word.capitalize())
        for word in title.split("-")
        if word
    )


def phase_label(phase_file: str | Path, *, kind: str = "phase") -> str:
    """Return the user-facing phase label."""
    number, title, _ = parse_phase_file(phase_file)
    return f"{kind.capitalize()} {number} ({display_title(title)})"
