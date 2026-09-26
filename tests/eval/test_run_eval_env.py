"""Loading behaviour of the local eval environment file."""

from tests.eval import run_eval


def test_load_local_eval_env_reads_unset_eval_variables(tmp_path, monkeypatch):
    """EVAL variables in `.env.eval` that aren't set explicitly are used by make eval."""
    (tmp_path / ".env.eval").write_text(
        "EVAL_AI_BASE_URL=https://eval.example/v1\n"
        "EVAL_AI_API_KEY=eval-secret\n"
        "EVAL_AI_MODEL=eval-model\n"
        "AI_API_KEY=must-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_eval, "REPO_ROOT", tmp_path)
    for key in ("EVAL_AI_BASE_URL", "EVAL_AI_API_KEY", "EVAL_AI_MODEL", "AI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    run_eval.load_local_eval_env()

    assert run_eval.os.environ["EVAL_AI_BASE_URL"] == "https://eval.example/v1"
    assert run_eval.os.environ["EVAL_AI_API_KEY"] == "eval-secret"
    assert run_eval.os.environ["EVAL_AI_MODEL"] == "eval-model"
    assert "AI_API_KEY" not in run_eval.os.environ


def test_load_local_eval_env_keeps_explicit_environment_value(tmp_path, monkeypatch):
    """Eval variables provided explicitly by the terminal or CI win over the local file."""
    (tmp_path / ".env.eval").write_text("EVAL_AI_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.setattr(run_eval, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("EVAL_AI_MODEL", "ci-model")

    run_eval.load_local_eval_env()

    assert run_eval.os.environ["EVAL_AI_MODEL"] == "ci-model"


def test_main_loads_local_eval_env_before_running_chat_cases(tmp_path, monkeypatch):
    """When run directly, the local eval config takes effect before the chat cases start."""
    (tmp_path / ".env.eval").write_text(
        "EVAL_AI_BASE_URL=https://eval.example/v1\n"
        "EVAL_AI_API_KEY=eval-secret\n"
        "EVAL_AI_MODEL=eval-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_eval, "REPO_ROOT", tmp_path)
    for key in ("EVAL_AI_BASE_URL", "EVAL_AI_API_KEY", "EVAL_AI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(run_eval, "run_structured", lambda only: (1, 1))
    monkeypatch.setattr(run_eval.sys, "argv", ["run_eval.py"])
    seen: dict[str, tuple[str, str, str] | None] = {}

    async def fake_run_chat(only, use_judge):
        seen["config"] = run_eval._eval_ai_config()
        return 1, 1

    monkeypatch.setattr(run_eval, "run_chat", fake_run_chat)

    assert run_eval.main() == 0
    assert seen["config"] == ("https://eval.example/v1", "eval-secret", "eval-model")
