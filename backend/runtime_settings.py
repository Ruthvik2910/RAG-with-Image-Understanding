"""Settings for the API integration; protected legacy configuration is unchanged."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
MIB = 1024 * 1024

def positive(name, default, cast=int):
    value = cast(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f'{name} must be positive')
    return value

DATA_DIR = Path(os.getenv('RAG_DATA_DIR', './data')).resolve()
DEFAULT_LIMITS = {'.pdf': 10, '.docx': 10, '.txt': 5, '.md': 5}
UPLOAD_LIMITS = {
    ext: int(positive(f'UPLOAD_{ext[1:].upper()}_MAX_MB', limit, float) * MIB)
    for ext, limit in DEFAULT_LIMITS.items()
}
IMAGE_LIMIT = int(positive('UPLOAD_IMAGE_MAX_MB', 10, float) * MIB)
MAX_FILES = positive('UPLOAD_MAX_FILES', 5)
MAX_EXPANDED_DOCX = positive('DOCX_MAX_EXPANDED_MB', 50) * MIB
FULL_PART_CHARS = min(positive('FULL_DOCUMENT_PART_CHARS', 8000), 10000)
CONTEXT_CHARS = positive('RAG_CONTEXT_CHARS', 16000)
CHUNK_CHARS = positive('RAG_CHUNK_CHARS', 1200)
CHUNK_OVERLAP = int(os.getenv('RAG_CHUNK_OVERLAP', '150'))
if not 0 <= CHUNK_OVERLAP < CHUNK_CHARS:
    raise ValueError('RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_CHARS')
WEB_APPROVAL = os.getenv('WEB_SEARCH_REQUIRE_APPROVAL', 'false').lower() == 'true'
APPROVAL_TTL = positive('APPROVAL_TTL_SECONDS', 600)
COOKIE_NAME = 'rag_session'
COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
COOKIE_SAMESITE = os.getenv('SESSION_COOKIE_SAMESITE', 'lax')
if COOKIE_SAMESITE not in {'lax', 'strict', 'none'}:
    raise ValueError('SESSION_COOKIE_SAMESITE must be lax, strict, or none')
if COOKIE_SAMESITE == 'none' and not COOKIE_SECURE:
    raise ValueError('Cross-site cookies require SESSION_COOKIE_SECURE=true')
ALLOWED_ORIGINS = list(dict.fromkeys([
    os.getenv('FRONTEND_URL', 'http://localhost:3000'),
    'http://localhost:3000', 'http://127.0.0.1:3000',
]))
