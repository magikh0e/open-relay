# Roadmap

What is likely to be built next, what is being considered, and what has been
looked at and deliberately ruled out.

No dates. This is a small project with one maintainer, and a list of promised
quarters would be fiction. Order here is rough priority, not a schedule.

If you want something on this list, [an issue](https://github.com/magikh0e/open-relay/issues)
arguing for it is more useful than a vote.

---

## Shipped

Kept here briefly so the reasoning above them is not lost when the entry moves.

- **Bot accounts** (1.26.0). Built as designed: `is_bot` on the user record so
  bots reuse avatars, member lists, mentions and presence; a long-lived opaque
  token stored as a SHA-256 hash and shown once; `read` / `write` / `react`
  scopes and no moderation scope; access through ordinary channel membership.
  One thing was added during the build that the plan did not name: the scopes
  sit on top of a **deny-by-default allowlist**, so a bot can reach only the
  endpoints explicitly listed for it and a newly added endpoint is closed to
  bots until somebody opens it. Checking a handful of obvious endpoints would
  have left every other one open by omission. See the
  [user guide](docs/USER_GUIDE.md#bot-accounts) and
  [developer guide](docs/DEVELOPER_GUIDE.md).
- **End-to-end encrypted group messages** (1.25.0). Per-group key sealed to each
  member, rotated whenever somebody joins or leaves.
- **Frontend tests.** The web client had none, which mattered more once outside
  contributions were invited. There is now a Vitest suite over the parts where
  being wrong is expensive: the crypto, the URL sanitising, and the message
  renderer. It runs in CI beside the backend suite. Deliberately not a
  coverage-chasing exercise; the components with the most lines are the ones
  where a test would mostly assert that the markup still looks like the markup.

---

## Next up

Designed rather than merely wished for; the shape below is what would actually
get built.

### One-to-one voice calls

Audio only, one to one, peer to peer.

- Signalling relayed over the existing WebSocket. The server checks that the
  caller is in the DM, forwards the payload to the other member, and stores
  nothing.
- **The SDP is sealed with the DM's existing pairwise key.** SDP carries the
  DTLS fingerprints that secure the media, so a server able to rewrite them
  could sit in the middle of the call. Encrypting it with a key the server
  cannot read closes that, and the safety number people can already compare
  then covers their calls as well as their messages. Calls therefore require
  encryption to be set up on both sides.
- coturn in the Compose stack for NAT traversal, with time-limited HMAC
  credentials from an authenticated endpoint rather than a static password.
  Roughly 10 to 20 percent of calls cannot connect directly and need it.
- Call state lives in the two clients. The server relays and nothing more.

Known limits, which would ship documented rather than discovered:

- Ringing only works while the app is open. A web push cannot wake a closed tab
  quickly enough to ring reliably.
- The server still sees that a call happened, between whom and for how long,
  and if TURN relays it, how much data moved. It cannot decrypt any of it.

---

## Considered

Smaller or less certain, in no particular order.

- **Message expiry.** Delete ciphertext after a chosen interval. Not forward
  secrecy, but it bounds how much a future key compromise exposes, and it works
  for plaintext channels too. Cheap next to a ratchet.
- **Push notifications for incoming calls**, if the reliability turns out better
  in practice than expected.
- **Read receipts and per-channel notification settings**, both frequently
  wanted and neither yet designed.

---

## Deliberately not planned

Ruled out after looking properly, with the reason. Any of these could be
reopened by a good argument.

### Forward secrecy

The current design uses static ECDH keys, so a compromised key exposes past
messages as well as future ones. Real forward secrecy means a ratchet that
discards keys as they are used.

That directly conflicts with something this app deliberately provides: your
history is stored server-side and readable from any device you can unlock. Keys
that are discarded stop **you** reading your own history on a new machine. Signal
manages forward secrecy precisely because it does not keep readable history on
the server.

Ratchet state is also per-device, so two browsers on one identity would
desynchronise; solving that means giving every device its own identity, which is
a rewrite of the key model rather than an addition to it.

The trade has been made in favour of history staying available, and the privacy
policy says so plainly. Message expiry is the cheaper way to bound exposure.

### Group calls over an SFU

A selective forwarding unit scales calls past four people, but it decrypts media
in order to forward it, so the operator could listen. Keeping end-to-end
encryption through an SFU needs SFrame, which is a serious undertaking with
uneven browser support.

Peer-to-peer calls are end-to-end encrypted with no extra work. Small calls that
are genuinely private beat large calls that quietly are not.

### Client-side plugins

Plugins running in the page could read decrypted messages and the unwrapped
private key straight out of memory. The central claim of this project is that
nobody but the participants can read a DM; a browser plugin API would make that
false and require saying so.

Extensibility is served by bot accounts instead, which now exist: the code runs
elsewhere, holds a scoped token, and sees only the channels it was added to.

### Community-scale features

Forums, stage channels, discovery, automod, custom emoji, roles with granular
per-channel permissions. Open Relay is built for a group of people who know each
other and trust whoever runs the server. Those features serve public communities
of thousands, which is a different product with different tradeoffs.
