"""Transactional, session-scoped document registry. Original bytes and text are durable."""
import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from backend import runtime_settings as settings

PUBLIC_FIELDS = 'document_id file_name file_type size_bytes status uploaded_at chunk_count encoding extraction_complete'.split()

class Repository:
    def __init__(self, root=None):
        self.root = root or settings.DATA_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'documents.sqlite3'
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS documents (
                scope TEXT NOT NULL, document_id TEXT NOT NULL, file_name TEXT NOT NULL,
                file_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, status TEXT NOT NULL,
                uploaded_at TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
                encoding TEXT NOT NULL, extraction_complete INTEGER NOT NULL DEFAULT 1,
                original BLOB NOT NULL, text TEXT NOT NULL, pages TEXT NOT NULL,
                PRIMARY KEY(scope, document_id), UNIQUE(scope, file_name)
              );
              CREATE TABLE IF NOT EXISTS threads (
                scope TEXT NOT NULL, thread_id TEXT NOT NULL, expires REAL NOT NULL,
                PRIMARY KEY(scope, thread_id)
              );
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('session_secret', secrets.token_hex(32)))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def secret(self):
        with self.connect() as db:
            return db.execute("SELECT value FROM settings WHERE key='session_secret'").fetchone()[0].encode()

    @staticmethod
    def public(row):
        return {key: row[key] for key in PUBLIC_FIELDS}

    def list(self, scope):
        with self.connect() as db:
            return [self.public(r) for r in db.execute(
                'SELECT * FROM documents WHERE scope=? ORDER BY uploaded_at, file_name', (scope,))]

    def get(self, scope, document_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM documents WHERE scope=? AND document_id=?', (scope, document_id)).fetchone()
            return dict(row) if row else None

    def put(self, scope, name, data, extracted):
        # Content identity is scoped; duplicate bytes keep their first display name.
        identifier = hashlib.sha256(data).hexdigest()
        with self.connect() as db:
            existing = db.execute('SELECT * FROM documents WHERE scope=? AND document_id=?', (scope, identifier)).fetchone()
            if existing and existing['extraction_complete']:
                return self.public(existing), True
            if existing:
                db.execute("UPDATE documents SET original=?, text=?, pages=?, size_bytes=?, encoding=?, status='extracted', extraction_complete=1 WHERE scope=? AND document_id=?",
                           (data, extracted.text, json.dumps(extracted.pages), len(data), extracted.encoding, scope, identifier))
                row = db.execute('SELECT * FROM documents WHERE scope=? AND document_id=?', (scope, identifier)).fetchone()
                return self.public(row), False
            same_name = db.execute('SELECT 1 FROM documents WHERE scope=? AND file_name=?', (scope, name)).fetchone()
            if same_name:
                raise ValueError('A different document with this filename already exists. Rename it or delete the earlier upload.')
            from pathlib import Path
            db.execute('''INSERT INTO documents
              (scope, document_id, file_name, file_type, size_bytes, status, uploaded_at, encoding, original, text, pages)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (scope, identifier, name, Path(name).suffix.lower(), len(data), 'extracted',
               datetime.now(timezone.utc).isoformat(), extracted.encoding, data, extracted.text,
               json.dumps(extracted.pages)))
            row = db.execute('SELECT * FROM documents WHERE scope=? AND document_id=?', (scope, identifier)).fetchone()
            return self.public(row), False

    def mark_indexed(self, scope, identifier, chunks):
        with self.connect() as db:
            db.execute("UPDATE documents SET status='ready', chunk_count=? WHERE scope=? AND document_id=?", (chunks, scope, identifier))

    def delete(self, scope, identifier):
        with self.connect() as db:
            return db.execute('DELETE FROM documents WHERE scope=? AND document_id=?', (scope, identifier)).rowcount > 0

    def thread(self, scope, identifier, expires):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO threads VALUES (?, ?, ?)', (scope, identifier, expires))

    def thread_expiry(self, scope, identifier):
        with self.connect() as db:
            row = db.execute('SELECT expires FROM threads WHERE scope=? AND thread_id=?', (scope, identifier)).fetchone()
            return row[0] if row else None

    def consume_thread(self, scope, identifier):
        # Claim before resuming: duplicate approvals never execute a tool twice.
        with self.connect() as db:
            return db.execute('DELETE FROM threads WHERE scope=? AND thread_id=?', (scope, identifier)).rowcount > 0


def chunks_for(row):
    text, pages = row['text'], json.loads(row['pages'])
    offset = 0
    index = 0
    while offset < len(text):
        end = min(len(text), offset + settings.CHUNK_CHARS)
        # Prefer complete lines, but keep very long lines bounded.
        boundary = text.rfind('\n', offset + settings.CHUNK_CHARS // 2, end)
        if boundary >= 0 and end < len(text):
            end = boundary + 1
        page = next((p['page'] for p in pages if p['start'] <= offset < p['end']), 1)
        yield {
            'text': text[offset:end], 'metadata': {
                'document_id': row['document_id'], 'file_name': row['file_name'],
                'scope': row['scope'], 'page': page, 'chunk_index': index,
                'line_start': text.count('\n', 0, offset) + 1,
                'line_end': text.count('\n', 0, end) + 1,
            }
        }
        if end == len(text):
            break
        offset = max(offset + 1, end - settings.CHUNK_OVERLAP)
        index += 1
