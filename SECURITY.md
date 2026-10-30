# Security

## Reporting a vulnerability

Please report it privately through GitHub's "Report a vulnerability" button on
this repository's Security tab, not in a public issue. Include what you did,
what happened and which commit you ran.

## What Butler assumes

Butler is a single-user assistant for your own machines. Its access model and
its limits are described in the README's "Security model and its limits"
section and in `docs/remote-access.md`. In short: the daemon trusts the
machine it runs on, your Tailscale login and devices you paired yourself;
coding agents it starts run with full permissions in the folders you give
them.

## Known dependency advisories

Python: `pip-audit -r requirements.txt` reports no known vulnerabilities
(2026-10-30).

`npm audit` reports these, and none has an upstream fix as of 2026-10-29.
All of them sit in the local text-to-speech stack (`kokoro-js` and what it
pulls in) and handle model and image files Butler downloads itself, not
input from other people:

- `@huggingface/transformers` (through `kokoro-js`)
- `onnxruntime-node` and its dependency `adm-zip`
- `sharp`

The WhatsApp library `@whiskeysockets/baileys` is pinned at `7.0.0-rc14`,
which fixes the critical message-spoofing advisory in earlier release
candidates.
