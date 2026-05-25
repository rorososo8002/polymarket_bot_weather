from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import requests
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from .models import OrderBook, OrderLevel, RawMarket
from .stations import STATION_MAP
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

    def discover_weather_markets(self, limit: int = 20, max_pages: int | None = None) -> list[RawMarket]:
        """Fetch active markets and keep likely weather-related questions."""
        markets = self._discover_weather_markets_from_category_pages(limit)
        if len(markets) >= limit:
            return markets[:limit]

        seen_ids = {market.market_id for market in markets}
        url = f"{self.gamma_base}/markets"
        page_size = max(limit * 5, 50)
        page_limit = max_pages if max_pages is not None else max(4, min(limit, 8))
        offset = 0
        pages_scanned = 0

        while len(markets) < limit and pages_scanned < page_limit:
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
                rows = data.get("markets") or data.get("data") or []
            else:
                rows = data
            if not rows:
                break

            for row in rows:
                if not isinstance(row, dict) or not self._is_weather_market(row):
                    continue
                market = self._parse_market(row)
                if market.market_id not in seen_ids and (market.yes_token_id or market.no_token_id):
                    markets.append(market)
                    seen_ids.add(market.market_id)
                if len(markets) >= limit:
                    break

            offset += page_size
            pages_scanned += 1
        return markets

    def _discover_weather_markets_from_category_pages(self, limit: int) -> list[RawMarket]:
        slugs: list[str] = []
        for path in ("/weather/temperature", "/weather/high-temperature", "/weather/low-temperature", "/weather/precipitation"):
            try:
                slugs.extend(self._event_slugs_from_page(path))
            except (requests.HTTPError, RetryError, requests.RequestException):
                continue

        markets: list[RawMarket] = []
        seen_ids: set[str] = set()
        for slug in dict.fromkeys(slugs):
            try:
                event = self._get(f"{self.gamma_base}/events/slug/{quote(slug, safe='')}")
            except (requests.HTTPError, RetryError, requests.RequestException, ValueError):
                continue
            rows = event.get("markets") if isinstance(event, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or not self._is_weather_market(row):
                    continue
                market = self._parse_market(row)
                if market.market_id in seen_ids or not (market.yes_token_id or market.no_token_id):
                    continue
                markets.append(market)
                seen_ids.add(market.market_id)
                if len(markets) >= limit:
                    return markets
        return markets

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
                r"highest-temperature|lowest-temperature|temperature|rainfall|rain|precipitation|snowfall|snow"
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
    def _flatten_market_text(row: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("question", "title", "slug", "description", "category", "subcategory"):
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
        if not parsed.city or parsed.city.lower() not in STATION_MAP:
            return False
        if parsed.city and parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator:
            return True
        if parsed.city and parsed.variable in {"precipitation", "snow"}:
            return True
        metadata = cls._flatten_metadata_text(row)
        supported_metadata = re.search(r"\b(weather|climate|temperature|rainfall|snowfall|precipitation)\b", metadata)
        return bool(supported_metadata and parsed.city and parsed.confidence >= 0.70)

    def _parse_market(self, row: dict[str, Any]) -> RawMarket:
        tokens = row.get("tokens") or row.get("clobTokenIds") or []
        yes_token_id = None
        no_token_id = None

        if isinstance(tokens, list) and tokens and isinstance(tokens[0], dict):
            for token in tokens:
                outcome = str(token.get("outcome") or token.get("name") or "").upper()
                token_id = str(token.get("token_id") or token.get("tokenId") or token.get("id") or "")
                if outcome == "YES":
                    yes_token_id = token_id
                elif outcome == "NO":
                    no_token_id = token_id
        else:
            raw_ids = row.get("clobTokenIds")
            if isinstance(raw_ids, str):
                try:
                    raw_ids = json.loads(raw_ids)
                except json.JSONDecodeError:
                    raw_ids = []
            if isinstance(raw_ids, list) and len(raw_ids) >= 2:
                yes_token_id = str(raw_ids[0])
                no_token_id = str(raw_ids[1])

        return RawMarket(
            market_id=str(row.get("id") or row.get("market") or row.get("conditionId") or "unknown"),
            question=str(row.get("question") or row.get("title") or ""),
            slug=row.get("slug"),
            active=bool(row.get("active", True)),
            closed=bool(row.get("closed", False)),
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            condition_id=row.get("conditionId") or row.get("condition_id"),
            raw=row,
        )

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
    def _parse_levels(rows: list[dict[str, Any]]) -> list[OrderLevel]:
        levels: list[OrderLevel] = []
        for row in rows:
            try:
                price = float(row.get("price"))
                size = float(row.get("size"))
            except (TypeError, ValueError):
                continue
            if 0 < price < 1 and size > 0:
                levels.append(OrderLevel(price=price, size=size))
        return levels

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
