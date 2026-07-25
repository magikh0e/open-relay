# Security policy

Open Relay is a chat service people trust with private conversations, including
end-to-end encrypted ones. Security reports are welcome and taken seriously.

## Reporting a vulnerability

**Please do not open a public issue.** A public report on a running chat service
is a live exploit notice for every deployment of it.

Report it privately: go to the repository's **Security** tab and choose
**Report a vulnerability**. That opens a private advisory visible only to the
maintainer.

Useful things to include:

- What the flaw is, and which component it affects.
- Steps to reproduce, ideally against a local `docker compose` deployment.
- What an attacker can actually achieve with it.
- Any suggested fix, if you have one.

You will get an acknowledgement, and credit in the release notes when the fix
ships unless you would rather stay anonymous.

## Supported versions

This is a small project with a single active line. Fixes land on the latest
release; there are no long-term support branches. Self-hosters should run a
recent version, and the desktop client updates itself.

## What is in scope

Anything that breaks the security boundary the project claims, in particular:

- Reading message content across accounts, or bypassing channel membership.
- Breaking the end-to-end encryption boundary for one-to-one DMs, or causing
  plaintext to reach the server when the client believes it is encrypted.
- Privilege escalation, including obtaining `is_admin` or channel
  owner/moderator rights you were not granted.
- Authentication and session flaws: token forgery, tokens surviving a password
  change, invite or registration-mode bypass, channel key bypass.
- XSS, SSRF, SQL injection, or path traversal, especially in upload handling.
- Defeating the rate limits on login, registration, messaging or uploads.
- Leaking uploads or attachments belonging to conversations you are not in.

## What is already documented, and not a vulnerability

The project is deliberately explicit about the limits of what it protects. The
following are known, documented design decisions rather than bugs. See
["What it deliberately does not protect"](README.md) and the in-app privacy
policy.

- **Whoever runs the server can read channel and group messages.** Only
  one-to-one DMs can be end-to-end encrypted. A server operator reading a
  channel is the design, not a flaw.
- **Encryption hides message contents, not metadata.** Who talked to whom, and
  when, is recorded.
- **Files in ordinary channels sit behind an unguessable but publicly reachable
  link.** Attachments in encrypted DMs are encrypted; ordinary ones are not.
- **A forgotten encryption passphrase is unrecoverable.** There is no reset by
  design.
- Findings that require an already-compromised server, database or administrator
  account.
- Missing hardening headers or scanner output with no demonstrated impact.

If you think one of these is worse than documented, or the documentation
overstates a protection, that is worth reporting: an inaccurate claim is treated
as a security problem in its own right.
