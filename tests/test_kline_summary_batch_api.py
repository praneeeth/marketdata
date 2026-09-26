from src.modules.market.api import klines


def test_summary_batch_preserves_input_order_across_markets(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_summary(self, symbol: str):
        calls.append((self.market.value, symbol))
        return {"trend": f"trend-{symbol}"}

    monkeypatch.setattr(klines.KlineCollector, "get_kline_summary", fake_summary)

    payload = klines.KlineSummaryBatchRequest(
        items=[
            klines.KlineSummaryItem(symbol="INFY", market="IN"),
            klines.KlineSummaryItem(symbol="TCS", market="IN"),
            klines.KlineSummaryItem(symbol="RELIANCE", market="IN"),
        ]
    )

    result = klines.get_kline_summary_batch(payload)

    assert [item["symbol"] for item in result] == ["INFY", "TCS", "RELIANCE"]
    assert [item["market"] for item in result] == ["IN", "IN", "IN"]
    assert [item["summary"]["trend"] for item in result] == [
        "trend-INFY",
        "trend-TCS",
        "trend-RELIANCE",
    ]
    assert sorted(calls) == sorted([("IN", "INFY"), ("IN", "TCS"), ("IN", "RELIANCE")])


def test_summary_batch_keeps_other_items_when_one_summary_fails(monkeypatch):
    def fake_summary(self, symbol: str):
        if symbol == "BAD":
            raise RuntimeError("provider unavailable")
        return {"trend": f"trend-{symbol}"}

    monkeypatch.setattr(klines.KlineCollector, "get_kline_summary", fake_summary)

    payload = klines.KlineSummaryBatchRequest(
        items=[
            klines.KlineSummaryItem(symbol="GOOD", market="IN"),
            klines.KlineSummaryItem(symbol="BAD", market="IN"),
        ]
    )

    result = klines.get_kline_summary_batch(payload)

    assert result[0]["summary"] == {"trend": "trend-GOOD"}
    assert result[1]["summary"]["error"] == "provider unavailable"
