// No extra test dependencies: compile the real TSX with the existing TypeScript
// compiler, replace only stylesheet imports (Node has no CSS loader), and render
// the component with the same React server renderer used by Next.js.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile, writeFile, unlink } from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const sourceUrl = new URL("../src/components/ResponseMarkdown.tsx", import.meta.url);
const source = await readFile(sourceUrl, "utf8");
const css = await readFile(new URL("../src/components/ResponseMarkdown.module.css", import.meta.url), "utf8");
const generated = new URL(`./.response-render-${process.pid}.mjs`, import.meta.url);
const normalizerFile = new URL(`./.response-normalize-${process.pid}.mjs`, import.meta.url);
const normalizerSource = await readFile(new URL("../src/components/normalizeResponseMarkdown.ts", import.meta.url), "utf8");
await writeFile(normalizerFile, ts.transpileModule(normalizerSource, { compilerOptions: {
  target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext,
} }).outputText);
const loadable = source.replace('"./normalizeResponseMarkdown"', JSON.stringify(normalizerFile.href))
  .replace(/^import "katex\/dist\/katex.min.css";\r?\n/m, "")
  .replace(/^import styles from ".\/ResponseMarkdown.module.css";/m,
    'const styles = { response: "response", tableScroll: "tableScroll" };');
await writeFile(generated, ts.transpileModule(loadable, { compilerOptions: {
  target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX,
} }).outputText);
let ResponseMarkdown;
try { ResponseMarkdown = (await import(generated.href)).default; }
finally { await unlink(generated); await unlink(normalizerFile); }
const render = (text) => renderToStaticMarkup(createElement(ResponseMarkdown, null, text));
const count = (html, tag) => (html.match(new RegExp(`<${tag}(?:>|\\s)`, "g")) || []).length;

const examples = [
  ["simple comparison", `| Feature | ArrayList | LinkedList |
| --- | --- | --- |
| Random access | Fast | Slow |
| Insertion at beginning | Slow | Fast |`],
  ["bold and code", `| Method | Time Complexity | Description |
| --- | --- | --- |
| \`get(index)\` | **O(1)** | Fast access |
| \`removeFirst()\` | **O(1)** | Removes first element |`],
  ["emojis", `| Situation | ArrayList | LinkedList |
| --- | --- | --- |
| Fast access | ✅ | ❌ |
| Queue operations | ❌ | ✅ |`],
  ["long descriptions", `| Data Structure | Advantages | Disadvantages |
| --- | --- | --- |
| ArrayList | Fast random access and better cache locality | Insertion at the beginning requires shifting elements |
| LinkedList | Efficient insertion and removal at both ends | Extra memory overhead for nodes |`],
];
for (const [name, markdown] of examples) {
  test(`requested table: ${name}`, () => {
    const html = render(markdown);
    assert.equal(count(html, "table"), 1);
    assert.equal(count(html, "thead"), 1);
    assert.equal(count(html, "th"), 3);
    assert.equal(count(html, "tr"), 3);
    assert.equal(count(html, "td"), 6);
    assert.ok(html.includes('role="region" aria-label="Response table" tabindex="0"'));
    assert.equal((html.match(/scope="col"/g) || []).length, 3);
    if (name === "bold and code") {
      assert.ok(html.includes("<code>get(index)</code>"));
      assert.ok(html.includes("<strong>O(1)</strong>"));
    }
    if (name === "emojis") assert.ok(html.includes("✅") && html.includes("❌"));
    if (name === "long descriptions") assert.ok(html.includes("Insertion at the beginning requires shifting elements"));
  });
}

for (const columns of [2, 4, 6, 12]) {
  test(`generic table with ${columns} columns and multiple rows`, () => {
    const row = (prefix) => "| " + Array.from({ length: columns }, (_, i) => `${prefix}${i}`).join(" | ") + " |";
    const markdown = [row("Header"), "| " + Array(columns).fill("---").join(" | ") + " |", row("A"), row("B"), row("C")].join("\n");
    const html = render(markdown);
    assert.equal(count(html, "th"), columns);
    assert.equal(count(html, "td"), columns * 3);
  });
}

test("alignment markers, percentages, numbers, and italics", () => {
  const html = render("| Left | Middle | Right |\n| :--- | :---: | ---: |\n| *ready* | 95.5% | 1,234.50 |");
  assert.equal((html.match(/text-align:left/g) || []).length, 2);
  assert.equal((html.match(/text-align:center/g) || []).length, 2);
  assert.equal((html.match(/text-align:right/g) || []).length, 2);
  assert.ok(html.includes("<em>ready</em>") && html.includes("95.5%") && html.includes("1,234.50"));
});

test("escaped pipes and inline code remain valid GFM", () => {
  const html = render("| Pattern | Text |\n| --- | --- |\n| `a\\|b` | left \\| right |");
  assert.equal(count(html, "td"), 2);
  assert.ok(html.includes("<code>a|b</code>") && html.includes("left | right"));
});

test("math in cells renders as KaTeX, without treating inline code as math", () => {
  const html = render("| Formula | Literal |\n| --- | --- |\n| $\\frac{a}{b}$ | `$x$` |");
  assert.ok(html.includes('class="katex"') && html.includes("<math"));
  assert.ok(html.includes("<mfrac>"));
  assert.ok(html.includes("<code>$x$</code>"));
  assert.equal(count(html, "td"), 2);
});

test("paragraphs, headings, emphasis, lists, blockquotes, links, citations, and entities", () => {
  const markdown = "Hello&#x20;world.\n\n## Heading\n\n**Bold** and *italic* with `inline`.\n\n- First\n- Second\n\n1. Step one\n2. Step two\n\n> Quoted evidence\n\n[Source 1](https://example.org/docs?q=1&v=2) and [2].";
  const html = render(markdown);
  for (const tag of ["p", "h2", "strong", "em", "ul", "ol", "blockquote", "a"]) assert.ok(count(html, tag) >= 1, tag);
  assert.ok(html.includes("Hello world.") && html.includes("<code>inline</code>"));
  assert.ok(html.includes('href="https://example.org/docs?q=1&amp;v=2"'));
  assert.ok(html.includes('rel="noopener noreferrer"') && html.includes("and [2]."));
});

for (const language of ["java", "python"]) {
  test(`${language} code keeps indentation, backslashes, pipes, and angle brackets`, () => {
    const code = language === "java"
      ? 'if (a < b) {\n\tSystem.out.println("C:\\\\temp | **literal**");\n}'
      : 'pattern = r"\\d+\\|\\w+"\nif x < 5:\n    print("**not bold**")';
    const html = render(`\`\`\`${language}\n${code}\n\`\`\``);
    assert.ok(html.includes(`<pre><code class="language-${language}">`));
    assert.ok(html.includes("&lt;") && html.includes("|"));
    assert.equal(count(html, "strong"), 0);
    assert.ok(language === "java" ? html.includes("\tSystem") : html.includes("    print"));
  });
}

test("fenced table examples are code, never accidentally converted to tables", () => {
  const html = render("```markdown\n" + examples[0][1] + "\n```");
  assert.equal(count(html, "table"), 0);
  assert.equal(count(html, "pre"), 1);
});

test("inline and display mathematics still render alongside a table", () => {
  const html = render("Inline $x^2$\n\n$$\nx = \\frac{1}{2}\\times 40\n$$\n\n" + examples[0][1]);
  assert.ok(html.includes('class="katex-display"'));
  assert.ok((html.match(/class="katex"/g) || []).length >= 2);
  assert.equal(count(html, "table"), 1);
});

test("malformed math does not crash or suppress surrounding tables", () => {
  assert.doesNotThrow(() => render("$\\undefinedCommand$\n\n" + examples[0][1]));
  assert.equal(count(render("$\\undefinedCommand$\n\n" + examples[0][1]), "table"), 1);
});

test("progressive streaming prefixes are reparsed safely, then finish as one complete table", () => {
  const markdown = examples[1][1];
  // Test every character boundary, including partial separator/code/formatting tokens.
  for (let length = 0; length <= markdown.length; length++) {
    assert.doesNotThrow(() => render(markdown.slice(0, length)), `prefix length ${length}`);
  }
  const partial = render(markdown.slice(0, markdown.indexOf("| `removeFirst")));
  assert.equal(count(partial, "table"), 1);
  assert.equal(count(partial, "td"), 3);
  const complete = render(markdown);
  assert.equal(count(complete, "table"), 1);
  assert.equal(count(complete, "td"), 6);
  assert.ok(complete.includes("<code>removeFirst()</code>"));
  // Restarting with a new answer must not retain an old table or parser state.
  assert.equal(count(render("A new plain answer."), "table"), 0);
});

test("CRLF, multiple independent tables, and incomplete separators are safe", () => {
  const html = render(examples[0][1].replaceAll("\n", "\r\n") + "\n\nParagraph.\n\n" + examples[2][1]);
  assert.equal(count(html, "table"), 2);
  assert.doesNotThrow(() => render("| A | B |\n| --"));
});

test("raw HTML, script tags, event handlers, and unsafe links cannot execute", () => {
  const markdown = '<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29)\n\n[also bad](data:text/html;base64,AAAA)\n\n| Cell | Safe |\n| --- | --- |\n| <iframe src="https://example.org"></iframe> | ✅ |';
  const html = render(markdown);
  assert.doesNotMatch(html, /<(?:script|iframe|img)(?:>|\s)/i);
  assert.doesNotMatch(html, /\sonerror=/i);
  assert.doesNotMatch(html, /href="(?:javascript:|data:)/i);
  assert.equal(count(html, "table"), 1);
});

test("untrusted math cannot introduce HTML attributes or dangerous URLs", () => {
  const html = render('$\\href{javascript:alert(1)}{click}$ and $\\htmlClass{injected}{x}$');
  assert.doesNotMatch(html, /href="javascript:/i);
  assert.doesNotMatch(html, /class="injected"/i);
});

test("table CSS is scoped, scrollable, bordered, padded, and does not override alignment", () => {
  assert.match(css, /\.tableScroll\s*\{[^}]*overflow-x:\s*auto/s);
  assert.match(css, /\.tableScroll\s*\{[^}]*max-width:\s*100%/s);
  assert.match(css, /\.tableScroll table/); // table selector is scoped
  assert.match(css, /border-collapse:\s*collapse/);
  assert.match(css, /\.tableScroll :is\(th, td\)\s*\{[^}]*padding:/s);
  assert.doesNotMatch(css, /\.tableScroll (?:th|td|:is\(th, td\))\s*\{[^}]*text-align:/s);
  assert.match(source, /skipHtml/);
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|rehypeRaw/);
});

test("only generated responses use Markdown; exact document text keeps its raw display", async () => {
  const page = await readFile(new URL("../src/app/page.tsx", import.meta.url), "utf8");
  assert.ok(page.includes('response.route === "full" ?'));
  assert.ok(page.includes('<ResponseMarkdown>{getAnswer()}</ResponseMarkdown>'));
  assert.ok(page.includes('<ResponseMarkdown>{response.image_analysis}</ResponseMarkdown>'));
});


test("long unbroken text and special characters are preserved without splitting cells", () => {
  const long = "LongCell".repeat(100);
  const html = render(`| Text | Value |\n| --- | --- |\n| ${long} | café — αβ © 100% & more |`);
  assert.equal(count(html, "td"), 2);
  assert.ok(html.includes(long) && html.includes("café — αβ © 100% &amp; more"));
});

test("requested inline and display LaTeX delimiters render equations", () => {
  const html = render(String.raw`Let \(x\) be years. Therefore \[40+x=2\,(10+x)\] and \(60=2\times30\).`);
  assert.ok((html.match(/class="katex"/g) || []).length >= 3);
  assert.ok(html.includes('class="katex-display"'));
});

test("aligned fractions and doubled serialized delimiters render without raw commands", () => {
  const html = render(String.raw`\[\begin{aligned}40+x&=2(10+x)\\20&=x\end{aligned}\]`);
  assert.ok(html.includes('class="katex-display"') && html.includes('mtable'));
  assert.ok(render(String.raw`The answer is \\(x=20\\).`).includes('class="katex"'));
  assert.ok(render(String.raw`\(\frac{1}{2}\times40\)`).includes('<mfrac>'));
});

test("escaped answer labels and prose textbf are presented as bold", () => {
  const html = render(String.raw`\*\*Answer:\*\* 20 years. \textbf{Final Answer}`);
  assert.ok(html.includes('<strong>Answer:</strong>'));
  assert.ok(html.includes('<strong>Final Answer</strong>'));
});

test("normal and double-encoded whitespace entities become safe readable text", () => {
  const html = render('Hello&#x20;world. Again&amp;#x20;here &amp; there.');
  assert.ok(html.includes('Hello world. Again here &amp; there.'));
  const code = render('`&#x20; &amp;#x20;`');
  assert.ok(code.includes('<code>&amp;#x20; &amp;amp;#x20;</code>'));
});

test("code, paths, URLs, and regexes survive prose normalization", () => {
  const code = String.raw`C:\Users\surya\(archive\) /\(x\)/ \*\*literal\*\*`;
  const html = render('```java\n' + code + '\n```\n\n' + '`' + code + '`');
  assert.equal((html.match(/class="katex"/g) || []).length, 0);
  assert.ok(html.includes('C:\\Users'));
  const prose = render(String.raw`C:\(archive\) /home/\(file\) https://example.org/\(x\)`);
  assert.equal((prose.match(/class="katex"/g) || []).length, 0);
});

test("combined father/son explanation renders math, lists, and final bold answer", () => {
  const html = render(String.raw`Let \(x\) be years from now.

1. Father: \(40+x\).
2. Son: \(10+x\).

\[40+x=2(10+x)\]

\[20=x\]

**Final Answer: 20 years.**`);
  assert.equal(count(html, 'ol'), 1);
  assert.ok(html.includes('<strong>Final Answer: 20 years.</strong>'));
  assert.ok((html.match(/class="katex"/g) || []).length >= 5);
});

test("existing dollar math keeps its TeX commands and row breaks unchanged", () => {
  const html = render(String.raw`$\textbf{Answer}$ and $\frac{1}{2}$`);
  assert.equal((html.match(/class="katex"/g) || []).length, 2);
  assert.ok(html.includes('mathvariant="bold"'));
  assert.ok(!html.includes('**Answer**'));
});

test("LaTeX delimiters inside table cells do not break table rows", () => {
  const html = render(String.raw`| Formula | Value |
| --- | --- |
| \[x+2\] | \(20\) |`);
  assert.equal(count(html,'table'),1);
  assert.equal(count(html,'td'),2);
  assert.equal((html.match(/class="katex"/g) || []).length,2);
});


test("intentional escaped literal asterisks in prose are not blindly removed", () => {
  const html = render(String.raw`These are literal: \*\*example\*\*.`);
  assert.ok(html.includes('**example**'));
  assert.equal(count(html,'strong'),0);
});
