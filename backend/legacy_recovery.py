"""Explicit administrator-authorized recovery of unscoped Chroma metadata.

Never claim overlapping/stripped legacy chunks are an exact original document.
Read SQLite in read-only mode; do not migrate or mutate the original database.
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from backend.repository import Repository


def recover_legacy(repository: Repository, scope: str, source: Path):
    groups = defaultdict(list)
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id, key, string_value, int_value FROM embedding_metadata ORDER BY id').fetchall()
    records = defaultdict(dict)
    for identifier, key, string_value, int_value in rows:
        records[identifier][key] = string_value if string_value is not None else int_value
    for record in records.values():
        if record.get('document_id') and record.get('file_name'):
            groups[record['document_id']].append(record)
    recovered = []
    with repository.connect() as db:
        for identifier, chunks in groups.items():
            chunks.sort(key=lambda c: int(c.get('chunk_index', 0)))
            filename = str(chunks[0]['file_name']).replace('\\', '/').rsplit('/', 1)[-1]
            text = '\n'.join(str(c.get('chroma:document', '')) for c in chunks)
            if not text:
                continue
            # A recovery must never replace a complete upload with older chunks.
            cursor = db.execute('''INSERT OR IGNORE INTO documents
                (scope, document_id, file_name, file_type, size_bytes, status, uploaded_at,
                 chunk_count, encoding, extraction_complete, original, text, pages)
                 VALUES (?, ?, ?, ?, 0, 'legacy_partial', ?, ?, '', 0, ?, ?, ?)''',
                (scope, identifier, filename, Path(filename).suffix.lower(), datetime.now(timezone.utc).isoformat(),
                 len(chunks), b'', text, json.dumps([{'page': 1, 'start': 0, 'end': len(text)}])))
            if cursor.rowcount:
                recovered.append(filename)
    return recovered
