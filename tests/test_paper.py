from __future__ import annotations

import csv
from dataclasses import replace

from weather_bot.config import Settings
from weather_bot.models import EdgeResult, RawMarket, WeatherSignal
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
            "entry_size_fraction_override": 0.50,
            "selected_side_probability": 0.96,
            "probability_tier": "95",
            "event_cap_override_fraction": 0.50,
        }
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


def test_open_trade_records_price_anomaly_tag(tmp_path):
    row = _open_row(tmp_path, price_anomaly=True)

    assert row["price_anomaly"] == "true"
    assert row["signal_family"] == "abnormal_official_station_mispricing"


def test_open_trade_records_station_observation_version(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    observed_at = "2026-06-23T06:00:00+00:00"
    signal = replace(_signal(), nowcast={"observed_at": observed_at})

    broker.open_position(_market(), "yes", _result(), signal=signal)

    with (tmp_path / "trades.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["station_observed_at"] == observed_at


def test_decision_records_strategy_metadata(tmp_path):
    broker = PaperBroker(_settings(tmp_path))
    signal = _signal()
    result = _result(price_anomaly=True)

    broker.log_decision(_market(), result, signal.note, signal=signal)

    with (tmp_path / "decisions.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["strategy_mode"] == "hybrid_observation_edge"
    assert row["signal_family"] == "abnormal_official_station_mispricing"
    assert row["price_anomaly"] == "true"
    assert row["settlement_precision_confidence"] == "verified"


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


def test_broker_allows_structured_95_tier_to_bypass_ordinary_city_event_caps(tmp_path):
    broker = PaperBroker(_settings(tmp_path))

    position = broker.open_position(
        _market(),
        "yes",
        _result(size_usd=50.0, probability_tier="95", event_cap_override_fraction=0.50),
        city="seoul",
        date_hint="today",
        entry_bankroll_usd=100.0,
        signal=_concentrated_signal(),
    )

    assert position is not None
    assert position.cost_usd == 50.0


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
        _result(size_usd=50.01, probability_tier="95", event_cap_override_fraction=0.50),
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
