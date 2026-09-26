"""Migration 130 translates stored Chinese labels to English.

The Chinese inputs are built from code points so the test source stays ASCII.
"""

from sqlalchemy import create_engine, text

WATCH_ZH = chr(0x89C2) + chr(0x671B)  # "watch"
PREPARE_ADD_ZH = chr(0x51C6) + chr(0x5907) + chr(0x52A0) + chr(0x4ED3)  # "prepare to add"
DAILY_REPORT_ZH = chr(0x6536) + chr(0x76D8) + chr(0x590D) + chr(0x76D8)  # "daily close report"
TREND_ZH = chr(0x8D8B) + chr(0x52BF) + chr(0x5EF6) + chr(0x7EED)  # "trend continuation"
DEFAULT_ACCOUNT_ZH = chr(0x9ED8) + chr(0x8BA4) + chr(0x8D26) + chr(0x6237)  # "default account"


def test_m130_translates_stored_labels(tmp_path):
    from src.platform.persistence.migrations import _m130_english_labels

    engine = create_engine(f"sqlite:///{tmp_path / 'labels.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE stock_suggestions (id INTEGER PRIMARY KEY, action_label TEXT, agent_label TEXT)"))
        conn.execute(text("CREATE TABLE strategy_signal_runs (id INTEGER PRIMARY KEY, action_label TEXT, strategy_name TEXT)"))
        conn.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(
            text("INSERT INTO stock_suggestions (action_label, agent_label) VALUES (:a, :g), (:p, :g)"),
            {"a": WATCH_ZH, "p": PREPARE_ADD_ZH, "g": DAILY_REPORT_ZH},
        )
        conn.execute(
            text("INSERT INTO strategy_signal_runs (action_label, strategy_name) VALUES (:p, :s)"),
            {"p": PREPARE_ADD_ZH, "s": TREND_ZH},
        )
        conn.execute(
            text("INSERT INTO accounts (name) VALUES (:d), ('My Zerodha')"),
            {"d": DEFAULT_ACCOUNT_ZH},
        )

        _m130_english_labels(conn)
        _m130_english_labels(conn)  # re-runnable

        suggestions = conn.execute(text("SELECT action_label, agent_label FROM stock_suggestions ORDER BY id")).all()
        signals = conn.execute(text("SELECT action_label, strategy_name FROM strategy_signal_runs")).all()
        accounts = [r[0] for r in conn.execute(text("SELECT name FROM accounts ORDER BY id"))]
    engine.dispose()

    assert suggestions == [("Watch", "Daily close report"), ("Plan to add", "Daily close report")]
    assert signals == [("Prepare to add", "Trend continuation")]
    assert accounts == ["Default account", "My Zerodha"]


def test_m130_skips_missing_tables(tmp_path):
    """A database without these tables is left alone."""
    from src.platform.persistence.migrations import _m130_english_labels

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with engine.begin() as conn:
        _m130_english_labels(conn)
    engine.dispose()
