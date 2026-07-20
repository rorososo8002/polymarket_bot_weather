from __future__ import annotations

import csv
from dataclasses import replace

import pytest

from weather_bot.config import Settings
from weather_bot.models import EdgeResult, MarketRuleProvenance, RawMarket, WeatherSignal
from weather_bot.paper import PaperBroker
from weather_bot.weather_client import parse_weather_question


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "bankroll_usd": 100.0,
        "min_order_usd": 1.0,
        "weather_taker_fee_rate": 0.0,
        "state_path": str(tmp_path / "state.json"),
        "trades_csv_path": str(tmp_path / "trades.csv"),
        "decisions_csv_path": str(tmp_path / "decisions.csv"),
        "raw_snapshots_path": str(tmp_path / "raw.jsonl"),
        "portfolio_decisions_jsonl_path": str(tmp_path / "portfolio.jsonl"),
    }
    values.update(overrides)
    return Settings(**values)


def _market() -> RawMarket:
    question = "Will the highest temperature in Seoul be 23C today?"
    return RawMarket("m1", question, "m1", True, False, "yes", "no")


def _signal() -> WeatherSignal:
    market = _market()
    return WeatherSignal(
        p_true=0.96,
        confidence=1.0,
        source="official-station-intraday-high-strong-yes",
        note="official station evidence",
        parsed=parse_weather_question(market.question),
        strategy_mode="hybrid_observation_edge",
        signal_family="intraday_observation_edge",
        settlement_precision_confidence="verified",
    )


def _result(
    *,
    price_anomaly: bool = False,
    size_usd: float = 10.0,
    probability_tier: str = "",
    event_cap_override_fraction: float | None = None,
) -> EdgeResult:
    return EdgeResult(
        side="YES",
        p_true=0.96,
        p_exec=0.50,
        net_edge=0.46,
        size_usd=size_usd,
        size_shares=size_usd / 0.50,
        reason="YES edge=0.4600, expected_net_return=80.00%",
        price_anomaly=price_anomaly,
        strategy_mode="hybrid_observation_edge",
        signal_family=(
            "abnormal_official_station_mispricing"
            if price_anomaly
            else "intraday_observation_edge"
        ),
        entry_size_fraction_override=event_cap_override_fraction,
        selected_side_probability=0.96,
        probability_tier=probability_tier,
        event_cap_override_fraction=event_cap_override_fraction,
    )


def _concentrated_signal() -> WeatherSignal:
    return WeatherSignal(
        **{
            **_signal().__dict__,
            "entry_size_fraction_override": 0.20,
            "selected_side_probability": 0.96,
            "probability_tier": "95",
            "event_cap_override_fraction": 0.20,
        }
    )


def _direct_exact_no_market(
    market_id: str,
    bucket: int,
    *,
    metric: str = "highest",
    unit: str = "C",
    wunderground_rules: bool = True,
) -> RawMarket:
    question = f"Will the {metric} temperature in Seoul be {bucket}°{unit} on May 25?"
    return RawMarket(
        market_id,
        question,
        market_id,
        True,
        False,
        f"{market_id}-yes",
        f"{market_id}-no",
        event_id="seoul-may-25",
        rule_provenance=(
            MarketRuleProvenance(
                market_id=market_id,
                question=question,
                resolution_source="https://www.wunderground.com/history/daily/kr/incheon/RKSI",
            )
            if wunderground_rules
            else None
        ),
    )


def _direct_exact_no_signal(market: RawMarket) -> WeatherSignal:
    return WeatherSignal(
        p_true=0.0,
        confidence=1.0,
        source="official-station-lock-strong_no",
        note="direct settlement history irreversibly broke exact bucket",
        parsed=parse_weather_question(market.question),
        nowcast={
            "source": "wunderground-history-direct",
            "target_date_local": "2026-05-25",
        },
        signal_family="lock_only",
        settlement_precision_confidence="verified",
    )


def _direct_exact_no_result(*, size_usd: float = 500.0) -> EdgeResult:
    return EdgeResult(
        side="NO",
        p_true=0.0,
        p_exec=0.85,
        net_edge=0.15,
        size_usd=size_usd,
        size_shares=size_usd / 0.85,
        reason="direct exact NO",
        expected_net_profit_usd=size_usd * ((1.0 / 0.85) - 1.0),
        signal_family="lock_only",
        entry_size_fraction_override=1.0,
        selected_side_probability=1.0,
        probability_tier="lock_high_exact_no",
        event_cap_override_fraction=1.0,
    )


def _upstream_exact_no_signal(market: RawMarket, **nowcast_overrides) -> WeatherSignal:
    signal = _direct_exact_no_signal(market)
    return replace(
        signal,
        note="upstream same-station observation irreversibly broke exact bucket",
        nowcast={
            "station_id": "RKSI",
            "source": "aviationweather-metar",
            "target_date_local": "2026-05-25",
            "station_local_date": "2026-05-25",
            "daily_extremes_complete": True,
            "freshness_seconds": 120,
            "data_block_reason": "",
            "entry_evidence_mode": "upstream_same_station_paper",
            "settlement_source_verified": False,
            "upstream_bucket_distance_c": 2.0,
            "upstream_min_bucket_distance_c": 2.0,
            **nowcast_overrides,
        },
        strategy_mode="upstream_lock_paper",
        signal_family="upstream_lock_paper",
        raw_probability=0.0,
        conservative_yes_probability=0.04,
        conservative_no_probability=0.96,
        raw_selected_side_probability=1.0,
        selected_side_probability=0.96,
    )


def _upstream_exact_no_result(*, size_usd: float = 500.0, price: float = 0.85) -> EdgeResult:
    return replace(
        _direct_exact_no_result(size_usd=size_usd),
        p_exec=price,
        size_shares=size_usd / price,
        signal_family="upstream_lock_paper",
        probability_tier="upstream_2c_exact_no",
        event_cap_override_fraction=None,
        raw_probability=0.0,
        conservative_yes_probability=0.04,
        conservative_no_probability=0.96,
        raw_selected_side_probability=1.0,
        selected_side_probability=0.96,
    )


def _audit_signal(*, side: str = "YES") -> WeatherSignal:
    selected_probability = 0.96 if side == "YES" else 0.91
    raw_selected_probability = 0.97 if side == "YES" else 0.55
    return WeatherSignal(
        **{
            **_signal().__dict__,
            "p_true": 0.97 if side == "YES" else 0.45,
            "raw_probability": 0.97 if side == "YES" else 0.45,
            "conservative_yes_probability": 0.96 if side == "YES" else 0.30,
            "conservative_no_probability": 0.02 if side == "YES" else 0.91,
            "raw_selected_side_probability": raw_selected_probability,
            "selected_side_probability": selected_probability,
            "calibration_sample_days": 1460,
            "calibration_profile_key": "RKSI|month=6|minute=900|high|C",
            "calibration_status": "RESIDUAL_PROBABILITY_OK",
            "probability_tier": "95" if side == "YES" else "90",
            "event_cap_override_fraction": 0.50 if side == "YES" else None,
            "entry_size_fraction_override": 0.50 if side == "YES" else 0.25,
        }
    )


def _audit_result(*, side: str = "YES") -> EdgeResult:
    selected_probability = 0.96 if side == "YES" else 0.91
    raw_selected_probability = 0.97 if side == "YES" else 0.55
    return EdgeResult(
        side=side,
        p_true=0.97 if side == "YES" else 0.45,
        p_exec=0.50,
        net_edge=0.41,
        size_usd=40.0,
        size_shares=80.0,
        reason=f"{side} edge=0.4100, expected_net_return=70.00%, entry_fee=$1.0000",
        expected_net_profit_usd=28.0,
        strategy_mode="hybrid_observation_edge",
        signal_family="intraday_observation_edge",
        entry_size_fraction_override=0.50 if side == "YES" else 0.25,
        raw_selected_side_probability=raw_selected_probability,
        selected_side_probability=selected_probability,
        calibration_sample_days=1460,
        calibration_profile_key="RKSI|month=6|minute=900|high|C",
        calibration_status="RESIDUAL_PROBABILITY_OK",
        probability_tier="95" if side == "YES" else "90",
        event_cap_override_fraction=0.50 if side == "YES" else None,
        requested_size_usd=50.0,
        executable_size_usd=40.0,
    )


def _open_row(tmp_path, *, price_anomaly: bool = False) -> dict[str, str]:
    broker = PaperBroker(_settings(tmp_path))
    broker.open_position(
        _market(),
        "yes",
        _result(price_anomaly=price_anomaly),
        signal=_signal(),
    )
    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        return next(csv.DictReader(handle))


def test_open_trade_records_strategy_mode_and_signal_family(tmp_path):
    row = _open_row(tmp_path)

    assert row["strategy_mode"] == "hybrid_observation_edge"
    assert row["signal_family"] == "intraday_observation_edge"
    assert row["settlement_precision_confidence"] == "verified"


def test_skip_diagnostics_batch_appends_once(tmp_path, monkeypatch):
    broker = PaperBroker(_settings(tmp_path))
    writes: list[list[str]] = []
    monkeypatch.setattr(
        broker,
        "_append_skip_diagnostic_lines",
        lambda lines: writes.append(list(lines)),
    )
    skipped = EdgeResult("SKIP", 0.5, None, -999.0, 0.0, 0.0, "SKIP_TEST: no entry")

    with broker.batch_skip_diagnostics():
        broker.log_decision(_market(), skipped, "first", signal=_signal())
        broker.log_decision(_market(), skipped, "second", signal=_signal())

    assert [len(lines) for lines in writes] == [2]


def test_open_trade_records_price_anomaly_tag(tmp_path):
    row = _open_row(tmp_path, price_anomaly=True)

    assert row["price_anomaly"] == "true"
    assert row["signal_family"] == "abnormal_official_station_mispricing"


def test_open_trade_records_station_observation_version(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    observed_at = "2026-06-23T06:00:00+00:00"
    signal = replace(
        _signal(),
        nowcast={
            "observed_at": observed_at,
            "request_started_at": "2026-06-23T06:04:00+00:00",
            "source_received_at": "2026-06-23T06:03:30+00:00",
            "bot_received_at": "2026-06-23T06:04:02+00:00",
            "source_latency_seconds": 210,
            "source_latency_status": "measured",
            "bot_detection_latency_seconds": 242,
        },
    )

    broker.open_position(_market(), "yes", _result(), signal=signal)

    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["station_observed_at"] == observed_at
    assert row["request_started_at"] == "2026-06-23T06:04:00+00:00"
    assert row["source_received_at"] == "2026-06-23T06:03:30+00:00"
    assert row["bot_received_at"] == "2026-06-23T06:04:02+00:00"
    assert row["source_latency_seconds"] == "210"
    assert row["source_latency_status"] == "measured"
    assert row["bot_detection_latency_seconds"] == "242"


def test_decision_records_strategy_metadata(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    signal = replace(
        _signal(),
        nowcast={
            "observed_at": "2026-06-23T06:00:00+00:00",
            "request_started_at": "2026-06-23T06:04:00+00:00",
            "source_received_at": "",
            "bot_received_at": "2026-06-23T06:04:02+00:00",
            "source_latency_seconds": None,
            "source_latency_status": "provider-timestamp-unavailable",
            "bot_detection_latency_seconds": 242,
        },
    )
    result = _result(price_anomaly=True)

    broker.log_decision(_market(), result, signal.note, signal=signal)

    with (tmp_path / "decisions.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["strategy_mode"] == "hybrid_observation_edge"
    assert row["signal_family"] == "abnormal_official_station_mispricing"
    assert row["price_anomaly"] == "true"
    assert row["settlement_precision_confidence"] == "verified"
    assert row["request_started_at"] == "2026-06-23T06:04:00+00:00"
    assert row["source_received_at"] == ""
    assert row["bot_received_at"] == "2026-06-23T06:04:02+00:00"
    assert row["source_latency_seconds"] == ""
    assert row["source_latency_status"] == "provider-timestamp-unavailable"
    assert row["bot_detection_latency_seconds"] == "242"


def test_decision_records_structured_probability_and_sizing_audit_fields(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    signal = _audit_signal()
    result = _audit_result()

    broker.log_decision(_market(), result, signal.note, signal=signal)

    with (tmp_path / "decisions.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["raw_selected_side_probability"] == "0.970000"
    assert row["selected_side_probability"] == "0.960000"
    assert row["probability_tier"] == "95"
    assert row["calibration_sample_days"] == "1460"
    assert row["calibration_profile_key"] == "RKSI|month=6|minute=900|high|C"
    assert row["calibration_status"] == "RESIDUAL_PROBABILITY_OK"
    assert row["requested_size_usd"] == "50.000000"
    assert row["executable_size_usd"] == "40.000000"
    assert row["event_cap_override_fraction"] == "0.500000"
    assert row["fee_rate"] == "0.000000"
    assert row["entry_fee_usdc"] == "1.000000"
    assert row["expected_net_profit_usd"] == "28.000000"


def test_decision_records_station_formation_audit_without_relying_on_truncated_note(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    signal = WeatherSignal(
        **{
            **_audit_signal().__dict__,
            "nowcast": {
                "station_timezone": "Asia/Seoul",
                "target_date_local": "2026-06-22",
                "station_local_date": "2026-06-22",
                "station_local_time": "15:30",
                "strategy_direction": "high",
                "formation_monitoring_status": "started",
                "monitoring_start_local_minute": 780,
                "first_final_high_local_minute_q25": 750.0,
                "first_final_high_local_minute_median": 810.0,
                "first_final_high_local_minute_q75": 870.0,
                "remaining_movement_probability": 0.35,
                "midnight_reset_status": "verified",
                "data_block_reason": "",
                "clob_accepting_orders": True,
                "clob_enable_order_book": True,
                "strategy_allowed_reason": "residual probability passed",
            },
        }
    )

    broker.log_decision(_market(), _audit_result(), "x" * 1000, signal=signal)

    with (tmp_path / "decisions.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert len(row["note"]) == 500
    assert row["station_local_date"] == "2026-06-22"
    assert row["station_local_time"] == "15:30"
    assert row["formation_monitoring_status"] == "started"
    assert row["remaining_movement_probability"] == "0.35"
    assert row["clob_accepting_orders"] == "True"
    assert row["strategy_allowed_reason"] == "residual probability passed"


def test_no_open_trade_uses_explicit_conservative_no_probability_without_double_inversion(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    market = _market()
    signal = _audit_signal(side="NO")
    result = _audit_result(side="NO")

    position = broker.open_position(
        market,
        "no",
        result,
        signal=signal,
    )

    assert position is not None
    assert position.metadata["entry_side_probability"] == 0.91
    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["entry_side_probability"] == "0.910000"
    assert row["raw_selected_side_probability"] == "0.550000"
    assert row["selected_side_probability"] == "0.910000"
    assert row["probability_tier"] == "90"
    assert row["requested_size_usd"] == "50.000000"
    assert row["executable_size_usd"] == "40.000000"


def test_close_trade_preserves_entry_strategy_metadata(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    signal = _signal()
    market = _market()
    position = broker.open_position(
        market,
        "yes",
        _result(price_anomaly=True),
        signal=signal,
    )
    assert position is not None

    broker.close_position(position, market, 0.75, "take profit")

    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    close_row = rows[-1]
    assert close_row["action"] == "CLOSE"
    assert close_row["strategy_mode"] == "hybrid_observation_edge"
    assert close_row["signal_family"] == "abnormal_official_station_mispricing"
    assert close_row["price_anomaly"] == "true"
    assert close_row["settlement_precision_confidence"] == "verified"


def test_broker_allows_structured_95_tier_to_use_twenty_percent_cap(tmp_path):
    broker = PaperBroker(_settings(tmp_path))

    position = broker.open_position(
        _market(),
        "yes",
        _result(size_usd=20.0, probability_tier="95", event_cap_override_fraction=0.20),
        city="seoul",
        date_hint="today",
        entry_bankroll_usd=100.0,
        signal=_concentrated_signal(),
    )

    assert position is not None
    assert position.cost_usd == 20.0


def test_broker_allows_two_direct_exact_no_legs_to_share_full_city_date_budget(tmp_path):
    broker = PaperBroker(
        _settings(
            tmp_path,
            bankroll_usd=1000.0,
            max_event_portfolio_legs=2,
            max_single_market_fraction=0.50,
            max_city_exposure_fraction=0.50,
            max_event_date_exposure_fraction=0.50,
            large_bankroll_event_date_exposure_fraction=0.50,
            max_total_exposure_fraction=0.50,
        )
    )
    first_market = _direct_exact_no_market("seoul-26", 26)
    second_market = _direct_exact_no_market("seoul-27", 27)

    first = broker.open_position(
        first_market,
        first_market.no_token_id or "",
        _direct_exact_no_result(),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_direct_exact_no_signal(first_market),
    )
    second = broker.open_position(
        second_market,
        second_market.no_token_id or "",
        _direct_exact_no_result(),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_direct_exact_no_signal(second_market),
    )

    assert first is not None
    assert second is not None
    assert first.metadata["nowcast_source"] == "wunderground-history-direct"
    assert second.metadata["nowcast_source"] == "wunderground-history-direct"
    assert broker.event_date_position_count("seoul", "may 25") == 2
    assert broker.event_date_exposure("seoul", "may 25") == 1000.0


def test_broker_keeps_non_direct_concentrated_override_exclusive(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    first_market = _market()
    second_market = replace(
        first_market,
        market_id="m2",
        question="Will the highest temperature in Seoul be 24C today?",
        slug="m2",
        yes_token_id="m2-yes",
        no_token_id="m2-no",
    )

    first = broker.open_position(
        first_market,
        first_market.yes_token_id or "",
        _result(size_usd=10.0, probability_tier="95", event_cap_override_fraction=0.20),
        city="seoul",
        date_hint="today",
        entry_bankroll_usd=100.0,
        signal=_concentrated_signal(),
    )
    second = broker.open_position(
        second_market,
        second_market.yes_token_id or "",
        _result(size_usd=10.0, probability_tier="95", event_cap_override_fraction=0.20),
        city="seoul",
        date_hint="today",
        entry_bankroll_usd=100.0,
        signal=replace(
            _concentrated_signal(),
            parsed=parse_weather_question(second_market.question),
        ),
    )

    assert first is not None
    assert second is None
    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle))[-1]["action"] == "SKIP_EVENT_DATE_CONCENTRATION"


def test_broker_blocks_direct_exact_no_pair_across_high_and_low_metrics(tmp_path):
    broker = PaperBroker(_settings(tmp_path, bankroll_usd=1000.0, max_event_portfolio_legs=2))
    high_market = _direct_exact_no_market("seoul-high-26", 26)
    low_market = _direct_exact_no_market("seoul-low-27", 27, metric="lowest")

    first = broker.open_position(
        high_market,
        high_market.no_token_id or "",
        _direct_exact_no_result(size_usd=100.0),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_direct_exact_no_signal(high_market),
    )
    second = broker.open_position(
        low_market,
        low_market.no_token_id or "",
        _direct_exact_no_result(size_usd=100.0),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_direct_exact_no_signal(low_market),
    )

    assert first is not None
    assert second is None


def test_broker_blocks_third_direct_exact_no_at_two_leg_cap(tmp_path):
    broker = PaperBroker(_settings(tmp_path, bankroll_usd=1000.0, max_event_portfolio_legs=2))
    opened = []
    for market_id, bucket in [("seoul-26", 26), ("seoul-27", 27), ("seoul-28", 28)]:
        raw_market = _direct_exact_no_market(market_id, bucket)
        opened.append(
            broker.open_position(
                raw_market,
                raw_market.no_token_id or "",
                _direct_exact_no_result(size_usd=300.0),
                city="seoul",
                date_hint="may 25",
                entry_bankroll_usd=1000.0,
                signal=_direct_exact_no_signal(raw_market),
            )
        )

    assert opened[0] is not None
    assert opened[1] is not None
    assert opened[2] is None
    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle))[-1]["action"] == "SKIP_EVENT_DATE_LEG_CAP"


def test_broker_blocks_second_direct_exact_no_without_wunderground_market_rules(tmp_path):
    broker = PaperBroker(_settings(tmp_path, bankroll_usd=1000.0, max_event_portfolio_legs=2))
    first_market = _direct_exact_no_market("seoul-26", 26, wunderground_rules=False)
    second_market = _direct_exact_no_market("seoul-27", 27, wunderground_rules=False)

    first = broker.open_position(
        first_market,
        first_market.no_token_id or "",
        _direct_exact_no_result(size_usd=50.0),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_direct_exact_no_signal(first_market),
    )
    second = broker.open_position(
        second_market,
        second_market.no_token_id or "",
        _direct_exact_no_result(size_usd=50.0),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_direct_exact_no_signal(second_market),
    )

    assert first is not None
    assert second is None


@pytest.mark.parametrize(
    ("p_true", "data_block_reason"),
    [
        (0.01, ""),
        (0.0, "conflicting-direct-history"),
    ],
)
def test_broker_rejects_concentrated_override_for_nonzero_or_blocked_direct_signal(
    tmp_path,
    p_true,
    data_block_reason,
):
    broker = PaperBroker(_settings(tmp_path, bankroll_usd=1000.0, strategy_mode="lock_only"))
    raw_market = _direct_exact_no_market("seoul-26", 26)
    signal = _direct_exact_no_signal(raw_market)
    signal = replace(
        signal,
        p_true=p_true,
        nowcast={
            **signal.nowcast,
            "data_block_reason": data_block_reason,
        },
    )
    result = replace(_direct_exact_no_result(size_usd=40.0), p_true=p_true)

    position = broker.open_position(
        raw_market,
        raw_market.no_token_id or "",
        result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=signal,
    )

    assert position is None
    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle))[-1]["action"] == "SKIP_LOCK_ONLY_EXACT_NO"


@pytest.mark.parametrize("price", [0.9001, 0.99])
def test_broker_lock_only_final_gate_rejects_price_above_ninety_cents(tmp_path, price):
    broker = PaperBroker(_settings(tmp_path, bankroll_usd=1000.0, strategy_mode="lock_only"))
    market = _direct_exact_no_market("seoul-26", 26)
    result = replace(
        _direct_exact_no_result(size_usd=40.0),
        p_exec=price,
        size_shares=40.0 / price,
    )

    position = broker.open_position(
        market,
        market.no_token_id or "",
        result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_direct_exact_no_signal(market),
    )

    assert position is None
    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle))[-1]["action"] == "SKIP_LOCK_ONLY_EXACT_NO"


def test_broker_lock_only_final_gate_keeps_fahrenheit_exact_no_supported(tmp_path):
    broker = PaperBroker(_settings(tmp_path, bankroll_usd=1000.0, strategy_mode="lock_only"))
    market = _direct_exact_no_market("seoul-84f", 84, unit="F")

    position = broker.open_position(
        market,
        market.no_token_id or "",
        _direct_exact_no_result(size_usd=40.0),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_direct_exact_no_signal(market),
    )

    assert position is not None


@pytest.mark.parametrize(
    ("nowcast_overrides", "price", "allowed"),
    [
        ({}, 0.85, True),
        ({"daily_extremes_complete": False}, 0.85, False),
        ({"station_id": "HKO"}, 0.85, False),
        ({"station_id": "WRONG"}, 0.85, False),
        ({"station_local_date": "2026-05-24"}, 0.85, False),
        ({"freshness_seconds": 5401}, 0.85, False),
        ({"source": "official-station-fixture"}, 0.85, False),
        ({"entry_evidence_mode": "forged"}, 0.85, False),
        ({"settlement_source_verified": True}, 0.85, False),
        ({"upstream_bucket_distance_c": 1.0}, 0.85, False),
        ({}, 0.8501, False),
    ],
)
def test_broker_upstream_lock_paper_final_gate_is_fail_closed(
    tmp_path,
    nowcast_overrides,
    price,
    allowed,
):
    broker = PaperBroker(
        _settings(
            tmp_path,
            bankroll_usd=1000.0,
            strategy_mode="upstream_lock_paper",
        )
    )
    market = _direct_exact_no_market("seoul-upstream-29", 29)

    position = broker.open_position(
        market,
        market.no_token_id or "",
        _upstream_exact_no_result(size_usd=40.0, price=price),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_upstream_exact_no_signal(market, **nowcast_overrides),
    )

    assert (position is not None) is allowed


@pytest.mark.parametrize(
    ("signal_overrides", "result_overrides", "unit"),
    [
        ({"signal_family": "lock_only"}, {}, "C"),
        ({}, {"signal_family": "lock_only"}, "C"),
        ({}, {"probability_tier": "lock_high_exact_no"}, "C"),
        ({"p_true": 0.01}, {"p_true": 0.01}, "C"),
        (
            {
                "conservative_yes_probability": 0.01,
                "conservative_no_probability": 0.99,
            },
            {
                "conservative_yes_probability": 0.01,
                "conservative_no_probability": 0.99,
            },
            "C",
        ),
        ({}, {}, "F"),
    ],
)
def test_broker_upstream_final_gate_rejects_forged_certainty_markers(
    tmp_path,
    signal_overrides,
    result_overrides,
    unit,
):
    broker = PaperBroker(
        _settings(tmp_path, bankroll_usd=1000.0, strategy_mode="upstream_lock_paper")
    )
    market = _direct_exact_no_market("seoul-upstream-forged", 29, unit=unit)
    signal = replace(_upstream_exact_no_signal(market), **signal_overrides)
    result = replace(_upstream_exact_no_result(size_usd=40.0), **result_overrides)

    position = broker.open_position(
        market,
        market.no_token_id or "",
        result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=signal,
    )

    assert position is None
    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle))[-1]["action"] == "SKIP_UPSTREAM_LOCK_PAPER_EXACT_NO"


def test_broker_upstream_pair_keeps_one_fixed_fifty_dollar_city_date_budget(tmp_path):
    broker = PaperBroker(
        _settings(
            tmp_path,
            bankroll_usd=1000.0,
            strategy_mode="upstream_lock_paper",
            max_event_portfolio_legs=2,
        )
    )
    market = _direct_exact_no_market("seoul-upstream-27", 27)

    first = broker.open_position(
        market,
        market.no_token_id or "",
        _upstream_exact_no_result(size_usd=40.0),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
        signal=_upstream_exact_no_signal(market),
    )
    second = broker.open_position(
        market,
        market.no_token_id or "",
        _upstream_exact_no_result(size_usd=10.0),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=999.0,
        allow_same_side_add=True,
        signal=_upstream_exact_no_signal(market),
    )
    third = broker.open_position(
        market,
        market.no_token_id or "",
        _upstream_exact_no_result(size_usd=10.0),
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=999.0,
        allow_same_side_add=True,
        signal=_upstream_exact_no_signal(market),
    )

    assert first is not None
    assert second is not None
    assert third is None
    assert broker.event_date_exposure("seoul", "may 25") == pytest.approx(50.0)
    assert broker.state.cash_usd == pytest.approx(950.0)


def test_broker_rejects_unstructured_95_tier_above_ordinary_event_cap(tmp_path):
    broker = PaperBroker(_settings(tmp_path))

    position = broker.open_position(
        _market(),
        "yes",
        _result(size_usd=50.0, probability_tier="95"),
        city="seoul",
        date_hint="today",
        entry_bankroll_usd=100.0,
        signal=_signal(),
    )

    assert position is None


def test_broker_rejects_direct_call_above_structured_event_cap(tmp_path):
    broker = PaperBroker(
        _settings(
            tmp_path,
            max_single_market_fraction=0.75,
            max_city_exposure_fraction=0.75,
            max_event_date_exposure_fraction=0.75,
            large_bankroll_event_date_exposure_fraction=0.75,
        )
    )

    position = broker.open_position(
        _market(),
        "yes",
        _result(size_usd=20.01, probability_tier="95", event_cap_override_fraction=0.20),
        city="seoul",
        date_hint="today",
        entry_bankroll_usd=100.0,
        signal=_concentrated_signal(),
    )

    assert position is None


def test_broker_rejects_direct_call_above_single_market_cap_without_event_metadata(tmp_path):
    broker = PaperBroker(
        _settings(
            tmp_path,
            max_single_market_fraction=0.50,
            max_city_exposure_fraction=0.75,
            max_event_date_exposure_fraction=0.75,
            large_bankroll_event_date_exposure_fraction=0.75,
        )
    )

    position = broker.open_position(
        _market(),
        "yes",
        _result(size_usd=50.01, probability_tier="95", event_cap_override_fraction=0.50),
        entry_bankroll_usd=100.0,
        signal=_concentrated_signal(),
    )

    assert position is None
