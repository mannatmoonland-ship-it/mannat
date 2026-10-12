from pathlib import Path


APP_JS = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")


def test_trade_delete_handler_uses_the_in_scope_trade_map_and_document_id():
    start = APP_JS.index("async function removeTrade(")
    end = APP_JS.index("\n\nfunction openTradeLedgerModal(", start)
    delete_handler = APP_JS[start:end]

    assert "tradesById.get(tradeId)?.name" in delete_handler
    assert "trades.find(" not in delete_handler
    assert '`/api/trades/${encodeURIComponent(tradeId)}`' in delete_handler
    assert '"DELETE"' in delete_handler
    assert "await loadTrades();" in delete_handler
