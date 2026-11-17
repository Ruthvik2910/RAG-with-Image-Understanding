import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import styles from "./ResponseMarkdown.module.css";
import { normalizeResponseMarkdown } from "./normalizeResponseMarkdown";

const components: Components = {
  // The wrapper, rather than the page, scrolls when a table has many columns.
  table: ({ children }) => (
    <div className={styles.tableScroll} role="region" aria-label="Response table" tabIndex={0}>
      <table>{children}</table>
    </div>
  ),
  th: ({ children, style }) => <th scope="col" style={style}>{children}</th>,
  a: ({ children, href, title }) => (
    <a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>
  ),
};

/** Parse the current complete text on every render, including streamed prefixes.
 * Do not strip pipes, escapes, newlines, or incomplete rows. The Markdown parser
 * handles them and reinterprets a partial table as further text arrives.
 */
export default function ResponseMarkdown({ children }: { children: string }) {
  return (
    <div className={styles.response}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { trust: false, strict: "warn", maxExpand: 1000 }]]}
        components={components}
        skipHtml
      >
        {normalizeResponseMarkdown(children)}
      </ReactMarkdown>
    </div>
  );
}
