// Reproducible documentation screenshots. Dev-only, not part of the app or its
// build — Playwright is deliberately NOT a project dependency. To regenerate:
//
//   1. start the dev stack (see README) so the app is on :5173 and API on :8000
//   2. python backend/demo_setup.py          # seed the demo conversation
//   3. npm i -D playwright && npx playwright install chromium
//   4. node capture-screenshots.mjs
import { chromium } from "playwright";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const ROOT = dirname(fileURLToPath(import.meta.url));
const OUT = join(ROOT, "..", "docs", "screenshots");
const APP = "http://localhost:5173";
const API = "http://localhost:8000";

// Minimal in-page crypto, matching the app's e2ee.js, so the encrypted-DM
// screenshot shows a genuinely established secure conversation.
const CRYPTO_SETUP = async (page, apiBase) => {
  await page.evaluate(async (API) => {
    const b64 = (b) => btoa(String.fromCharCode(...new Uint8Array(b)));
    const enc = new TextEncoder();
    async function bundle(pass) {
      const kp = await crypto.subtle.generateKey(
        { name: "ECDH", namedCurve: "P-256" }, true, ["deriveKey", "deriveBits"]
      );
      const pub = b64(await crypto.subtle.exportKey("spki", kp.publicKey));
      const pkcs8 = await crypto.subtle.exportKey("pkcs8", kp.privateKey);
      const salt = crypto.getRandomValues(new Uint8Array(16));
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const base = await crypto.subtle.importKey("raw", enc.encode(pass), "PBKDF2", false, ["deriveKey"]);
      const wk = await crypto.subtle.deriveKey(
        { name: "PBKDF2", salt, iterations: 300000, hash: "SHA-256" },
        base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]
      );
      const wrapped = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, wk, pkcs8);
      return { kp, public_key: pub, wrapped_private_key: b64(wrapped), salt: b64(salt), iv: b64(iv) };
    }
    const tok = (u) => fetch(API + "/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username_or_email: u, password: "password123" }),
    }).then((r) => r.json()).then((j) => j.access_token);

    // Bob publishes a key so Alice has someone to be encrypted with.
    const bobTok = await tok("bob");
    const bob = await bundle("bobs passphrase");
    await fetch(API + "/keys/me", { method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${bobTok}` },
      body: JSON.stringify({ public_key: bob.public_key, wrapped_private_key: bob.wrapped_private_key, salt: bob.salt, iv: bob.iv }) });

    // Alice publishes and caches her unlocked key exactly as the app would.
    // (Log in via the API rather than reading localStorage: the app migrates
    // the global chat_access token to a per-origin key on load, so the plain
    // key is gone by the time this runs.)
    const aliceTok = await tok("alice");
    const al = await bundle("alices passphrase");
    await fetch(API + "/keys/me", { method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${aliceTok}` },
      body: JSON.stringify({ public_key: al.public_key, wrapped_private_key: al.wrapped_private_key, salt: al.salt, iv: al.iv }) });
    const jwk = await crypto.subtle.exportKey("jwk", al.kp.privateKey);
    sessionStorage.setItem("relay_e2ee_priv", JSON.stringify(jwk));

    // Post a genuine encrypted exchange so the DM shows real, decryptable
    // content rather than an empty conversation. Both sides share one key.
    const bobPub = await crypto.subtle.importKey(
      "spki", Uint8Array.from(atob(bob.public_key), (c) => c.charCodeAt(0)),
      { name: "ECDH", namedCurve: "P-256" }, true, []
    );
    const shared = await crypto.subtle.deriveKey(
      { name: "ECDH", public: bobPub }, al.kp.privateKey,
      { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]
    );
    const seal = async (text) => {
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, shared, enc.encode(text));
      const out = new Uint8Array(iv.length + ct.byteLength);
      out.set(iv, 0); out.set(new Uint8Array(ct), iv.length);
      return b64(out);
    };
    const dms = await fetch(API + "/dms", { headers: { Authorization: `Bearer ${aliceTok}` } }).then((r) => r.json());
    const dm = dms.find((d) => d.name === "Bob") || dms[0];
    const send = async (tok, text) => fetch(API + `/channels/${dm.id}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` },
      body: JSON.stringify({ content: await seal(text), encrypted: true }),
    });
    await send(aliceTok, "did the safety numbers match on your end?");
    await send(bobTok, "yep — all twelve groups line up ✅");
    await send(aliceTok, "perfect. sending the seed-bank spreadsheet now 🌱");
  }, apiBase);
};

async function authInject(page) {
  await page.goto(APP, { waitUntil: "domcontentloaded" });
  await page.evaluate(async (API) => {
    const r = await fetch(API + "/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username_or_email: "alice", password: "password123" }),
    });
    const t = await r.json();
    localStorage.setItem("chat_access", t.access_token);
    localStorage.setItem("chat_refresh", t.refresh_token);
  }, API);
}

async function openChannelNamed(page, name) {
  await page.getByText(name, { exact: true }).first().click();
  await page.waitForTimeout(900);
}

const run = async () => {
  const fs = await import("fs");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();

  // 1) Login screen (fresh, logged out).
  const ctx1 = await browser.newContext({ viewport: { width: 1360, height: 850 }, deviceScaleFactor: 2 });
  const p1 = await ctx1.newPage();
  await p1.goto(APP, { waitUntil: "networkidle" });
  await p1.mouse.move(0, 0);
  await p1.waitForTimeout(700);
  await p1.screenshot({ path: join(OUT, "01-login.png") });
  await ctx1.close();

  // 2) Main chat — the #garden channel.
  const ctx2 = await browser.newContext({ viewport: { width: 1360, height: 850 }, deviceScaleFactor: 2 });
  const p2 = await ctx2.newPage();
  await authInject(p2);
  await p2.reload({ waitUntil: "networkidle" });
  await p2.waitForTimeout(1000);
  await openChannelNamed(p2, "garden");
  await p2.mouse.move(0, 0);
  await p2.waitForTimeout(400);
  await p2.screenshot({ path: join(OUT, "02-chat.png") });

  // 3) Encrypted DM with the safety number revealed.
  await CRYPTO_SETUP(p2, API);
  await p2.reload({ waitUntil: "networkidle" });
  await p2.waitForTimeout(1200);
  await openChannelNamed(p2, "Bob");
  await p2.waitForTimeout(1500); // let the shared key derive
  const badge = await p2.$(".e2ee-badge");
  if (badge) { await badge.click(); await p2.waitForTimeout(500); }
  await p2.mouse.move(0, 0);
  await p2.waitForTimeout(300);
  await p2.screenshot({ path: join(OUT, "03-encrypted-dm.png") });
  await ctx2.close();

  // 4) Mobile.
  const ctx3 = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true });
  const p3 = await ctx3.newPage();
  await authInject(p3);
  await p3.reload({ waitUntil: "networkidle" });
  await p3.waitForTimeout(1000);
  await openChannelNamed(p3, "garden");
  await p3.waitForTimeout(800);
  // Clear any message's tap-to-reveal toolbar so it doesn't overlap the shot.
  await p3.mouse.move(0, 0);
  await p3.evaluate(() =>
    document.querySelectorAll(".msg.active").forEach((m) => m.classList.remove("active"))
  );
  await p3.waitForTimeout(200);
  await p3.screenshot({ path: join(OUT, "04-mobile.png") });
  await ctx3.close();

  // 5) Group DM ("Harvest Planning", seeded by demo_setup / seed_group).
  const ctx4 = await browser.newContext({ viewport: { width: 1360, height: 850 }, deviceScaleFactor: 2 });
  const p4 = await ctx4.newPage();
  await authInject(p4);
  await p4.reload({ waitUntil: "networkidle" });
  await p4.waitForTimeout(1000);
  await openChannelNamed(p4, "Harvest Planning");
  await p4.mouse.move(0, 0);
  await p4.waitForTimeout(400);
  await p4.screenshot({ path: join(OUT, "07-group-dm.png") });

  // 6) Saved-servers picker. Stage a realistic saved list (cosmetic only: the
  // API base is fixed at load, so this just populates the picker's list) and
  // open it. The active server shows as "current"; one saved server as
  // "signed in".
  await p4.evaluate(() => {
    localStorage.setItem(
      "relay_servers",
      JSON.stringify(["https://chat.openrelay.pl", "https://orc.openrelay.pl"])
    );
    localStorage.setItem("chat_access:https://chat.openrelay.pl", "docs-demo");
  });
  await p4.click(".server-switch");
  await p4.waitForSelector(".modal", { timeout: 4000 });
  await p4.mouse.move(0, 0);
  await p4.waitForTimeout(400);
  await p4.screenshot({ path: join(OUT, "08-servers.png") });
  await ctx4.close();

  await browser.close();
  console.log("screenshots written to", OUT);
};

run().catch((e) => { console.error(e); process.exit(1); });
