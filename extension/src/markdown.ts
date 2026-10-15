// A small, safe renderer for the light markdown the Tutor writes. It builds DOM nodes from text
// nodes only (no HTML from the model), and tolerates text that is still arriving.

const FENCE_OPEN = /^\s*```\s*([\w+#.-]*)\s*$/;
const FENCE_CLOSE = /^\s*```\s*$/;
const ITEM = /^\s*(?:[-*]|\d+\.)\s+(.*)$/;
const ORDERED = /^\s*\d+\./;
const INLINE = /`([^`\n]+)`|\*\*([^*\n]+)\*\*/g;

type Line = { text: string; start: number };

function element(tag: string, parent: Node): HTMLElement {
  const node = document.createElement(tag);
  parent.appendChild(node);
  return node;
}

// Text at or after `freshFrom` goes into one fading span per word (the same words the pacer reveals).
function addText(parent: Node, text: string, start: number, freshFrom: number | undefined): void {
  const cut = freshFrom === undefined ? text.length : Math.min(text.length, Math.max(0, freshFrom - start));
  if (cut > 0) parent.appendChild(document.createTextNode(text.slice(0, cut)));
  for (const piece of text.slice(cut).matchAll(/\s+|\S+\s*/g)) {
    if (/^\s/.test(piece[0])) { parent.appendChild(document.createTextNode(piece[0])); continue; }
    const span = element("span", parent);
    span.className = "stream-word";
    span.dataset.from = String(start + cut + piece.index);
    span.textContent = piece[0];
  }
}

function addInline(parent: Node, text: string, start: number, freshFrom: number | undefined): void {
  let done = 0;
  for (const match of text.matchAll(INLINE)) {
    addText(parent, text.slice(done, match.index), start + done, freshFrom);
    if (match[1] !== undefined) addText(element("code", parent), match[1], start + match.index + 1, freshFrom);
    else addInline(element("strong", parent), match[2], start + match.index + 2, freshFrom);
    done = match.index + match[0].length;
  }
  addText(parent, text.slice(done), start + done, freshFrom);
}

function addCodeBlock(out: Node, lines: Line[], at: number, freshFrom: number | undefined): number {
  const language = FENCE_OPEN.exec(lines[at].text)![1];
  let end = at + 1;
  while (end < lines.length && !FENCE_CLOSE.test(lines[end].text)) end++;
  const closed = end < lines.length;
  const body = lines.slice(at + 1, end);
  // The empty piece after a trailing newline is not a code line while the block is still open.
  if (!closed && body.length && body[body.length - 1].text === "") body.pop();
  const code = element("code", element("pre", out));
  if (language) code.className = `language-${language}`;
  if (body.length) addText(code, body.map(line => line.text).join("\n"), body[0].start, freshFrom);
  return closed ? end + 1 : end;
}

function startsBlock(text: string): boolean { return !text.trim() || FENCE_OPEN.test(text) || ITEM.test(text); }

export function renderMarkdown(source: string, freshFrom?: number): DocumentFragment {
  const lines: Line[] = [];
  let offset = 0;
  for (const text of source.split("\n")) { lines.push({ text, start: offset }); offset += text.length + 1; }
  const out = document.createDocumentFragment();
  let at = 0;
  while (at < lines.length) {
    const line = lines[at];
    if (!line.text.trim()) { at++; continue; }
    if (FENCE_OPEN.test(line.text)) { at = addCodeBlock(out, lines, at, freshFrom); continue; }
    if (ITEM.test(line.text)) {
      const list = element(ORDERED.test(line.text) ? "ol" : "ul", out);
      for (; at < lines.length && ITEM.test(lines[at].text); at++) {
        const text = ITEM.exec(lines[at].text)![1];
        addInline(element("li", list), text, lines[at].start + lines[at].text.length - text.length, freshFrom);
      }
      continue;
    }
    const paragraph = element("p", out);
    for (let first = true; at < lines.length && (first || !startsBlock(lines[at].text)); at++, first = false) {
      if (!first) element("br", paragraph);
      addInline(paragraph, lines[at].text, lines[at].start, freshFrom);
    }
  }
  return out;
}
