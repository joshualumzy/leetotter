import { beforeEach, describe, expect, it } from "vitest";
import { JSDOM } from "jsdom";
import { renderMarkdown } from "./markdown";

beforeEach(() => {
  const dom = new JSDOM("");
  (globalThis as any).document = dom.window.document;
});

function html(source: string, freshFrom?: number): string {
  const box = document.createElement("div");
  box.append(renderMarkdown(source, freshFrom));
  return box.innerHTML;
}

describe("markdown renderer", () => {
  it("makes paragraphs from blank lines and keeps single newlines as line breaks", () => {
    expect(html("One\ntwo\n\nThree")).toBe("<p>One<br>two</p><p>Three</p>");
  });
  it("renders inline code and bold, and leaves a lone star alone", () => {
    expect(html("Use `set()` and **hash** a * b")).toBe("<p>Use <code>set()</code> and <strong>hash</strong> a * b</p>");
  });
  it("renders bullet and numbered lists", () => {
    expect(html("- one\n* two `x`")).toBe("<ul><li>one</li><li>two <code>x</code></li></ul>");
    expect(html("Steps:\n1. first\n2. second")).toBe("<p>Steps:</p><ol><li>first</li><li>second</li></ol>");
  });
  it("keeps code block newlines and indentation exactly, with the language as a class", () => {
    const code = "class Node:\n    def __init__(self):\n        self.kids = {}\n";
    expect(html("Try:\n```python\n" + code + "```\nDone")).toBe(
      `<p>Try:</p><pre><code class="language-python">class Node:\n    def __init__(self):\n        self.kids = {}</code></pre><p>Done</p>`,
    );
  });
  it("does not interpret markdown inside a code block", () => {
    expect(html("```\n- a **b** `c`\n\nd\n```")).toBe("<pre><code>- a **b** `c`\n\nd</code></pre>");
  });
  it("shows an unclosed fence as an open code block", () => {
    expect(html("```py\nx = 1\n")).toBe(`<pre><code class="language-py">x = 1</code></pre>`);
    expect(html("```")).toBe("<pre><code></code></pre>");
  });
  it("shows an unclosed backtick or bold marker literally", () => {
    expect(html("use `foo and **bar")).toBe("<p>use `foo and **bar</p>");
    expect(html("``")).toBe("<p>``</p>");
  });
  it("shows hostile text as text", () => {
    expect(html("<script>alert(1)</script> <img src=x onerror=alert(1)> [a](http://x)")).toBe(
      "<p>&lt;script&gt;alert(1)&lt;/script&gt; &lt;img src=x onerror=alert(1)&gt; [a](http://x)</p>",
    );
    expect(html("```\n<img onerror=x>\n```")).toBe("<pre><code>&lt;img onerror=x&gt;</code></pre>");
    expect(html("- <b>x</b>")).toBe("<ul><li>&lt;b&gt;x&lt;/b&gt;</li></ul>");
  });
  it("keeps a very long line whole", () => {
    const long = "x".repeat(5000);
    expect(html("```\n" + long + "\n```")).toBe(`<pre><code>${long}</code></pre>`);
    expect(html(long)).toBe(`<p>${long}</p>`);
  });
  it("renders nothing for empty text", () => {
    expect(html("")).toBe("");
    expect(html("\n\n")).toBe("");
  });

  describe("freshFrom", () => {
    it("wraps each word at or after the offset in a fading span, wherever it lands", () => {
      expect(html("ab cd\n\n- ef `gh`", 3)).toBe(
        `<p>ab <span class="stream-word" data-from="3">cd</span></p>` +
        `<ul><li><span class="stream-word" data-from="9">ef </span><code><span class="stream-word" data-from="13">gh</span></code></li></ul>`,
      );
    });
    it("fades inside a code block and keeps its whitespace", () => {
      expect(html("```\na\n  b c\n```", 6)).toBe(
        `<pre><code>a\n  <span class="stream-word" data-from="8">b </span><span class="stream-word" data-from="10">c</span></code></pre>`,
      );
    });
    it("wraps nothing when the offset is past the end, and a late offset splits a word run only at word starts", () => {
      expect(html("ab cd", 99)).toBe("<p>ab cd</p>");
      expect(html("ab cd", 0)).toBe(`<p><span class="stream-word" data-from="0">ab </span><span class="stream-word" data-from="3">cd</span></p>`);
    });
    it("has the same text content as the plain render", () => {
      const source = "Try `a`.\n\n```py\n  x = 1\n```\n- **b** c";
      const box = document.createElement("div");
      box.append(renderMarkdown(source, 4));
      const plain = document.createElement("div");
      plain.append(renderMarkdown(source));
      expect(box.textContent).toBe(plain.textContent);
    });
  });
});
