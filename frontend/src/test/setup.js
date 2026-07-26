// Test environment shims.
//
// jsdom implements the DOM, not the platform APIs around it. Two of those
// matter here, and both are supplied by Node itself rather than stubbed, so the
// tests exercise the real implementation the browser would use.

import { webcrypto } from "node:crypto";

// jsdom ships no Web Crypto. The e2ee module is built entirely on crypto.subtle,
// so without this every crypto test would be testing a mock instead of the
// algorithms the app actually relies on. Node's webcrypto is the same WebCrypto
// standard the browser exposes.
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, "crypto", {
    value: webcrypto,
    configurable: true,
  });
}

// jsdom has no localStorage in some configurations, and modules read it at
// import time. A plain in-memory implementation keeps that deterministic and
// lets a test start from a known-empty store.
if (typeof globalThis.localStorage === "undefined") {
  const store = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
      clear: () => store.clear(),
      key: (i) => [...store.keys()][i] ?? null,
      get length() {
        return store.size;
      },
    },
  });
}
