// End-to-end encryption for direct messages, built on the Web Crypto API only
// (no dependencies).
//
// Scheme: each user holds an ECDH P-256 keypair. For a 1:1 DM both sides derive
// the SAME shared secret (ECDH is symmetric: A's private + B's public equals B's
// private + A's public), so a single AES-256-GCM ciphertext serves both — no
// per-recipient copies. The private key is wrapped with a PBKDF2-derived key
// and only ever leaves the browser in that wrapped form; the server stores an
// opaque blob and cannot unwrap it.
//
// P-256 is used rather than X25519 because crypto.subtle supports it in every
// current browser.

const PBKDF2_ITERS = 300000;
const CACHE_KEY = "relay_e2ee_priv";

const enc = new TextEncoder();
const dec = new TextDecoder();

const b64 = (buf) =>
  btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = (s) =>
  Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

// --- key generation / import / export -------------------------------------

export async function generateKeyPair() {
  return crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, [
    "deriveKey",
    "deriveBits",
  ]);
}

export async function exportPublicKey(publicKey) {
  return b64(await crypto.subtle.exportKey("spki", publicKey));
}

export async function importPublicKey(publicKeyB64) {
  return crypto.subtle.importKey(
    "spki",
    unb64(publicKeyB64),
    { name: "ECDH", namedCurve: "P-256" },
    true,
    []
  );
}

// --- passphrase wrapping ---------------------------------------------------

async function passphraseKey(passphrase, salt) {
  const base = await crypto.subtle.importKey(
    "raw",
    enc.encode(passphrase),
    "PBKDF2",
    false,
    ["deriveKey"]
  );
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: PBKDF2_ITERS, hash: "SHA-256" },
    base,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"]
  );
}

/** Wrap a private key for storage. Returns base64 parts for the server. */
export async function wrapPrivateKey(privateKey, passphrase) {
  const pkcs8 = await crypto.subtle.exportKey("pkcs8", privateKey);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await passphraseKey(passphrase, salt);
  const wrapped = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, pkcs8);
  return {
    wrapped_private_key: b64(wrapped),
    salt: b64(salt),
    iv: b64(iv),
  };
}

/** Unwrap a stored private key. Throws if the passphrase is wrong. */
export async function unwrapPrivateKey(bundle, passphrase) {
  const key = await passphraseKey(passphrase, unb64(bundle.salt));
  let pkcs8;
  try {
    pkcs8 = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: unb64(bundle.iv) },
      key,
      unb64(bundle.wrapped_private_key)
    );
  } catch {
    // AES-GCM authentication failed — effectively always a bad passphrase.
    throw new Error("Wrong passphrase");
  }
  return crypto.subtle.importKey(
    "pkcs8",
    pkcs8,
    { name: "ECDH", namedCurve: "P-256" },
    true,
    ["deriveKey", "deriveBits"]
  );
}

// --- message encryption ----------------------------------------------------

/** Derive the AES key shared with a peer from their public key. */
export async function deriveSharedKey(privateKey, peerPublicKey) {
  return crypto.subtle.deriveKey(
    { name: "ECDH", public: peerPublicKey },
    privateKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"]
  );
}

/** Encrypt to base64(iv ‖ ciphertext). */
export async function encryptMessage(sharedKey, plaintext) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    sharedKey,
    enc.encode(plaintext)
  );
  const out = new Uint8Array(iv.length + ct.byteLength);
  out.set(iv, 0);
  out.set(new Uint8Array(ct), iv.length);
  return b64(out);
}

/** Reverse of encryptMessage. Throws if the payload isn't decryptable. */
export async function decryptMessage(sharedKey, payloadB64) {
  const bytes = unb64(payloadB64);
  const iv = bytes.slice(0, 12);
  const ct = bytes.slice(12);
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv },
    sharedKey,
    ct
  );
  return dec.decode(plain);
}

// --- session cache ---------------------------------------------------------
//
// The unwrapped private key is cached in sessionStorage so a page reload
// doesn't re-prompt for the passphrase. It is per-tab and cleared when the
// browser session ends. (It is deliberately NOT localStorage, which would
// persist the raw key on disk indefinitely.)

export async function cacheUnlockedKey(privateKey) {
  try {
    const jwk = await crypto.subtle.exportKey("jwk", privateKey);
    sessionStorage.setItem(CACHE_KEY, JSON.stringify(jwk));
  } catch {
    /* caching is best-effort */
  }
}

export async function loadCachedKey() {
  const raw = sessionStorage.getItem(CACHE_KEY);
  if (!raw) return null;
  try {
    return await crypto.subtle.importKey(
      "jwk",
      JSON.parse(raw),
      { name: "ECDH", namedCurve: "P-256" },
      true,
      ["deriveKey", "deriveBits"]
    );
  } catch {
    sessionStorage.removeItem(CACHE_KEY);
    return null;
  }
}

export function clearCachedKey() {
  sessionStorage.removeItem(CACHE_KEY);
}

// --- file attachments ------------------------------------------------------
//
// Same AES-GCM envelope as messages, applied to the raw file bytes. The name
// and MIME type are encrypted separately so the server stores no hint of what
// the file is — it only ever sees an opaque blob.

/** Encrypt a File. Returns the ciphertext blob plus encrypted metadata. */
export async function encryptFile(sharedKey, file) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    sharedKey,
    await file.arrayBuffer()
  );
  const payload = new Uint8Array(iv.length + ct.byteLength);
  payload.set(iv, 0);
  payload.set(new Uint8Array(ct), iv.length);
  const meta = await encryptMessage(
    sharedKey,
    JSON.stringify({ name: file.name, type: file.type })
  );
  return { blob: new Blob([payload]), meta };
}

/** Decrypt a fetched attachment back into a blob URL plus its real name/type. */
export async function decryptFile(sharedKey, buffer, metaB64) {
  const bytes = new Uint8Array(buffer);
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: bytes.slice(0, 12) },
    sharedKey,
    bytes.slice(12)
  );
  let meta = { name: "file", type: "application/octet-stream" };
  try {
    meta = JSON.parse(await decryptMessage(sharedKey, metaB64));
  } catch {
    /* fall back to generic name/type */
  }
  const blob = new Blob([plain], { type: meta.type || "application/octet-stream" });
  return { url: URL.createObjectURL(blob), name: meta.name, type: meta.type };
}

// --- key verification (safety numbers) -------------------------------------
//
// The server hands out public keys, so in principle it could substitute its
// own and read "encrypted" DMs transparently. Comparing this fingerprint out of
// band — in person, over the phone — is the only way to rule that out. Both
// sides derive it from the same two public keys, sorted, so the value matches
// regardless of who computes it.

export async function safetyNumber(publicKeyA, publicKeyB) {
  const pair = [publicKeyA, publicKeyB].sort().join("|");
  const digest = await crypto.subtle.digest("SHA-256", enc.encode(pair));
  const bytes = new Uint8Array(digest);
  // 60 digits, grouped in fives — same shape as Signal's, and easy to read
  // aloud without losing your place.
  let out = "";
  for (let i = 0; i < 12; i++) {
    const chunk = (bytes[i * 2] << 8) | bytes[i * 2 + 1];
    out += String(chunk % 100000).padStart(5, "0");
  }
  return out.match(/.{5}/g).join(" ");
}
