#!/usr/bin/env python3
"""
kb_search.py — Semantic KB retrieval for AnalystOS.

Builds and queries a two-tier search index over the db-knowledge/ directory:
  1. FTS5 keyword index (SQLite) — fast, deterministic, optionally committable to git
  2. Embedding vector index (SQLite) — optional, provider-agnostic, gitignored

Uses only stdlib (sqlite3, json, pathlib, re, urllib.request) plus optional
sentence-transformers or openai for the vector path. Gracefully degrades to
FTS-only when embedding deps are unavailable.

Usage:
    python .claude/scripts/kb_search.py --build [--kb db-knowledge/] [--incremental]
    python .claude/scripts/kb_search.py --query "customer LTV" [--top-k 5] [--format json|text]
    python .claude/scripts/kb_search.py --query "timestamp gotchas" --type table
    python .claude/scripts/kb_search.py --session-prime [--top-k 15] [--format context]
    python .claude/scripts/kb_search.py --status [--format json]
    python .claude/scripts/kb_search.py --config .claude/db-connections/active.yaml --build
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# ── Constants ──────────────────────────────────────────────────────────────────

CONNECTIONS_PATH = Path(".claude/db-connections/active.yaml")
DEFAULT_KB_PATH = Path("db-knowledge/")
DEFAULT_FTS_INDEX_PATH = ".claude/kb-index/kb_fts.db"
DEFAULT_VECTOR_INDEX_PATH = ".claude/kb-index/kb_vectors.db"
MAX_EXCERPT_CHARS = 200
RRF_K = 60  # Reciprocal Rank Fusion constant


# ── Config loading ─────────────────────────────────────────────────────────────

DEFAULT_RAG_CONFIG: dict = {
    "enabled": True,
    "fts": {
        "index_path": DEFAULT_FTS_INDEX_PATH,
        "commit_to_git": False,
    },
    "embeddings": {
        "enabled": False,
        "provider": "none",
        "model": "text-embedding-3-small",
        "api_key_env": "OPENAI_API_KEY",
        "local_model": "all-MiniLM-L6-v2",
        "index_path": DEFAULT_VECTOR_INDEX_PATH,
    },
    "chunking": {
        "strategy": "section",
        "max_chunk_tokens": 512,
    },
    "search": {
        "default_top_k": 5,
        "fts_weight": 0.6,
        "embedding_weight": 0.4,
    },
    "session": {
        "prime_on_startup": True,
        "prime_top_k": 15,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_rag_config(config_path: Path = CONNECTIONS_PATH) -> dict:
    """
    Load the top-level `rag:` stanza from active.yaml, merged with defaults.
    Returns DEFAULT_RAG_CONFIG if file absent or rag block is missing.
    Falls back to regex parsing if PyYAML is not installed.
    """
    if not config_path.exists():
        return dict(DEFAULT_RAG_CONFIG)

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return dict(DEFAULT_RAG_CONFIG)

    # Try PyYAML first
    try:
        import yaml  # type: ignore
        full_config = yaml.safe_load(content) or {}
        rag_block = full_config.get("rag") or {}
        if rag_block is False or rag_block is None:
            # rag: ~ or rag: false means disabled
            cfg = dict(DEFAULT_RAG_CONFIG)
            cfg["enabled"] = False
            return cfg
        return _deep_merge(DEFAULT_RAG_CONFIG, rag_block)
    except ImportError:
        pass
    except Exception:
        pass

    # Regex fallback — limited but handles the common cases
    if re.search(r"^\s*enabled\s*:\s*false\b", content, re.MULTILINE | re.IGNORECASE):
        cfg = dict(DEFAULT_RAG_CONFIG)
        cfg["enabled"] = False
        return cfg

    return dict(DEFAULT_RAG_CONFIG)


# ── Path metadata extraction ───────────────────────────────────────────────────

def extract_path_metadata(path: Path, kb_root: Path) -> dict:
    """
    Parse a file path into structured chunk metadata.

    Supported path structures (relative to kb_root):
      _gotchas.md                                 → cross-connection gotchas
      _open-questions.md                          → open questions
      {conn}/_gotchas.md                          → connection-level gotchas
      {conn}/{schema}/_schema-overview.md         → schema overview
      {conn}/{schema}/_gotchas.md                 → schema-level gotchas
      {conn}/{schema}/{table}.md                  → table doc
      {conn}/{schema}/_queries/{name}.sql         → saved query
    """
    try:
        rel = path.relative_to(kb_root)
    except ValueError:
        rel = path

    parts = rel.parts
    name = path.name
    stem = path.stem

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    meta: dict = {
        "path": str(rel).replace("\\", "/"),
        "connection": None,
        "schema": None,
        "table": None,
        "doc_type": "unknown",
        "last_modified": mtime,
    }

    if len(parts) == 1:
        if name == "_gotchas.md":
            meta["doc_type"] = "gotchas"
        elif name == "_open-questions.md":
            meta["doc_type"] = "open_questions"
        return meta

    if len(parts) == 2:
        meta["connection"] = parts[0]
        if name == "_gotchas.md":
            meta["doc_type"] = "gotchas"
        elif name == "_schema-overview.md":
            meta["doc_type"] = "schema_overview"
        return meta

    if len(parts) == 3:
        meta["connection"] = parts[0]
        meta["schema"] = parts[1]
        if name == "_gotchas.md":
            meta["doc_type"] = "gotchas"
        elif name == "_schema-overview.md":
            meta["doc_type"] = "schema_overview"
        elif not name.startswith("_") and name.endswith(".md"):
            meta["doc_type"] = "table"
            meta["table"] = stem
        return meta

    if len(parts) == 4:
        meta["connection"] = parts[0]
        meta["schema"] = parts[1]
        if parts[2] == "_queries" and name.endswith(".sql"):
            meta["doc_type"] = "query"
            meta["table"] = stem
        return meta

    return meta


# ── Section splitting ──────────────────────────────────────────────────────────

def split_by_heading(content: str, level: int = 2) -> list:
    """
    Split markdown content by headings at exactly `level` hashes.
    Returns list of (heading_text, section_body) tuples.
    Preamble before the first heading is returned with heading='' if non-empty.
    """
    pattern = re.compile(r"^#{" + str(level) + r"}\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(content))

    if not matches:
        stripped = content.strip()
        return [("", stripped)] if stripped else []

    sections = []
    preamble = content[: matches[0].start()].strip()
    if preamble:
        sections.append(("", preamble))

    for i, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        sections.append((heading, body))

    return sections


def make_excerpt(text: str, max_chars: int = MAX_EXCERPT_CHARS) -> str:
    """Return a clean, truncated excerpt stripped of markdown noise."""
    # Strip code fences, inline code, bold/italic markers
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", lambda m: m.group(0)[1:-1], text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


# ── Chunking by file type ──────────────────────────────────────────────────────

def chunk_table_file(content: str, meta: dict) -> list:
    """One chunk per ## section in a {table}.md file."""
    table_name = (meta.get("table") or "unknown").upper()
    chunks = []
    for heading, body in split_by_heading(content, level=2):
        if not body and not heading:
            continue
        title = f"{table_name} — {heading}" if heading else table_name
        chunks.append({**meta, "section": heading or "Header", "title": title, "body": body})
    return chunks


def chunk_gotchas_file(content: str, meta: dict) -> list:
    """
    One chunk per ### ⚠️ entry in a _gotchas.md file.
    Falls back to whole-file chunking if no ⚠️ entries found.
    """
    scope = meta.get("schema") or meta.get("connection") or "project"
    pattern = re.compile(r"^###\s+⚠️\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(content))

    chunks = []
    if matches:
        for i, match in enumerate(matches):
            heading = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[start:end].strip()
            chunks.append({
                **meta,
                "section": f"⚠️ {heading}",
                "title": f"⚠️ {heading} [{scope}]",
                "body": body,
            })
    else:
        for heading, body in split_by_heading(content, level=2):
            if not body:
                continue
            title = f"Gotchas: {heading or scope}"
            chunks.append({**meta, "section": heading or "Gotchas", "title": title, "body": body})
        if not chunks and content.strip():
            chunks.append({**meta, "section": "Gotchas", "title": f"Gotchas [{scope}]", "body": content.strip()})
    return chunks


def chunk_schema_overview(content: str, meta: dict) -> list:
    """
    Chunk a _schema-overview.md:
    - Entity/relationship sections: sub-chunk by ### headings
    - Gotchas section (## 6 or containing 'gotcha'): one chunk
    - Other ## sections: one chunk each
    """
    connection = meta.get("connection") or ""
    schema = (meta.get("schema") or "schema").upper()
    prefix = f"{connection}/{schema}" if connection else schema

    chunks = []
    for heading, body in split_by_heading(content, level=2):
        if not body:
            continue
        normalized = heading.lower()
        title = f"{prefix} — {heading}" if heading else prefix

        # Sub-chunk entity/relationship sections by ### headings
        if any(kw in normalized for kw in ("entit", "relationship", "object", "cluster")):
            sub = split_by_heading(body, level=3)
            if len(sub) > 1:
                for sub_heading, sub_body in sub:
                    if not sub_body:
                        continue
                    sub_title = f"{title} / {sub_heading}" if sub_heading else title
                    section_label = f"{heading} / {sub_heading}" if sub_heading else heading
                    chunks.append({**meta, "section": section_label, "title": sub_title, "body": sub_body})
                continue

        chunks.append({**meta, "section": heading or "Header", "title": title, "body": body})
    return chunks


def chunk_sql_file(content: str, meta: dict) -> list:
    """
    Two chunks per .sql file: comment header block + SQL body.
    """
    lines = content.splitlines()
    header_lines, sql_lines = [], []
    in_header = True
    for line in lines:
        if in_header and (line.startswith("--") or not line.strip()):
            header_lines.append(line)
        else:
            in_header = False
            sql_lines.append(line)

    query_name = meta.get("table") or "query"
    connection = meta.get("connection") or ""
    schema = meta.get("schema") or ""
    scope = "/".join(filter(None, [connection, schema]))

    chunks = []
    if header_lines:
        header_text = "\n".join(header_lines).strip()
        name_match = re.search(r"--\s*Name\s*:\s*(.+)", header_text)
        display_name = name_match.group(1).strip() if name_match else query_name
        chunks.append({
            **meta,
            "section": "Query Header",
            "title": f"Query: {display_name} [{scope}]",
            "body": header_text,
        })

    sql_text = "\n".join(sql_lines).strip()
    if sql_text:
        chunks.append({
            **meta,
            "section": "SQL Body",
            "title": f"SQL: {query_name} [{scope}]",
            "body": sql_text,
        })
    return chunks


def chunk_file(path: Path, kb_root: Path) -> list:
    """Dispatch to the correct chunker based on file type and name."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not content.strip():
        return []

    meta = extract_path_metadata(path, kb_root)
    doc_type = meta["doc_type"]

    if path.suffix == ".sql":
        return chunk_sql_file(content, meta)
    if doc_type == "gotchas":
        return chunk_gotchas_file(content, meta)
    if doc_type == "schema_overview":
        return chunk_schema_overview(content, meta)
    if doc_type == "table":
        return chunk_table_file(content, meta)
    if doc_type == "open_questions":
        return [{**meta, "section": "Open Questions", "title": "Open Questions", "body": content.strip()}]
    return []


def chunk_kb(kb_root: Path) -> list:
    """Walk the KB directory and produce all chunks."""
    chunks = []
    for path in sorted(kb_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "README.md":
            continue
        if path.suffix not in (".md", ".sql"):
            continue
        if "_obsidian-vault" in str(path):
            continue
        chunks.extend(chunk_file(path, kb_root))
    return chunks


def _unique_file_count(chunks: list) -> int:
    return len({c.get("path") for c in chunks if c.get("path")})


# ── FTS5 index ─────────────────────────────────────────────────────────────────

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks USING fts5(
    title,
    body,
    path UNINDEXED,
    connection UNINDEXED,
    schema_name UNINDEXED,
    table_name UNINDEXED,
    section UNINDEXED,
    doc_type UNINDEXED,
    last_modified UNINDEXED,
    tokenize = 'porter ascii'
);
CREATE TABLE IF NOT EXISTS kb_files (
    path TEXT PRIMARY KEY,
    mtime REAL,
    chunk_count INTEGER DEFAULT 0,
    indexed_at REAL
);
CREATE TABLE IF NOT EXISTS kb_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def open_fts_db(index_path: str) -> sqlite3.Connection:
    Path(index_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    for stmt in _FTS_DDL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
    return conn


def _insert_chunks(chunks: list, conn: sqlite3.Connection, skip_file_table: bool = False) -> None:
    rows = []
    file_counts: dict = {}
    for c in chunks:
        rows.append((
            c.get("title", ""),
            c.get("body", ""),
            c.get("path", ""),
            c.get("connection") or "",
            c.get("schema") or "",
            c.get("table") or "",
            c.get("section") or "",
            c.get("doc_type") or "",
            str(c.get("last_modified") or ""),
        ))
        p = c.get("path", "")
        file_counts[p] = (c.get("last_modified") or 0, file_counts.get(p, (0, 0))[1] + 1)

    conn.executemany(
        "INSERT INTO kb_chunks(title,body,path,connection,schema_name,table_name,section,doc_type,last_modified) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        rows,
    )
    if not skip_file_table:
        now = time.time()
        conn.executemany(
            "INSERT OR REPLACE INTO kb_files(path,mtime,chunk_count,indexed_at) VALUES(?,?,?,?)",
            [(p, mtime, count, now) for p, (mtime, count) in file_counts.items()],
        )


def fts_build_full(chunks: list, conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM kb_chunks")
    conn.execute("DELETE FROM kb_files")
    _insert_chunks(chunks, conn)
    conn.execute("INSERT OR REPLACE INTO kb_meta(key,value) VALUES('built_at',?)", (str(time.time()),))
    conn.commit()


def fts_build_incremental(chunks: list, conn: sqlite3.Connection) -> tuple:
    """Re-index only changed files. Returns (files_updated, chunks_added)."""
    by_path: dict = {}
    for c in chunks:
        by_path.setdefault(c.get("path", ""), []).append(c)

    files_updated = 0
    chunks_added = 0
    now = time.time()

    for path_str, file_chunks in by_path.items():
        current_mtime = file_chunks[0].get("last_modified", 0)
        row = conn.execute("SELECT mtime FROM kb_files WHERE path=?", (path_str,)).fetchone()
        if row and abs(row[0] - current_mtime) < 0.001:
            continue

        conn.execute("DELETE FROM kb_chunks WHERE path=?", (path_str,))
        _insert_chunks(file_chunks, conn, skip_file_table=True)
        conn.execute(
            "INSERT OR REPLACE INTO kb_files(path,mtime,chunk_count,indexed_at) VALUES(?,?,?,?)",
            (path_str, current_mtime, len(file_chunks), now),
        )
        files_updated += 1
        chunks_added += len(file_chunks)

    conn.execute("INSERT OR REPLACE INTO kb_meta(key,value) VALUES('built_at',?)", (str(now),))
    conn.commit()
    return files_updated, chunks_added


def fts_search(query: str, conn: sqlite3.Connection, top_k: int = 5, doc_type_filter: str = None) -> list:
    """BM25 FTS search. Returns results sorted best-first."""
    try:
        where = "WHERE kb_chunks MATCH ?"
        params: list = [query]
        if doc_type_filter:
            where += " AND doc_type=?"
            params.append(doc_type_filter)

        rows = conn.execute(
            f"SELECT title,body,path,connection,schema_name,table_name,section,doc_type,"
            f"last_modified,bm25(kb_chunks) AS score "
            f"FROM kb_chunks {where} ORDER BY score LIMIT ?",
            params + [top_k],
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    results = []
    for i, row in enumerate(rows):
        title, body, path, conn_name, schema, table, section, doc_type, last_modified, score = row
        results.append({
            "rank": i + 1,
            "score": round(abs(float(score)), 4),
            "path": path,
            "connection": conn_name or None,
            "schema": schema or None,
            "table": table or None,
            "section": section,
            "doc_type": doc_type,
            "title": title,
            "excerpt": make_excerpt(body),
            "last_modified": last_modified,
        })
    return results


def fts_get_built_at(conn: sqlite3.Connection):
    row = conn.execute("SELECT value FROM kb_meta WHERE key='built_at'").fetchone()
    if row:
        try:
            return float(row[0])
        except (ValueError, TypeError):
            pass
    return None


# ── Embedding support ──────────────────────────────────────────────────────────

def _openai_embedder(api_key: str, model: str):
    def embed(texts: list) -> list:
        url = "https://api.openai.com/v1/embeddings"
        payload = json.dumps({"input": texts, "model": model}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [item["embedding"] for item in data["data"]]
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
            print(f"OpenAI embedding error: {e}", file=sys.stderr)
            return [[] for _ in texts]
    return embed


def _local_embedder(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        model = SentenceTransformer(model_name)
        def embed(texts: list) -> list:
            return model.encode(texts).tolist()
        return embed
    except ImportError:
        return None


def get_embedder(config: dict):
    """
    Return an embedding callable (texts → list[list[float]]) or None.
    Auto-degrades: local → openai → None (FTS-only).
    """
    emb = config.get("embeddings", {})
    if not emb.get("enabled", False):
        return None

    provider = emb.get("provider", "none")

    if provider == "local":
        embedder = _local_embedder(emb.get("local_model", "all-MiniLM-L6-v2"))
        if embedder:
            return embedder
        print("sentence-transformers not installed — falling back to openai", file=sys.stderr)
        provider = "openai"

    if provider == "openai":
        env_name = emb.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(env_name)
        if not api_key:
            print(f"{env_name} not set — embeddings disabled (FTS-only)", file=sys.stderr)
            return None
        return _openai_embedder(api_key, emb.get("model", "text-embedding-3-small"))

    if provider == "anthropic":
        print("Anthropic/Voyage embeddings not yet supported — using FTS-only", file=sys.stderr)
        return None

    return None  # provider == "none" or unknown


# ── Vector index ───────────────────────────────────────────────────────────────

_VEC_DDL = """
CREATE TABLE IF NOT EXISTS kb_vectors (
    chunk_id TEXT PRIMARY KEY,
    path TEXT,
    connection TEXT,
    schema_name TEXT,
    table_name TEXT,
    section TEXT,
    doc_type TEXT,
    title TEXT,
    body TEXT,
    embedding TEXT,
    last_modified REAL
);
CREATE TABLE IF NOT EXISTS vec_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def open_vector_db(index_path: str) -> sqlite3.Connection:
    Path(index_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in _VEC_DDL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
    return conn


def _cosine(a: list, b: list) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / mag if mag else 0.0


def vector_build(chunks: list, embedder, conn: sqlite3.Connection, batch_size: int = 20) -> int:
    conn.execute("DELETE FROM kb_vectors")
    total = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i: i + batch_size]
        texts = [f"{c.get('title','')} {c.get('body','')}" for c in batch]
        vectors = embedder(texts)
        rows = []
        for j, (chunk, vec) in enumerate(zip(batch, vectors)):
            cid = f"{chunk.get('path','')}::{chunk.get('section','')}::{i+j}"
            rows.append((
                cid,
                chunk.get("path", ""),
                chunk.get("connection") or "",
                chunk.get("schema") or "",
                chunk.get("table") or "",
                chunk.get("section") or "",
                chunk.get("doc_type") or "",
                chunk.get("title", ""),
                chunk.get("body", ""),
                json.dumps(vec),
                chunk.get("last_modified") or 0,
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO kb_vectors"
            "(chunk_id,path,connection,schema_name,table_name,section,doc_type,title,body,embedding,last_modified)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        total += len(batch)

    conn.execute("INSERT OR REPLACE INTO vec_meta(key,value) VALUES('built_at',?)", (str(time.time()),))
    conn.commit()
    return total


def vector_search(query: str, embedder, conn: sqlite3.Connection, top_k: int = 5, doc_type_filter: str = None) -> list:
    qvecs = embedder([query])
    if not qvecs or not qvecs[0]:
        return []
    qv = qvecs[0]

    where = "WHERE 1=1"
    params: list = []
    if doc_type_filter:
        where += " AND doc_type=?"
        params.append(doc_type_filter)

    rows = conn.execute(
        f"SELECT path,connection,schema_name,table_name,section,doc_type,title,body,embedding "
        f"FROM kb_vectors {where}",
        params,
    ).fetchall()

    scored = []
    for row in rows:
        path, conn_name, schema, table, section, doc_type, title, body, emb_json = row
        try:
            vec = json.loads(emb_json)
        except (json.JSONDecodeError, TypeError):
            continue
        score = _cosine(qv, vec)
        scored.append((score, path, conn_name, schema, table, section, doc_type, title, body))

    scored.sort(reverse=True)
    results = []
    for i, (score, path, conn_name, schema, table, section, doc_type, title, body) in enumerate(scored[:top_k]):
        results.append({
            "rank": i + 1,
            "score": round(score, 4),
            "path": path,
            "connection": conn_name or None,
            "schema": schema or None,
            "table": table or None,
            "section": section,
            "doc_type": doc_type,
            "title": title,
            "excerpt": make_excerpt(body),
        })
    return results


# ── Reciprocal Rank Fusion ─────────────────────────────────────────────────────

def rrf_fuse(fts: list, vec: list, fts_w: float, vec_w: float, top_k: int) -> list:
    scores: dict = {}
    items: dict = {}
    for i, r in enumerate(fts):
        key = f"{r['path']}::{r['section']}"
        scores[key] = scores.get(key, 0) + fts_w / (RRF_K + i + 1)
        items[key] = r
    for i, r in enumerate(vec):
        key = f"{r['path']}::{r['section']}"
        scores[key] = scores.get(key, 0) + vec_w / (RRF_K + i + 1)
        if key not in items:
            items[key] = r

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for i, (key, score) in enumerate(ranked[:top_k]):
        item = dict(items[key])
        item["rank"] = i + 1
        item["score"] = round(score, 6)
        results.append(item)
    return results


# ── Build command ──────────────────────────────────────────────────────────────

def cmd_build(kb_root: Path, config: dict, incremental: bool = False) -> None:
    if not kb_root.exists():
        print(f"Error: KB directory not found: {kb_root}", file=sys.stderr)
        sys.exit(1)

    fts_path = config["fts"]["index_path"]
    vec_path = config["embeddings"]["index_path"]

    t0 = time.time()
    chunks = chunk_kb(kb_root)
    n_files = _unique_file_count(chunks)

    if not chunks:
        print("No indexable content found. KB may be empty.")
        return

    fts_conn = open_fts_db(fts_path)
    if incremental:
        f_upd, c_add = fts_build_incremental(chunks, fts_conn)
        index_note = f"incremental: {f_upd} file(s) updated, {c_add} chunk(s) reindexed"
    else:
        fts_build_full(chunks, fts_conn)
        index_note = f"{len(chunks)} chunks indexed"
    fts_conn.close()

    embedder = get_embedder(config)
    if embedder:
        vec_conn = open_vector_db(vec_path)
        count = vector_build(chunks, embedder, vec_conn)
        vec_conn.close()
        index_note += f", {count} vectors"

    elapsed = time.time() - t0
    print(f"KB semantic index updated: {len(chunks)} chunks from {n_files} files. ({index_note}, {elapsed:.1f}s)")


# ── Search command ─────────────────────────────────────────────────────────────

def cmd_search(query: str, config: dict, top_k: int, output_format: str, doc_type_filter: str = None) -> None:
    fts_path = config["fts"]["index_path"]
    vec_path = config["embeddings"]["index_path"]
    search_cfg = config.get("search", {})

    if not Path(fts_path).exists():
        out = {"query": query, "index_age_hours": None, "results": [], "error": "index_not_built", "fallback": True}
        _print_search(out, output_format)
        return

    fts_conn = open_fts_db(fts_path)
    built_ts = fts_get_built_at(fts_conn)
    age_hours = round((time.time() - built_ts) / 3600, 2) if built_ts else None
    fts_results = fts_search(query, fts_conn, top_k=top_k, doc_type_filter=doc_type_filter)
    fts_conn.close()

    final = fts_results
    embedder = get_embedder(config)
    if embedder and Path(vec_path).exists():
        vec_conn = open_vector_db(vec_path)
        vec_results = vector_search(query, embedder, vec_conn, top_k=top_k, doc_type_filter=doc_type_filter)
        vec_conn.close()
        if vec_results:
            fw = search_cfg.get("fts_weight", 0.6)
            vw = search_cfg.get("embedding_weight", 0.4)
            final = rrf_fuse(fts_results, vec_results, fw, vw, top_k)

    out = {"query": query, "index_age_hours": age_hours, "results": final, "error": None, "fallback": False}
    _print_search(out, output_format)


def _print_search(result: dict, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(result, indent=2))
        return

    if result.get("error"):
        print(f"Error: {result['error']}")
        if result.get("fallback"):
            print("(skills will fall back to path-based KB lookup)")
        return

    results = result.get("results", [])
    if not results:
        print(f"No results for: {result['query']}")
        return

    age = result.get("index_age_hours")
    age_str = f" (index age: {age}h)" if age is not None else ""
    print(f"Search: {result['query']}{age_str}\n")
    for r in results:
        print(f"  [{r['rank']}] {r['title']}")
        print(f"       {r['path']}")
        if r.get("excerpt"):
            print(f"       {r['excerpt']}")
        print()


# ── Status command ─────────────────────────────────────────────────────────────

def cmd_status(config: dict, output_format: str) -> None:
    fts_path = config["fts"]["index_path"]
    vec_path = config["embeddings"]["index_path"]

    fts_info: dict = {"exists": False, "chunks": 0, "files": 0, "built_at": None, "age_hours": None}
    vec_info: dict = {"enabled": config["embeddings"].get("enabled", False), "exists": False}

    if Path(fts_path).exists():
        try:
            c = open_fts_db(fts_path)
            fts_info["chunks"] = (c.execute("SELECT COUNT(*) FROM kb_chunks").fetchone() or [0])[0]
            fts_info["files"] = (c.execute("SELECT COUNT(*) FROM kb_files").fetchone() or [0])[0]
            built_ts = fts_get_built_at(c)
            c.close()
            fts_info["exists"] = True
            if built_ts:
                fts_info["built_at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(built_ts))
                fts_info["age_hours"] = round((time.time() - built_ts) / 3600, 2)
        except Exception as e:
            fts_info["error"] = str(e)

    vec_info["exists"] = Path(vec_path).exists()

    status = {"enabled": config.get("enabled", True), "fts": fts_info, "embeddings": vec_info}

    if output_format == "json":
        print(json.dumps(status, indent=2))
        return

    label = "enabled" if status["enabled"] else "disabled"
    print(f"Semantic index: {label}")
    if fts_info["exists"]:
        built = fts_info.get("built_at", "unknown")
        age = fts_info.get("age_hours")
        age_str = f" ({age}h ago)" if age is not None else ""
        print(f"  FTS index  : {fts_info['chunks']} chunks from {fts_info['files']} files, built {built}{age_str}")
    else:
        print("  FTS index  : not built — run /db-index to build")
    if config["embeddings"].get("enabled"):
        print(f"  Vector index: {'present' if vec_info['exists'] else 'not built'}")


# ── Session prime command ──────────────────────────────────────────────────────

def cmd_session_prime(config: dict, top_k: int, output_format: str) -> None:
    """Produce a compact context block for session startup."""
    fts_path = config["fts"]["index_path"]

    if not Path(fts_path).exists():
        result = {"error": "index_not_built", "fallback": True, "context": None}
        print(json.dumps(result) if output_format == "json" else "Error: index_not_built")
        return

    conn = open_fts_db(fts_path)
    total_chunks = (conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone() or [0])[0]
    total_files = (conn.execute("SELECT COUNT(*) FROM kb_files").fetchone() or [0])[0]
    built_ts = fts_get_built_at(conn)
    built_at = time.strftime("%Y-%m-%d %H:%M", time.localtime(built_ts)) if built_ts else "unknown"

    gotchas = conn.execute(
        "SELECT title, body, connection, schema_name, table_name FROM kb_chunks "
        "WHERE doc_type='gotchas' ORDER BY last_modified DESC LIMIT ?",
        (min(top_k, 10),),
    ).fetchall()

    open_qs = conn.execute(
        "SELECT body FROM kb_chunks WHERE doc_type='open_questions' LIMIT 1"
    ).fetchall()

    tables = conn.execute(
        "SELECT DISTINCT table_name, connection, schema_name "
        "FROM kb_chunks WHERE doc_type='table' AND table_name != '' "
        "ORDER BY last_modified DESC"
    ).fetchall()

    conn.close()

    lines = [
        f"<!-- KB Semantic Index: {total_chunks} chunks from {total_files} files, built {built_at} -->",
        "",
        "## Knowledge Base Summary (semantic index)",
        "",
    ]

    if tables:
        table_list = ", ".join(f"`{t[0]}`" for t in tables[:30])
        suffix = "…" if len(tables) > 30 else ""
        lines.append(f"**Documented tables ({len(tables)}):** {table_list}{suffix}")
        lines.append("")

    if gotchas:
        lines.append("### Active Gotchas")
        for title, body, connection, schema, table in gotchas:
            scope_parts = [p for p in [connection, schema, table] if p]
            scope = " / ".join(scope_parts)
            excerpt = make_excerpt(body, max_chars=150)
            scope_label = f" [{scope}]" if scope else ""
            lines.append(f"- **{title}**{scope_label}: {excerpt}")
        lines.append("")

    if open_qs:
        lines.append("### Open Questions")
        lines.append(make_excerpt(open_qs[0][0], max_chars=300))
        lines.append("")

    context = "\n".join(lines)

    if output_format == "json":
        print(json.dumps({"error": None, "fallback": False, "context": context}))
    else:
        print(context)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AnalystOS KB semantic search — build index and run queries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build", action="store_true", help="Build or rebuild the search index")
    action.add_argument("--query", metavar="TEXT", help="Search the index")
    action.add_argument("--session-prime", action="store_true", help="Emit compact session startup context")
    action.add_argument("--status", action="store_true", help="Show index health")

    parser.add_argument("--kb", metavar="PATH", default="db-knowledge/", help="KB root directory")
    parser.add_argument("--incremental", action="store_true", help="Only re-index changed files")
    parser.add_argument("--top-k", type=int, default=None, help="Max results to return")
    parser.add_argument("--type", metavar="DOC_TYPE", dest="doc_type",
                        help="Filter by doc_type: table | gotchas | query | schema_overview")
    parser.add_argument("--format", default="json", choices=["json", "text", "context"],
                        help="Output format (default: json)")
    parser.add_argument("--config", metavar="PATH", default=str(CONNECTIONS_PATH),
                        help="Path to active.yaml")

    args = parser.parse_args()
    config = load_rag_config(Path(args.config))

    if args.build:
        cmd_build(Path(args.kb), config, incremental=args.incremental)
    elif args.query:
        top_k = args.top_k or config["search"]["default_top_k"]
        cmd_search(args.query, config, top_k=top_k, output_format=args.format, doc_type_filter=args.doc_type)
    elif args.session_prime:
        top_k = args.top_k or config["session"]["prime_top_k"]
        cmd_session_prime(config, top_k=top_k, output_format=args.format or "context")
    elif args.status:
        cmd_status(config, output_format=args.format)


if __name__ == "__main__":
    main()
