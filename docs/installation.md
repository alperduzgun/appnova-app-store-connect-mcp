# Installation Guide

This guide walks you through getting your App Store Connect credentials and connecting the MCP server to Claude.

---

## Step 1 — Create an App Store Connect API Key

You need an **Account Holder** or **Admin** role on your Apple Developer account to create API keys.

1. Go to [App Store Connect → Users and Access](https://appstoreconnect.apple.com/access/integrations/api)
2. Click the **Integrations** tab in the top navigation
3. In the left sidebar, select **App Store Connect API** under Keys
4. Click the **+** button (or **Generate API Key** if this is your first key)
5. Enter a name (e.g. `Claude MCP`) and select **Admin** role
6. Click **Generate**

> **Role note:** Admin gives full access to all tools. If you only need reviews, sales, and metadata — App Manager is sufficient. Finance reports require Admin or Finance role.

---

## Step 2 — Collect your credentials

After generating the key, you need four values:

### Issuer ID

Displayed at the top of the **App Store Connect API** keys page, above the table of active keys. It looks like:

```
xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### Key ID

Shown in the **Key ID** column next to your newly created key. It looks like:

```
XXXXXXXXXX
```
(10 uppercase alphanumeric characters)

### Private Key (.p8 file)

Click **Download API Key** next to your key.

> ⚠️ **The private key can only be downloaded once.** If you lose it, you must revoke the key and create a new one. Store it in a secure location (e.g. 1Password, macOS Keychain).

The downloaded file is named `AuthKey_XXXXXXXXXX.p8` and its contents look like:

```
-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg...
-----END PRIVATE KEY-----
```

### Vendor Number

Required for sales and finance reports.

1. Go to [App Store Connect → Payments and Financial Reports](https://appstoreconnect.apple.com/finance/reports/overview)
2. Your Vendor Number is displayed in the top-left corner of the page

It is an 8-digit number (e.g. `12345678`).

---

## Step 3 — Format the private key

The private key must be on a single line with `\\n` between each line (not real newlines — HTTP headers cannot contain line breaks).

Run this command to get the correctly formatted value:

```bash
awk '{printf "%s\\\\n", $0}' ~/Downloads/AuthKey_XXXXXXXXXX.p8 | sed 's/\\\\n$//'
```

Copy the entire output — it will look like:

```
-----BEGIN PRIVATE KEY-----\nMIGHAgEAMBMGByqGSM49...\n-----END PRIVATE KEY-----
```

---

## Step 4 — Add to Claude

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "appstore": {
      "url": "https://haikuku-appnova-app-store-connect-mcp.hf.space/mcp",
      "headers": {
        "x-appstore-issuer-id": "YOUR_ISSUER_ID",
        "x-appstore-key-id": "YOUR_KEY_ID",
        "x-appstore-private-key": "-----BEGIN PRIVATE KEY-----\\nMIGH...\\n-----END PRIVATE KEY-----",
        "x-appstore-vendor-number": "YOUR_VENDOR_NUMBER"
      }
    }
  }
}
```

Restart Claude Desktop. Ask *"List my apps"* to verify.

### Claude Code

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "appstore": {
      "type": "http",
      "url": "https://haikuku-appnova-app-store-connect-mcp.hf.space/mcp",
      "headers": {
        "x-appstore-issuer-id": "YOUR_ISSUER_ID",
        "x-appstore-key-id": "YOUR_KEY_ID",
        "x-appstore-private-key": "-----BEGIN PRIVATE KEY-----\\nMIGH...\\n-----END PRIVATE KEY-----",
        "x-appstore-vendor-number": "YOUR_VENDOR_NUMBER"
      }
    }
  }
}
```

Restart Claude Code. Run `/mcp` to confirm `appstore` appears as connected.

---

## Optional — Pin a default app

If you only manage one app, add `x-appstore-app-id` to skip calling `select_app` at the start of every session:

```json
"x-appstore-app-id": "6502225088"
```

Use either the numeric App Store ID (found in App Store Connect → App Information → Apple ID) or the bundle ID (e.g. `com.example.myapp`).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Missing App Store Connect credentials` error | Headers not reaching the server | Check that your MCP client supports custom headers; verify the config JSON is valid |
| `401 Unauthorized` from Apple | Wrong Issuer ID or Key ID | Copy them again directly from the App Store Connect page |
| `Invalid signature` / JWT error | Private key incorrectly formatted | Re-run the formatting command in Step 3; ensure you're using `\\n` not real newlines |
| Finance tools return no data | Wrong Vendor Number | Find it at Payments and Financial Reports page |
| `403 Forbidden` | API key role insufficient | Create a new key with Admin role |
| Key download button is greyed out | Key was already downloaded | Revoke and create a new key |

---

## Security notes

- Your credentials are sent over HTTPS and used only to sign JWT tokens for Apple's API
- They are **not logged, not stored, and not shared** between users
- The server is open source — review `service.py` to verify
- API keys do not expire; revoke them in App Store Connect if compromised
- You can scope keys to specific apps using **Individual Keys** instead of Team Keys (available in the same Integrations → App Store Connect API section)
