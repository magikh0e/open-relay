# Roadmap

What has recently landed, what is likely to be built next, what is being
considered, and what has been looked at and deliberately ruled out.

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
- **Frontend tests** (unreleased). The web client had none, which mattered more
  once outside contributions were invited. There is now a Vitest suite over the
  parts where being wrong is expensive: the crypto, the URL sanitising, and the
  message renderer. It runs in CI beside the backend suite. Deliberately not a
  coverage-chasing exercise; the components with the most lines are the ones
  where a test would mostly assert that the markup still looks like the markup.
  The crypto tests run against real WebCrypto, since a green suite over a
  mocked crypto layer would say nothing about the guarantee it protects.

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
- **Rotation without the owner.** Publishing a key epoch is owner-only, and the
  server refuses encrypted sends under a key whose shares no longer match the
  membership. An owner can also leave a group, and nothing transfers ownership
  when they do. So a membership change after the owner goes leaves an encrypted
  group unable to send: an availability cliff rather than a security hole, but a
  sharp one, and reachable through ordinary use. A site admin can rotate as
  well, which is the current escape hatch, though it means the admin holds a key
  to a group they may not be in. Transferring ownership on departure is the
  smaller fix; letting any member commit a rotation is the more general one.
  Neither is designed yet.
- **Automated accessibility checks.** The unit suite covers logic, not what the
  interface is actually like to use. A contrast and target-size pass found five
  places where white sat on the accent fill at 3.16:1, avatar initials that
  passed on two of twelve palette colours, and header buttons at 20px against a
  24px minimum, one of which deletes a channel. All were found by measuring by
  hand, which means the next regression will be too. Worth wiring into CI, and
  the natural rung above the tests that now exist.

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

### MLS (RFC 9420) for group encryption

The obvious question, given groups here already use numbered epochs that rotate
on membership change and messages that name the epoch which sealed them. That is
the same shape as MLS in outline, so the comparison is worth making explicitly
rather than leaving people to wonder.

MLS exists to provide forward secrecy and post-compromise security through a
ratchet tree. That is its reason to be; the rest of what it does can be done more
simply. Which puts it straight into the conflict described above, and the outcome
is a fork with no good branch:

- Keep history readable from a new device, which means retaining the ratchet
  secrets, and the property MLS was adopted for is switched off. All of the
  complexity, none of the benefit.
- Take real forward secrecy, and server-side readable history goes. That is a
  decision about what this app is, not a cryptographic upgrade.

MLS does not resolve that tension, it relocates it.

The costs are concrete as well. Every client is a leaf with its own key package,
so it needs per-device identities, which is the same rewrite of the key model
that rules out ratchets generally. There is no WebCrypto path to it, so it means
a WASM library inside the one module where dependency count matters most, against
a frontend whose entire runtime today is React and React-DOM. And it assumes a
delivery service for ordering and an authentication service for credentials,
where this server deliberately knows nothing except how to store an opaque blob
and increment an integer.

The scaling argument, which is the usual reason to reach for it, does not apply
at this size. Sealing a key to each member is O(n) against MLS's O(log n), which
at a cap of 20 is twenty operations against about five. That gap starts mattering
in the hundreds, and group sizes in the hundreds are already ruled out below.

What would reopen it: raising the cap past roughly fifty, or deciding to move to
per-device identities for some other reason. **Post-compromise security is the
one genuine gap**, and worth naming separately from forward secrecy, because
there is nothing like it here at all: a stolen key stays useful forever and no
amount of epoch rotation heals it. It is arguably the more valuable of the two
properties for a self-hosted tool. It also requires discarding keys, so it sits
behind the same trade.

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
