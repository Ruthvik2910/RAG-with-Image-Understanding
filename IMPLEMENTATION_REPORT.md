# Implementation and verification report

Date: 16 November 2026

## Delivered result and scope

The existing Next.js frontend now uses a corrected FastAPI integration with durable, session-scoped document storage and an API-facing LangGraph workflow. The explicitly protected files have not been edited, replaced, or monkey-patched. Their original byte hashes are included in `PROTECTED_FILES_SHA256.json`.

The ZIP does not contain the newer implementations mentioned in the instructions for several protected filenames. Fifteen protected files are present, and they are older implementations. Missing protected modules were not invented or created under those names. The integration is implemented in new, differently named modules and the remaining editable files. Run `backend.api:api`; a direct import of the preserved legacy `backend.graph.graph.app` still invokes the old graph.

## Actual root causes and fixes

| Finding in supplied code | Corrected behavior |
|---|---|
| The router/graders use `with_structured_output`, which can require a tool call when the provider returns ordinary refusal text. | Active routing/grading uses ordinary completions and validated JSON text; no tools are bound. Refusals are valid final responses. A provider `tool_use_failed` refusal is recovered defensively. |
| The protected graph has only local/web routes and retrieves candidates before deciding the request type. | Security precedes intent routing. Metadata, full text, summaries, semantic QA, conversation, and web are distinct paths. Ambiguous questions use a plain-JSON classification call. |
| The API metadata detector is substring based (`md`, `file`, etc.) and can intercept content questions. | Intent patterns distinguish metadata operations from content requests. Counts/names/status always come from SQLite records. |
| Metadata is inferred from global vector chunks, with no owner/session association. | One registry row per unique document per signed browser session. Both database lookups and Chroma filters enforce that scope. History and approval ownership are scoped too. |
| The API deletes uploaded temporary files after creating chunks. | Original bytes, exact extracted text, extraction boundaries, metadata, and status are stored transactionally and retained after restart. |
| TXT loader requires UTF-8; ordinary splitting/formatting strips/truncates evidence. | Text supports Unicode BOMs/UTF-8 and common legacy encodings. CRLF, tabs, indentation, comments, and Unicode are preserved in extracted storage. |
| Full-content requests run through semantic top-K and a context formatter limited to 6,000 characters (2,000 per document). | Exact content is returned directly from saved text without similarity retrieval or generation. Long documents have contiguous sequential parts with explicit offsets and line ranges. |
| PDF validation checks only a signature; there is no scanned-page OCR path. DOCX structure/table fidelity is limited. | Parse every PDF page, preserve page boundaries, OCR textless pages, and surface failures. DOCX walks paragraphs/tables in body order with list markers and tab-separated cells. |
| Every web route enters an unconditional LangGraph interrupt. | Routine read-only web runs directly. An environment policy enables real interrupt/resume approval when explicitly configured. Missing/expired/foreign/replayed approvals fail safely. |
| One generic 25 MB upload cap is used; the frontend does not display backend-configured per-type limits. Some security size constants are unused. | Central integration settings define per-type limits. `/config` is the UI source of truth; server validation runs before extraction/embedding. Oversized, empty, mismatched, unsupported, and corrupted files return clear HTTP errors. |
| Local retrieval grading routes insufficient evidence to web automatically. | No web fallback on local retrieval/indexing failures. The answer explains missing files, incomplete indexing, or insufficient evidence. Web is chosen explicitly by routing policy. |
| Global Chroma identity deduplicates chunks/files without preserving exact contents or scopes. | Scoped content hashes deduplicate documents, not chunks. Same-name/different-byte conflicts are explicit. Deletion removes the scoped vector entries and registry/original text. |
| Sources lack consistent page/line context, and output errors can expose raw provider messages. | Evidence carries file/page/line attribution or real web titles/URLs, is wrapped as untrusted data, and undergoes grounding verification. User-facing provider errors are sanitized. |
| Image model selection contains invented aliases and rejects arbitrary configured provider IDs. Image requests skip some text security checks. | Configured vision IDs pass through to the provider; prompt checks, safety classification, MIME/content validation, limits, and output redaction also cover images. |
| Frontend fetches do not persist cross-origin session credentials, and approval controls rely on several inferred statuses. | Shared request helper establishes a single signed HttpOnly session and includes credentials. Approval controls require backend `status=approval_required`. |
| CLI invokes the old graph and calls nonexistent observer methods; duplicate API handlers and dashboard route counters are inconsistent. | CLI calls the same API as the web UI; the duplicate handler is removed. Observability counts active route labels. |
| Windows dependency/build artifacts are included in the supplied ZIP; the frontend imports Google fonts at build time. | Deliver source and lockfile, reinstall natively with `npm ci`, and use system sans/monospace fonts. Docker excludes host dependencies/secrets and persists the corrected data directory. |

## Routing architecture

1. Existing `check_prompt` blocks prompt-injection/instruction-override patterns.
2. High-confidence harmful requests are refused before any provider/tool call.
3. Deterministic intents serve database metadata and exact document text without an LLM.
4. Local/web/image tool paths perform a model safety check before retrieval/search. An invalid safety result fails closed.
5. Stable conversation uses a plain completion with safety instructions.
6. Semantic local questions retrieve scoped chunks; multi-document selection retrieves each file independently.
7. Summaries process all sequential chunks and reduce notes within a bounded context budget. Their generated nature is stated; the API does not claim verified grounding for an unverified summary.
8. Read-only web uses actual Tavily results, with URL-bearing attribution, and obeys configured approval policy.
9. Local/web answers are verified against supplied evidence, retried once if needed, and withheld as factual claims if grounding still fails.
10. Every final response uses the existing output-security sanitizer. Exact documents are sanitized **before** splitting so secrets spanning part boundaries cannot leak. Model outputs retain the existing maximum-output policy; exact content uses bounded parts instead of truncating the entire file.

## Storage, identity, and recovery

- `data/documents.sqlite3`: scoped document registry, original byte BLOB, text, page offsets, metadata, extraction status, session signing secret, and approval ownership/expiry.
- `data/vectors`: Chroma collection `rag-scoped-v2`, using Gemini embeddings and scoped metadata filters.
- `data/sessions/<scope>`: isolated evaluation and observability histories.
- ID: SHA-256 of original bytes, scoped by session. Duplicate bytes retain the first filename. Different bytes with the same filename are rejected (409).
- A failed embedding call leaves a document `extracted`, with original/exact text intact. The upload reports 503 and the UI offers **Retry indexing**.
- Only the original database is retained from the old runtime. It contains one unique filename, `Dynamic_Programming.txt`, represented by nine chunks. Its original uploaded file is absent.
- Administrator-key recovery reads the original database in read-only mode, records recovered documents as `legacy_partial`, and refuses to label reconstructed chunks as exact original content. Reuploading original bytes replaces an incomplete legacy representation.
- Session isolation is implemented, not account authentication. Cookie loss means a new session; there is no claim of authenticated multi-device user accounts.

## API endpoints

Existing endpoints remain: `/`, `/health`, `/documents`, `/upload`, `/documents/{document_id}` (DELETE), `/chat`, `/chat/image`, `/chat/approval`, `/evaluation` (GET/DELETE), `/observability` (GET/DELETE).

New endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /config` | Actual file/image limits, batch limit, approval policy, recovery availability; no credentials. |
| `GET /documents/{document_id}/text?part=1` | Exact saved text with contiguous pagination, line/character ranges, completeness, and redaction status. |
| `POST /documents/{document_id}/index` | Retry indexing saved extracted text. |
| `POST /documents/import-legacy` | Explicit scoped recovery, authorized by `X-Legacy-Import-Key`; requires configured `LEGACY_IMPORT_KEY`. |

`GET /documents` returns the registry count and metadata, including unique document ID, filename, extension, size, upload timestamp, indexing status, chunk count, encoding, and extraction completeness. Named existence/type/size/status queries are handled through the existing metadata chat route; duplicate endpoints are not introduced.

## Default size limits

| Format | Limit | Configuration |
|---|---:|---|
| PDF | 10 MiB | `UPLOAD_PDF_MAX_MB` |
| DOCX | 10 MiB | `UPLOAD_DOCX_MAX_MB` |
| TXT | 5 MiB | `UPLOAD_TXT_MAX_MB` |
| Markdown | 5 MiB | `UPLOAD_MD_MAX_MB` |
| PNG/JPEG/WEBP | 10 MiB | `UPLOAD_IMAGE_MAX_MB` |

The UI labels MiB-sized units as MB for consistency with the requested messages; each unit is 1,048,576 bytes. Maximum batch: five files (`UPLOAD_MAX_FILES`). Maximum expanded DOCX: 50 MiB (`DOCX_MAX_EXPANDED_MB`). Empty/invalid/corrupted documents fail with 422, unsupported extensions with 415, oversize with 413, conflicts with 409, and unavailable providers with 503. Partial batch successes include a `rejected` list which the UI displays.

## Changed and added source files

| File | Change |
|---|---|
| `backend/api.py` | Replaced duplicated integration code with scoped API wiring, durable uploads, error responses, text/reindex/config/recovery APIs, secure approval ownership, and scoped dashboards. |
| `backend/main.py` | CLI now uses the corrected HTTP API and one retained session. |
| `backend/image_search.py` | Configurable vision-model ID handling, lazy credential validation, removed speculative thinking/model aliases. |
| `backend/observability.py` | Counts corrected local/web route labels. |
| `backend/security/output_guard.py` | Optional truncation flag: normal output policy unchanged; full-document sanitation happens before pagination. |
| `backend/runtime_settings.py` (new) | Central settings for limits, storage, chunks/context, cookie policy, and approval. |
| `backend/file_processing.py` (new) | MIME/content/size validation, faithful TXT decoding, PDF pages/OCR, DOCX structure/tables, safe images. |
| `backend/repository.py` (new) | Transactional SQLite registry, exact originals/text, unique scoped identity, indexing status, approval ownership. |
| `backend/integrations.py` (new) | Lazy Groq/Gemini/Chroma/Tavily adapters, no required tool calls, refusal compatibility, provider errors, scoped vector operations. |
| `backend/orchestration.py` (new) | Active LangGraph workflow with security, intent, metadata, exact text, summaries, local/web generation and verification, configurable interrupt/resume. |
| `backend/legacy_recovery.py` (new) | Explicit read-only recovery from unscoped legacy SQLite metadata without pretending chunks are exact. |
| `frontend/src/lib/api.ts` (new) | Session initialization and credential-bearing fetch helper. |
| `frontend/src/app/page.tsx` | Live limits, size validation, partial errors, file sizes/status, index retry, recovery UI, exact-text formatting/pagination, explicit approval status, image-only submit. |
| `frontend/src/app/evaluation/page.tsx` | Scoped API helper and effect scheduling compatible with lint rules. |
| `frontend/src/app/observability/page.tsx` | Scoped API helper and effect scheduling compatible with lint rules. |
| `frontend/src/app/layout.tsx` | Application metadata, explicit React children type, and system fonts for network-independent builds. |
| `frontend/src/app/globals.css` | System sans/monospace theme mapping. |
| `frontend/next.config.ts` | Explicit Turbopack root; standalone build retained. |
| `frontend/next-env.d.ts` | Next.js-generated route/root-parameter type imports from the successful build. |
| `tests/conftest.py` (new) | Isolated databases and deterministic external-provider doubles. |
| `tests/test_application.py` (new) | Regression/API, real parsers, SQLite/Chroma, OCR, security, session isolation, and error coverage. |
| `tests/test_browser.py` (new) | Opt-in production-frontend interaction smoke test, with provider doubles. |
| `tests/fixtures/Dynamic_Programming.txt` (new) | Clearly synthetic Java regression fixture with all three Fibonacci implementations/comments/complexities. |
| `backend/graph/tests/test_graph.py` | Updated obsolete graph expectations to the active integration routing contract. |
| `backend/graph/chains/tests/test_chains.py` | Explicit opt-in live-provider tests for ordinary completion/JSON/refusal/web; no hidden network tests. |
| `requirements-api.txt` (new) | Exact direct dependency versions used to test the active API. |
| `requirements.txt` | Added required extraction/integration dependencies; existing optional legacy dependencies retained. |
| `.env.example` | Integration limits, storage/cookie/approval/recovery configuration; no keys. |
| `Dockerfile` | Tesseract system package. |
| `docker-compose.yml` | Persist corrected `data/` storage; frontend port 3000. |
| `frontend/Dockerfile` | Corrected frontend container PORT/EXPOSE to 3000. |
| `.dockerignore`, `frontend/.dockerignore` (new) | Exclude host dependencies, generated data/builds, and credentials from build contexts. |
| `.gitignore` | Ignore corrected runtime data and credentials. |
| `README.md`, `START_HERE.md`, `IMPLEMENTATION_REPORT.md`, `PROTECTED_FILES_SHA256.json` | Active setup, full report, and protected-file integrity evidence. |

All existing protected files are byte-for-byte unchanged: `backend/config.py`, `backend/ingestion.py`, `backend/graph/state.py`, `backend/graph/context.py`, `backend/graph/graph.py`, the protected `grade_documents`, `generate`, `web_search_approval`, `build_response`, and `security` nodes, and the protected router/generation/retrieval/hallucination/answer chains.

## Tests actually executed

| Command / check | Actual final result |
|---|---|
| `python -m pytest -q` | **83 passed, 2 skipped, 1 warning**. Skipped modules: live providers and opt-in browser suite. Warning is from the installed anyio pytest plugin's deprecation notice. |
| Real Chroma regression with deterministic embeddings | Passed: scoped add/search/delete and isolation; no external embedding API used. |
| Real scanned-PDF OCR | Passed using Tesseract and an image-only PDF fixture. |
| API tests | Passed: all routing examples, metadata, chunk-vs-document counts, duplicate/conflicting files, deletion, restart persistence, exact CRLF/tab/Unicode contents and pagination, three Java implementations/comments/complexities, PDF pages, DOCX table order, invalid/oversize/mixed files, retry indexing, refusal and provider errors, grounding failure, approval scope/expiry/replay, secret redaction across part boundaries, image validation and mocked vision, scoped logs, admin-only legacy recovery. |
| `python -m compileall -q backend` | Passed. |
| `npm ci --include=optional --no-audit --no-fund` | Passed from the supplied frontend lockfile on Linux. |
| `npm run lint` | Passed (exit 0). |
| `npm run build` | Passed (exit 0), including TypeScript and static generation for homepage/evaluation/observability. |
| SHA-256 comparison of the 15 protected files | Passed: no byte changes. |
| `RUN_BROWSER_TESTS=1 python -m pytest tests/test_browser.py -q` | **Blocked/failed before page interaction**: Playwright browser download was unavailable; installed system Chromium then aborted because this environment forbids its required socket/ptrace operations. No visual/browser pass is claimed. The reusable smoke test remains included. |
| Live Groq, Gemini embeddings/vision, Tavily calls | **Not run**. Original credentials were neither loaded nor used. Set your own keys and run the opt-in live checks. |
| Docker build/start | Not run. |

Initial development failures (a cookie-test setup error, a layout type import, old dashboard effect lint errors, and Windows native dependencies) were corrected and the relevant checks were rerun successfully. The remaining browser limitation above is explicitly unresolved in this environment.

## Remaining limits and exact startup commands

1. Reupload the original legacy `Dynamic_Programming.txt` for complete exact text. Its nine old chunks cannot restore lost formatting or prove completeness.
2. External service availability, account/model access, quota, and real multimodal answer quality remain unverified until your credentials are configured.
3. HITL checkpoints are in memory; use one backend worker. Restarted/expired requests must be asked again. A production multi-worker deployment would require a shared persistent checkpointer.
4. Session scoping is not a login/account system. Preserve the browser cookie and `data/`. Clearing a cookie creates a separate knowledge base.
5. Safety detectors are defensive checks, not a guarantee of catching every adversarial phrasing. Existing prompt/content/output protections are retained and used by the active API.
6. PDF/DOCX extraction remains constrained by source quality, encryption, formatting, and OCR availability. Failures are explicit instead of silently returning partial success.

Backend, from the project root after environment activation/configuration:

```bash
python -m pip install -r requirements-api.txt
python -m uvicorn backend.api:api --host 127.0.0.1 --port 8000
```

Frontend, in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. See `START_HERE.md` for Windows activation, environment variables, legacy recovery, production commands, and live/browser checks.
