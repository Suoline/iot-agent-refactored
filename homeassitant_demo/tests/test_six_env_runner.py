from __future__ import annotations

from typing import Any

import six_env_runner


def test_run_six_envs_calls_all_envs_in_order_and_callback_receives_instruction(monkeypatch: Any) -> None:
    init_calls: list[str] = []
    restore_calls: list[str] = []
    callback_calls: list[str] = []

    def fake_init_env(env_id: str, **_: Any) -> dict[str, Any]:
        init_calls.append(env_id)
        return {"status": "initialized", "env_id": env_id}

    def fake_restore_env(**_: Any) -> dict[str, Any]:
        restore_calls.append("restore")
        return {"status": "restored"}

    def fake_callback(instruction: str) -> str:
        callback_calls.append(instruction)
        return f"ok-{instruction}"

    monkeypatch.setattr(six_env_runner, "init_env", fake_init_env)
    monkeypatch.setattr(six_env_runner, "restore_env", fake_restore_env)

    instruction = "demo"
    results = six_env_runner.run_six_envs(instruction=instruction, func=fake_callback)

    assert init_calls == list(six_env_runner.SIX_ENV_IDS)
    assert callback_calls == [instruction] * len(six_env_runner.SIX_ENV_IDS)
    assert len(restore_calls) == len(six_env_runner.SIX_ENV_IDS)
    assert len(results) == len(six_env_runner.SIX_ENV_IDS)
    assert all(item["init_ok"] is True for item in results)
    assert all(item["func_ok"] is True for item in results)
    assert all(item["restore_ok"] is True for item in results)
    assert all(item["errors"] == [] for item in results)


def test_run_six_envs_with_none_func_uses_noop_callback(monkeypatch: Any) -> None:
    def fake_init_env(env_id: str, **_: Any) -> dict[str, Any]:
        return {"status": "initialized", "env_id": env_id}

    def fake_restore_env(**_: Any) -> dict[str, Any]:
        return {"status": "restored"}

    monkeypatch.setattr(six_env_runner, "init_env", fake_init_env)
    monkeypatch.setattr(six_env_runner, "restore_env", fake_restore_env)

    results = six_env_runner.run_six_envs(instruction="no-op", func=None)

    assert len(results) == len(six_env_runner.SIX_ENV_IDS)
    assert all(item["init_ok"] is True for item in results)
    assert all(item["func_ok"] is True for item in results)
    assert all(item["restore_ok"] is True for item in results)
    assert all(item["func_result"] is None for item in results)


def test_run_six_envs_init_failure_still_restores_and_continues(monkeypatch: Any) -> None:
    init_calls: list[str] = []
    restore_calls: list[str] = []
    callback_calls = 0
    failing_env = six_env_runner.SIX_ENV_IDS[1]

    def fake_init_env(env_id: str, **_: Any) -> dict[str, Any]:
        init_calls.append(env_id)
        if env_id == failing_env:
            raise RuntimeError("init boom")
        return {"status": "initialized", "env_id": env_id}

    def fake_restore_env(**_: Any) -> dict[str, Any]:
        restore_calls.append("restore")
        return {"status": "restored"}

    def fake_callback(_: str) -> str:
        nonlocal callback_calls
        callback_calls += 1
        return "ok"

    monkeypatch.setattr(six_env_runner, "init_env", fake_init_env)
    monkeypatch.setattr(six_env_runner, "restore_env", fake_restore_env)

    results = six_env_runner.run_six_envs(instruction="demo", func=fake_callback)
    result_by_env = {item["env_id"]: item for item in results}

    assert init_calls == list(six_env_runner.SIX_ENV_IDS)
    assert len(restore_calls) == len(six_env_runner.SIX_ENV_IDS)
    assert callback_calls == len(six_env_runner.SIX_ENV_IDS) - 1
    assert result_by_env[failing_env]["init_ok"] is False
    assert result_by_env[failing_env]["func_ok"] is False
    assert result_by_env[failing_env]["restore_ok"] is True
    assert any("init_env failed:" in msg for msg in result_by_env[failing_env]["errors"])


def test_run_six_envs_callback_failure_still_restores_and_continues(monkeypatch: Any) -> None:
    restore_calls: list[str] = []
    failing_env = six_env_runner.SIX_ENV_IDS[2]

    def fake_init_env(env_id: str, **_: Any) -> dict[str, Any]:
        return {"status": "initialized", "env_id": env_id}

    def fake_restore_env(**_: Any) -> dict[str, Any]:
        restore_calls.append("restore")
        return {"status": "restored"}

    call_count = 0

    def fake_callback(_: str) -> str:
        nonlocal call_count
        current_env = six_env_runner.SIX_ENV_IDS[call_count]
        call_count += 1
        if current_env == failing_env:
            raise RuntimeError("callback boom")
        return "ok"

    monkeypatch.setattr(six_env_runner, "init_env", fake_init_env)
    monkeypatch.setattr(six_env_runner, "restore_env", fake_restore_env)

    results = six_env_runner.run_six_envs(instruction="demo", func=fake_callback)
    result_by_env = {item["env_id"]: item for item in results}

    assert len(restore_calls) == len(six_env_runner.SIX_ENV_IDS)
    assert result_by_env[failing_env]["init_ok"] is True
    assert result_by_env[failing_env]["func_ok"] is False
    assert result_by_env[failing_env]["restore_ok"] is True
    assert any("callback failed:" in msg for msg in result_by_env[failing_env]["errors"])
    for env_id, item in result_by_env.items():
        if env_id == failing_env:
            continue
        assert item["func_ok"] is True


def test_run_six_envs_restore_failure_is_recorded(monkeypatch: Any) -> None:
    failing_env = six_env_runner.SIX_ENV_IDS[4]
    state = {"current_env_id": None}

    def fake_init_env(env_id: str, **_: Any) -> dict[str, Any]:
        state["current_env_id"] = env_id
        return {"status": "initialized", "env_id": env_id}

    def fake_restore_env(**_: Any) -> dict[str, Any]:
        if state["current_env_id"] == failing_env:
            raise RuntimeError("restore boom")
        return {"status": "restored"}

    def fake_callback(_: str) -> str:
        return "ok"

    monkeypatch.setattr(six_env_runner, "init_env", fake_init_env)
    monkeypatch.setattr(six_env_runner, "restore_env", fake_restore_env)

    results = six_env_runner.run_six_envs(instruction="demo", func=fake_callback)
    result_by_env = {item["env_id"]: item for item in results}

    assert result_by_env[failing_env]["restore_ok"] is False
    assert any("restore_env failed:" in msg for msg in result_by_env[failing_env]["errors"])
    for env_id, item in result_by_env.items():
        if env_id == failing_env:
            continue
        assert item["restore_ok"] is True
