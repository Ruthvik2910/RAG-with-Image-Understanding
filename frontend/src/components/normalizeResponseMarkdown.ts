/** Normalize recognized presentation artifacts in prose; code stays opaque. */
function inlineProse(text: string): string {
  let result = "";
  for (let i = 0; i < text.length;) {
    const rest = text.slice(i);
    const link = rest.match(/^(?:https?:\/\/|www\.)[^\s<>]+|^!?\[[^\]\n]*\]\((?:\\.|[^\\\n])*?\)/);
    if (link) { result += link[0]; i += link[0].length; continue; }
    // Already valid dollar-delimited math must never be rewritten as prose.
    if (text[i] === "$" && text[i - 1] !== "\\") {
      const marker = rest.startsWith("$$") ? "$$" : "$";
      const end = text.indexOf(marker, i + marker.length);
      if (end < 0) return result + rest;
      result += text.slice(i, end + marker.length); i = end + marker.length; continue;
    }
    if (text[i] === "`") {
      const run = rest.match(/^`+/)![0];
      const tokens = /`+/g;
      tokens.lastIndex = i + run.length;
      let end: RegExpExecArray | null;
      let closing = -1;
      while ((end = tokens.exec(text))) {
        if (end[0].length === run.length) { closing = end.index + run.length; break; }
      }
      if (closing < 0) return result + rest;
      result += text.slice(i, closing); i = closing; continue;
    }
    const delimiter = rest.match(/^(\\{1,2})([([])/);
    if (delimiter && !/[\w:\/\\]/.test(text[i - 1] || "")) {
      const slashes = delimiter[1];
      const display = delimiter[2] === "[";
      const close = slashes + (display ? "]" : ")");
      const end = text.indexOf(close, i + delimiter[0].length);
      if (end >= 0) {
        let math = text.slice(i + delimiter[0].length, end);
        // Only known doubled commands inside doubled delimiters are repaired.
        // TeX row breaks and all code/path backslashes remain untouched.
        if (slashes.length === 2) math = math.replace(/\\\\(?=(?:begin|end|frac|times|textbf|text|sqrt|cdot|quad)\b)/g, "\\");
        const inTableRow = /^\s*\|/.test(text.slice(text.lastIndexOf("\n", i - 1) + 1, i));
        result += display && !inTableRow ? `\n\n$$\n${math.trim()}\n$$\n\n` : `$${math.trim()}$`;
        i = end + close.length; continue;
      }
    }
    const emphasis = rest.match(/^(\\{1,2})\*\1\*/);
    if (emphasis && text[i - 1] !== "\\") {
      const marker = emphasis[0];
      const end = text.indexOf(marker, i + marker.length);
      const label = end >= 0 ? text.slice(i + marker.length, end) : "";
      if (end >= 0 && !label.includes("\n") && /^(?:final\s+answer|answer|result|solution)\b/i.test(label)) {
        result += `**${label}**`;
        i = end + marker.length; continue;
      }
    }
    const bold = rest.match(/^\\{1,2}textbf\{([^{}\n]+)\}/);
    if (bold && !/[\w:\/\\]/.test(text[i - 1] || "")) {
      result += `**${bold[1]}**`; i += bold[0].length; continue;
    }
    // Markdown decodes normal entities. Repair only double-encoded whitespace,
    // never arbitrary HTML and never entities inside code.
    const space = rest.match(/^&amp;(#(?:x20|32|xa0|160);|nbsp;)/i);
    if (space) { result += "&" + space[1]; i += space[0].length; continue; }
    result += text[i]; i++;
  }
  return result;
}

export function normalizeResponseMarkdown(source: string): string {
  let output = "";
  let prose = "";
  let fence = "";
  let fenceLength = 0;
  const flush = () => { output += inlineProse(prose); prose = ""; };
  for (const line of source.match(/[^\n]*\n|[^\n]+$/g) || []) {
    const candidate = line.match(/^(?: {0,3}> ?)* {0,3}(`{3,}|~{3,})([^\n]*)/);
    if (fence) {
      output += line;
      if (candidate && candidate[1][0] === fence && candidate[1].length >= fenceLength && !candidate[2].trim()) fence = "";
    } else if (candidate) {
      flush(); fence = candidate[1][0]; fenceLength = candidate[1].length; output += line;
    } else if (/^(?: {4}|\t)/.test(line)) {
      flush(); output += line;
    } else {
      prose += line;
    }
  }
  flush();
  return output;
}
