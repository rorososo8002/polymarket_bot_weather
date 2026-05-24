import sys, io
sys.path.insert(0, 'src')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

results = []

try:
    from weather_bot.config import load_settings, Settings
    s = load_settings()
    assert hasattr(s, 'require_date_hint_for_trade')
    assert s.require_date_hint_for_trade == True
    results.append("[PASS] config: require_date_hint_for_trade=True")
except Exception as e:
    results.append(f"[FAIL] config: {e}")

try:
    from weather_bot.paper import PaperBroker
    from weather_bot.models import PaperPosition, PaperState
    from datetime import datetime, timezone
    broker = PaperBroker(s)
    broker.state = PaperState(cash_usd=1000.0)
    assert not broker.has_any_position('market-001')
    pos = PaperPosition(
        position_id='pid-1', market_id='market-001', question='test?',
        token_id='tok-yes', side='YES', entry_price=0.5,
        shares=10.0, cost_usd=5.0,
        opened_at=datetime.now(timezone.utc).isoformat()
    )
    broker.state.positions.append(pos)
    assert broker.has_any_position('market-001') == True
    assert broker.has_position('market-001', 'YES') == True
    assert broker.has_position('market-001', 'NO') == False
    results.append("[PASS] paper: has_any_position YES/NO side 구분 정상")
except Exception as e:
    results.append(f"[FAIL] paper: {e}")

try:
    broker2 = PaperBroker(s)
    broker2.state = PaperState(cash_usd=1000.0)
    summary = broker2.stats_summary()
    assert '\ud0c0\uc785\ubcc4 \uc2b9\ub960 \ud1b5\uacc4' in summary
    results.append("[PASS] paper: stats_summary 인코딩 정상")
except Exception as e:
    results.append(f"[FAIL] paper: stats_summary {e}")

try:
    from weather_bot.models import WeatherSignal, ParsedWeatherQuestion
    from weather_bot.live_paper_runner import evaluate_market
    from unittest.mock import MagicMock
    no_date = ParsedWeatherQuestion(
        city='new york', latitude=40.77, longitude=-73.96,
        threshold_f=80.0, threshold_original=80.0, threshold_unit='F',
        operator='>=', variable='temperature', date_hint=None, confidence=0.80
    )
    sig = WeatherSignal(p_true=0.7, confidence=0.80, source='test', note='', parsed=no_date)
    market_mock = MagicMock()
    market_mock.yes_token_id = 'tok-yes'
    market_mock.no_token_id = 'tok-no'
    result, _ = evaluate_market(market_mock, sig, MagicMock(), s, 1000.0, 'temperature')
    assert result.side == 'SKIP'
    assert 'date_hint' in result.reason or '\ub0a0\uc9dc' in result.reason
    results.append(f"[PASS] live_paper_runner: date_hint=None -> SKIP ({result.reason[:50]})")
except Exception as e:
    results.append(f"[FAIL] live_paper_runner date_hint: {e}")

print("\n".join(results))
print("\n=== " + ("ALL PASS" if all("[PASS]" in r for r in results) else "SOME FAILED") + " ===")
