---
title: appnova-app-store-connect-mcp
emoji: 🍎
colorFrom: gray
colorTo: orange
sdk: docker
app_port: 7860
pinned: false
---

# appnova-app-store-connect-mcp

**App Store Connect MCP server for Claude** — manage your iOS app's entire lifecycle with natural language. The only MCP server with first-class **App Store Optimization (ASO)** support: edit metadata, keywords, and descriptions across all locales directly from Claude.

48 tools · 14 categories · Python · FastMCP

---

## Why This MCP?

Most App Store Connect tools expose raw API endpoints. This server is designed for **AI-assisted iOS growth workflows**:

- **ASO automation** — read and update app name, subtitle, keywords, descriptions across all locales in one conversation
- **Review management** — triage 1-star reviews, draft responses, and publish them without leaving Claude
- **Release workflows** — create version → update metadata → submit for review → monitor phased rollout, all via natural language
- **Revenue intelligence** — query sales, finance reports, and subscription metrics with plain English

---

## Features

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

## Quick Start

### 1. Clone and set up

```bash
git clone https://github.com/alperduzgun/appnova-app-store-connect-mcp.git
cd appnova-app-store-connect-mcp
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your App Store Connect API credentials
```

### 3. Add to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "appnova-appstore": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/appnova-app-store-connect-mcp/server.py"],
      "env": {
        "APPSTORE_ISSUER_ID": "your-issuer-id",
        "APPSTORE_KEY_ID": "your-key-id",
        "APPSTORE_PRIVATE_KEY_PATH": "/path/to/AuthKey_XXXXXXXXXX.p8",
        "APPSTORE_VENDOR_NUMBER": "your-vendor-number",
        "APPSTORE_APP_ID": "com.example.yourapp"
      }
    }
  }
}
```

---

## Configuration

Get these values from [App Store Connect → Users and Access → Integrations](https://appstoreconnect.apple.com/access/integrations/api):

| Variable | Required | Description |
|----------|----------|-------------|
| `APPSTORE_ISSUER_ID` | ✅ | Issuer ID from App Store Connect API keys page |
| `APPSTORE_KEY_ID` | ✅ | Key ID of your API key |
| `APPSTORE_PRIVATE_KEY_PATH` | ✅ | Absolute path to your `.p8` private key file |
| `APPSTORE_VENDOR_NUMBER` | ✅ | Required for sales and finance reports |
| `APPSTORE_APP_ID` | Optional | Numeric App Store ID or bundle ID — skip `select_app` if set |
| `APPSTORE_BUNDLE_ID` | Optional | Bundle ID when `APPSTORE_APP_ID` is a bundle string |

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

**Capability check:**
```
"List all active capabilities for com.example.myapp and confirm Sign in with
Apple is enabled"
```

**Release workflow:**
```
"Create a new 2.5.0 version, I'll tell you what to put in what's new,
then submit it for review"
```

---

## Requirements

- Python 3.11+
- [FastMCP](https://github.com/jlowin/fastmcp)
- App Store Connect API key with **Admin** or **App Manager** role
- Claude Desktop (or any MCP-compatible client)

---

## License

MIT
