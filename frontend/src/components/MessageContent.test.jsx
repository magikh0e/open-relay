// Message rendering.
//
// This is the one place message text is parsed for display, which makes it the
// app's main injection surface: the text is written by other people and, in an
// encrypted conversation, the server never saw it to sanitise it. The component
// builds React nodes rather than HTML strings, so escaping is structural, and
// these tests pin that it stays that way.
//
// The rest covers the formatting rules people actually rely on, including the
// single-line fence that shipped broken once and rendered every code block
// empty.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import MessageContent from "./MessageContent.jsx";

afterEach(cleanup);

const user = (over = {}) => ({
  id: "u1",
  username: "alice",
  display_name: "Alice",
  ...over,
});

/** Render and hand back the container, since we assert on structure a lot. */
function draw(props) {
  return render(<MessageContent {...props} />).container;
}

describe("injection safety", () => {
  it("renders markup as literal text, not as elements", () => {
    const c = draw({ content: "<script>alert('xss')</script>" });
    // The text is present verbatim and no script element was created.
    expect(c.textContent).toBe("<script>alert('xss')</script>");
    expect(c.querySelector("script")).toBeNull();
  });

  it("does not create an element from an img tag with an onerror handler", () => {
    const c = draw({ content: '<img src=x onerror="alert(1)">' });
    expect(c.querySelector("img")).toBeNull();
    expect(c.textContent).toContain("onerror");
  });

  it("keeps markup literal inside formatting too", () => {
    // Formatting wraps escaped text; it must not become a hole in the escaping.
    const c = draw({ content: "**<b>bold</b>**" });
    const strong = c.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong.textContent).toBe("<b>bold</b>");
    expect(strong.querySelector("b")).toBeNull();
  });

  it("never sets innerHTML anywhere in the output", () => {
    const c = draw({ content: "<i>x</i> `<u>y</u>` ```<s>z</s>```" });
    expect(c.querySelectorAll("i, u, s").length).toBe(0);
  });
});

describe("mentions", () => {
  it("highlights a mention the server resolved", () => {
    draw({ content: "hey @alice look", mentions: [user()], myId: "u2" });
    const btn = screen.getByRole("button", { name: "@alice" });
    expect(btn).toBeTruthy();
    expect(btn.textContent).toBe("@Alice"); // display name, not the handle
  });

  it("leaves an unresolved @name as plain text", () => {
    // Only tokens the server matched to real users are highlighted, so a
    // stranger cannot fake a mention pill by typing one.
    const c = draw({ content: "hey @nobody", mentions: [], myId: "u2" });
    expect(c.querySelector("button")).toBeNull();
    expect(c.textContent).toBe("hey @nobody");
  });

  it("marks a mention of yourself differently", () => {
    draw({ content: "@alice ping", mentions: [user()], myId: "u1" });
    expect(screen.getByRole("button", { name: "@alice" }).className).toContain(
      "mention-me"
    );
  });

  it("does not treat an email address as a mention", () => {
    const c = draw({
      content: "mail bob@alice.com please",
      mentions: [user()],
      myId: "u2",
    });
    expect(c.querySelector("button")).toBeNull();
  });
});

describe("formatting", () => {
  it.each([
    ["**bold**", "strong", "bold"],
    ["*italic*", "em", "italic"],
    ["~~struck~~", "del", "struck"],
    ["`code`", "code", "code"],
  ])("renders %s", (input, tag, text) => {
    const c = draw({ content: input });
    expect(c.querySelector(tag)?.textContent).toBe(text);
  });

  it("keeps formatting characters literal inside backticks", () => {
    // Pasted code full of asterisks must survive unchanged.
    const c = draw({ content: "`a * b ** c`" });
    expect(c.querySelector("code").textContent).toBe("a * b ** c");
    expect(c.querySelector("strong")).toBeNull();
    expect(c.querySelector("em")).toBeNull();
  });

  it("prefers bold over italic", () => {
    const c = draw({ content: "**both**" });
    expect(c.querySelector("strong")).not.toBeNull();
    expect(c.querySelector("em")).toBeNull();
  });
});

describe("code blocks", () => {
  it("renders a fenced block", () => {
    const c = draw({ content: "```\ndef hello():\n    return 1\n```" });
    expect(c.querySelector("pre.code-block code").textContent).toBe(
      "def hello():\n    return 1"
    );
  });

  it("renders a single-line fence with its content", () => {
    // This shipped broken in 1.18.0: the language-tag pattern swallowed the
    // code on a one-line fence and every block came out empty.
    const c = draw({ content: "```echo hi```" });
    expect(c.querySelector("pre.code-block code").textContent).toBe("echo hi");
  });

  it("ignores a language tag but keeps the code", () => {
    const c = draw({ content: "```python\nx = 1\n```" });
    expect(c.querySelector("pre.code-block code").textContent).toBe("x = 1");
  });

  it("does not parse mentions or formatting inside a block", () => {
    const c = draw({
      content: "```\n@alice **not bold**\n```",
      mentions: [user()],
      myId: "u2",
    });
    expect(c.querySelector("button")).toBeNull();
    expect(c.querySelector("strong")).toBeNull();
    expect(c.querySelector("code").textContent).toContain("@alice **not bold**");
  });

  it("still formats text around a block", () => {
    const c = draw({ content: "before **b**\n```\ncode\n```\nafter `c`" });
    expect(c.querySelector("strong").textContent).toBe("b");
    expect(c.querySelector("pre code").textContent).toBe("code");
    expect(c.textContent).toContain("after");
  });
});

describe("inline media", () => {
  it.each([
    ["https://media.giphy.com/media/abc/giphy.gif"],
    ["https://i.giphy.com/abc.webp"],
    ["https://i.imgur.com/abc123.png"],
  ])("embeds a trusted host: %s", (url) => {
    const img = draw({ content: url }).querySelector("img.gif-msg");
    expect(img).not.toBeNull();
    expect(img.getAttribute("referrerPolicy") ?? img.referrerPolicy).toBe("no-referrer");
  });

  it("rewrites a gifv to a gif so it actually plays", () => {
    const img = draw({ content: "https://i.imgur.com/abc123.gifv" }).querySelector("img");
    expect(img.getAttribute("src")).toBe("https://i.imgur.com/abc123.gif");
  });

  it.each([
    ["https://evil.test/x.gif"],
    ["https://notgiphy.com/a.gif"],
    ["http://i.imgur.com/abc123.png"],
    ["https://i.imgur.com.evil.test/a.png"],
  ])("does not embed an untrusted URL: %s", (url) => {
    // Anything outside the host allowlist stays text, so a message cannot make
    // the client fetch from an arbitrary host.
    const c = draw({ content: url });
    expect(c.querySelector("img")).toBeNull();
    expect(c.textContent).toBe(url);
  });
});

describe("empty input", () => {
  it("renders nothing for empty content", () => {
    expect(draw({ content: "" }).textContent).toBe("");
    expect(draw({ content: null }).textContent).toBe("");
  });
});
