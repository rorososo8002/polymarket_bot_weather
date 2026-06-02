import json
from pathlib import Path

from weather_bot.shadow_signals import (
    PublicPolymarketDataClient,
    build_shadow_report,
    collect_public_trade_signals,
    compare_signals_to_bot,
    later_outcome_from_gamma_market,
    public_trade_to_signal,
    write_bounded_shadow_jsonl,
)
from weather_bot.models import RawMarket


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_public_trade_to_signal_derives_market_direction_and_public_evidence():
    signal = public_trade_to_signal(
        {
            "proxyWallet": "0x1111111111111111111111111111111111111111",
            "side": "BUY",
            "asset": "yes-token",
            "conditionId": "0x" + "a" * 64,
            "size": 200,
            "price": 0.62,
            "timestamp": 1_799_996_700,
            "title": "Highest temperature in Seoul on June 2?",
            "slug": "highest-temperature-in-seoul-on-june-2-2026",
            "eventSlug": "highest-temperature-in-seoul-on-june-2-2026",
            "outcome": "YES",
            "transactionHash": "0xtx",
        }
    )

    assert signal is not None
    assert signal.source == "polymarket_public_trade"
    assert signal.evidence_level == "observed_public_api"
    assert signal.wallet == "0x1111111111111111111111111111111111111111"
    assert signal.implied_side == "YES"
    assert signal.usdc_size == 124.0
    assert signal.observed_at == "2027-01-15T07:05:00+00:00"


def test_public_trade_to_signal_inverts_sell_no_to_yes_direction():
    signal = public_trade_to_signal(
        {
            "proxyWallet": "0x1111111111111111111111111111111111111111",
            "side": "SELL",
            "conditionId": "0x" + "a" * 64,
            "size": 50,
            "price": 0.40,
            "timestamp": 1_799_996_700,
            "title": "Highest temperature in Seoul on June 2?",
            "slug": "highest-temperature-in-seoul-on-june-2-2026",
            "outcome": "NO",
        }
    )

    assert signal is not None
    assert signal.implied_side == "YES"


def test_public_trade_to_signal_can_attach_later_outcome_from_closed_gamma_market():
    market = RawMarket(
        market_id="m1",
        question="Highest temperature in Seoul on June 2?",
        slug="highest-temperature-in-seoul-on-june-2-2026",
        active=False,
        closed=True,
        condition_id="0x" + "a" * 64,
        raw={"outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]'},
    )

    signal = public_trade_to_signal(
        {
            "proxyWallet": "0x1111111111111111111111111111111111111111",
            "side": "BUY",
            "conditionId": "0x" + "a" * 64,
            "size": 100,
            "price": 0.50,
            "timestamp": 1_799_996_700,
            "title": "Highest temperature in Seoul on June 2?",
            "slug": "highest-temperature-in-seoul-on-june-2-2026",
            "outcome": "YES",
        },
        {market.condition_id: market},
    )

    assert later_outcome_from_gamma_market(market) == "YES"
    assert signal is not None
    assert signal.later_outcome == "YES"


def test_bounded_shadow_jsonl_deduplicates_and_keeps_newest_rows(tmp_path):
    path = tmp_path / "shadow.jsonl"
    rows = []
    for i in range(4):
        rows.append(
            public_trade_to_signal(
                {
                    "proxyWallet": "0x1111111111111111111111111111111111111111",
                    "side": "BUY",
                    "conditionId": "0x" + "a" * 64,
                    "size": 100 + i,
                    "price": 0.50,
                    "timestamp": 1_800_000_000 + i,
                    "title": f"market {i}",
                    "slug": f"slug-{i}",
                    "outcome": "YES",
                    "transactionHash": f"0xtx{i}",
                }
            )
        )

    write_bounded_shadow_jsonl(path, [row for row in rows if row is not None], max_rows=2)
    write_bounded_shadow_jsonl(path, [rows[-1]], max_rows=2)

    stored = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["transaction_hash"] for row in stored] == ["0xtx3", "0xtx2"]


def test_bounded_shadow_jsonl_keeps_distinct_rows_from_one_transaction(tmp_path):
    path = tmp_path / "shadow.jsonl"
    rows = [
        public_trade_to_signal(
            {
                "proxyWallet": "0x1111111111111111111111111111111111111111",
                "side": "BUY",
                "conditionId": "0x" + condition_digit * 64,
                "size": 100,
                "price": 0.50,
                "timestamp": 1_800_000_000,
                "title": f"market {outcome}",
                "slug": f"slug-{outcome.lower()}",
                "outcome": outcome,
                "transactionHash": "0xsame-transaction",
            }
        )
        for condition_digit, outcome in (("a", "YES"), ("b", "NO"))
    ]

    write_bounded_shadow_jsonl(path, rows, max_rows=10)

    stored = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(stored) == 2
    assert {row["condition_id"] for row in stored} == {"0x" + "a" * 64, "0x" + "b" * 64}


def test_bounded_shadow_jsonl_keeps_no_rows_when_limit_is_zero(tmp_path):
    path = tmp_path / "shadow.jsonl"
    signal = public_trade_to_signal(
        {
            "proxyWallet": "0x1111111111111111111111111111111111111111",
            "side": "BUY",
            "conditionId": "0x" + "a" * 64,
            "size": 100,
            "price": 0.50,
            "timestamp": 1_800_000_000,
            "title": "market",
            "slug": "slug",
            "outcome": "YES",
            "transactionHash": "0xtx",
        }
    )

    write_bounded_shadow_jsonl(path, [signal], max_rows=0)

    assert path.read_text(encoding="utf-8") == ""


def test_collect_public_trade_signals_rechecks_minimum_trade_size_locally():
    market = RawMarket(
        market_id="m1",
        question="Highest temperature in Seoul on June 2?",
        slug="highest-temperature-in-seoul-on-june-2-2026",
        active=True,
        closed=False,
        condition_id="0x" + "a" * 64,
    )

    class DataClient:
        def get_trades_for_market(self, condition_id, *, limit, min_cash):
            return [
                {
                    "proxyWallet": "0x1111111111111111111111111111111111111111",
                    "side": "BUY",
                    "conditionId": condition_id,
                    "size": 10,
                    "price": 0.50,
                    "timestamp": 1_800_000_000,
                    "title": market.question,
                    "slug": market.slug,
                    "outcome": "YES",
                    "transactionHash": "0xsmall",
                }
            ]

    signals = collect_public_trade_signals(
        [market],
        DataClient(),
        min_trade_usdc=100.0,
    )

    assert signals == []


def test_public_data_client_uses_bounded_trade_query_parameters():
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return Response()

    client = PublicPolymarketDataClient(
        data_base="https://data-api.polymarket.com",
        timeout=3.0,
        get=fake_get,
    )

    assert client.get_trades_for_market("0x" + "b" * 64, limit=25, min_cash=250.0) == []
    assert calls == [
        {
            "url": "https://data-api.polymarket.com/trades",
            "params": {
                "market": "0x" + "b" * 64,
                "limit": "25",
                "offset": "0",
                "takerOnly": "true",
                "filterType": "CASH",
                "filterAmount": "250.0",
            },
            "timeout": 3.0,
        }
    ]


def test_compare_signals_to_bot_reports_timing_side_and_outcome(tmp_path):
    signal = public_trade_to_signal(
        {
            "proxyWallet": "0x1111111111111111111111111111111111111111",
            "side": "BUY",
            "conditionId": "0x" + "a" * 64,
            "size": 100,
            "price": 0.60,
            "timestamp": 1_799_996_700,
            "title": "Highest temperature in Seoul on June 2?",
            "slug": "highest-temperature-in-seoul-on-june-2-2026",
            "outcome": "YES",
            "later_outcome": "YES",
        }
    )
    decisions = tmp_path / "paper_decisions.csv"
    write(
        decisions,
        "\n".join(
            [
                "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note",
                "2027-01-15T07:03:00+00:00,m1,highest-temperature-in-seoul-on-june-2-2026,q,temperature,YES,0.70,0.55,0.15,10,18,,,,,,YES edge,",
            ]
        )
        + "\n",
    )

    comparisons = compare_signals_to_bot([signal], decisions, comparison_window_seconds=600)

    assert len(comparisons) == 1
    assert comparisons[0].timing_relation == "external_after_bot"
    assert comparisons[0].lag_seconds == 120
    assert comparisons[0].side_relation == "same_side"
    assert comparisons[0].signal_won is True
    assert comparisons[0].bot_won is True


def test_shadow_report_distinguishes_evidence_from_speculation_and_defers_experiment(tmp_path):
    signals_path = tmp_path / "shadow.jsonl"
    decisions_path = tmp_path / "paper_decisions.csv"
    notes_path = tmp_path / "public_notes.jsonl"
    signal = public_trade_to_signal(
        {
            "proxyWallet": "0x1111111111111111111111111111111111111111",
            "side": "BUY",
            "conditionId": "0x" + "a" * 64,
            "size": 100,
            "price": 0.60,
            "timestamp": 1_799_996_700,
            "title": "Highest temperature in Seoul on June 2?",
            "slug": "highest-temperature-in-seoul-on-june-2-2026",
            "outcome": "YES",
            "later_outcome": "YES",
        }
    )
    write_bounded_shadow_jsonl(signals_path, [signal], max_rows=10)
    write(
        decisions_path,
        "\n".join(
            [
                "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note",
                "2027-01-15T07:03:00+00:00,m1,highest-temperature-in-seoul-on-june-2-2026,q,temperature,SKIP,0.51,,0.01,0,0,,,,,,confidence too low,",
            ]
        )
        + "\n",
    )
    write(
        notes_path,
        "\n".join(
            [
                json.dumps({"classification": "evidence", "source_url": "https://example.com/post", "claim": "Trader posted a filled order."}),
                json.dumps({"classification": "speculation", "source_url": "https://example.com/post2", "claim": "The post might imply a weather view."}),
            ]
        )
        + "\n",
    )

    report = build_shadow_report(
        signals_path,
        decisions_path,
        public_notes_path=notes_path,
        max_rows=10,
        min_resolved_for_experiment=3,
    )

    assert "observed_public_api=1" in report
    assert "public_note_evidence=1" in report
    assert "public_note_speculation=1" in report
    assert "paper-only experiment promotion: hold" in report
    assert "Automatic copy trading: prohibited" in report


def test_shadow_report_compares_external_and_bot_win_rates_on_the_same_entry_sample(tmp_path):
    signals_path = tmp_path / "shadow.jsonl"
    decisions_path = tmp_path / "paper_decisions.csv"
    signals = []
    decisions = [
        "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note"
    ]
    for i in range(20):
        slug = f"market-{i}"
        later_outcome = "YES" if i < 8 or i >= 10 else "NO"
        signals.append(
            public_trade_to_signal(
                {
                    "proxyWallet": "0x1111111111111111111111111111111111111111",
                    "side": "BUY",
                    "conditionId": "0x" + f"{i + 1:064x}",
                    "size": 200,
                    "price": 0.60,
                    "timestamp": 1_799_996_700,
                    "title": slug,
                    "slug": slug,
                    "outcome": "YES",
                    "later_outcome": later_outcome,
                    "transactionHash": f"0xtx{i}",
                }
            )
        )
        bot_side = "YES" if i < 10 else "SKIP"
        decisions.append(
            f"2027-01-15T07:03:00+00:00,m{i},{slug},q,temperature,{bot_side},0.70,0.55,0.15,10,18,,,,,,decision,"
        )

    write_bounded_shadow_jsonl(signals_path, signals, max_rows=30)
    write(decisions_path, "\n".join(decisions) + "\n")

    report = build_shadow_report(
        signals_path,
        decisions_path,
        max_rows=30,
        min_resolved_for_experiment=20,
    )

    assert "external_signal_wins=18/20" in report
    assert "matched_external_signal_wins=8/10" in report
    assert "matched_bot_entry_wins=8/10" in report
    assert "paper-only experiment promotion: hold" in report
    assert "paper-only experiment promotion: candidate" not in report
