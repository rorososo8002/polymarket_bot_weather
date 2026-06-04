from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import requests
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from .models import OrderBook, OrderLevel, RawMarket
from .orderbook_validation import valid_level_size, valid_orderbook_price
from .stations import TRADING_READY_STATION_MAP
from .weather_client import parse_weather_question


class PolymarketClient:
    def __init__(self, gamma_base: str, clob_base: str, timeout: float = 15.0) -> None:
        self.gamma_base = gamma_base.rstrip("/")
        self.clob_base = clob_base.rstrip("/")
        self.timeout = timeout
        self.web_base = "https://polymarket.com"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def discover_weather_markets(self, max_pages: int = 8, page_size: int = 100) -> list[RawMarket]:
        """Fetch weather events and expand every supported binary market found."""
        markets, seen_event_ids = self._discover_weather_markets_from_category_pages()

        seen_market_ids = {market.market_id for market in markets}
        url = f"{self.gamma_base}/events"
        page_size = max(1, int(page_size))
        page_limit = max(0, int(max_pages))
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

    def _discover_weather_markets_from_category_pages(self) -> tuple[list[RawMarket], set[str]]:
        slugs: list[str] = []
        for path in ("/weather/temperature", "/weather/high-temperature", "/weather/low-temperature"):
            try:
                slugs.extend(self._event_slugs_from_page(path))
            except (requests.HTTPError, RetryError, requests.RequestException):
                continue

        markets: list[RawMarket] = []
        seen_market_ids: set[str] = set()
        seen_event_ids: set[str] = set()
        for slug in dict.fromkeys(slugs):
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
            if not isinstance(row, dict) or not self._is_weather_market(row):
                continue
            market = self._parse_market(row, event_id=event_id or None, event_slug=event_slug)
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
    def _is_weather_market(cls, row: dict[str, Any]) -> bool:
        question = str(row.get("question") or row.get("title") or "")
        parsed = parse_weather_question(question)
        if not parsed.city or parsed.city.lower() not in TRADING_READY_STATION_MAP:
            return False
        if parsed.city and parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator:
            return True
        metadata = cls._flatten_metadata_text(row)
        supported_metadata = re.search(r"\b(weather|climate|temperature)\b", metadata)
        return bool(supported_metadata and parsed.city and parsed.confidence >= 0.70)

    def _parse_market(
        self,
        row: dict[str, Any],
        *,
        event_id: str | None = None,
        event_slug: str | None = None,
    ) -> RawMarket:
        yes_token_id, no_token_id = self._token_ids_from_token_objects(row.get("tokens"))
        if not (yes_token_id and no_token_id):
            yes_token_id, no_token_id = self._token_ids_from_outcomes(
                row.get("outcomes"),
                row.get("clobTokenIds"),
            )

        return RawMarket(
            market_id=str(row.get("id") or row.get("market") or row.get("conditionId") or "unknown"),
            question=str(row.get("question") or row.get("title") or ""),
            slug=row.get("slug"),
            active=bool(row.get("active", True)),
            closed=bool(row.get("closed", False)),
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            condition_id=row.get("conditionId") or row.get("condition_id"),
            event_id=event_id or row.get("eventId") or row.get("event_id"),
            event_slug=event_slug or row.get("eventSlug") or row.get("event_slug"),
            raw=row,
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
