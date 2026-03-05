"""App Store Connect API service — JWT auth, analytics, reviews, metadata."""

import io
import json
import os
import sys
import time
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.appstoreconnect.apple.com/v1"
CACHE_TTL = 1800               # 30 min
FINANCE_CACHE_TTL = 86400      # 24h — finance reports are static once published
BUILDS_CACHE_TTL = 300         # 5 min — builds change frequently
MAX_RETRIES = 3
MAX_SALES_CONCURRENCY = 5      # guard against Apple 429s on parallel day fetches
RETRYABLE_STATUS = frozenset({429, 500, 502, 503})
MAX_REVIEW_RESPONSE_LEN = 5900  # Apple's documented limit

# Per-request credentials — set by server.py middleware for HTTP mode
_request_credentials: ContextVar[Optional[dict]] = ContextVar('request_credentials', default=None)


# ─── Observability ───────────────────────────────────────────────────────────

def _log(level: str, msg: str, **ctx) -> None:
    """Structured JSON log to stderr (MCP servers must not write to stdout)."""
    print(
        json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "level": level, "msg": msg, **ctx}),
        file=sys.stderr,
    )


# ─── Per-user state (keyed by issuer_id + key_id) ────────────────────────────

@dataclass
class _UserState:
    token: Optional[str] = None
    token_expiry: float = 0
    active_app: Optional[dict] = None
    numeric_app_id: Optional[str] = None
    analytics_ids: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_int(value: object) -> int:
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0


# ─── Service ─────────────────────────────────────────────────────────────────

class AppStoreConnectService:
    def __init__(self) -> None:
        self._states: dict[tuple, _UserState] = {}  # (issuer_id, key_id) → state
        self._sales_sem = asyncio.Semaphore(MAX_SALES_CONCURRENCY)

    # ── Credentials & per-user state ──────────────────────────────────────────

    def _get_creds(self) -> dict:
        """Return credentials from HTTP headers (multi-tenant) or env vars (local dev)."""
        creds = _request_credentials.get(None)
        if creds:
            return creds
        # Fallback: env vars for local development
        private_key = os.environ.get("APPSTORE_PRIVATE_KEY")
        if not private_key:
            path = os.environ.get("APPSTORE_PRIVATE_KEY_PATH", "")
            if path and os.path.exists(path):
                with open(path) as f:
                    private_key = f.read()
        return {
            "issuer_id": os.environ.get("APPSTORE_ISSUER_ID", ""),
            "key_id": os.environ.get("APPSTORE_KEY_ID", ""),
            "private_key": private_key or "",
            "vendor_number": os.environ.get("APPSTORE_VENDOR_NUMBER", ""),
            "app_id": os.environ.get("APPSTORE_APP_ID"),
        }

    @property
    def _state(self) -> _UserState:
        creds = self._get_creds()
        key = (creds["issuer_id"], creds["key_id"])
        if key not in self._states:
            self._states[key] = _UserState()
        return self._states[key]

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _generate_token(self) -> str:
        now = time.time()
        if self._state.token and now < self._state.token_expiry - 300:
            return self._state.token
        creds = self._get_creds()
        if not creds["issuer_id"] or not creds["key_id"] or not creds["private_key"]:
            raise ValueError("Missing App Store Connect credentials")
        # Railway/HF env vars may store newlines as literal \n — unescape them
        private_key = creds["private_key"].replace("\\n", "\n")
        expiry = now + 1200  # 20 min; refresh after 15
        self._state.token = jwt.encode(
            {"iss": creds["issuer_id"], "iat": int(now),
             "exp": int(expiry), "aud": "appstoreconnect-v1"},
            private_key,
            algorithm="ES256",
            headers={"alg": "ES256", "kid": creds["key_id"], "typ": "JWT"},
        )
        self._state.token_expiry = expiry
        return self._state.token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._generate_token()}",
                "Content-Type": "application/json"}

    # ── HTTP with retry ───────────────────────────────────────────────────────

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """All HTTP calls go through here — retry on transient errors."""
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=kwargs.pop("timeout", 30)) as c:
                    r = await c.request(method, url, **kwargs)
                if r.status_code in RETRYABLE_STATUS:
                    wait = 2 ** attempt
                    _log("warning", "Retryable HTTP error",
                         status=r.status_code, url=url, attempt=attempt + 1, retry_in_s=wait)
                    await asyncio.sleep(wait)
                    continue
                return r
            except httpx.TimeoutException as exc:
                last_exc = exc
                wait = 2 ** attempt
                _log("warning", "Request timeout", url=url, attempt=attempt + 1, retry_in_s=wait)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(wait)
        raise (last_exc or RuntimeError(f"Max retries exceeded: {url}"))

    async def _get(self, url: str, params: Optional[dict] = None) -> dict:
        r = await self._request("GET", url, headers=self._auth_headers(), params=params)
        r.raise_for_status()
        return r.json()

    async def _post(self, url: str, data: dict) -> dict:
        r = await self._request("POST", url, headers=self._auth_headers(), json=data)
        r.raise_for_status()
        return r.json()

    async def _patch(self, url: str, data: dict) -> dict:
        r = await self._request("PATCH", url, headers=self._auth_headers(), json=data)
        r.raise_for_status()
        return {} if r.status_code == 204 else r.json()

    async def _delete(self, url: str) -> None:
        r = await self._request("DELETE", url, headers=self._auth_headers())
        r.raise_for_status()

    # ── App ID resolution ─────────────────────────────────────────────────────

    async def _get_numeric_app_id(self) -> str:
        # Priority 1: app selected via set_app()
        if self._state.active_app:
            return self._state.active_app["id"]
        # Priority 2: cached numeric ID from env resolution
        if self._state.numeric_app_id:
            return self._state.numeric_app_id
        # Priority 3: backward compat — APPSTORE_APP_ID env var or header
        app_id = self._get_creds().get("app_id") or os.environ.get("APPSTORE_APP_ID")
        if not app_id:
            raise RuntimeError("No app selected. Call select_app() first.")
        if "." in app_id:
            bundle_id = os.environ.get("APPSTORE_BUNDLE_ID", app_id)
            result = await self._get(
                f"{BASE_URL}/apps",
                {"filter[bundleId]": bundle_id, "fields[apps]": "name,bundleId"},
            )
            if not result.get("data"):
                raise RuntimeError(f"App not found for bundle ID: {bundle_id}")
            app_id = result["data"][0]["id"]
        self._state.numeric_app_id = app_id
        return app_id

    # ── Multi-app management ──────────────────────────────────────────────────

    async def list_apps(self) -> dict:
        """List all apps available in this App Store Connect account."""
        return await self._get(
            f"{BASE_URL}/apps",
            {"fields[apps]": "name,bundleId,primaryLocale", "limit": 200},
        )

    async def set_app(self, app_id_or_bundle_id: str) -> dict:
        """Select the active app by numeric ID or bundle ID. Clears caches."""
        if "." in app_id_or_bundle_id:
            result = await self._get(
                f"{BASE_URL}/apps",
                {"filter[bundleId]": app_id_or_bundle_id, "fields[apps]": "name,bundleId"},
            )
            if not result.get("data"):
                raise RuntimeError(f"App not found for bundle ID: {app_id_or_bundle_id}")
            data = result["data"][0]
        else:
            result = await self._get(
                f"{BASE_URL}/apps/{app_id_or_bundle_id}",
                {"fields[apps]": "name,bundleId"},
            )
            data = result["data"]
        self._state.active_app = {
            "id": data["id"],
            "name": data["attributes"].get("name"),
            "bundleId": data["attributes"].get("bundleId"),
            "source": "api",
        }
        self._state.numeric_app_id = None  # clear env-based cache
        self._state.cache.clear()          # invalidate analytics/sales cache
        self._state.analytics_ids.clear()  # invalidate analytics request IDs
        return self._state.active_app

    def get_active_app(self) -> Optional[dict]:
        """Return the currently selected app, or env fallback info if set."""
        if self._state.active_app:
            return self._state.active_app
        app_id = self._get_creds().get("app_id") or os.environ.get("APPSTORE_APP_ID")
        if app_id:
            return {"id": app_id, "name": None, "bundleId": app_id if "." in app_id else None, "source": "env"}
        return None

    # ── Reviews ───────────────────────────────────────────────────────────────

    async def get_customer_reviews(self, limit: int = 50) -> dict:
        if not 1 <= limit <= 200:
            raise ValueError(f"limit must be 1–200, got {limit}")
        app_id = await self._get_numeric_app_id()
        return await self._get(
            f"{BASE_URL}/apps/{app_id}/customerReviews",
            {"limit": limit, "sort": "-createdDate",
             "fields[customerReviews]": "rating,title,body,reviewerNickname,createdDate,territory",
             "include": "response",
             "fields[customerReviewResponses]": "responseBody,state,lastModifiedDate"},
        )

    async def get_ratings(self) -> dict:
        app_id = await self._get_numeric_app_id()
        result = await self._get(
            f"{BASE_URL}/apps/{app_id}/customerReviews",
            {"limit": 200, "sort": "-createdDate", "fields[customerReviews]": "rating,createdDate"},
        )
        reviews = result.get("data", [])
        ratings = [r["attributes"]["rating"] for r in reviews
                   if r.get("attributes", {}).get("rating")]
        avg = round(sum(ratings) / len(ratings), 1) if ratings else 0.0
        total = result.get("meta", {}).get("paging", {}).get("total", len(ratings))
        return {
            "ratingAverage": avg,
            "ratingCount": total,
            "sampleSize": len(ratings),
            # Surface accuracy limitation when there are more than 200 reviews
            "note": "Average computed from most recent 200 reviews only" if total > 200 else None,
        }

    async def post_review_response(self, review_id: str, response_body: str) -> dict:
        if not review_id.strip():
            raise ValueError("review_id must not be empty")
        if len(response_body) > MAX_REVIEW_RESPONSE_LEN:
            raise ValueError(
                f"response_body exceeds {MAX_REVIEW_RESPONSE_LEN} chars (got {len(response_body)})"
            )
        return await self._post(
            f"{BASE_URL}/customerReviewResponses",
            {"data": {"type": "customerReviewResponses",
                      "attributes": {"responseBody": response_body},
                      "relationships": {"review": {"data": {"type": "customerReviews",
                                                            "id": review_id}}}}},
        )

    async def delete_review_response(self, response_id: str) -> None:
        if not response_id.strip():
            raise ValueError("response_id must not be empty")
        await self._delete(f"{BASE_URL}/customerReviewResponses/{response_id}")

    async def update_review_response(self, response_id: str, response_body: str) -> dict:
        """Edit an existing developer review response via PATCH."""
        if not response_id.strip():
            raise ValueError("response_id must not be empty")
        if len(response_body) > MAX_REVIEW_RESPONSE_LEN:
            raise ValueError(
                f"response_body exceeds {MAX_REVIEW_RESPONSE_LEN} chars (got {len(response_body)})"
            )
        return await self._patch(
            f"{BASE_URL}/customerReviewResponses/{response_id}",
            {"data": {"type": "customerReviewResponses", "id": response_id,
                      "attributes": {"responseBody": response_body}}},
        )

    # ── Metadata ──────────────────────────────────────────────────────────────

    async def update_app_info_localization(self, localization_id: str, attrs: dict) -> dict:
        if not localization_id.strip():
            raise ValueError("localization_id must not be empty")
        return await self._patch(
            f"{BASE_URL}/appInfoLocalizations/{localization_id}",
            {"data": {"type": "appInfoLocalizations", "id": localization_id,
                      "attributes": attrs}},
        )

    async def update_version_localization(self, localization_id: str, attrs: dict) -> dict:
        if not localization_id.strip():
            raise ValueError("localization_id must not be empty")
        return await self._patch(
            f"{BASE_URL}/appStoreVersionLocalizations/{localization_id}",
            {"data": {"type": "appStoreVersionLocalizations", "id": localization_id,
                      "attributes": attrs}},
        )

    async def get_locales(self) -> dict:
        """Lightweight locale index: IDs + locale codes for both appInfo and version locs.
        Apple's API strips relationship data when fields[] is used, so we fetch per-version."""
        app_id = await self._get_numeric_app_id()

        # appInfo localizations (name + subtitle — always editable)
        info_r = await self._get(
            f"{BASE_URL}/apps/{app_id}/appInfos",
            {"include": "appInfoLocalizations",
             "fields[appInfoLocalizations]": "name,subtitle,locale"},
        )
        info_locs = [
            {"id": loc["id"],
             "locale": loc["attributes"].get("locale"),
             "name": loc["attributes"].get("name"),
             "subtitle": loc["attributes"].get("subtitle")}
            for loc in info_r.get("included", [])
            if loc.get("type") == "appInfoLocalizations"
        ]

        # Version localizations — fetch per-version to avoid relationship stripping
        vers_r = await self._get(
            f"{BASE_URL}/apps/{app_id}/appStoreVersions",
            {"fields[appStoreVersions]": "versionString,appStoreState",
             "filter[appStoreState]": "READY_FOR_SALE,PREPARE_FOR_SUBMISSION",
             "limit": 5},
        )
        version_locs: list[dict] = []
        for ver in vers_r.get("data", []):
            ver_str = ver["attributes"].get("versionString", "")
            state = ver["attributes"].get("appStoreState", "")
            locs_r = await self._get(
                f"{BASE_URL}/appStoreVersions/{ver['id']}/appStoreVersionLocalizations",
                {"fields[appStoreVersionLocalizations]": "locale"},
            )
            version_locs.extend(
                {"id": loc["id"], "locale": loc["attributes"].get("locale"),
                 "version": ver_str, "state": state}
                for loc in locs_r.get("data", [])
            )

        return {"appInfoLocalizations": info_locs, "versionLocalizations": version_locs}

    async def get_locale_metadata(self, locale: str) -> dict:
        """Full ASO metadata for one locale: name, subtitle, keywords, description, whatsNew."""
        if not locale.strip():
            raise ValueError("locale must not be empty")
        app_id = await self._get_numeric_app_id()

        # appInfo localization for this locale
        info_r = await self._get(
            f"{BASE_URL}/apps/{app_id}/appInfos",
            {"include": "appInfoLocalizations",
             "fields[appInfoLocalizations]": "name,subtitle,locale"},
        )
        info_loc = next(
            (loc for loc in info_r.get("included", [])
             if loc.get("type") == "appInfoLocalizations"
             and loc["attributes"].get("locale", "").lower() == locale.lower()),
            None,
        )

        # Fetch version localizations per-version, filtered by locale
        vers_r = await self._get(
            f"{BASE_URL}/apps/{app_id}/appStoreVersions",
            {"fields[appStoreVersions]": "versionString,appStoreState",
             "filter[appStoreState]": "READY_FOR_SALE,PREPARE_FOR_SUBMISSION",
             "limit": 5},
        )
        version_data: list[dict] = []
        for ver in vers_r.get("data", []):
            locs_r = await self._get(
                f"{BASE_URL}/appStoreVersions/{ver['id']}/appStoreVersionLocalizations",
                {"filter[locale]": locale,
                 "fields[appStoreVersionLocalizations]": "locale,keywords,description,whatsNew"},
            )
            for loc in locs_r.get("data", []):
                version_data.append({
                    "id": loc["id"],
                    "version": ver["attributes"].get("versionString"),
                    "state": ver["attributes"].get("appStoreState"),
                    "keywords": loc["attributes"].get("keywords"),
                    "description": loc["attributes"].get("description"),
                    "whatsNew": loc["attributes"].get("whatsNew"),
                })

        if not info_loc and not version_data:
            _log("warning", "Locale not found", locale=locale)

        return {
            "locale": locale,
            "appInfo": {
                "id": info_loc["id"],
                "name": info_loc["attributes"].get("name"),
                "subtitle": info_loc["attributes"].get("subtitle"),
            } if info_loc else None,
            "versions": version_data,
        }

    # ── Analytics (DRY: one method for ONGOING + ONE_TIME_SNAPSHOT) ──────────

    async def _ensure_analytics_request(self, access_type: str) -> str:
        """Get or create an analytics report request. Idempotent — handles 409."""
        if access_type in self._state.analytics_ids:
            return self._state.analytics_ids[access_type]

        app_id = await self._get_numeric_app_id()

        # Try fetching existing request
        try:
            r = await self._get(
                f"{BASE_URL}/apps/{app_id}/analyticsReportRequests",
                {"filter[accessType]": access_type, "limit": 1},
            )
            if r.get("data"):
                self._state.analytics_ids[access_type] = str(r["data"][0]["id"])
                return self._state.analytics_ids[access_type]
        except Exception as e:
            _log("warning", "Could not list analytics requests",
                 access_type=access_type, error=str(e))

        # Create new — handle 409 (already exists) by re-fetching
        try:
            r = await self._post(
                f"{BASE_URL}/analyticsReportRequests",
                {"data": {"type": "analyticsReportRequests",
                          "attributes": {"accessType": access_type},
                          "relationships": {"app": {"data": {"type": "apps", "id": app_id}}}}},
            )
            self._state.analytics_ids[access_type] = str(r["data"]["id"])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                _log("info", "409 on analytics request create — re-fetching",
                     access_type=access_type)
                fb = await self._get(
                    f"{BASE_URL}/apps/{app_id}/analyticsReportRequests", {"limit": 50}
                )
                for item in fb.get("data", []):
                    if item.get("attributes", {}).get("accessType") == access_type:
                        self._state.analytics_ids[access_type] = str(item["id"])
                        break
                if access_type not in self._state.analytics_ids:
                    raise RuntimeError(
                        f"No {access_type} analytics request found after 409"
                    )
            else:
                _log("error", "Failed to create analytics request",
                     access_type=access_type, status=e.response.status_code)
                raise

        return self._state.analytics_ids[access_type]

    async def _download_instance(self, instance_id: str) -> list[dict]:
        """Download + parse a gzip TSV analytics report instance. Retries on failure."""
        import csv
        import gzip

        for attempt in range(MAX_RETRIES):
            try:
                r = await self._request(
                    "GET",
                    f"{BASE_URL}/analyticsReportInstances/{instance_id}",
                    headers={**self._auth_headers(), "Accept": "application/a-gzip"},
                    follow_redirects=True,
                    timeout=60,
                )
                r.raise_for_status()
                try:
                    text = gzip.decompress(r.content).decode("utf-8")
                except Exception:
                    text = r.content.decode("utf-8", errors="replace")
                return list(csv.DictReader(io.StringIO(text), delimiter="\t"))
            except Exception as exc:
                if attempt == MAX_RETRIES - 1:
                    _log("error", "Instance download failed permanently",
                         instance_id=instance_id, error=str(exc))
                    raise
                wait = 2 ** attempt
                _log("warning", "Instance download error, retrying",
                     instance_id=instance_id, attempt=attempt + 1, retry_in_s=wait)
                await asyncio.sleep(wait)
        return []  # unreachable but satisfies type checker

    async def get_analytics(self, days: int = 30) -> dict:
        """Engagement analytics via Apple Analytics Reports API."""
        if not 1 <= days <= 365:
            raise ValueError(f"days must be 1–365, got {days}")

        cache_key = f"analytics:{days}"
        cached = self._state.cache.get(cache_key)
        if cached and time.time() - cached["ts"] < CACHE_TTL:
            return cached["data"]

        try:
            ongoing_id = await self._ensure_analytics_request("ONGOING")
            snapshot_id: Optional[str] = None
            try:
                snapshot_id = await self._ensure_analytics_request("ONE_TIME_SNAPSHOT")
            except Exception as e:
                _log("warning", "Snapshot request unavailable", error=str(e))

            async def get_report_map(req_id: str) -> dict[str, str]:
                r = await self._get(
                    f"{BASE_URL}/analyticsReportRequests/{req_id}/reports", {"limit": 200}
                )
                return {x["attributes"]["name"]: x["id"] for x in r.get("data", [])}

            ongoing_map = await get_report_map(ongoing_id)
            snapshot_map = await get_report_map(snapshot_id) if snapshot_id else {}
            all_names = list({**ongoing_map, **snapshot_map}.keys())

            if not all_names:
                return {"status": "pending",
                        "message": "Apple is preparing analytics reports. Try again in a few hours."}

            result: dict = {"status": "ok", "days": days, "availableReports": all_names}

            async def fetch_report(name: str, field_map: dict[str, str]) -> list[dict]:
                for rmap in (ongoing_map, snapshot_map):
                    if name not in rmap:
                        continue
                    try:
                        inst_r = await self._get(
                            f"{BASE_URL}/analyticsReports/{rmap[name]}/instances",
                            {"filter[granularity]": "DAILY", "limit": min(days, 90)},
                        )
                    except Exception as e:
                        _log("warning", "Failed to fetch report instances",
                             report=name, error=str(e))
                        continue
                    instances = inst_r.get("data", [])
                    if not instances:
                        continue

                    async def dl(inst: dict) -> Optional[dict]:
                        try:
                            rows = await self._download_instance(inst["id"])
                            agg: dict = {"date": inst["attributes"].get("processingDate", "")}
                            for key, col in field_map.items():
                                cols = col.split("|")
                                agg[key] = sum(
                                    _parse_int(row.get(c, 0))
                                    for row in rows for c in cols if c in row
                                )
                            return agg
                        except Exception as e:
                            _log("warning", "Instance download failed",
                                 instance_id=inst["id"], error=str(e))
                            return None

                    raw = await asyncio.gather(*[dl(i) for i in instances])
                    daily = sorted([r for r in raw if r], key=lambda x: x["date"])
                    if daily:
                        return daily[-days:]
                return []

            eng = await fetch_report(
                "App Store Discovery and Engagement Standard",
                {"impressions": "Impressions|Total Impressions",
                 "pageViews": "Product Page Views|Page Views",
                 "appUnits": "App Units|Total Downloads",
                 "redownloads": "Redownloads"},
            )
            if eng:
                pv = sum(d.get("pageViews", 0) for d in eng)
                au = sum(d.get("appUnits", 0) for d in eng)
                result["engagement"] = {
                    "daily": eng,
                    "totals": {
                        "impressions": sum(d.get("impressions", 0) for d in eng),
                        "pageViews": pv,
                        "appUnits": au,
                        "redownloads": sum(d.get("redownloads", 0) for d in eng),
                        "conversionRate": round(au / pv * 100, 1) if pv else 0.0,
                    },
                }

            sess = await fetch_report(
                "App Sessions Standard",
                {"sessions": "Sessions|Total Sessions",
                 "activeDevices": "Active Devices|Unique Devices"},
            )
            crashes = await fetch_report(
                "App Runtime Usage", {"crashes": "Crashes|Total Crashes"}
            )
            if sess or crashes:
                merged: dict[str, dict] = {}
                for row in sess:
                    merged.setdefault(row["date"],
                                      {"date": row["date"], "sessions": 0,
                                       "activeDevices": 0, "crashes": 0})
                    merged[row["date"]].update(
                        {"sessions": row.get("sessions", 0),
                         "activeDevices": row.get("activeDevices", 0)}
                    )
                for row in crashes:
                    merged.setdefault(row["date"],
                                      {"date": row["date"], "sessions": 0,
                                       "activeDevices": 0, "crashes": 0})
                    merged[row["date"]]["crashes"] = row.get("crashes", 0)
                usage = sorted(merged.values(), key=lambda x: x["date"])
                result["usage"] = {
                    "daily": usage,
                    "totals": {k: sum(d.get(k, 0) for d in usage)
                               for k in ("sessions", "activeDevices", "crashes")},
                }

            if not result.get("engagement") and not result.get("usage"):
                result["status"] = "pending"
                result["message"] = (
                    f"Apple is processing analytics data. "
                    f"{len(all_names)} report types configured."
                )

            self._state.cache[cache_key] = {"data": result, "ts": time.time()}
            return result

        except Exception as e:
            _log("error", "get_analytics failed", days=days, error=str(e))
            return {"status": "error", "message": str(e)}

    async def get_sales(self, days: int = 30) -> dict:
        """Daily sales report: downloads, redownloads, updates, subscriptions, revenue."""
        if not 1 <= days <= 365:
            raise ValueError(f"days must be 1–365, got {days}")

        cache_key = f"sales:{days}"
        cached = self._state.cache.get(cache_key)
        if cached and time.time() - cached["ts"] < CACHE_TTL:
            return cached["data"]

        import csv as csv_mod
        import gzip
        from datetime import date as dt_date, timedelta

        try:
            vendor = self._get_creds().get("vendor_number") or os.environ.get("APPSTORE_VENDOR_NUMBER")
            if not vendor:
                raise RuntimeError("APPSTORE_VENDOR_NUMBER env var (or x-appstore-vendor-number header) is required for sales reports.")
            daily: dict[str, dict] = {}
            failed_days: list[str] = []

            async def fetch_day(d: dt_date) -> None:
                async with self._sales_sem:  # cap concurrency → prevent 429
                    for attempt in range(MAX_RETRIES):
                        try:
                            r = await self._request(
                                "GET",
                                f"{BASE_URL}/salesReports",
                                headers={**self._auth_headers(), "Accept": "application/a-gzip"},
                                params={
                                    "filter[reportType]": "SALES",
                                    "filter[reportSubType]": "SUMMARY",
                                    "filter[frequency]": "DAILY",
                                    "filter[reportDate]": str(d),
                                    "filter[vendorNumber]": vendor,
                                },
                            )
                            if r.status_code == 404:
                                return  # no sales data for this day — normal
                            if r.status_code in RETRYABLE_STATUS:
                                wait = 2 ** attempt
                                _log("warning", "Sales report retryable error",
                                     date=str(d), status=r.status_code, retry_in_s=wait)
                                await asyncio.sleep(wait)
                                continue
                            r.raise_for_status()
                            break
                        except httpx.TimeoutException:
                            if attempt == MAX_RETRIES - 1:
                                _log("error", "Sales report timeout after retries", date=str(d))
                                failed_days.append(str(d))
                                return
                            await asyncio.sleep(2 ** attempt)
                    else:
                        failed_days.append(str(d))
                        return

                    try:
                        text = gzip.decompress(r.content).decode("utf-8")
                    except Exception:
                        text = r.content.decode("utf-8", errors="replace")

                    rows = list(csv_mod.DictReader(io.StringIO(text), delimiter="\t"))
                    entry = daily.setdefault(
                        str(d),
                        {"date": str(d), "downloads": 0, "redownloads": 0,
                         "updates": 0, "iap": 0, "subscriptions": 0, "revenue": 0.0},
                    )
                    redownload_currencies = {
                        row.get("Currency of Proceeds", "").strip()
                        for row in rows
                        if row.get("Product Type Identifier") in ("7F", "7")
                    }
                    for row in rows:
                        ptype = row.get("Product Type Identifier", "")
                        currency = row.get("Currency of Proceeds", "").strip()
                        units = int(row.get("Units") or 0)
                        proceeds = float(row.get("Developer Proceeds") or 0)
                        if ptype in ("1F", "1", "GK1"):
                            entry["downloads"] += units
                            entry["revenue"] += proceeds   # 0 for free apps, non-zero for paid
                        elif ptype == "3F" and currency not in redownload_currencies:
                            entry["downloads"] += units
                            entry["revenue"] += proceeds
                        elif ptype in ("7F", "7"):
                            entry["redownloads"] += units
                        elif ptype == "1T":
                            entry["updates"] += units
                        elif ptype in ("F1", "2", "3", "IAY", "FY", "GKY"):
                            entry["subscriptions"] += units
                            entry["revenue"] += proceeds
                        elif ptype in ("IA1", "IA9", "IANA", "IAC"):
                            # Non-subscription in-app purchases
                            entry["iap"] += units
                            entry["revenue"] += proceeds

            dates = [dt_date.today() - timedelta(days=i) for i in range(2, days + 2)]
            await asyncio.gather(*[fetch_day(d) for d in dates])

            sorted_daily = sorted(daily.values(), key=lambda x: x["date"])
            totals = {k: sum(d[k] for d in sorted_daily)
                      for k in ("downloads", "redownloads", "updates", "iap", "subscriptions")}
            totals["revenue"] = round(sum(d["revenue"] for d in sorted_daily), 2)

            result: dict = {"status": "ok", "days": days, "daily": sorted_daily, "totals": totals}
            if failed_days:
                result["warnings"] = f"Failed to fetch {len(failed_days)} day(s): {failed_days[:5]}"
                _log("warning", "Some sales days failed to fetch", count=len(failed_days),
                     days=failed_days[:5])

            self._state.cache[cache_key] = {"data": result, "ts": time.time()}
            return result

        except Exception as e:
            _log("error", "get_sales failed", days=days, error=str(e))
            return {"status": "error", "message": str(e)}


    # ── Subscriptions ─────────────────────────────────────────────────────────

    async def list_subscription_groups(self) -> list:
        """List subscription groups with all subscriptions included."""
        app_id = await self._get_numeric_app_id()
        result = await self._get(
            f"{BASE_URL}/apps/{app_id}/subscriptionGroups",
            {
                "include": "subscriptions",
                "fields[subscriptions]": "name,productId,state,subscriptionPeriod,groupLevel,reviewNote",
                "limit": 50,
            },
        )
        sub_map = {
            item["id"]: item
            for item in result.get("included", [])
            if item.get("type") == "subscriptions"
        }
        groups = []
        for g in result.get("data", []):
            sub_rels = g.get("relationships", {}).get("subscriptions", {}).get("data", [])
            subscriptions = []
            for rel in sub_rels:
                s = sub_map.get(rel["id"])
                if not s:
                    continue
                attrs = s.get("attributes", {})
                subscriptions.append({
                    "id": s["id"],
                    "name": attrs.get("name"),
                    "productId": attrs.get("productId"),
                    "state": attrs.get("state"),
                    "subscriptionPeriod": attrs.get("subscriptionPeriod"),
                    "groupLevel": attrs.get("groupLevel"),
                })
            groups.append({
                "id": g["id"],
                "referenceName": g["attributes"].get("referenceName"),
                "subscriptions": subscriptions,
            })
        return groups

    # ── In-App Events ──────────────────────────────────────────────────────────

    async def list_app_events(self) -> list:
        """List in-app events with localizations."""
        app_id = await self._get_numeric_app_id()
        result = await self._get(
            f"{BASE_URL}/apps/{app_id}/appEvents",
            {
                "include": "localizations",
                "fields[appEventLocalizations]": "name,shortDescription,locale",
                "limit": 50,
            },
        )
        loc_map: dict = {}
        for item in result.get("included", []):
            if item.get("type") == "appEventLocalizations":
                loc_map[item["id"]] = item["attributes"]
        events = []
        for ev in result.get("data", []):
            attrs = ev.get("attributes", {})
            loc_rels = ev.get("relationships", {}).get("localizations", {}).get("data", [])
            localizations = [
                {"id": r["id"], **loc_map[r["id"]]}
                for r in loc_rels if r["id"] in loc_map
            ]
            events.append({
                "id": ev["id"],
                "referenceName": attrs.get("referenceName"),
                "badge": attrs.get("badge"),
                "eventState": attrs.get("eventState"),
                "purchaseRequirement": attrs.get("purchaseRequirement"),
                "purpose": attrs.get("purpose"),
                "priority": attrs.get("priority"),
                "primaryLocale": attrs.get("primaryLocale"),
                "localizations": localizations,
            })
        return events

    # ── Phased Release ─────────────────────────────────────────────────────────

    async def get_phased_release(self, version_id: str) -> dict:
        """Get phased release status for a version (data=null if not active)."""
        return await self._get(
            f"{BASE_URL}/appStoreVersions/{version_id}/appStoreVersionPhasedRelease",
        )

    async def create_phased_release(self, version_id: str) -> dict:
        """Enable 7-day phased release for a PENDING_DEVELOPER_RELEASE version."""
        return await self._post(
            f"{BASE_URL}/appStoreVersionPhasedReleases",
            {
                "data": {
                    "type": "appStoreVersionPhasedReleases",
                    "attributes": {"phasedReleaseState": "ACTIVE"},
                    "relationships": {
                        "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}
                    },
                }
            },
        )

    async def update_phased_release(self, phased_release_id: str, state: str) -> dict:
        """Update phased release: ACTIVE (resume), PAUSED, or COMPLETE (100% rollout)."""
        return await self._patch(
            f"{BASE_URL}/appStoreVersionPhasedReleases/{phased_release_id}",
            {
                "data": {
                    "type": "appStoreVersionPhasedReleases",
                    "id": phased_release_id,
                    "attributes": {"phasedReleaseState": state},
                }
            },
        )

    # ── Finance Reports ───────────────────────────────────────────────────────

    async def get_finance_report(self, year: int, month: int) -> dict:
        """Monthly financial report: proceeds by territory and currency (calendar month)."""
        import csv as csv_mod
        import gzip
        from datetime import date

        if not 1 <= month <= 12:
            raise ValueError(f"month must be 1–12, got {month}")
        today = date.today()
        if year < 2010 or (year > today.year) or (year == today.year and month >= today.month):
            raise ValueError(f"No finance data available for {year}-{month:02d}")

        cache_key = f"finance:{year}:{month}"
        cached = self._state.cache.get(cache_key)
        if cached and time.time() - cached["ts"] < FINANCE_CACHE_TTL:
            return cached["data"]

        vendor = self._get_creds().get("vendor_number") or os.environ.get("APPSTORE_VENDOR_NUMBER")
        if not vendor:
            raise RuntimeError("APPSTORE_VENDOR_NUMBER env var (or x-appstore-vendor-number header) is required for finance reports.")

        try:
            r = await self._request(
                "GET",
                f"{BASE_URL}/financeReports",
                headers={**self._auth_headers(), "Accept": "application/a-gzip"},
                params={
                    "filter[reportType]": "FINANCIAL",
                    "filter[reportDate]": f"{year}-{month:02d}",
                    "filter[regionCode]": "WW",
                    "filter[vendorNumber]": vendor,
                },
                timeout=60,
            )
            if r.status_code == 404:
                return {"status": "not_found", "year": year, "month": month,
                        "message": "No finance data for this period."}
            r.raise_for_status()

            try:
                text = gzip.decompress(r.content).decode("utf-8")
            except Exception:
                text = r.content.decode("utf-8", errors="replace")

            rows = list(csv_mod.DictReader(io.StringIO(text), delimiter="\t"))
            # Skip Apple's summary footer rows (Start Date = "Total_*")
            data_rows = [
                row for row in rows
                if not str(row.get("Start Date", "")).startswith("Total_")
            ]

            by_territory: dict[str, dict] = {}
            total_proceeds: dict[str, float] = {}

            for row in data_rows:
                territory = (row.get("Country Of Sale") or "Unknown").strip()
                proceeds = float(row.get("Extended Partner Share") or 0)
                currency = (row.get("Partner Share Currency") or "USD").strip()
                quantity = _parse_int(row.get("Quantity") or 0)

                tkey = f"{territory}:{currency}"
                if tkey not in by_territory:
                    by_territory[tkey] = {"territory": territory, "currency": currency,
                                          "proceeds": 0.0, "units": 0}
                by_territory[tkey]["proceeds"] += proceeds
                by_territory[tkey]["units"] += quantity
                total_proceeds[currency] = total_proceeds.get(currency, 0.0) + proceeds

            territories = sorted(by_territory.values(), key=lambda x: -x["proceeds"])
            result = {
                "status": "ok",
                "year": year,
                "month": month,
                "totalProceeds": {k: round(v, 2) for k, v in total_proceeds.items()},
                "byTerritory": [{**t, "proceeds": round(t["proceeds"], 2)} for t in territories],
                "rowCount": len(data_rows),
            }
            self._state.cache[cache_key] = {"data": result, "ts": time.time()}
            return result

        except Exception as e:
            _log("error", "get_finance_report failed", year=year, month=month, error=str(e))
            return {"status": "error", "year": year, "month": month, "message": str(e)}

    async def get_finance_summary(self, months: int = 3) -> dict:
        """Aggregate finance reports for the last N complete calendar months."""
        from datetime import date

        if not 1 <= months <= 12:
            raise ValueError(f"months must be 1–12, got {months}")

        today = date.today()
        targets: list[tuple[int, int]] = []
        # Start from 2 months ago (last complete month may still be processing)
        for i in range(2, months + 2):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            targets.append((y, m))

        results = await asyncio.gather(*[self.get_finance_report(y, m) for y, m in targets])

        combined_proceeds: dict[str, float] = {}
        monthly: list[dict] = []
        for (y, m), r in zip(targets, results):
            if r.get("status") == "ok":
                for currency, amount in r.get("totalProceeds", {}).items():
                    combined_proceeds[currency] = combined_proceeds.get(currency, 0.0) + amount
                monthly.append({
                    "year": y, "month": m,
                    "totalProceeds": r.get("totalProceeds", {}),
                    "rowCount": r.get("rowCount", 0),
                })
            else:
                monthly.append({"year": y, "month": m, "status": r.get("status"), "message": r.get("message")})

        return {
            "status": "ok",
            "months": months,
            "totalProceeds": {k: round(v, 2) for k, v in combined_proceeds.items()},
            "monthly": sorted(monthly, key=lambda x: (x["year"], x["month"])),
        }

    # ── TestFlight ────────────────────────────────────────────────────────────

    async def list_builds(self, limit: int = 10) -> list:
        """List recent TestFlight builds: version, buildNumber, processingState, dates."""
        if not 1 <= limit <= 200:
            raise ValueError(f"limit must be 1–200, got {limit}")
        app_id = await self._get_numeric_app_id()

        cache_key = f"builds:{limit}"
        cached = self._state.cache.get(cache_key)
        if cached and time.time() - cached["ts"] < BUILDS_CACHE_TTL:
            return cached["data"]

        # Note: fields[builds] strips relationship data — use top-level /v1/builds with app filter
        # to keep preReleaseVersion relationships intact (same pattern as get_locales)
        result = await self._get(
            f"{BASE_URL}/builds",
            {
                "filter[app]": app_id,
                "limit": limit,
                "include": "preReleaseVersion",
                "fields[preReleaseVersions]": "version,platform",
            },
        )

        pre_release_map = {
            item["id"]: item["attributes"]
            for item in result.get("included", [])
            if item.get("type") == "preReleaseVersions"
        }

        builds = []
        for b in result.get("data", []):
            attrs = b.get("attributes", {})
            pre_rel_data = b.get("relationships", {}).get("preReleaseVersion", {}).get("data")
            pre_rel = pre_release_map.get(pre_rel_data["id"]) or {} if pre_rel_data else {}
            builds.append({
                "id": b["id"],
                "version": pre_rel.get("version"),
                "platform": pre_rel.get("platform"),
                "buildNumber": attrs.get("version"),
                "processingState": attrs.get("processingState"),
                "uploadedDate": attrs.get("uploadedDate"),
                "expirationDate": attrs.get("expirationDate"),
                "minOsVersion": attrs.get("minOsVersion"),
            })

        self._state.cache[cache_key] = {"data": builds, "ts": time.time()}
        return builds

    async def list_beta_groups(self) -> list:
        """List TestFlight beta groups: name, isInternal, testerCount, publicLink."""
        app_id = await self._get_numeric_app_id()

        cache_key = "beta_groups"
        cached = self._state.cache.get(cache_key)
        if cached and time.time() - cached["ts"] < BUILDS_CACHE_TTL:
            return cached["data"]

        result = await self._get(
            f"{BASE_URL}/betaGroups",
            {
                "filter[app]": app_id,
                "fields[betaGroups]": "name,isInternalGroup,publicLink,publicLinkEnabled,publicLinkLimit",
                "limit": 50,
            },
        )

        groups = [
            {
                "id": g["id"],
                "name": g["attributes"].get("name"),
                "isInternalGroup": g["attributes"].get("isInternalGroup"),
                "publicLink": g["attributes"].get("publicLink"),
                "publicLinkEnabled": g["attributes"].get("publicLinkEnabled"),
            }
            for g in result.get("data", [])
        ]

        self._state.cache[cache_key] = {"data": groups, "ts": time.time()}
        return groups

    async def add_beta_tester(self, email: str, first_name: str, last_name: str, group_id: str) -> dict:
        """Invite a new beta tester to a TestFlight group."""
        if not email.strip():
            raise ValueError("email must not be empty")
        if not group_id.strip():
            raise ValueError("group_id must not be empty")
        result = await self._post(
            f"{BASE_URL}/betaTesters",
            {
                "data": {
                    "type": "betaTesters",
                    "attributes": {"email": email, "firstName": first_name, "lastName": last_name},
                    "relationships": {
                        "betaGroups": {"data": [{"type": "betaGroups", "id": group_id}]}
                    },
                }
            },
        )
        self._state.cache.pop("beta_groups", None)
        return result

    async def remove_beta_tester(self, tester_id: str, group_id: str) -> dict:
        """Remove a beta tester from a TestFlight group."""
        if not tester_id.strip():
            raise ValueError("tester_id must not be empty")
        if not group_id.strip():
            raise ValueError("group_id must not be empty")
        r = await self._request(
            "DELETE",
            f"{BASE_URL}/betaGroups/{group_id}/relationships/betaTesters",
            headers=self._auth_headers(),
            json={"data": [{"type": "betaTesters", "id": tester_id}]},
        )
        r.raise_for_status()
        self._state.cache.pop("beta_groups", None)
        return {"status": "removed", "tester_id": tester_id, "group_id": group_id}

    # ── Custom Product Pages ──────────────────────────────────────────────────

    async def list_custom_product_pages(self) -> list:
        """List all Custom Product Pages: name, url, visibility, version state."""
        app_id = await self._get_numeric_app_id()
        try:
            result = await self._get(
                f"{BASE_URL}/apps/{app_id}/appCustomProductPages",
                {
                    "include": "appCustomProductPageVersions",
                    "fields[appCustomProductPages]": "name,url,visible,appCustomProductPageVersions",
                    "fields[appCustomProductPageVersions]": "version,state,deepLink",
                    "limit": 100,
                },
            )
        except Exception as e:
            # 404 means CPP feature not enabled or no pages exist
            _log("info", "list_custom_product_pages returned no data", error=str(e))
            return []

        version_map = {
            item["id"]: item["attributes"]
            for item in result.get("included", [])
            if item.get("type") == "appCustomProductPageVersions"
        }

        pages = []
        for p in result.get("data", []):
            attrs = p.get("attributes", {})
            version_rels = p.get("relationships", {}).get("appCustomProductPageVersions", {}).get("data", [])
            versions = [
                {"id": v["id"], **version_map.get(v["id"], {})}
                for v in version_rels
            ]
            pages.append({
                "id": p["id"],
                "name": attrs.get("name"),
                "url": attrs.get("url"),
                "visible": attrs.get("visible"),
                "versions": versions,
            })
        return pages

    async def get_custom_product_page(self, cpp_id: str) -> dict:
        """Get CPP detail including all versions and per-locale promotional text."""
        if not cpp_id.strip():
            raise ValueError("cpp_id must not be empty")

        result = await self._get(
            f"{BASE_URL}/appCustomProductPages/{cpp_id}",
            {
                "include": "appCustomProductPageVersions",
                "fields[appCustomProductPages]": "name,url,visible,appCustomProductPageVersions",
                "fields[appCustomProductPageVersions]": "version,state,deepLink",
            },
        )

        data = result.get("data", {})
        attrs = data.get("attributes", {})

        versions = []
        for v in result.get("included", []):
            if v.get("type") != "appCustomProductPageVersions":
                continue
            v_attrs = v.get("attributes", {})
            try:
                locs_r = await self._get(
                    f"{BASE_URL}/appCustomProductPageVersions/{v['id']}/appCustomProductPageLocalizations",
                    {"fields[appCustomProductPageLocalizations]": "locale,promotionalText"},
                )
                locales = [
                    {"id": loc["id"],
                     "locale": loc["attributes"].get("locale"),
                     "promotionalText": loc["attributes"].get("promotionalText")}
                    for loc in locs_r.get("data", [])
                ]
            except Exception as e:
                _log("warning", "Failed to fetch CPP localizations", version_id=v["id"], error=str(e))
                locales = []
            versions.append({
                "id": v["id"],
                "version": v_attrs.get("version"),
                "state": v_attrs.get("state"),
                "deepLink": v_attrs.get("deepLink"),
                "locales": locales,
            })

        return {
            "id": data.get("id"),
            "name": attrs.get("name"),
            "url": attrs.get("url"),
            "visible": attrs.get("visible"),
            "versions": versions,
        }

    # ── App Version / Submission ──────────────────────────────────────────────

    async def list_app_versions(self, limit: int = 5) -> list:
        """List recent App Store versions: versionString, state, platform, dates."""
        if not 1 <= limit <= 50:
            raise ValueError(f"limit must be 1–50, got {limit}")
        app_id = await self._get_numeric_app_id()
        result = await self._get(
            f"{BASE_URL}/apps/{app_id}/appStoreVersions",
            {
                "filter[platform]": "IOS",
                "fields[appStoreVersions]": "versionString,appStoreState,platform,createdDate,releaseType,downloadable",
                "limit": limit,
            },
        )
        return [
            {
                "id": v["id"],
                "versionString": v["attributes"].get("versionString"),
                "appStoreState": v["attributes"].get("appStoreState"),
                "platform": v["attributes"].get("platform"),
                "createdDate": v["attributes"].get("createdDate"),
                "releaseType": v["attributes"].get("releaseType"),
                "downloadable": v["attributes"].get("downloadable"),
            }
            for v in result.get("data", [])
        ]

    async def create_app_version(self, version_string: str, platform: str = "IOS") -> dict:
        """Create a new App Store version record. Requires an existing READY_FOR_SALE version."""
        if not version_string.strip():
            raise ValueError("version_string must not be empty")
        platform = platform.upper()
        if platform not in ("IOS", "MAC_OS", "TV_OS"):
            raise ValueError(f"platform must be IOS, MAC_OS, or TV_OS, got {platform}")
        app_id = await self._get_numeric_app_id()
        return await self._post(
            f"{BASE_URL}/appStoreVersions",
            {
                "data": {
                    "type": "appStoreVersions",
                    "attributes": {"platform": platform, "versionString": version_string},
                    "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
                }
            },
        )

    async def submit_for_review(self, version_id: str) -> dict:
        """Submit an App Store version for App Review.
        Requires PREPARE_FOR_SUBMISSION state — cannot be undone without cancel_submission."""
        if not version_id.strip():
            raise ValueError("version_id must not be empty")
        return await self._post(
            f"{BASE_URL}/appStoreVersionSubmissions",
            {
                "data": {
                    "type": "appStoreVersionSubmissions",
                    "relationships": {
                        "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}
                    },
                }
            },
        )

    async def cancel_submission(self, submission_id: str) -> dict:
        """Cancel an in-progress App Review submission. Cannot be undone."""
        if not submission_id.strip():
            raise ValueError("submission_id must not be empty")
        await self._delete(f"{BASE_URL}/appStoreVersionSubmissions/{submission_id}")
        return {"status": "cancelled", "submission_id": submission_id}

    # ── In-App Purchases ──────────────────────────────────────────────────────

    BASE_URL_V2 = "https://api.appstoreconnect.apple.com/v2"

    async def delete_in_app_purchase(self, iap_id: str) -> None:
        """Delete a draft in-app purchase by its resource ID."""
        await self._delete(f"{self.BASE_URL_V2}/inAppPurchases/{iap_id}")

    async def list_in_app_purchases(self) -> dict:
        """List all in-app purchases (consumables, non-consumables, non-renewing) for the active app."""
        app_id = await self._get_numeric_app_id()
        return await self._get(
            f"{BASE_URL}/apps/{app_id}/inAppPurchasesV2",
            {"fields[inAppPurchases]": "name,productId,inAppPurchaseType,state", "limit": 200},
        )

    async def create_in_app_purchase(
        self, name: str, product_id: str, iap_type: str
    ) -> dict:
        """Create a consumable, non-consumable, or non-renewing in-app purchase.
        iap_type: CONSUMABLE | NON_CONSUMABLE | NON_RENEWING_SUBSCRIPTION"""
        valid_types = {"CONSUMABLE", "NON_CONSUMABLE", "NON_RENEWING_SUBSCRIPTION"}
        if iap_type not in valid_types:
            raise ValueError(f"iap_type must be one of {valid_types}")
        app_id = await self._get_numeric_app_id()
        return await self._post(
            f"{self.BASE_URL_V2}/inAppPurchases",
            {
                "data": {
                    "type": "inAppPurchases",
                    "attributes": {
                        "name": name,
                        "productId": product_id,
                        "inAppPurchaseType": iap_type,
                    },
                    "relationships": {
                        "app": {"data": {"type": "apps", "id": app_id}}
                    },
                }
            },
        )

    async def create_iap_localization(
        self, iap_id: str, name: str, locale: str, description: Optional[str] = None
    ) -> dict:
        """Add a localization (display name + optional description) to an in-app purchase."""
        attrs: dict = {"name": name, "locale": locale}
        if description:
            attrs["description"] = description
        return await self._post(
            f"{BASE_URL}/inAppPurchaseLocalizations",
            {
                "data": {
                    "type": "inAppPurchaseLocalizations",
                    "attributes": attrs,
                    "relationships": {
                        "inAppPurchase": {"data": {"type": "inAppPurchases", "id": iap_id}}
                    },
                }
            },
        )

    # ── Subscription Groups & Subscriptions ───────────────────────────────────

    async def create_subscription_group(self, reference_name: str) -> dict:
        """Create a subscription group for the active app."""
        app_id = await self._get_numeric_app_id()
        return await self._post(
            f"{BASE_URL}/subscriptionGroups",
            {
                "data": {
                    "type": "subscriptionGroups",
                    "attributes": {"referenceName": reference_name},
                    "relationships": {
                        "app": {"data": {"type": "apps", "id": app_id}}
                    },
                }
            },
        )

    async def create_subscription(
        self,
        group_id: str,
        name: str,
        product_id: str,
        period: str,
        review_note: Optional[str] = None,
    ) -> dict:
        """Create a subscription within a group.
        period: ONE_WEEK | ONE_MONTH | TWO_MONTHS | THREE_MONTHS | SIX_MONTHS | ONE_YEAR"""
        valid_periods = {
            "ONE_WEEK", "ONE_MONTH", "TWO_MONTHS",
            "THREE_MONTHS", "SIX_MONTHS", "ONE_YEAR",
        }
        if period not in valid_periods:
            raise ValueError(f"period must be one of {valid_periods}")
        attrs: dict = {"name": name, "productId": product_id, "subscriptionPeriod": period}
        if review_note:
            attrs["reviewNote"] = review_note
        return await self._post(
            f"{BASE_URL}/subscriptions",
            {
                "data": {
                    "type": "subscriptions",
                    "attributes": attrs,
                    "relationships": {
                        "group": {"data": {"type": "subscriptionGroups", "id": group_id}}
                    },
                }
            },
        )

    async def create_subscription_localization(
        self,
        subscription_id: str,
        name: str,
        locale: str,
        description: Optional[str] = None,
    ) -> dict:
        """Add a localization to a subscription."""
        attrs: dict = {"name": name, "locale": locale}
        if description:
            attrs["description"] = description
        return await self._post(
            f"{BASE_URL}/subscriptionLocalizations",
            {
                "data": {
                    "type": "subscriptionLocalizations",
                    "attributes": attrs,
                    "relationships": {
                        "subscription": {"data": {"type": "subscriptions", "id": subscription_id}}
                    },
                }
            },
        )


    # ── Pricing ───────────────────────────────────────────────────────────────

    async def get_iap_price_points(self, iap_id: str, territory: str = "USA") -> dict:
        """List ALL available price points for a consumable/non-consumable IAP in a territory.
        Paginates automatically — Apple returns up to 800 tiers."""
        import urllib.parse as _urlparse

        all_data: list = []
        params: dict = {
            "filter[territory]": territory,
            "fields[inAppPurchasePricePoints]": "customerPrice,proceeds,territory",
            "limit": 200,
        }
        while True:
            page = await self._get(
                f"{self.BASE_URL_V2}/inAppPurchases/{iap_id}/pricePoints", params
            )
            all_data.extend(page.get("data", []))
            next_link = page.get("links", {}).get("next")
            if not next_link:
                break
            cursor = _urlparse.parse_qs(
                _urlparse.urlparse(next_link).query
            ).get("cursor", [None])[0]
            if not cursor:
                break
            params["cursor"] = cursor
        return {"data": all_data}

    async def set_iap_price_by_amount(
        self, iap_id: str, amount: float, territory: str = "USA"
    ) -> dict:
        """Find the price point matching *amount* (USD) and set it as the IAP price.
        Raises ValueError if no exact match is found (within $0.01)."""
        points = (await self.get_iap_price_points(iap_id, territory)).get("data", [])
        match = next(
            (p for p in points if abs(float(p["attributes"]["customerPrice"]) - amount) < 0.01),
            None,
        )
        if not match:
            available = sorted(
                {float(p["attributes"]["customerPrice"]) for p in points}
            )
            raise ValueError(
                f"No price point found for ${amount} in {territory}. "
                f"Closest options: {available[-5:]}"
            )
        return await self.set_iap_price(iap_id, match["id"], territory)

    async def set_iap_price(
        self, iap_id: str, price_point_id: str, territory: str = "USA"
    ) -> dict:
        """Set a price for a consumable/non-consumable IAP using a price point ID.
        Get price_point_id from get_iap_price_points first."""
        return await self._post(
            f"{BASE_URL}/inAppPurchasePriceSchedules",
            {
                "data": {
                    "type": "inAppPurchasePriceSchedules",
                    "relationships": {
                        "inAppPurchase": {
                            "data": {"type": "inAppPurchases", "id": iap_id}
                        },
                        "baseTerritory": {
                            "data": {"type": "territories", "id": territory}
                        },
                        "manualPrices": {
                            "data": [{"type": "inAppPurchasePrices", "id": "${price}"}]
                        },
                    },
                },
                "included": [
                    {
                        "type": "inAppPurchasePrices",
                        "id": "${price}",
                        "attributes": {"startDate": None},
                        "relationships": {
                            "inAppPurchasePricePoint": {
                                "data": {
                                    "type": "inAppPurchasePricePoints",
                                    "id": price_point_id,
                                }
                            }
                        },
                    }
                ],
            },
        )

    async def get_subscription_price_points(
        self, subscription_id: str, territory: str = "USA"
    ) -> dict:
        """List ALL available price points for a subscription in a territory.
        Paginates automatically — Apple returns up to 800 tiers."""
        import urllib.parse as _urlparse

        all_data: list = []
        params: dict = {
            "filter[territory]": territory,
            "fields[subscriptionPricePoints]": "customerPrice,proceeds,territory",
            "limit": 200,
        }
        while True:
            page = await self._get(
                f"{BASE_URL}/subscriptions/{subscription_id}/pricePoints", params
            )
            all_data.extend(page.get("data", []))
            next_link = page.get("links", {}).get("next")
            if not next_link:
                break
            cursor = _urlparse.parse_qs(
                _urlparse.urlparse(next_link).query
            ).get("cursor", [None])[0]
            if not cursor:
                break
            params["cursor"] = cursor
        return {"data": all_data}

    async def set_subscription_price_by_amount(
        self, subscription_id: str, amount: float, territory: str = "USA"
    ) -> dict:
        """Find the price point matching *amount* (USD) and set it as the subscription price.
        Raises ValueError if no exact match is found (within $0.01)."""
        points = (
            await self.get_subscription_price_points(subscription_id, territory)
        ).get("data", [])
        match = next(
            (p for p in points if abs(float(p["attributes"]["customerPrice"]) - amount) < 0.01),
            None,
        )
        if not match:
            available = sorted(
                {float(p["attributes"]["customerPrice"]) for p in points}
            )
            raise ValueError(
                f"No price point found for ${amount} in {territory}. "
                f"Closest options: {available[-5:]}"
            )
        return await self.set_subscription_price(subscription_id, match["id"], territory)

    async def set_subscription_price(
        self, subscription_id: str, price_point_id: str, territory: str = "USA"
    ) -> dict:
        """Set a price for a subscription using a price point ID.
        Get price_point_id from get_subscription_price_points first."""
        return await self._post(
            f"{BASE_URL}/subscriptionPrices",
            {
                "data": {
                    "type": "subscriptionPrices",
                    "attributes": {
                        "startDate": None,
                        "preserveCurrentPrice": False,
                    },
                    "relationships": {
                        "subscription": {
                            "data": {"type": "subscriptions", "id": subscription_id}
                        },
                        "subscriptionPricePoint": {
                            "data": {
                                "type": "subscriptionPricePoints",
                                "id": price_point_id,
                            }
                        },
                        "territory": {
                            "data": {"type": "territories", "id": territory}
                        },
                    },
                }
            },
        )


    # ── Bundle ID Capabilities ────────────────────────────────────────────────

    VALID_CAPABILITY_TYPES = frozenset({
        "APPLE_ID_AUTH", "PUSH_NOTIFICATIONS", "IN_APP_PURCHASE", "GAME_CENTER",
        "ASSOCIATED_DOMAINS", "APP_ATTEST", "ACCESS_WIFI_INFORMATION",
        "DATA_PROTECTION", "ICLOUD", "WALLET", "HEALTHKIT", "HOMEKIT",
        "WIRELESS_ACCESSORY_CONFIGURATION", "NETWORK_EXTENSIONS",
        "HOTSPOT", "MULTIPATH", "NFC_TAG_READING",
    })

    async def _get_numeric_bundle_id(self, identifier: str) -> str:
        """Bundle identifier string → numeric bundleId resource ID."""
        data = await self._get(
            f"{BASE_URL}/bundleIds",
            {
                "filter[identifier]": identifier,
                "fields[bundleIds]": "identifier,name,platform,seedId",
            },
        )
        items = data.get("data", [])
        if not items:
            raise ValueError(f"No bundle ID found for identifier: {identifier}")
        return items[0]["id"]

    async def get_bundle_id_info(self, identifier: str) -> dict:
        """Get bundle ID numeric ID, platform, and seed ID for a given identifier string."""
        if not identifier.strip():
            raise ValueError("identifier must not be empty")
        data = await self._get(
            f"{BASE_URL}/bundleIds",
            {
                "filter[identifier]": identifier,
                "fields[bundleIds]": "identifier,name,platform,seedId",
            },
        )
        items = data.get("data", [])
        if not items:
            raise ValueError(f"No bundle ID found for identifier: {identifier}")
        item = items[0]
        attrs = item.get("attributes", {})
        return {
            "id": item["id"],
            "identifier": attrs.get("identifier"),
            "name": attrs.get("name"),
            "platform": attrs.get("platform"),
            "seedId": attrs.get("seedId"),
        }

    async def list_bundle_id_capabilities(self, identifier: str) -> dict:
        """List all active capabilities for a bundle ID."""
        if not identifier.strip():
            raise ValueError("identifier must not be empty")
        data = await self._get(
            f"{BASE_URL}/bundleIds",
            {
                "filter[identifier]": identifier,
                "include": "bundleIdCapabilities",
                "fields[bundleIds]": "identifier,name,platform,seedId",
                "fields[bundleIdCapabilities]": "capabilityType,settings",
            },
        )
        items = data.get("data", [])
        if not items:
            raise ValueError(f"No bundle ID found for identifier: {identifier}")
        item = items[0]
        attrs = item.get("attributes", {})
        capabilities = [
            {
                "id": cap["id"],
                "capabilityType": cap["attributes"].get("capabilityType"),
                "settings": cap["attributes"].get("settings", []),
            }
            for cap in data.get("included", [])
            if cap.get("type") == "bundleIdCapabilities"
        ]
        return {
            "bundleId": item["id"],
            "identifier": attrs.get("identifier"),
            "name": attrs.get("name"),
            "platform": attrs.get("platform"),
            "capabilities": capabilities,
        }

    async def enable_bundle_id_capability(self, identifier: str, capability_type: str) -> dict:
        """Enable a capability for a bundle ID. Idempotent — 409 Conflict means already active."""
        if not identifier.strip():
            raise ValueError("identifier must not be empty")
        if capability_type not in self.VALID_CAPABILITY_TYPES:
            raise ValueError(
                f"capability_type must be one of {sorted(self.VALID_CAPABILITY_TYPES)}"
            )
        numeric_id = await self._get_numeric_bundle_id(identifier)
        r = await self._request(
            "POST",
            f"{BASE_URL}/bundleIdCapabilities",
            headers=self._auth_headers(),
            json={
                "data": {
                    "type": "bundleIdCapabilities",
                    "attributes": {"capabilityType": capability_type, "settings": []},
                    "relationships": {
                        "bundleId": {"data": {"type": "bundleIds", "id": numeric_id}}
                    },
                }
            },
        )
        if r.status_code == 409:
            # Already enabled — idempotent success
            return {
                "status": "already_enabled",
                "identifier": identifier,
                "capabilityType": capability_type,
            }
        r.raise_for_status()
        data = r.json()
        cap = data.get("data", {})
        return {
            "status": "enabled",
            "id": cap.get("id"),
            "identifier": identifier,
            "capabilityType": cap.get("attributes", {}).get("capabilityType"),
        }

    async def disable_bundle_id_capability(self, capability_id: str) -> dict:
        """Remove a capability from a bundle ID by its resource ID."""
        if not capability_id.strip():
            raise ValueError("capability_id must not be empty")
        await self._delete(f"{BASE_URL}/bundleIdCapabilities/{capability_id}")
        return {"status": "deleted", "capability_id": capability_id}


service = AppStoreConnectService()
