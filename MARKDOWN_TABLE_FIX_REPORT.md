# Markdown table rendering fix

## Root cause traced through the actual response flow

The active flow is:

1. `backend/integrations.py` returns generated completion text.
2. `backend/orchestration.py` applies the existing output-security sanitizer.
3. `backend/api.py` places the answer string in the JSON response without escaping or rewriting Markdown table pipes/separator rows.
4. The frontend reads `res.json()` and `getAnswer()` selects `answer`, `generation`, or `response`.
5. Previously, `frontend/src/app/page.tsx` rendered that string as `{getAnswer()}` inside a `whitespace-pre-wrap` div. This creates a React text node, not a Markdown syntax tree. The project had no installed Markdown/GFM renderer or table styles.

The cause is therefore missing Markdown parsing in the chat display, rather than backend table mutation. No backend changes were needed or made. The supplied API returns completed JSON responses and does not currently implement a network streaming endpoint.

The attached text file also contains an older evaluation-dashboard request. This update follows the latest message's explicit table-only scope; evaluation and observability have not been edited.

## Exact files changed or added

Existing files modified:

| File | Targeted change |
|---|---|
| `frontend/src/app/page.tsx` | Import/use the shared renderer for generated chat answers, including the answer/generation/response fallback and optional analysis text. Preserve literal display for the exact full-document route. |
| `frontend/package.json` | Add the missing Markdown/GFM/math rendering dependencies, pinned to exact versions. Existing framework versions/scripts are unchanged. |
| `frontend/package-lock.json` | Lock the required rendering dependencies for `npm ci`. |

New files:

| File | Purpose |
|---|---|
| `frontend/src/components/ResponseMarkdown.tsx` | Shared safe React Markdown renderer, GFM tables, math, accessible scroll wrapper, table header scopes, and safe links. |
| `frontend/src/components/ResponseMarkdown.module.css` | Scoped dark-theme table borders, header background, spacing, alternating rows, wrapping, horizontal scrolling, and standard Markdown/code/math styles. |
| `frontend/tests/response-markdown.test.mjs` | Focused rendering regression tests using existing TypeScript, React, and Node test tools. No additional test dependency is required. |
| `MARKDOWN_TABLE_FIX_REPORT.md` | This report. |

A byte comparison against the previous complete project ZIP confirmed that all other existing files are unchanged, including every backend file, dashboard page, security workflow, LangGraph file, ingestion/retrieval module, upload handler, image-understanding module, and database. No interface layout or navigation was redesigned.

## Implementation

The renderer uses:

- `react-markdown@10.1.0` for actual Markdown rendering.
- `remark-gfm@4.0.1` for generic GFM tables, alignment markers, and other GFM syntax.
- `remark-math@6.0.0`, `rehype-katex@7.0.1`, and `katex@0.16.47` for math expressions inside and outside cells, including standard `$...$`, `$$...$$`, and fenced math. The stylesheet and renderer use the same deduplicated KaTeX version.

These dependencies were necessary because no Markdown/table/math renderer existed in the supplied application. No second competing renderer, HTML execution library, syntax-highlighting package, or new test runner was installed.

There is no table-specific text replacement, sample-specific rule, pipe splitting, line-break rewriting, or blanket backslash removal. Valid source Markdown reaches the parser intact. Escaped pipes are handled by the GFM parser. Java/Python code fences keep their text, indentation, language class, special characters, and backslashes.

Generated answers from all existing routes use the same renderer. Exact document display is deliberately still literal, since uploaded file text must not be reinterpreted or have its formatting changed.

### Table appearance and accessibility

- Semantic `<table>`, `<thead>`, `<tbody>`, `<th scope="col">`, and `<td>` elements.
- Left/center/right alignment from GFM colon markers is preserved.
- Distinct slate header rows, cell borders, padding, and alternating body rows match the existing dark palette.
- Long text can wrap, while tables with many columns scroll inside a bounded horizontal container.
- The scroll container has a region label and keyboard focus so keyboard users can reach wide content.
- CSS is scoped to generated responses using a CSS Module; dashboard and app-wide styling are unchanged.

### Security

Raw HTML is disabled with `skipHtml`. No `dangerouslySetInnerHTML`, `rehype-raw`, or executable HTML injection is used. React Markdown's default URL filtering remains enabled; links use `noopener noreferrer`. KaTeX runs with `trust=false` and bounded macro expansion. Existing backend sanitization/security code is byte-for-byte unchanged.

### Streaming

The renderer accepts the accumulated response string and reparses it on each render. Incomplete prefixes remain renderable; once valid header/separator/body syntax arrives, it becomes a table. Rows and formatting tokens are never stripped or guessed.

Tests render the actual component at every character boundary of a generated table, including incomplete separators, backticks, bold markers, and rows. They then verify the final table has the expected complete structure and that a later new answer retains no old table state.

This verifies progressive **renderer input**, not end-to-end network streaming. The current application is non-streaming; adding an API streaming transport would exceed this table-rendering-only request and was not done.

## Tests and actual results

Run focused tests from `frontend/`:

```bash
node --test tests/response-markdown.test.mjs
```

**Result: 24 tests passed, 0 failed, 0 skipped.**

The tests use the real TSX component and plugins, compiled with the project's existing TypeScript compiler and rendered with React's server renderer. Only stylesheet imports are replaced in the Node harness, since Node does not load CSS Modules. Stylesheet integration is checked by the Next.js production build, while focused CSS assertions check scroll/border/padding/alignment rules.

Covered cases:

1. Supplied simple ArrayList/LinkedList comparison table.
2. Supplied table with bold text and inline code.
3. Supplied emoji table.
4. Supplied long-description table.
5. Progressive text delivery at every character boundary and complete final rendering.
6. Generic tables with 2, 4, 6, and 12 columns and multiple rows.
7. Left/center/right alignment, italic text, numbered values, and percentages.
8. Escaped pipes and inline code containing pipes.
9. Mathematical fractions inside table cells, with literal math notation preserved in code.
10. Normal paragraphs, headings, bold, italic, bullet/numbered lists, blockquotes, inline code, links, source references, and safely decoded text entities.
11. Java and Python fenced code, indentation, backslashes, pipes, and angle brackets.
12. Table syntax inside a fenced example remains code rather than becoming a live table.
13. Inline/display math alongside a table and malformed math that does not crash the surrounding response.
14. CRLF, multiple independent tables, and incomplete separators.
15. Raw HTML/script/iframe/event-handler payloads, unsafe link schemes, and untrusted math HTML/URL commands.
16. Long unbroken cell content, accented text, Greek letters, copyright symbols, percentages, and ampersands.
17. Scoped responsive CSS and literal exact-document display.

Additional checks:

| Check | Result |
|---|---|
| `npm ci --no-audit --no-fund` | Passed from the updated lockfile. |
| `npm run lint` | Passed. |
| `npm run build` | Passed, including TypeScript and production page generation. |
| Existing-file byte comparison | Only the three existing files listed above changed. |
| Browser desktop/mobile visual inspection | Blocked: installed Chromium cannot start because this environment denies its required socket operation. No visual-browser pass is claimed. |
| Live model/API calls | Not required or run; rendering tests use fixed response text and do not alter backend behavior. |

A security test intentionally supplies a disallowed KaTeX HTML-extension command; KaTeX emits a warning and the test confirms it does not create the requested HTML attributes. All tests pass with the security restriction intact.

## Run the updated project

After replacing your project folder, reinstall the frontend dependencies:

```bash
cd frontend
npm ci
npm run dev
```

For a production build:

```bash
npm run build
npm start
```

Backend startup remains exactly the same, from the project root with your existing environment:

```bash
python -m uvicorn backend.api:api --host 127.0.0.1 --port 8000
```

The complete ZIP retains the prior source, setup guides, tests, and original database. Installed dependencies, credentials, and generated build artifacts are not bundled.
