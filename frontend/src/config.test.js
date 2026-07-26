// Server resolution and URL handling.
//
// This module decides which origin the client talks to and sanitises every URL
// the server hands back. Both were tightened in 1.23.2 after a review: the
// server origin can come from localStorage or an injected global, and
// attachment URLs come from the API, so neither is trustworthy input. These
// pin that a hostile value cannot reach a navigation or fetch sink.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { resolveUrl, savedServers } from "./config.js";

describe("resolveUrl", () => {
  it("passes through ordinary http(s) URLs", () => {
    expect(resolveUrl("https://example.com/a.png")).toBe("https://example.com/a.png");
    expect(resolveUrl("http://example.com/a.png")).toBe("http://example.com/a.png");
  });

  it("resolves a relative API path against the current origin", () => {
    // Attachments arrive as "/api/uploads/<id>" and must work from any client.
    const out = resolveUrl("/api/uploads/abc123");
    expect(out).toMatch(/^https?:\/\/[^/]+\/api\/uploads\/abc123$/);
  });

  it("allows blob: and data:, which the app creates itself", () => {
    // Image compression and decrypted attachments produce these locally.
    expect(resolveUrl("data:image/png;base64,iVBORw0KGgo=")).toMatch(/^data:image\/png/);
    expect(resolveUrl("blob:http://localhost/8f3a")).toMatch(/^blob:/);
  });

  it.each([
    ["javascript:alert(1)"],
    ["JavaScript:alert(1)"],
    ["  javascript:alert(1)"],
    ["vbscript:msgbox(1)"],
    ["file:///etc/passwd"],
    ["about:blank"],
  ])("refuses %s", (hostile) => {
    // Anything outside the scheme allowlist resolves to "", never to the input.
    const out = resolveUrl(hostile);
    expect(out).toBe("");
  });

  it("refuses a protocol-relative URL", () => {
    // "//evil.test/x" would silently load from another host on https pages.
    expect(resolveUrl("//evil.test/x.png")).toBe("");
  });

  it("returns falsy input unchanged rather than inventing a URL", () => {
    expect(resolveUrl("")).toBe("");
    expect(resolveUrl(null)).toBe(null);
    expect(resolveUrl(undefined)).toBe(undefined);
  });

  it("treats a bare string as a path on our own origin, not a foreign host", () => {
    // "not a url" is a legitimate relative path, so resolving it is correct.
    // What matters is where it lands: our origin, over http(s), never elsewhere.
    const out = resolveUrl("not a url");
    expect(out).toMatch(/^https?:\/\//);
    expect(new URL(out).origin).toBe(location.origin);
  });

  it("does not throw on input the URL parser rejects", () => {
    expect(resolveUrl("http://")).toBe("");
  });
});

describe("server origin resolution", () => {
  // SERVER is read once at module load, so each case needs a fresh import with
  // the environment already staged.
  async function loadWith({ injected, stored } = {}) {
    vi.resetModules();
    localStorage.clear();
    if (injected !== undefined) window.__RELAY_SERVER__ = injected;
    else delete window.__RELAY_SERVER__;
    if (stored !== undefined) localStorage.setItem("relay_server", stored);
    return import("./config.js");
  }

  beforeEach(() => localStorage.clear());
  afterEach(() => {
    delete window.__RELAY_SERVER__;
    localStorage.clear();
  });

  it("defaults to same origin when nothing is configured", async () => {
    const { SERVER, API_BASE } = await loadWith({});
    expect(SERVER).toBe("");
    expect(API_BASE).toBe("/api");
  });

  it("takes an injected origin from a native shell", async () => {
    const { SERVER } = await loadWith({ injected: "https://chat.example.com" });
    expect(SERVER).toBe("https://chat.example.com");
  });

  it("prefers the injected origin over a stored one", async () => {
    const { SERVER } = await loadWith({
      injected: "https://injected.example",
      stored: "https://stored.example",
    });
    expect(SERVER).toBe("https://injected.example");
  });

  it("strips a trailing slash so paths never double up", async () => {
    const { API_BASE } = await loadWith({ injected: "https://chat.example.com/" });
    expect(API_BASE).toBe("https://chat.example.com/api");
  });

  it.each([
    ["javascript:alert(1)"],
    ["ftp://files.example.com"],
    ["//evil.test"],
    ["evil.test"],
  ])("ignores a non-http(s) stored origin: %s", async (hostile) => {
    // A hostile localStorage value must fall back to same origin, never become
    // the API base.
    const { SERVER, API_BASE } = await loadWith({ stored: hostile });
    expect(SERVER).toBe("");
    expect(API_BASE).toBe("/api");
  });

  it("maps http to ws and https to wss", async () => {
    const secure = await loadWith({ injected: "https://chat.example.com" });
    expect(secure.wsBase()).toBe("wss://chat.example.com");
    const plain = await loadWith({ injected: "http://localhost:8000" });
    expect(plain.wsBase()).toBe("ws://localhost:8000");
  });
});

describe("savedServers", () => {
  beforeEach(() => localStorage.clear());

  it("returns an empty list when nothing is saved", () => {
    expect(savedServers()).toEqual([]);
  });

  it("survives corrupt JSON rather than throwing", () => {
    // This is read on every load of the picker; a bad value must not break it.
    localStorage.setItem("relay_servers", "{not json");
    expect(savedServers()).toEqual([]);
  });

  it("drops non-string entries", () => {
    localStorage.setItem("relay_servers", JSON.stringify(["https://a.test", 42, null]));
    expect(savedServers()).toEqual(["https://a.test"]);
  });

  it("returns an empty list when the stored value is not an array", () => {
    localStorage.setItem("relay_servers", JSON.stringify({ a: 1 }));
    expect(savedServers()).toEqual([]);
  });
});
