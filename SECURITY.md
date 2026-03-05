# Security Policy

## Credential Handling

This server receives App Store Connect API credentials (Issuer ID, Key ID, Private Key) as HTTP headers per request. These credentials are:

- **Not logged** — never written to stdout, stderr, or any file
- **Not stored** — held in memory only for the duration of the request
- **Not shared** — each user's credentials are fully isolated via Python `ContextVar`
- **Not transmitted** — used only to sign JWT tokens for Apple's API, never forwarded elsewhere

The server is open source. You can verify this behavior in `service.py` (`_get_creds`, `_generate_token`) and `server.py` (`CredentialsMiddleware`).

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not** open a public issue.

Report it privately by emailing the repository owner via GitHub's private vulnerability reporting:
**GitHub → Security tab → "Report a vulnerability"**

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive a response within 48 hours. We will work with you to understand and address the issue before any public disclosure.

## API Key Safety

- Your `.p8` private key can only be downloaded once from App Store Connect — store it securely
- API keys do not expire; revoke compromised keys immediately at [App Store Connect → Users and Access → Integrations](https://appstoreconnect.apple.com/access/integrations/api)
- Use **Individual Keys** scoped to specific apps for tighter access control
