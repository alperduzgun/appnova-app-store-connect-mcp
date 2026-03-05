"""
appnova-appstore-mcp
App Store Connect MCP server: analytics, sales, reviews, ratings, metadata.
"""

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from service import service

mcp = FastMCP(
    "appnova-appstore",
    instructions=(
        "App Store Connect tools for iOS app management. "
        "APP SELECTION: if APPSTORE_APP_ID env is not set, call list_apps first, "
        "then select_app(app_id) to choose the active app before using any other tool. "
        "METADATA WORKFLOW: call get_locales first to get localization IDs, "
        "then get_locale_metadata(locale) for full content of one locale, "
        "then update_info / update_version with the IDs. "
        "REVIEW WORKFLOW: get_reviews → respond_to_review (new) or "
        "update_review_response (edit existing in-place) or "
        "delete_review_response + respond_to_review (delete and re-post). "
        "ANALYTICS: get_sales for revenue/downloads, get_analytics for impressions/sessions. "
        "FINANCE: get_finance_report(year, month) for monthly proceeds, get_finance_summary for multi-month overview. "
        "TESTFLIGHT: list_builds / list_beta_groups to inspect builds; add/remove_beta_tester to manage testers. "
        "VERSIONS: list_app_versions for release history; create_app_version → submit_for_review to release; "
        "cancel_submission to abort. CAUTION: submit_for_review and cancel_submission are irreversible. "
        "CUSTOM PRODUCT PAGES: list_custom_product_pages / get_custom_product_page for CPP management."
    ),
)


# ─── App selection ───────────────────────────────────────────────────────────

@mcp.tool()
async def list_apps() -> str:
    """List all apps in your App Store Connect account (name, bundle ID, numeric ID).
    Call this first if APPSTORE_APP_ID is not set in the environment."""
    return json.dumps(await service.list_apps(), indent=2)


@mcp.tool()
async def select_app(app_id: str) -> str:
    """Select the active app for all subsequent tool calls.
    app_id: numeric App Store ID (e.g. '6502225088') or bundle ID (e.g. 'com.example.app').
    All tools operate on this app until select_app is called again."""
    result = await service.set_app(app_id)
    return json.dumps({"status": "selected", "app": result}, indent=2)


@mcp.tool()
async def get_active_app() -> str:
    """Show the currently selected app (name, bundle ID, numeric ID).
    Returns null if no app is selected and APPSTORE_APP_ID is not set."""
    return json.dumps({"activeApp": service.get_active_app()}, indent=2)


# ─── Analytics & Sales ───────────────────────────────────────────────────────

@mcp.tool()
async def get_sales(days: int = 30) -> str:
    """Get App Store sales: downloads, redownloads, updates, subscriptions, revenue.
    Returns daily breakdown and totals for the last N days (default 30, max 365).
    Warnings field is set if any day failed to fetch (network error, rate-limit)."""
    return json.dumps(await service.get_sales(days), indent=2)


@mcp.tool()
async def get_analytics(days: int = 30) -> str:
    """Get App Store engagement analytics: impressions, page views, app units,
    conversion rate, sessions, active devices, crashes.
    Returns daily breakdown and totals via Apple Analytics Reports API.
    days: 1–365, default 30. Status may be 'pending' while Apple prepares data."""
    return json.dumps(await service.get_analytics(days), indent=2)


# ─── Reviews ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_reviews(limit: int = 50, rating: Optional[int] = None) -> str:
    """Get customer reviews sorted by newest first.
    Returns rating, title, body, reviewer nickname, territory, and developer response (if any).
    limit: 1–200 (default 50). When rating filter is used, fetches 200 reviews to maximize results.
    rating: filter by star rating 1–5 (e.g. rating=1 for only 1-star reviews). Optional."""
    if rating is not None:
        if rating not in range(1, 6):
            return json.dumps({"error": "rating must be 1–5"})
        # Fetch max pool when filtering so user gets as many matching reviews as possible
        result = await service.get_customer_reviews(200)
        result["data"] = [
            r for r in result.get("data", [])
            if r.get("attributes", {}).get("rating") == rating
        ]
    else:
        result = await service.get_customer_reviews(limit)
    return json.dumps(result, indent=2)


@mcp.tool()
async def get_ratings() -> str:
    """Get app rating summary: average rating, total review count, and sample size.
    Note: average is computed from the most recent 200 reviews when total > 200."""
    return json.dumps(await service.get_ratings(), indent=2)


@mcp.tool()
async def respond_to_review(review_id: str, response_body: str) -> str:
    """Post a new developer response to a customer review that has no response yet.
    review_id: the review ID from get_reviews → data[].id.
    response_body: response text, max 5900 characters.
    To edit an existing response: call delete_review_response first, then call this again."""
    return json.dumps(await service.post_review_response(review_id, response_body), indent=2)


@mcp.tool()
async def delete_review_response(response_id: str) -> str:
    """Delete an existing developer review response.
    response_id: from get_reviews → data[].relationships.response.data.id.
    After deleting, call respond_to_review to post a new response."""
    await service.delete_review_response(response_id)
    return json.dumps({"status": "deleted", "response_id": response_id})


@mcp.tool()
async def update_review_response(response_id: str, response_body: str) -> str:
    """Edit an existing developer review response in-place (PATCH).
    response_id: from get_reviews → data[].relationships.response.data.id.
    response_body: updated text, max 5900 characters.
    Use this instead of delete + respond_to_review when you want to edit without
    losing the original response timestamp."""
    return json.dumps(
        await service.update_review_response(response_id, response_body), indent=2
    )


# ─── Metadata / ASO ──────────────────────────────────────────────────────────

@mcp.tool()
async def get_locales() -> str:
    """List all available localizations with their IDs — lightweight, call first.
    Returns appInfoLocalizations (for name/subtitle edits via update_info)
    and versionLocalizations (for keywords/description edits via update_version).
    Use the IDs and locale codes here to target specific locales."""
    return json.dumps(await service.get_locales(), indent=2)


@mcp.tool()
async def get_locale_metadata(locale: str = "en-US") -> str:
    """Get full ASO metadata for a single locale: name, subtitle, keywords, description, whatsNew.
    locale: locale code from get_locales, e.g. 'en-US', 'tr', 'de-DE', 'fr-FR', 'ja'.
    Returns appInfo (always editable) and versions (READY_FOR_SALE + PREPARE_FOR_SUBMISSION)."""
    return json.dumps(await service.get_locale_metadata(locale), indent=2)


@mcp.tool()
async def update_info(localization_id: str, name: Optional[str] = None,
                      subtitle: Optional[str] = None) -> str:
    """Update app name and/or subtitle. Always editable regardless of version state.
    localization_id: appInfo localization ID from get_locales → appInfoLocalizations[].id.
    name: new app name (optional). subtitle: new subtitle, max 30 chars (optional).
    Provide at least one of name or subtitle."""
    attrs = {k: v for k, v in {"name": name, "subtitle": subtitle}.items() if v is not None}
    if not attrs:
        return json.dumps({"error": "Provide at least one of: name, subtitle"})
    return json.dumps(await service.update_app_info_localization(localization_id, attrs), indent=2)


@mcp.tool()
async def update_version(localization_id: str, keywords: Optional[str] = None,
                         description: Optional[str] = None,
                         whats_new: Optional[str] = None) -> str:
    """Update keywords, description, or what's new text.
    Requires the version to be in PREPARE_FOR_SUBMISSION state.
    localization_id: version localization ID from get_locales → versionLocalizations[].id
    where state == 'PREPARE_FOR_SUBMISSION'.
    keywords: comma-separated, max 100 chars total. description: full app description.
    whats_new: release notes. Provide at least one."""
    attrs = {k: v for k, v in {
        "keywords": keywords, "description": description, "whatsNew": whats_new,
    }.items() if v is not None}
    if not attrs:
        return json.dumps({"error": "Provide at least one of: keywords, description, whats_new"})
    return json.dumps(await service.update_version_localization(localization_id, attrs), indent=2)


# ─── Subscriptions ───────────────────────────────────────────────────────────

@mcp.tool()
async def list_subscription_groups() -> str:
    """List all subscription groups and their individual subscriptions.
    Returns referenceName for each group and per-subscription: name, productId,
    state (APPROVED/WAITING_FOR_REVIEW/etc), subscriptionPeriod, groupLevel."""
    return json.dumps(await service.list_subscription_groups(), indent=2)


# ─── In-App Events ────────────────────────────────────────────────────────────

@mcp.tool()
async def list_app_events() -> str:
    """List all in-app events (promotional events) with their localizations.
    Returns eventState (DRAFT/LIVE/ARCHIVED), badge, purpose, and per-locale
    name/shortDescription. Use for monitoring active promotions."""
    return json.dumps(await service.list_app_events(), indent=2)


# ─── Phased Release ───────────────────────────────────────────────────────────

@mcp.tool()
async def get_phased_release(version_id: str) -> str:
    """Get phased release status for an App Store version.
    version_id: from list_app_versions → [].id.
    Returns null data if phased release is not enabled for this version.
    phasedReleaseState: ACTIVE, PAUSED, COMPLETE, or INACTIVE."""
    return json.dumps(await service.get_phased_release(version_id), indent=2)


@mcp.tool()
async def create_phased_release(version_id: str) -> str:
    """Enable 7-day phased rollout for an App Store version.
    version_id: from list_app_versions → [].id. Requires PENDING_DEVELOPER_RELEASE state.
    Rolls out to 1%→2%→5%→10%→20%→50%→100% of users over 7 days.
    WARNING: Only use when ready to release. Use update_phased_release to pause/complete."""
    return json.dumps(await service.create_phased_release(version_id), indent=2)


@mcp.tool()
async def update_phased_release(phased_release_id: str, state: str) -> str:
    """Update phased release state.
    phased_release_id: from get_phased_release → data.id.
    state: ACTIVE (resume rollout), PAUSED (stop temporarily), COMPLETE (release to 100% now).
    WARNING: COMPLETE is irreversible — all users get the update immediately."""
    return json.dumps(await service.update_phased_release(phased_release_id, state), indent=2)


# ─── Finance Reports ─────────────────────────────────────────────────────────

@mcp.tool()
async def get_finance_report(year: int, month: int) -> str:
    """Get monthly financial report: actual proceeds by territory and currency.
    year: calendar year (e.g. 2025). month: calendar month 1–12.
    Reports are typically available 2–3 months after the period closes.
    Returns totalProceeds per currency, byTerritory breakdown, and row count.
    Uses Apple fiscal calendar internally (FY starts in October)."""
    return json.dumps(await service.get_finance_report(year, month), indent=2)


@mcp.tool()
async def get_finance_summary(months: int = 3) -> str:
    """Aggregate financial reports for the last N complete calendar months (default 3, max 12).
    Returns combined totalProceeds across all currencies and per-month breakdown.
    Useful for revenue trends without querying each month individually."""
    return json.dumps(await service.get_finance_summary(months), indent=2)


# ─── TestFlight ───────────────────────────────────────────────────────────────

@mcp.tool()
async def list_builds(limit: int = 10) -> str:
    """List recent TestFlight builds sorted by upload date (newest first).
    Returns version, buildNumber, processingState (PROCESSING / VALID / INVALID),
    uploadedDate, expirationDate, and minOsVersion.
    limit: 1–200, default 10. Results cached for 5 minutes."""
    return json.dumps(await service.list_builds(limit), indent=2)


@mcp.tool()
async def list_beta_groups() -> str:
    """List all TestFlight beta groups for the active app.
    Returns id, name, isInternalGroup, publicLinkEnabled, and publicLink.
    Use group IDs with add_beta_tester and remove_beta_tester."""
    return json.dumps(await service.list_beta_groups(), indent=2)


@mcp.tool()
async def add_beta_tester(email: str, first_name: str, last_name: str, group_id: str) -> str:
    """Invite a new external beta tester to a TestFlight group.
    email: tester's email address. first_name / last_name: display name.
    group_id: beta group ID from list_beta_groups → [].id.
    The tester receives an email invitation to install the app via TestFlight."""
    return json.dumps(await service.add_beta_tester(email, first_name, last_name, group_id), indent=2)


@mcp.tool()
async def remove_beta_tester(tester_id: str, group_id: str) -> str:
    """Remove a beta tester from a TestFlight group.
    tester_id: the tester's betaTester resource ID.
    group_id: beta group ID from list_beta_groups → [].id.
    The tester loses access to the build in TestFlight immediately."""
    return json.dumps(await service.remove_beta_tester(tester_id, group_id), indent=2)


# ─── Custom Product Pages ─────────────────────────────────────────────────────

@mcp.tool()
async def list_custom_product_pages() -> str:
    """List all Custom Product Pages (CPPs) for the active app.
    Returns id, name, url slug, visibility, and version state for each CPP.
    CPPs are used for Search Ads deep links and A/B testing of store metadata."""
    return json.dumps(await service.list_custom_product_pages(), indent=2)


@mcp.tool()
async def get_custom_product_page(cpp_id: str) -> str:
    """Get full details for a Custom Product Page including all versions and localizations.
    cpp_id: CPP resource ID from list_custom_product_pages → [].id.
    Returns version state, deepLink, lastModifiedDate, and per-locale promotional text."""
    return json.dumps(await service.get_custom_product_page(cpp_id), indent=2)


# ─── App Version / Submission ─────────────────────────────────────────────────

@mcp.tool()
async def list_app_versions(limit: int = 5) -> str:
    """List recent iOS App Store versions sorted by creation date (newest first).
    Returns versionString, appStoreState, platform, createdDate, and releaseType.
    Common states: READY_FOR_SALE, PREPARE_FOR_SUBMISSION, IN_REVIEW, PENDING_DEVELOPER_RELEASE.
    limit: 1–50, default 5."""
    return json.dumps(await service.list_app_versions(limit), indent=2)


@mcp.tool()
async def create_app_version(version_string: str, platform: str = "IOS") -> str:
    """Create a new App Store version record in PREPARE_FOR_SUBMISSION state.
    version_string: e.g. '2.1.0'. platform: IOS (default), MAC_OS, or TV_OS.
    Requires an existing READY_FOR_SALE version. After creating, update metadata
    via update_version, then call submit_for_review to submit."""
    return json.dumps(await service.create_app_version(version_string, platform), indent=2)


@mcp.tool()
async def submit_for_review(version_id: str) -> str:
    """Submit an App Store version for App Review.
    version_id: appStoreVersions resource ID from list_app_versions → [].id.
    REQUIRES the version to be in PREPARE_FOR_SUBMISSION state with all required
    metadata filled in (screenshots, description, keywords, whats new).
    WARNING: This action triggers App Review. Use cancel_submission to withdraw."""
    return json.dumps(await service.submit_for_review(version_id), indent=2)


@mcp.tool()
async def cancel_submission(submission_id: str) -> str:
    """Cancel an in-progress App Review submission.
    submission_id: appStoreVersionSubmissions resource ID from submit_for_review → data.id.
    WARNING: Cancelling returns the version to PREPARE_FOR_SUBMISSION state.
    The version can be re-submitted after fixing any issues."""
    return json.dumps(await service.cancel_submission(submission_id), indent=2)


# ─── In-App Purchases ─────────────────────────────────────────────────────────

@mcp.tool()
async def list_in_app_purchases() -> str:
    """List all in-app purchases (consumables, non-consumables, non-renewing subscriptions)
    for the active app. Returns id, name, productId, inAppPurchaseType, and state for each."""
    return json.dumps(await service.list_in_app_purchases(), indent=2)


@mcp.tool()
async def delete_in_app_purchase(iap_id: str) -> str:
    """Delete a draft in-app purchase from App Store Connect.
    iap_id: the in-app purchase resource ID (numeric, from the App Store Connect URL).
    Only works for IAPs in Draft state — submitted or approved IAPs cannot be deleted."""
    await service.delete_in_app_purchase(iap_id)
    return json.dumps({"status": "deleted", "iap_id": iap_id})


@mcp.tool()
async def create_in_app_purchase(name: str, product_id: str, iap_type: str) -> str:
    """Create an in-app purchase product for the active app.
    name: display name shown in App Store (e.g. '50K Credits').
    product_id: reverse-domain identifier matching App Store Connect (e.g. 'ai.girlfriend.companion.credits.50k').
    iap_type: CONSUMABLE | NON_CONSUMABLE | NON_RENEWING_SUBSCRIPTION.
    After creating, call create_iap_localization to add locale display names."""
    return json.dumps(await service.create_in_app_purchase(name, product_id, iap_type), indent=2)


@mcp.tool()
async def create_iap_localization(
    iap_id: str, name: str, locale: str, description: Optional[str] = None
) -> str:
    """Add a localization (display name + optional description) to an in-app purchase.
    iap_id: the in-app purchase resource ID from create_in_app_purchase → data.id.
    name: localized display name. locale: e.g. 'en-US', 'tr'.
    description: optional short description shown to users. Max ~45 characters."""
    return json.dumps(
        await service.create_iap_localization(iap_id, name, locale, description), indent=2
    )


# ─── Subscriptions ────────────────────────────────────────────────────────────

@mcp.tool()
async def create_subscription_group(reference_name: str) -> str:
    """Create a subscription group for the active app.
    reference_name: internal name (not shown to users), e.g. 'Premium'.
    Returns the group ID needed for create_subscription."""
    return json.dumps(await service.create_subscription_group(reference_name), indent=2)


@mcp.tool()
async def create_subscription(
    group_id: str,
    name: str,
    product_id: str,
    period: str,
    review_note: Optional[str] = None,
) -> str:
    """Create a subscription product within a subscription group.
    group_id: from create_subscription_group → data.id or list_subscription_groups → [].id.
    name: internal reference name. product_id: reverse-domain identifier.
    period: ONE_WEEK | ONE_MONTH | TWO_MONTHS | THREE_MONTHS | SIX_MONTHS | ONE_YEAR.
    review_note: optional note for App Review. After creating, call create_subscription_localization."""
    return json.dumps(
        await service.create_subscription(group_id, name, product_id, period, review_note),
        indent=2,
    )


@mcp.tool()
async def create_subscription_localization(
    subscription_id: str,
    name: str,
    locale: str,
    description: Optional[str] = None,
) -> str:
    """Add a localization to a subscription product.
    subscription_id: from create_subscription → data.id.
    name: localized display name shown to users. locale: e.g. 'en-US', 'tr'.
    description: optional promotional description."""
    return json.dumps(
        await service.create_subscription_localization(
            subscription_id, name, locale, description
        ),
        indent=2,
    )


# ─── Pricing ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_iap_price_points(iap_id: str, territory: str = "USA") -> str:
    """List available price points for a consumable/non-consumable IAP.
    iap_id: in-app purchase resource ID from create_in_app_purchase → data.id.
    territory: ISO 3166-1 alpha-3 country code, default 'USA'.
    Returns customerPrice and proceeds for each tier — use the id with set_iap_price."""
    return json.dumps(await service.get_iap_price_points(iap_id, territory), indent=2)


@mcp.tool()
async def set_iap_price(iap_id: str, price_point_id: str, territory: str = "USA") -> str:
    """Set a price for a consumable/non-consumable IAP using a raw price point ID.
    iap_id: in-app purchase resource ID.
    price_point_id: from get_iap_price_points → data[].id.
    territory: ISO 3166-1 alpha-3, default 'USA' (base territory — Apple auto-calculates others)."""
    return json.dumps(await service.set_iap_price(iap_id, price_point_id, territory), indent=2)


@mcp.tool()
async def set_iap_price_by_amount(
    iap_id: str, amount: float, territory: str = "USA"
) -> str:
    """Set a price for a consumable/non-consumable IAP by dollar amount.
    Automatically finds the matching price tier — no need to look up price_point_id manually.
    iap_id: in-app purchase resource ID from create_in_app_purchase → data.id.
    amount: target price in USD (e.g. 6.99, 24.99, 149.99).
    territory: ISO 3166-1 alpha-3, default 'USA'."""
    try:
        result = await service.set_iap_price_by_amount(iap_id, amount, territory)
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def get_subscription_price_points(subscription_id: str, territory: str = "USA") -> str:
    """List available price points for a subscription product.
    subscription_id: from create_subscription → data.id.
    territory: ISO 3166-1 alpha-3, default 'USA'.
    Returns customerPrice and proceeds for each tier — use the id with set_subscription_price."""
    return json.dumps(
        await service.get_subscription_price_points(subscription_id, territory), indent=2
    )


@mcp.tool()
async def set_subscription_price(
    subscription_id: str, price_point_id: str, territory: str = "USA"
) -> str:
    """Set a price for a subscription product using a raw price point ID.
    subscription_id: from create_subscription → data.id.
    price_point_id: from get_subscription_price_points → data[].id.
    territory: ISO 3166-1 alpha-3, default 'USA'."""
    return json.dumps(
        await service.set_subscription_price(subscription_id, price_point_id, territory),
        indent=2,
    )


@mcp.tool()
async def set_subscription_price_by_amount(
    subscription_id: str, amount: float, territory: str = "USA"
) -> str:
    """Set a price for a subscription product by dollar amount.
    Automatically finds the matching price tier — no need to look up price_point_id manually.
    subscription_id: from create_subscription → data.id.
    amount: target price in USD (e.g. 9.99, 24.99, 99.99).
    territory: ISO 3166-1 alpha-3, default 'USA'.
    Note: Apple's subscription pricing API may require the subscription to have
    complete metadata before accepting price changes."""
    try:
        result = await service.set_subscription_price_by_amount(
            subscription_id, amount, territory
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


# ─── Bundle ID Capabilities ───────────────────────────────────────────────────

@mcp.tool()
async def get_bundle_id_info(bundle_id: str) -> str:
    """Get numeric resource ID, platform, name, and seed ID for a bundle identifier.
    bundle_id: reverse-domain identifier string (e.g. 'ai.girlfriend.companion').
    Useful as a lookup step before enable_bundle_id_capability or list_bundle_id_capabilities."""
    try:
        return json.dumps(await service.get_bundle_id_info(bundle_id), indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def list_bundle_id_capabilities(bundle_id: str) -> str:
    """List all active capabilities for a bundle identifier.
    bundle_id: reverse-domain identifier string (e.g. 'ai.girlfriend.companion').
    Returns bundleId numeric ID, identifier, platform, and capabilities list with
    id, capabilityType, and settings for each active capability.
    Use capability IDs with disable_bundle_id_capability."""
    try:
        return json.dumps(await service.list_bundle_id_capabilities(bundle_id), indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def enable_bundle_id_capability(bundle_id: str, capability_type: str) -> str:
    """Enable a capability for a bundle identifier.
    bundle_id: reverse-domain identifier string (e.g. 'ai.girlfriend.companion').
    capability_type: one of APPLE_ID_AUTH, PUSH_NOTIFICATIONS, IN_APP_PURCHASE, GAME_CENTER,
    ASSOCIATED_DOMAINS, APP_ATTEST, ACCESS_WIFI_INFORMATION, DATA_PROTECTION, ICLOUD, WALLET,
    HEALTHKIT, HOMEKIT, WIRELESS_ACCESSORY_CONFIGURATION, NETWORK_EXTENSIONS, HOTSPOT,
    MULTIPATH, NFC_TAG_READING.
    Idempotent: returns status 'already_enabled' if the capability is already active."""
    try:
        return json.dumps(
            await service.enable_bundle_id_capability(bundle_id, capability_type), indent=2
        )
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def disable_bundle_id_capability(capability_id: str) -> str:
    """Remove a capability from a bundle ID.
    capability_id: the bundleIdCapabilities resource ID from list_bundle_id_capabilities → capabilities[].id
    (e.g. 'BPJKH7956F_APPLE_ID_AUTH').
    WARNING: This immediately disables the capability in the Developer Portal."""
    return json.dumps(await service.disable_bundle_id_capability(capability_id), indent=2)


if __name__ == "__main__":
    import os
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        port = int(os.environ.get("PORT", 8000))
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
