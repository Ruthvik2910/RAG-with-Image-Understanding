# Evaluation and response-formatting fixes

Date: 17 November 2026

This update starts from the latest project ZIP, including the previous Markdown-table fix. It addresses only evaluation/dashboard reporting and response presentation. No dependencies or package manifests were changed.

## 1. Evaluation root causes verified in the supplied code

### Different default API hostnames selected different sessions

The chat page and shared request helper default to `http://localhost:8000`. Observability uses the same hostname. Evaluation independently defaulted to `http://127.0.0.1:8000`.

Those hostnames do not share the application's session cookies. A chat request can therefore be recorded in one session while the Evaluation page requests another session's empty history. The evaluation page now imports the exact same `API_URL` as chat; authentication/session behavior itself was not changed.

A regression test reproduced the separate-host empty-history behavior. Other tests made 28 real requests through FastAPI's test client and verified 28 persisted evaluations in the matching session, then verified Refresh sees the next actual request.

### Observability percentage units were incorrect

The backend returns rates as fractions. The Observability page previously displayed `1` as `1.0%` and `0.5` as `0.5%`. Its formatter now multiplies by 100 exactly once, as Evaluation already does. Tests verify 0%, 50%, and 100%.

### Evaluation eligibility, recording, and persistence had gaps

- Pending approval responses were logged as evaluations and then the same logical request was evaluated again when resumed. Pending events now remain observability events and become evaluations only after completion or failure.
- Some approval/image terminal errors and early terminal image responses did not write evaluation records. Metrics-only recording calls now cover those outcomes without changing their execution, routing, security decisions, or returned content.
- Observability and evaluation writes were inside one broad try block. A failed observability write could prevent evaluation from being attempted. Their write errors are now isolated and logged independently.
- Stored `success=false` was discarded during loading and re-inferred from an empty error string. Explicit execution failure is now preserved.
- There was no persistent logical-request identifier for evaluation deduplication. New rows store the existing request/thread ID and terminal status. Repeated writes of that ID are deduplicated, including concurrent writes within the application's existing single-process deployment.

### Metric denominators needed to distinguish missing measurements

- Execution success is separate from grounding, answer quality, and retrieval relevance.
- Missing assessments are represented as `null`, not automatically scored as failures.
- Direct, image, summary, and refusal routes do not run the RAG graders; their execution can succeed without inventing passed grounding/quality grades.
- Grounding and quality rates use only records with an actual supplied assessment. Summary fields expose those denominator counts.
- Retrieval relevance remains the arithmetic mean of per-request `relevant/retrieved` ratios, but only for requests that actually retrieved documents. A conversation with no retrieval no longer dilutes it with a fabricated zero ratio.
- Average latency and retries use finite, nonnegative recorded measurements. Empty denominators return zero safely.
- The UI shows a dash for an unassessed metric on a nonempty history, and no fabricated zero/empty-state result when loading the endpoint failed.

### The two histories are intentionally not interchangeable

Observability records operational events, including approval pauses. Evaluation records terminal request outcomes. Their counts can legitimately differ; neither dataset is copied into the other.

The archived root-level history files contain 32 evaluation rows and 47 observability rows, including 15 pending observability events. These historical files are unchanged, are not assumed to have identical eligibility/timestamps, and are not merged into session history. The latest application intentionally scopes runtime data to each browser session. Older global history has no safe ownership mapping; missing past evaluations cannot be recreated from operational events without inventing answers or grades.

The current attachment contains screenshot descriptions, not image files. The reported numbers were treated as symptoms; the fixes and tests use the actual code, stored schemas, and API behavior.

## 2. Formatting root causes and changes

The previous update already provided React Markdown, GFM, and KaTeX. However, the math plugin recognizes dollar-delimited math and fenced math; it does not automatically interpret the model's `\(...\)` / `\[...\]` output. CommonMark can interpret these backslashes as ordinary punctuation escapes, leaving visible commands or unformatted equations.

A small rendering-only helper now normalizes recognized prose presentation forms before the existing renderer:

- `\(...\)` becomes inline math and `\[...\]` becomes display math.
- Fractions, multiplication, symbols, and aligned environments are rendered by the existing KaTeX plugin.
- Recognized accidentally doubled math delimiters are handled; only known doubled TeX commands within that form are repaired.
- Escaped bold answer/result/solution labels and prose `\textbf{...}` labels become normal bold presentation.
- Standard HTML entities are handled by the Markdown parser. Only double-encoded whitespace entities receive one extra normalization step.

Code spans, fenced/indented code, URLs, explicit Markdown links, paths/regex contexts, already valid dollar-delimited math, and intentional escaped literal asterisks are protected. There is no blanket removal of backslashes or arbitrary HTML decoding. Display-style delimiters inside a table cell use inline math so they do not split the table row.

The existing renderer still disables raw HTML, keeps safe URL filtering, and runs KaTeX with `trust=false` and bounded expansion. Security modules and backend sanitization are unchanged. Exact document text continues to use its original literal display.

## 3. Exact files modified

| Existing file | Targeted change |
|---|---|
| `backend/evaluation.py` | Terminal evaluation eligibility, explicit execution outcome, request-ID deduplication, tolerant history loading, unknown assessments, valid-measurement aggregation, assessment denominator fields, and isolated clear-history behavior. |
| `backend/api.py` | Metrics recording helper and recording calls within chat/approval/image handlers only. Pending responses are excluded from evaluation; missing terminal/error writes are added. No endpoint, request schema, routing policy, security decision, upload code, or vision implementation was changed. |
| `frontend/src/app/evaluation/page.tsx` | Use shared API hostname; display genuine data/error/empty states; distinguish unassessed values; explain metric denominators. Existing layout, colors, navigation, Refresh, and Clear History controls are retained. |
| `frontend/src/app/observability/page.tsx` | Correct percentage multiplication only. |
| `frontend/src/components/ResponseMarkdown.tsx` | Pass generated response text through the rendering-only normalization helper before the existing Markdown/math plugins. |
| `frontend/tests/response-markdown.test.mjs` | Extend the real-renderer tests with the requested delimiter/escaping/entity/combined cases, while keeping all previous GFM/code/security checks. |

New files:

| File | Purpose |
|---|---|
| `frontend/src/components/normalizeResponseMarkdown.ts` | Small code-aware presentation normalization utility. |
| `tests/test_evaluation_reporting.py` | Focused API/persistence/aggregation tests, including real JSONL data and session-isolation behavior. |
| `frontend/tests/evaluation-dashboard.test.mjs` | Tests the actual dashboard component, displayed values, and async Refresh/Clear handlers with deterministic hooks/network test doubles. |
| `EVALUATION_AND_FORMATTING_FIX_REPORT.md` | This report. |

A byte comparison against the previous ZIP confirmed exactly the six existing files above changed. Package manifests, dependency versions, historical records, database files/configuration, retrieval, ingestion, LangGraph/workflow files, security modules, document uploads, and image-understanding modules remain unchanged.

## 4. Tests actually executed

| Check | Actual final result |
|---|---|
| `python -m pytest -q` | **100 passed, 2 skipped**, with one installed anyio-plugin deprecation warning. The opt-in live-provider and browser suites are skipped by default. |
| `node --test tests/evaluation-dashboard.test.mjs tests/response-markdown.test.mjs` from `frontend/` | **41 passed, 0 failed, 0 skipped**: 8 dashboard tests and 33 formatting/regression tests. |
| `npm run lint` | Passed. |
| `npm run build` | Passed, including TypeScript and production page generation. |
| Python compilation of changed backend modules | Passed. |
| Scope/dependency/history byte comparison | Passed: no unrelated existing file changes. |
| Chromium visual-browser launch | Blocked by this environment's denied socket operation. No screenshot or visual-browser pass is claimed. |
| Live Groq/Gemini/Tavily calls | Not run; neither issue requires spending provider credits. |

The API tests cover actual persisted request data, the 28-request case, Refresh/new data, empty history, evaluation-only clearing, future records after clearing, provider failures, a 200 response with explicit execution failure, assessment denominators, valid latency/retry means, independent write failures, pending/resumed approvals, duplicate logical-request writes, concurrent writers, old explicit failures, malformed history, and image terminal-error recording.

Frontend tests exercise the actual component and event handlers with network/hook doubles, rather than claiming a full browser session. They verify displayed server values, shared hostname selection, percentage units, initial/empty/error states, Refresh, Clear History, and separate execution/quality states.

Formatting tests cover all requested examples, including the combined father/son answer, inline/display/aligned equations, fractions, multiplication, normal Markdown, bold, lists, GFM tables, Java/Python code, backslashes/indentation/paths/regexes, citations/links, text entities, progressive response prefixes, invalid math, and HTML/link/math injection attempts. A deliberately blocked KaTeX HTML-extension payload produces a warning while the security test passes.

## 5. Remaining limits

- Visual browser inspection is blocked here; build, actual React rendering, event-handler tests, and FastAPI persistence tests passed.
- Missing historical evaluation rows are not invented or rebuilt from observability totals. Existing unscoped history is preserved separately.
- Use one consistent frontend/backend hostname and the same browser session to see its data. The existing session ownership model is unchanged.
- The existing single-worker/in-memory HITL deployment constraint is unchanged. Evaluation JSONL duplicate-write locking covers the current process; no new multi-worker storage architecture was introduced.
- Invalid/incomplete LaTeX is not guessed into a different equation. Recognized complete forms are rendered safely; malformed input remains a safe fallback.
- No new dependencies were added. Activate the existing backend environment and run it as before.

## 6. Start and verify

Backend, from the project root:

```bash
python -m uvicorn backend.api:api --host 127.0.0.1 --port 8000
```

Frontend, in a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`, submit a question, then open Evaluation and use Refresh. The latest session's recorded outcome should appear. Use `NEXT_PUBLIC_API_URL` consistently if configuring a different backend origin.

To rerun the checks:

```bash
python -m pytest -q
cd frontend
node --test tests/evaluation-dashboard.test.mjs tests/response-markdown.test.mjs
npm run lint
npm run build
```

The complete project ZIP includes this report and retains the earlier setup guides, source files, tests, and original database. Credentials, installed dependencies, test-generated runtime data, and build output are not bundled.
