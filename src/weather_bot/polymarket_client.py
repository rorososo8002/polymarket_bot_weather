from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote

import requests
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from .market_rules import build_market_rule_provenance
from .models import MarketTradability, OrderBook, OrderLevel, RawMarket
from .orderbook_validation import valid_level_size, valid_orderbook_price
from .stations import TRADING_READY_STATION_MAP
from .weather_client import parse_weather_question


TRUE_API_BOOL_VALUES = {"true", "1", "yes", "y", "on"}
FALSE_API_BOOL_VALUES = {"false", "0", "no", "n", "off"}
CATEGORY_SLUG_DISCOVERY_LIMIT = 80
GROUPED_TEMPERATURE_TITLE_RE = re.compile(
    r"^\s*(?P<metric>highest|lowest)\s+temperature\s+in\s+(?P<city>.+?)\s+on\s+(?P<date>[^?]+?)\s*\??\s*$",
    re.IGNORECASE,
)
TEMPERATURE_OUTCOME_LABEL_RE = re.compile(
    r"\b\d{1,3}(?:\.\d+)?\s*(?:-|to)?\s*\d{0,3}(?:\.\d+)?\s*(?:[\u00b0\u00ba\u02da]?\s*[cf]\b|\u2103|\u2109)",
    re.IGNORECASE,
)
GROUPED_OUTCOME_TITLE_KEYS = (
    "groupItemTitle",
    "group_item_title",
    "outcomeTitle",
    "outcome_title",
    "outcome",
    "name",
)


def parse_api_bool(value: Any, *, default: bool | None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_API_BOOL_VALUES:
            return True
        if normalized in FALSE_API_BOOL_VALUES:
            return False
    return None


def _first_present_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class PolymarketClient:
    def __init__(
        self,
        gamma_base: str,
        clob_base: str,
        timeout: float = 15.0,
        tradability_cache_ttl_seconds: float = 30.0,
    ) -> None:
        self.gamma_base = gamma_base.rstrip("/")
        self.clob_base = clob_base.rstrip("/")
        self.timeout = timeout
        self.web_base = "https://polymarket.com"
        self.tradability_cache_ttl_seconds = max(0.0, float(tradability_cache_ttl_seconds))
        self._tradability_cache: dict[str, tuple[float, MarketTradability]] = {}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def discover_weather_markets(self, max_pages: int = 8, page_size: int = 100) -> list[RawMarket]:
        """Fetch weather events and expand every supported binary market found."""
        page_size = max(1, int(page_size))
        page_limit = max(0, int(max_pages))
        category_slug_limit = min(CATEGORY_SLUG_DISCOVERY_LIMIT, page_size * page_limit)
        markets, seen_event_ids = self._discover_weather_markets_from_category_pages(max_slugs=category_slug_limit)

        seen_market_ids = {market.market_id for market in markets}
        url = f"{self.gamma_base}/events"
        offset = 0
        pages_scanned = 0

        while pages_scanned < page_limit:
            params = {
                "active": "true",
                "closed": "false",
                "limit": str(page_size),
                "offset": str(offset),
            }
            try:
                data = self._get(url, params=params)
            except (requests.HTTPError, RetryError):
                if pages_scanned > 0 or markets:
                    break
                raise
            if isinstance(data, dict):
                rows = data.get("events") or data.get("data") or []
            else:
                rows = data
            if not rows:
                break

            for event in rows:
                if not isinstance(event, dict):
                    continue
                event_markets, event_id = self._parse_weather_event(event)
                if not event_markets or event_id in seen_event_ids:
                    continue
                for market in event_markets:
                    if market.market_id in seen_market_ids:
                        continue
                    markets.append(market)
                    seen_market_ids.add(market.market_id)
                seen_event_ids.add(event_id)

            offset += page_size
            pages_scanned += 1
        return markets

    def _discover_weather_markets_from_category_pages(self, *, max_slugs: int | None = None) -> tuple[list[RawMarket], set[str]]:
        slugs: list[str] = []
        for path in ("/weather/temperature", "/weather/high-temperature", "/weather/low-temperature"):
            try:
                slugs.extend(self._event_slugs_from_page(path))
            except (requests.HTTPError, RetryError, requests.RequestException):
                continue

        markets: list[RawMarket] = []
        seen_market_ids: set[str] = set()
        seen_event_ids: set[str] = set()
        unique_slugs = list(dict.fromkeys(slugs))
        if max_slugs is not None:
            unique_slugs = unique_slugs[: max(0, int(max_slugs))]
        for slug in unique_slugs:
            try:
                event = self._get(f"{self.gamma_base}/events/slug/{quote(slug, safe='')}")
            except (requests.HTTPError, RetryError, requests.RequestException, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            event.setdefault("slug", slug)
            event_markets, event_id = self._parse_weather_event(event)
            if not event_markets or event_id in seen_event_ids:
                continue
            for market in event_markets:
                if market.market_id in seen_market_ids:
                    continue
                markets.append(market)
                seen_market_ids.add(market.market_id)
            seen_event_ids.add(event_id)
        return markets, seen_event_ids

    def _parse_weather_event(self, event: dict[str, Any]) -> tuple[list[RawMarket], str]:
        rows = event.get("markets")
        if not isinstance(rows, list):
            return [], ""
        event_id = str(event.get("id") or event.get("eventId") or event.get("slug") or "")
        event_slug = str(event.get("slug") or "") or None
        markets: list[RawMarket] = []
        for row in rows:
            if not isinstance(row, dict) or not self._is_weather_market(row, event=event):
                continue
            if not self._is_new_entry_candidate(row):
                continue
            market = self._parse_market(row, event_id=event_id or None, event_slug=event_slug, event=event)
            if market.yes_token_id and market.no_token_id:
                markets.append(market)
        if not event_id and markets:
            event_id = "|".join(market.market_id for market in markets)
        return markets, event_id

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def _get_web_text(self, path: str) -> str:
        resp = requests.get(f"{self.web_base}{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def _event_slugs_from_page(self, path: str) -> list[str]:
        html = self._get_web_text(path)
        slugs = re.findall(r'href=["\']/event/([^"\'?#]+)', html)
        return [slug for slug in dict.fromkeys(slugs) if self._looks_like_weather_event_slug(slug)]

    @staticmethod
    def _looks_like_weather_event_slug(slug: str) -> bool:
        return bool(
            re.search(
                r"(^|-)("
                r"highest-temperature|lowest-temperature|temperature"
                r")($|-)",
                slug,
            )
        )

    def get_market(self, market_id: str) -> RawMarket:
        data = self._get(f"{self.gamma_base}/markets/{market_id}")
        if isinstance(data, dict) and isinstance(data.get("market"), dict):
            data = data["market"]
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected market response for {market_id}")
        return self._parse_market(data)

    def get_clob_market_tradability(self, condition_id: str) -> MarketTradability:
        normalized_condition_id = str(condition_id or "").strip()
        if not normalized_condition_id:
            raise ValueError("condition_id is required for CLOB tradability lookup")

        now = time.monotonic()
        cached = self._tradability_cache.get(normalized_condition_id)
        if cached is not None and now - cached[0] < self.tradability_cache_ttl_seconds:
            return cached[1]

        data = self._get(
            f"{self.clob_base}/clob-markets/{quote(normalized_condition_id, safe='')}"
        )
        if isinstance(data, dict) and isinstance(data.get("market"), dict):
            data = data["market"]
        elif isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected CLOB market response for {normalized_condition_id}")

        tradability = MarketTradability(
            active=parse_api_bool(_first_present_value(data, "active", "is_active"), default=None),
            closed=parse_api_bool(_first_present_value(data, "closed", "is_closed"), default=None),
            archived=parse_api_bool(_first_present_value(data, "archived", "is_archived"), default=None),
            accepting_orders=parse_api_bool(
                _first_present_value(data, "accepting_orders", "acceptingOrders", "accepting"),
                default=None,
            ),
            enable_order_book=parse_api_bool(
                _first_present_value(
                    data,
                    "enable_order_book",
                    "enableOrderBook",
                    "orderbook_enabled",
                    "orderBookEnabled",
                ),
                default=None,
            ),
            ready=parse_api_bool(data.get("ready"), default=None),
            funded=parse_api_bool(data.get("funded"), default=None),
            condition_id=_optional_text(
                _first_present_value(data, "condition_id", "conditionId", "market")
            )
            or normalized_condition_id,
            source="clob",
            raw=data,
        )
        self._tradability_cache[normalized_condition_id] = (now, tradability)
        return tradability

    @staticmethod
    def _flatten_metadata_text(row: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("category", "subcategory"):
            value = row.get(key)
            if value:
                parts.append(str(value))
        for key in ("tags", "categories"):
            values = row.get(key) or []
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, dict):
                        parts.extend(str(value.get(name, "")) for name in ("label", "name", "slug"))
                    else:
                        parts.append(str(value))
        return " ".join(parts).lower()

    @classmethod
    def _normalized_weather_question(cls, row: dict[str, Any], event: dict[str, Any] | None = None) -> str:
        question = str(row.get("question") or row.get("title") or "")
        parsed = parse_weather_question(question)
        if parsed.city and parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator:
            return question

        event = event or {}
        event_title = str(
            event.get("title")
            or event.get("question")
            or event.get("name")
            or row.get("eventTitle")
            or row.get("event_title")
            or ""
        ).strip()
        outcome_label = cls._grouped_outcome_label(row)
        synthesized = cls._synthesize_grouped_temperature_question(event_title, outcome_label)
        return synthesized or question

    @staticmethod
    def _grouped_outcome_label(row: dict[str, Any]) -> str:
        for key in GROUPED_OUTCOME_TITLE_KEYS:
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and TEMPERATURE_OUTCOME_LABEL_RE.search(text):
                return text
        return ""

    @staticmethod
    def _synthesize_grouped_temperature_question(event_title: str, outcome_label: str) -> str:
        if not event_title or not outcome_label:
            return ""
        match = GROUPED_TEMPERATURE_TITLE_RE.match(event_title)
        if not match:
            return ""
        metric = match.group("metric").lower()
        city = match.group("city").strip()
        date = match.group("date").strip()
        if not city or not date:
            return ""
        return f"Will the {metric} temperature in {city} be {outcome_label} on {date}?"

    @classmethod
    def _is_weather_market(cls, row: dict[str, Any], event: dict[str, Any] | None = None) -> bool:
        question = cls._normalized_weather_question(row, event=event)
        parsed = parse_weather_question(question)
        if not parsed.city or parsed.city.lower() not in TRADING_READY_STATION_MAP:
            return False
        if parsed.city and parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator:
            return True
        metadata = cls._flatten_metadata_text(row)
        supported_metadata = re.search(r"\b(weather|climate|temperature)\b", metadata)
        return bool(supported_metadata and parsed.city and parsed.confidence >= 0.70)

    @staticmethod
    def _is_new_entry_candidate(row: dict[str, Any]) -> bool:
        active = parse_api_bool(row.get("active"), default=True)
        closed = parse_api_bool(row.get("closed"), default=False)
        accepting_orders = parse_api_bool(
            _first_present_value(row, "accepting_orders", "acceptingOrders"),
            default=None,
        )
        return active is True and closed is False and accepting_orders is not False

    def _parse_market(
        self,
        row: dict[str, Any],
        *,
        event_id: str | None = None,
        event_slug: str | None = None,
        event: dict[str, Any] | None = None,
    ) -> RawMarket:
        yes_token_id, no_token_id = self._token_ids_from_token_objects(row.get("tokens"))
        if not (yes_token_id and no_token_id):
            yes_token_id, no_token_id = self._token_ids_from_outcomes(
                row.get("outcomes"),
                row.get("clobTokenIds"),
            )
        active = parse_api_bool(row.get("active"), default=True)
        closed = parse_api_bool(row.get("closed"), default=False)
        accepting_orders = parse_api_bool(
            _first_present_value(row, "accepting_orders", "acceptingOrders"),
            default=None,
        )
        enable_order_book = parse_api_bool(
            _first_present_value(row, "enable_order_book", "enableOrderBook"),
            default=None,
        )
        archived = parse_api_bool(row.get("archived"), default=None)
        ready = parse_api_bool(row.get("ready"), default=None)
        funded = parse_api_bool(row.get("funded"), default=None)
        accepting_order_timestamp = _optional_text(
            _first_present_value(row, "accepting_order_timestamp", "acceptingOrderTimestamp")
        )
        end_date_iso = _optional_text(
            _first_present_value(row, "endDateIso", "endDate", "end_date_iso", "end_date")
        )
        market_id = str(row.get("id") or row.get("market") or row.get("conditionId") or "unknown")
        question = self._normalized_weather_question(row, event=event)
        slug = row.get("slug")
        resolved_event_id = event_id or row.get("eventId") or row.get("event_id")
        resolved_event_slug = event_slug or row.get("eventSlug") or row.get("event_slug")

        return RawMarket(
            market_id=market_id,
            question=question,
            slug=slug,
            active=active is True,
            closed=closed is not False,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            condition_id=row.get("conditionId") or row.get("condition_id"),
            event_id=resolved_event_id,
            event_slug=resolved_event_slug,
            raw=row,
            rule_provenance=build_market_rule_provenance(
                market_id=market_id,
                question=question,
                slug=slug,
                event_slug=resolved_event_slug,
                raw=row,
                event=event,
            ),
            accepting_orders=accepting_orders,
            enable_order_book=enable_order_book,
            ready=ready,
            funded=funded,
            archived=archived,
            end_date_iso=end_date_iso,
            accepting_order_timestamp=accepting_order_timestamp,
            tradability_source="gamma",
        )

    @classmethod
    def _token_ids_from_token_objects(cls, value: Any) -> tuple[str | None, str | None]:
        if not isinstance(value, list) or not value:
            return None, None

        pairs: dict[str, str] = {}
        for token in value:
            if not isinstance(token, dict):
                return None, None
            outcome = cls._normalize_binary_outcome(token.get("outcome") or token.get("name"))
            token_id = str(token.get("token_id") or token.get("tokenId") or token.get("id") or "").strip()
            if outcome not in {"YES", "NO"} or not token_id or outcome in pairs:
                return None, None
            pairs[outcome] = token_id
        return cls._complete_binary_token_pair(pairs)

    @classmethod
    def _token_ids_from_outcomes(cls, outcomes_value: Any, token_ids_value: Any) -> tuple[str | None, str | None]:
        outcomes = cls._list_value(outcomes_value)
        token_ids = cls._list_value(token_ids_value)
        if len(outcomes) != 2 or len(token_ids) != 2:
            return None, None

        pairs: dict[str, str] = {}
        for outcome_value, token_id_value in zip(outcomes, token_ids):
            outcome = cls._normalize_binary_outcome(outcome_value)
            token_id = str(token_id_value).strip()
            if outcome not in {"YES", "NO"} or not token_id or outcome in pairs:
                return None, None
            pairs[outcome] = token_id
        return cls._complete_binary_token_pair(pairs)

    @staticmethod
    def _list_value(value: Any) -> list[Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        return value if isinstance(value, list) else []

    @staticmethod
    def _normalize_binary_outcome(value: Any) -> str | None:
        outcome = str(value or "").strip().upper()
        return outcome if outcome in {"YES", "NO"} else None

    @staticmethod
    def _complete_binary_token_pair(pairs: dict[str, str]) -> tuple[str | None, str | None]:
        if set(pairs) != {"YES", "NO"}:
            return None, None
        return pairs["YES"], pairs["NO"]

    def get_order_book(self, token_id: str) -> OrderBook:
        url = f"{self.clob_base}/book"
        data = self._get(url, params={"token_id": token_id})
        bids = self._parse_levels(data.get("bids") or [])
        asks = self._parse_levels(data.get("asks") or [])
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)
        return OrderBook(
            token_id=token_id,
            bids=bids,
            asks=asks,
            market=data.get("market"),
            timestamp=str(data.get("timestamp")) if data.get("timestamp") is not None else None,
            min_order_size=self._optional_float(data.get("min_order_size")),
            tick_size=self._optional_float(data.get("tick_size")),
            neg_risk=data.get("neg_risk") if isinstance(data.get("neg_risk"), bool) else None,
            book_hash=data.get("hash"),
            last_trade_price=self._optional_float(data.get("last_trade_price")),
            raw=data if isinstance(data, dict) else None,
        )

    @staticmethod
    def _parse_levels(rows: Any) -> list[OrderLevel]:
        if not isinstance(rows, list):
            return []
        levels: list[OrderLevel] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = valid_orderbook_price(row.get("price"))
            size = valid_level_size(row.get("size"), allow_zero=False)
            if price is None or size is None:
                continue
            levels.append(OrderLevel(price=price, size=size))
        return levels

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
