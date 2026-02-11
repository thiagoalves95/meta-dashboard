import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import requests
import pandas as pd

_REQUEST_TIMEOUT = 180
_MAX_RETRIES = 3
_QUARTER_DAYS = 90  # chunk size for daily data
_WORKERS = 4        # parallel threads

# All numeric fields that may come from Windsor.ai (kept for backwards compat)
NUMERIC_FIELDS = [
    "impressions", "clicks", "spend", "ctr", "cpc", "cpm",
    "reach", "frequency",
    "actions_link_click", "actions_landing_page_view",
    "actions_add_to_cart", "actions_initiate_checkout",
    "actions_purchase", "action_values_purchase",
    "actions_lead", "actions_complete_registration",
    "actions_view_content",
    "actions_post_engagement", "actions_post_reaction",
    "actions_comment", "actions_post_save",
    "video_views", "video_p25_watched", "video_p50_watched",
    "video_p75_watched", "video_p100_watched",
    "video_thruplay_watched",
]

# Field groups ordered from most to least expendable for fallback
_OPTIONAL_GROUPS = [
    ["video_p25_watched", "video_p50_watched", "video_p75_watched",
     "video_p100_watched", "video_thruplay_watched"],
    ["video_views"],
    ["actions_post_reaction", "actions_comment", "actions_post_save"],
    ["actions_post_engagement"],
    ["actions_complete_registration", "actions_view_content"],
    ["actions_add_to_cart", "actions_initiate_checkout"],
    ["actions_link_click", "actions_landing_page_view"],
    ["actions_lead"],
    ["quality_ranking", "engagement_rate_ranking", "conversion_rate_ranking"],
    ["promoted_post_full_picture"],
    ["desktop_feed_standard_preview_url"],
    ["image_url", "thumbnail_url"],
    ["body", "bodies", "title", "name"],
    ["campaign_objective"],
    ["publisher_platform", "platform_position"],
    ["age", "gender"],
    ["region"],
]


class WindsorClient:
    BASE_URL = "https://connectors.windsor.ai/facebook"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._numeric_fields = list(NUMERIC_FIELDS)
        self._optional_groups = list(_OPTIONAL_GROUPS)

    # ── Low-level request ─────────────────────────────────────────────────
    def _do_request(self, fields: list[str], date_from: str, date_to: str,
                    account_name: str | None = None,
                    date_aggregation: str | None = None,
                    filters: list | None = None) -> requests.Response:
        params = {
            "api_key": self.api_key,
            "date_from": date_from,
            "date_to": date_to,
            "fields": ",".join(fields),
        }
        if account_name:
            params["account_name"] = account_name
        if date_aggregation:
            params["date_aggregation"] = date_aggregation
        if filters:
            params["filter"] = json.dumps(filters)
        return requests.get(self.BASE_URL, params=params,
                            timeout=_REQUEST_TIMEOUT)

    # ── Single fetch with retry + 400 fallback ────────────────────────────
    def _fetch_single(
        self, fields: list[str], date_from: str, date_to: str,
        account_name: str | None = None,
        date_aggregation: str | None = None,
        filters: list | None = None,
    ) -> pd.DataFrame:
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._do_request(fields, date_from, date_to,
                                        account_name, date_aggregation, filters)
                if resp.status_code == 400:
                    remaining = list(fields)
                    for group in self._optional_groups:
                        before = len(remaining)
                        remaining = [f for f in remaining if f not in group]
                        if len(remaining) < before:
                            resp = self._do_request(remaining, date_from, date_to,
                                                    account_name, date_aggregation,
                                                    filters)
                            if resp.status_code != 400:
                                break

                resp.raise_for_status()
                data = resp.json()
                rows = data.get("data", [])
                if not rows:
                    return pd.DataFrame(columns=fields)

                df = pd.DataFrame(rows)
                for col in self._numeric_fields:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"], errors="coerce")
                return df

            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise last_exc

    # ── Chunked date ranges ───────────────────────────────────────────────
    @staticmethod
    def _make_chunks(date_from: str, date_to: str,
                     chunk_days: int) -> list[tuple[str, str]]:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
        chunks, cursor = [], d_from
        while cursor <= d_to:
            chunk_end = min(cursor + timedelta(days=chunk_days - 1), d_to)
            chunks.append((cursor.isoformat(), chunk_end.isoformat()))
            cursor = chunk_end + timedelta(days=1)
        return chunks

    # ── Main fetch orchestrator ───────────────────────────────────────────
    def _fetch(
        self, fields: list[str], date_from: str, date_to: str,
        account_name: str | None = None,
        date_aggregation: str | None = None,
        filters: list | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
        span = (d_to - d_from).days

        # Short range or aggregated → single request
        if span <= _QUARTER_DAYS or date_aggregation in ("month", "year"):
            if progress_cb:
                progress_cb(1, 1)
            return self._fetch_single(fields, date_from, date_to,
                                      account_name, date_aggregation, filters)

        # Long range daily data → quarterly chunks in parallel
        chunks = self._make_chunks(date_from, date_to, _QUARTER_DAYS)
        dfs = []
        done = 0

        def _worker(cf_ct):
            return self._fetch_single(fields, cf_ct[0], cf_ct[1],
                                      account_name, date_aggregation, filters)

        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            futures = {pool.submit(_worker, c): c for c in chunks}
            for fut in as_completed(futures):
                done += 1
                if progress_cb:
                    progress_cb(done, len(chunks))
                part = fut.result()
                if not part.empty:
                    dfs.append(part)

        if not dfs:
            return pd.DataFrame(columns=fields)
        return pd.concat(dfs, ignore_index=True)

    # ── Accounts ──────────────────────────────────────────────────────────
    def get_accounts(self, date_from: str, date_to: str,
                     progress_cb=None) -> list[str]:
        df = self._fetch(
            ["account_name", "spend"], date_from, date_to,
            date_aggregation="year",
            filters=[["spend", "gt", 0]],
            progress_cb=progress_cb,
        )
        if df.empty or "account_name" not in df.columns:
            return []
        return sorted(df["account_name"].dropna().unique().tolist())

    # ── Shared field lists ────────────────────────────────────────────────
    _PERFORMANCE = [
        "impressions", "clicks", "spend", "ctr", "cpc", "cpm",
        "reach", "frequency",
    ]
    _FUNNEL = [
        "actions_link_click", "actions_landing_page_view",
        "actions_add_to_cart", "actions_initiate_checkout",
        "actions_purchase", "action_values_purchase",
        "actions_lead", "actions_complete_registration",
        "actions_view_content",
    ]
    _ENGAGEMENT = [
        "actions_post_engagement", "actions_post_reaction",
        "actions_comment", "actions_post_save",
    ]
    _VIDEO = [
        "video_views", "video_p25_watched", "video_p50_watched",
        "video_p75_watched", "video_p100_watched",
        "video_thruplay_watched",
    ]
    _QUALITY = [
        "quality_ranking", "engagement_rate_ranking",
        "conversion_rate_ranking",
    ]
    _CREATIVE_ASSETS = [
        "image_url", "thumbnail_url", "promoted_post_full_picture",
        "desktop_feed_standard_preview_url",
        "body", "title", "name", "object_type", "creative_id",
    ]

    # ── Campaign level (monthly aggregated — fast) ────────────────────────
    def get_campaign_data(
        self, date_from: str, date_to: str, account_name: str | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        fields = [
            "date", "account_name", "campaign", "campaign_id",
            "campaign_status", "campaign_objective",
            *self._PERFORMANCE, *self._FUNNEL, *self._ENGAGEMENT, *self._VIDEO,
        ]
        return self._fetch(fields, date_from, date_to, account_name,
                           date_aggregation="month",
                           filters=[["spend", "gt", 0]],
                           progress_cb=progress_cb)

    # ── Campaign daily trend (minimal fields, daily) ──────────────────────
    def get_campaign_daily(
        self, date_from: str, date_to: str, account_name: str | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        fields = [
            "date", "campaign", "campaign_objective",
            "impressions", "clicks", "spend", "reach",
            "actions_purchase", "action_values_purchase",
        ]
        return self._fetch(fields, date_from, date_to, account_name,
                           filters=[["spend", "gt", 0]],
                           progress_cb=progress_cb)

    # ── Ad set level (monthly aggregated) ─────────────────────────────────
    def get_adset_data(
        self, date_from: str, date_to: str, account_name: str | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        fields = [
            "date", "account_name", "campaign", "campaign_id", "campaign_objective",
            "adset_name", "adset_id", "adset_status",
            *self._PERFORMANCE, *self._FUNNEL, *self._ENGAGEMENT, *self._VIDEO,
        ]
        return self._fetch(fields, date_from, date_to, account_name,
                           date_aggregation="month",
                           filters=[["spend", "gt", 0]],
                           progress_cb=progress_cb)

    # ── Ad / Creative level (monthly aggregated) ──────────────────────────
    def get_ad_data(
        self, date_from: str, date_to: str, account_name: str | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        fields = [
            "date", "account_name", "campaign", "campaign_id", "campaign_objective",
            "adset_name", "ad_name", "ad_id", "ad_status",
            *self._PERFORMANCE, *self._FUNNEL, *self._ENGAGEMENT, *self._VIDEO,
            *self._QUALITY, *self._CREATIVE_ASSETS,
        ]
        return self._fetch(fields, date_from, date_to, account_name,
                           date_aggregation="month",
                           filters=[["spend", "gt", 0]],
                           progress_cb=progress_cb)

    # ── Ad daily trend (for fatigue charts — minimal fields) ──────────────
    def get_ad_daily(
        self, date_from: str, date_to: str, account_name: str | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        fields = [
            "date", "ad_name", "impressions", "clicks", "spend", "frequency",
        ]
        return self._fetch(fields, date_from, date_to, account_name,
                           filters=[["spend", "gt", 0]],
                           progress_cb=progress_cb)

    # ── Demographic breakdown (monthly aggregated) ────────────────────────
    def get_demo_data(
        self, date_from: str, date_to: str, account_name: str | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        fields = [
            "date", "campaign", "campaign_objective",
            "age", "gender",
            *self._PERFORMANCE, *self._FUNNEL,
        ]
        return self._fetch(fields, date_from, date_to, account_name,
                           date_aggregation="month",
                           filters=[["spend", "gt", 0]],
                           progress_cb=progress_cb)

    # ── Placement breakdown (monthly aggregated) ──────────────────────────
    def get_placement_data(
        self, date_from: str, date_to: str, account_name: str | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        fields = [
            "date", "campaign", "campaign_objective",
            "publisher_platform", "platform_position",
            *self._PERFORMANCE, *self._FUNNEL,
        ]
        return self._fetch(fields, date_from, date_to, account_name,
                           date_aggregation="month",
                           filters=[["spend", "gt", 0]],
                           progress_cb=progress_cb)

    # ── Region breakdown (monthly aggregated) ─────────────────────────────
    def get_region_data(
        self, date_from: str, date_to: str, account_name: str | None = None,
        progress_cb=None,
    ) -> pd.DataFrame:
        fields = [
            "date", "campaign", "campaign_objective",
            "region",
            *self._PERFORMANCE, *self._FUNNEL,
        ]
        return self._fetch(fields, date_from, date_to, account_name,
                           date_aggregation="month",
                           filters=[["spend", "gt", 0]],
                           progress_cb=progress_cb)


# ═══════════════════════════════════════════════════════════════════════════════
#  GA4 CLIENT — Google Analytics 4 via Windsor.ai
# ═══════════════════════════════════════════════════════════════════════════════

def _camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


class GA4Client(WindsorClient):
    """Windsor.ai client for Google Analytics 4 data."""

    BASE_URL = "https://connectors.windsor.ai/googleanalytics4"

    _GA4_NUMERIC_FIELDS = [
        "sessions", "users", "newUsers", "bounceRate", "engagementRate",
        "screenPageViews", "averageSessionDuration", "sessionsPerUser",
        "conversions", "transactionRevenue", "eventCount",
        # snake_case variants that Windsor may return
        "new_users", "bounce_rate", "engagement_rate",
        "screen_page_views", "average_session_duration", "sessions_per_user",
        "transaction_revenue", "event_count",
    ]

    _GA4_OPTIONAL_GROUPS = [
        ["eventCount", "event_count"],
        ["sessionsPerUser", "sessions_per_user"],
        ["averageSessionDuration", "average_session_duration"],
        ["screenPageViews", "screen_page_views"],
        ["transactionRevenue", "transaction_revenue"],
        ["conversions"],
        ["engagementRate", "engagement_rate"],
        ["bounceRate", "bounce_rate"],
        ["newUsers", "new_users"],
        ["region"],
        ["country"],
        ["deviceCategory", "device_category"],
        ["pagePath", "page_path"],
        ["medium"],
        ["campaign"],
        ["source"],
    ]

    def __init__(self, api_key: str):
        super().__init__(api_key)
        self._numeric_fields = list(self._GA4_NUMERIC_FIELDS)
        self._optional_groups = list(self._GA4_OPTIONAL_GROUPS)

    # ── Override _fetch_single to add snake_case fallback + rate normalisation
    def _fetch_single(
        self, fields: list[str], date_from: str, date_to: str,
        account_name: str | None = None,
        date_aggregation: str | None = None,
        filters: list | None = None,
    ) -> pd.DataFrame:
        try:
            df = super()._fetch_single(
                fields, date_from, date_to,
                account_name, date_aggregation, filters,
            )
        except requests.exceptions.HTTPError:
            # Fallback: try snake_case field names
            snake_fields = [_camel_to_snake(f) for f in fields]
            df = super()._fetch_single(
                snake_fields, date_from, date_to,
                account_name, date_aggregation, filters,
            )
            # Rename back to camelCase for consistency
            rename_map = {_camel_to_snake(f): f for f in fields
                          if _camel_to_snake(f) != f}
            df = df.rename(columns=rename_map)

        return self._normalise_rates(df)

    @staticmethod
    def _normalise_rates(df: pd.DataFrame) -> pd.DataFrame:
        """Normalise bounceRate/engagementRate to 0-100 range."""
        for col in ("bounceRate", "engagementRate", "bounce_rate", "engagement_rate"):
            if col in df.columns:
                vals = pd.to_numeric(df[col], errors="coerce").fillna(0)
                # If max <= 1 the values are in 0-1 range → multiply by 100
                if len(vals) > 0 and vals.max() <= 1.0:
                    df[col] = vals * 100
                else:
                    df[col] = vals
        return df

    # ── GA4 data methods ──────────────────────────────────────────────────────

    def get_ga4_traffic(
        self, date_from: str, date_to: str, progress_cb=None,
    ) -> pd.DataFrame:
        """Traffic overview by source/medium (monthly aggregated)."""
        fields = [
            "date", "source", "medium", "campaign",
            "sessions", "users", "newUsers", "bounceRate",
            "engagementRate", "screenPageViews",
            "averageSessionDuration", "sessionsPerUser",
        ]
        return self._fetch(fields, date_from, date_to,
                           date_aggregation="month", progress_cb=progress_cb)

    def get_ga4_conversions(
        self, date_from: str, date_to: str, progress_cb=None,
    ) -> pd.DataFrame:
        """Conversion data by source/campaign (monthly aggregated)."""
        fields = [
            "date", "source", "campaign",
            "sessions", "conversions", "transactionRevenue",
            "users", "eventCount",
        ]
        return self._fetch(fields, date_from, date_to,
                           date_aggregation="month", progress_cb=progress_cb)

    def get_ga4_device(
        self, date_from: str, date_to: str, progress_cb=None,
    ) -> pd.DataFrame:
        """Device category breakdown (monthly aggregated)."""
        fields = [
            "date", "deviceCategory",
            "sessions", "users", "bounceRate", "engagementRate",
            "conversions", "transactionRevenue", "screenPageViews",
        ]
        return self._fetch(fields, date_from, date_to,
                           date_aggregation="month", progress_cb=progress_cb)

    def get_ga4_geo(
        self, date_from: str, date_to: str, progress_cb=None,
    ) -> pd.DataFrame:
        """Geography breakdown (monthly aggregated)."""
        fields = [
            "date", "country", "region",
            "sessions", "users", "conversions",
            "transactionRevenue", "bounceRate",
        ]
        return self._fetch(fields, date_from, date_to,
                           date_aggregation="month", progress_cb=progress_cb)

    def get_ga4_pages(
        self, date_from: str, date_to: str, progress_cb=None,
    ) -> pd.DataFrame:
        """Top pages performance (monthly aggregated)."""
        fields = [
            "date", "pagePath",
            "screenPageViews", "sessions", "users",
            "bounceRate", "engagementRate", "averageSessionDuration",
        ]
        return self._fetch(fields, date_from, date_to,
                           date_aggregation="month", progress_cb=progress_cb)

    def get_ga4_daily(
        self, date_from: str, date_to: str, progress_cb=None,
    ) -> pd.DataFrame:
        """Daily trend data by source."""
        fields = [
            "date", "source",
            "sessions", "users", "conversions",
            "transactionRevenue", "bounceRate", "engagementRate",
        ]
        return self._fetch(fields, date_from, date_to,
                           progress_cb=progress_cb)
