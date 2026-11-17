# RAG with Image Understanding — setup

This folder contains the complete project source. Reinstall dependencies for your own operating system. The ZIP excludes `.env`, installed dependencies, Git history, build outputs, and newly generated test/runtime data. The original `.chroma` database is retained for optional recovery.

## 1. Configure the backend

Use Python 3.11 or later. Open a terminal in this project folder.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
```

macOS/Linux:

```bash
source .venv/bin/activate
cp .env.example .env
```

Install the versions used to verify the active API:

```bash
python -m pip install -r requirements-api.txt
```

Fill in `.env`:

- `GROQ_API_KEY`: conversation, safety classification, and grounded generation.
- `GOOGLE_API_KEY`: document embeddings and image understanding.
- `TAVILY_API_KEY`: web and related-image search.
- `LLM_MODEL`, `EMBEDDING_MODEL`, `VISION_MODEL`: provider model IDs available to your account.

No secret values are included. Metadata and exact text can be served without model calls. If embedding fails during upload, the original bytes and extracted text are saved, the UI reports the indexing error, and **Retry indexing** resumes ingestion after configuration is fixed.

For scanned PDF OCR, install the Tesseract application and ensure `tesseract --version` works. Python OCR dependencies are included. Linux: `sudo apt-get install tesseract-ocr`. On Windows, install Tesseract and add its installation directory to PATH. The backend Docker image includes Tesseract.

Start the backend:

```bash
python -m uvicorn backend.api:api --host 127.0.0.1 --port 8000
```

API documentation: http://localhost:8000/docs

## 2. Start the frontend

Use Node 20.9 or later. Open a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. Use `localhost` consistently for both frontend and backend. Upload limits are fetched from `/config`; they are also enforced by the backend.

Production frontend commands:

```bash
cd frontend
npm run build
npm start
```

If the backend uses a different URL, create `frontend/.env.local` with `NEXT_PUBLIC_API_URL=https://your-backend.example` **before** building. Set backend `FRONTEND_URL` to the exact frontend origin. For truly cross-site HTTPS hosting, configure `SESSION_COOKIE_SECURE=true` and `SESSION_COOKIE_SAMESITE=none`, or use a same-site reverse proxy.

## 3. Use your documents

Upload originals through the web UI. Documents, original bytes, exact extracted text, vector indices, session signing secret, and scoped history persist under `data/`. Keep that directory when restarting or updating. Your browser's HttpOnly session cookie selects its knowledge base; do not clear it if you want to continue accessing that session. This is session isolation, not a multi-account login system.

Example questions:

- How many documents have I uploaded?
- List all uploaded documents.
- Is Dynamic_Programming.txt uploaded?
- Give me every line in Dynamic_Programming.txt.
- Explain Fibonacci memoization from my file.
- Summarize my uploaded PDF.
- Compare alpha.pdf and beta.pdf.
- What is Java?
- What happened in the latest technology news?

Long exact-content responses show a **Next part** button. API clients can follow `next_part` from `/documents/{document_id}/text?part=1`. Parts preserve text, CRLF, tabs, and indentation. Sensitive credentials are explicitly redacted; an affected response reports `exact=false`.

Duplicate bytes count as one document per session; the first filename is retained. A different file with an existing filename is rejected instead of silently overwriting it. Delete or rename the earlier upload first.

## 4. Recover the existing legacy database

The supplied database contains **one document** (`Dynamic_Programming.txt`) represented by **nine chunks**. The old upload code deleted the original temporary file. Chunks cannot prove the original whitespace, blank lines, or complete text, so recovery does not present them as exact content.

To recover its metadata into your current browser session:

1. Set `LEGACY_IMPORT_KEY` in `.env` to a random administrator key and restart the backend.
2. On the homepage, enter that key under **Recover existing documents** and click the recovery button.
3. Reupload your original `Dynamic_Programming.txt` for exact-text access and complete summaries. If you only have the old chunks, **Retry indexing** makes their partial evidence searchable, while exact-text requests continue to explain that the original is required.

Recovery is explicit because the old database has no user/session ownership. It is never exposed automatically to a new browser. The original database is read in read-only mode and is not modified.

The Java file under `tests/fixtures/` is a synthetic regression fixture with three implementations. It is not claimed to be your original upload.

## 5. Approval policy

Routine read-only web search runs automatically. To require approval for all web searches on your installation, set:

```dotenv
WEB_SEARCH_REQUIRE_APPROVAL=true
APPROVAL_TTL_SECONDS=600
```

Metadata, exact text, local retrieval, summaries, conversation, and refusals do not request approval. Pending approvals are owned by their session, expire, and are consumed once. LangGraph in-memory checkpoints are used: after a server restart an unfinished approval must be asked again. Run a **single backend worker** for this checkpoint configuration. Related-image search also respects the configured restriction and asks users to make a separate web-search request when approval is mandatory.

## 6. Test

```bash
python -m pytest -q
```

This runs deterministic provider doubles together with real SQLite, Chroma, file parsers, and OCR. It does not spend provider credits. Live-provider checks are opt-in and require valid keys:

Windows PowerShell:

```powershell
$env:RUN_LIVE_TESTS="1"
python -m pytest backend/graph/chains/tests -q
```

macOS/Linux:

```bash
RUN_LIVE_TESTS=1 python -m pytest backend/graph/chains/tests -q
```

Frontend checks:

```bash
cd frontend
npm run lint
npm run build
```

Optional browser regression test (uses deterministic provider doubles): install `playwright`, run `python -m playwright install chromium`, build the frontend, free ports 8000 and 3000, then run from the project root:

```bash
RUN_BROWSER_TESTS=1 python -m pytest tests/test_browser.py -q
```

On PowerShell set `$env:RUN_BROWSER_TESTS="1"` before the pytest command. If using an installed system browser, set `BROWSER_EXECUTABLE` to its absolute path. Docker can be started with `docker compose up --build`; keep the `data` volume writable by the container user. Docker was not exercised in this environment.
