---
title: appnova-app-store-connect-mcp
emoji: 🍎
colorFrom: gray
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# appnova-app-store-connect-mcp

**App Store Connect MCP server for Claude** — manage your iOS app's entire lifecycle with natural language. No Docker, no Python, no pip. Just add 4 lines to your Claude config and start talking to your App Store.

48 tools · 14 categories · Hosted on Hugging Face Spaces · Free

---

## Quick Start — Zero Setup (Recommended)

No installation required. The server is already running on Hugging Face Spaces.

> 📖 **Full installation guide with screenshots and troubleshooting:** [docs/installation.md](docs/installation.md)

### 1. Get your App Store Connect API credentials

Go to [App Store Connect → Users and Access → Integrations](https://appstoreconnect.apple.com/access/integrations/api) and create an API key with **Admin** or **App Manager** role. You'll need:

- **Issuer ID** — shown at the top of the API Keys page
- **Key ID** — shown next to your key
- **Private key** — download the `.p8` file (only downloadable once)
- **Vendor Number** — from [Payments and Financial Reports](https://appstoreconnect.apple.com/finance/reports/overview)

### 2. Add to your Claude client

#### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "appstore": {
      "url": "https://haikuku-appnova-app-store-connect-mcp.hf.space/mcp",
      "headers": {
        "x-appstore-issuer-id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        "x-appstore-key-id": "XXXXXXXXXX",
        "x-appstore-private-key": "-----BEGIN PRIVATE KEY-----\\nMIGH...\\n-----END PRIVATE KEY-----",
        "x-appstore-vendor-number": "12345678"
      }
    }
  }
}
```

#### Claude Code

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "appstore": {
      "type": "http",
      "url": "https://haikuku-appnova-app-store-connect-mcp.hf.space/mcp",
      "headers": {
        "x-appstore-issuer-id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        "x-appstore-key-id": "XXXXXXXXXX",
        "x-appstore-private-key": "-----BEGIN PRIVATE KEY-----\\nMIGH...\\n-----END PRIVATE KEY-----",
        "x-appstore-vendor-number": "12345678"
      }
    }
  }
}
```

### 3. Restart your Claude client

That's it. Ask Claude: *"List my apps"* to verify the connection.

---

## How to format the private key

Open your `.p8` file — it looks like this:

```
-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg...
-----END PRIVATE KEY-----
```

**Important:** In the JSON config you must use `\\n` (two characters: backslash + n) instead of real line breaks. Using a single `\n` creates an actual newline which breaks HTTP headers.

Use this one-liner to get the correctly formatted value:

```bash
awk '{printf "%s\\\\n", $0}' AuthKey_XXXXXXXXXX.p8 | sed 's/\\\\n$//'
```

Paste the output directly as the `x-appstore-private-key` value.

---

## Optional: Pin a default app

Add `x-appstore-app-id` to skip calling `select_app` every session:

```json
"headers": {
  "x-appstore-issuer-id": "...",
  "x-appstore-key-id": "...",
  "x-appstore-private-key": "...",
  "x-appstore-vendor-number": "...",
  "x-appstore-app-id": "6502225088"
}
```

Use either the numeric App Store ID or bundle ID (e.g. `com.example.yourapp`).

---

## Security

Your credentials are sent as HTTP headers directly to the server and used exclusively to sign JWT tokens for Apple's API. They are **not logged, not stored, and not shared**. Each request is fully isolated — credentials from one user never touch another user's session.

The server is open source: [github.com/alperduzgun/appnova-app-store-connect-mcp](https://github.com/alperduzgun/appnova-app-store-connect-mcp)

If you prefer to host it yourself, see the [Self-Hosting](#self-hosting) section below.

---

## What You Can Do

### App Store Optimization (ASO)
| Tool | Description |
|------|-------------|
| `get_locales` | List all localizations with IDs — lightweight index, call first |
| `get_locale_metadata` | Full metadata for one locale: name, subtitle, keywords, description, what's new |
| `update_info` | Update app name and/or subtitle (always editable, any version state) |
| `update_version` | Update keywords, description, what's new (requires PREPARE_FOR_SUBMISSION) |
| `list_custom_product_pages` | List all Custom Product Pages with version state and visibility |
| `get_custom_product_page` | Full CPP detail including per-locale promotional text |

### Reviews & Ratings
| Tool | Description |
|------|-------------|
| `get_reviews` | Fetch reviews sorted by newest; filter by star rating (1–5) |
| `get_ratings` | Average rating, total count, sample size |
| `respond_to_review` | Post a new developer response |
| `update_review_response` | Edit an existing response in-place (preserves timestamp) |
| `delete_review_response` | Delete a response before re-posting |

### Analytics & Sales
| Tool | Description |
|------|-------------|
| `get_analytics` | Impressions, page views, app units, conversion rate, sessions, crashes — daily breakdown up to 365 days |
| `get_sales` | Downloads, redownloads, updates, IAP, subscriptions, revenue — daily breakdown up to 365 days |

### Finance Reports
| Tool | Description |
|------|-------------|
| `get_finance_report` | Monthly proceeds by territory and currency for a specific month |
| `get_finance_summary` | Aggregated proceeds across the last N months (default 3, max 12) |

### Subscriptions
| Tool | Description |
|------|-------------|
| `list_subscription_groups` | All subscription groups and their products |
| `create_subscription_group` | Create a new subscription group |
| `create_subscription` | Create a subscription product (weekly → yearly) |
| `create_subscription_localization` | Add localized name and description to a subscription |

### In-App Purchases
| Tool | Description |
|------|-------------|
| `list_in_app_purchases` | All IAPs: consumable, non-consumable, non-renewing |
| `create_in_app_purchase` | Create a new IAP product |
| `create_iap_localization` | Add localized display name and description |
| `delete_in_app_purchase` | Delete a draft IAP |

### Pricing
| Tool | Description |
|------|-------------|
| `get_iap_price_points` | All available price tiers for an IAP in a territory |
| `set_iap_price` | Set IAP price by price point ID |
| `set_iap_price_by_amount` | Set IAP price by dollar amount — auto-matches tier |
| `get_subscription_price_points` | All available price tiers for a subscription |
| `set_subscription_price` | Set subscription price by price point ID |
| `set_subscription_price_by_amount` | Set subscription price by dollar amount — auto-matches tier |

### App Versions & Submission
| Tool | Description |
|------|-------------|
| `list_app_versions` | Recent versions with state (READY_FOR_SALE, IN_REVIEW, etc.) |
| `create_app_version` | Create a new version in PREPARE_FOR_SUBMISSION state |
| `submit_for_review` | Submit a version for App Review |
| `cancel_submission` | Withdraw an in-progress submission |

### Phased Release
| Tool | Description |
|------|-------------|
| `get_phased_release` | Current phased rollout status for a version |
| `create_phased_release` | Enable 7-day phased rollout (1%→2%→5%→10%→20%→50%→100%) |
| `update_phased_release` | Pause, resume, or complete rollout immediately |

### TestFlight
| Tool | Description |
|------|-------------|
| `list_builds` | Recent builds with processingState, version, expiration |
| `list_beta_groups` | All beta groups with public link info |
| `add_beta_tester` | Invite a tester to a beta group |
| `remove_beta_tester` | Remove a tester from a beta group |

### In-App Events
| Tool | Description |
|------|-------------|
| `list_app_events` | All promotional events with state (DRAFT/LIVE/ARCHIVED) and localizations |

### Bundle ID Capabilities
| Tool | Description |
|------|-------------|
| `get_bundle_id_info` | Numeric ID, platform, name, and seed ID for a bundle identifier |
| `list_bundle_id_capabilities` | All active capabilities (Sign in with Apple, Push, IAP, etc.) |
| `enable_bundle_id_capability` | Enable a capability — idempotent, safe to call repeatedly |
| `disable_bundle_id_capability` | Remove a capability by resource ID |

### App Selection
| Tool | Description |
|------|-------------|
| `list_apps` | All apps in your account |
| `select_app` | Set the active app by numeric ID or bundle ID |
| `get_active_app` | Show the currently selected app |

---

## Example Workflows

**ASO audit across all locales:**
```
"Get all my app's locale metadata and tell me which ones are missing keywords
or have subtitles under 20 characters"
```

**Review triage:**
```
"Show me all 1-star reviews from the last 50 and draft a response for each
one that mentions a crash"
```

**Revenue summary:**
```
"Give me a finance summary for the last 3 months and compare monthly trends"
```

**Release workflow:**
```
"Create a new 2.5.0 version, update the what's new text for en-US and tr,
then submit for review"
```

**Capability check:**
```
"List all active capabilities for com.example.myapp and confirm Sign in with
Apple is enabled"
```

---

## Requirements

- App Store Connect API key with **Admin** or **App Manager** role
- Claude Desktop 0.10+ (or any MCP client that supports HTTP transport with custom headers)

---

## Self-Hosting

Want to run the server yourself? Full source code and setup instructions at [github.com/alperduzgun/appnova-app-store-connect-mcp](https://github.com/alperduzgun/appnova-app-store-connect-mcp).

---

## License

MIT
