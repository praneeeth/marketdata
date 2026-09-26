"""Evaluation centre database migrations."""

from sqlalchemy import create_engine, text


def test_m120_adds_agent_prediction_evaluation_columns(tmp_path):
    """After the upgrade, the old item outcome table has group IDs and basis fields."""
    from src.platform.persistence.migrations import _m120_agent_prediction_evaluation

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """CREATE TABLE agent_prediction_outcomes (
                  id INTEGER PRIMARY KEY, agent_name TEXT, stock_symbol TEXT,
                  stock_market TEXT, prediction_date TEXT, horizon_days INTEGER,
                  action TEXT, action_label TEXT, outcome_status TEXT
                )"""
            )
        )
        _m120_agent_prediction_evaluation(conn)
        columns = {
            row[1]
            for row in conn.execute(
                text("PRAGMA table_info(agent_prediction_outcomes)")
            )
        }
    # Windows can't clean up the temp directory while the connection pool holds the sqlite file handle.
    engine.dispose()

    assert {"prediction_group_id", "horizon_unit"} <= columns


def test_m121_creates_backtest_runs_table(tmp_path):
    """The migration creates the persistent back-test runs table in an existing database."""
    from src.platform.persistence.migrations import _m121_backtest_runs

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-backtest.db'}")
    with engine.begin() as conn:
        _m121_backtest_runs(conn)
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(backtest_runs)"))
        }
    engine.dispose()

    assert {"status", "strategy_code", "config", "input_snapshot", "result"} <= columns
