from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# 固定的六套测试环境，按该顺序循环执行。
SIX_ENV_IDS: tuple[str, ...] = (
    "te_normal_pair_a_v1",
    "te_normal_pair_b_v1",
    "te_one_shot_network_error_pair_a_v1",
    "te_one_shot_network_error_pair_b_v1",
    "te_fake_success_pair_a_v1",
    "te_fake_success_pair_b_v1",
)


def _build_headers(token: str | None = None) -> dict[str, str]:
    """构建请求头；当传入 token 时自动附带 Bearer 鉴权。"""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_json(
    method: str,
    path: str,
    *,
    base_url: str = "http://127.0.0.1:8123",
    token: str | None = None,
    timeout: float = 10.0,
    payload: Any | None = None,
) -> Any:
    """发送 HTTP 请求并返回 JSON 反序列化结果。"""
    url = f"{base_url.rstrip('/')}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url=url, data=data, headers=_build_headers(token), method=method.upper())

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method.upper()} {path} failed with HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method.upper()} {path} failed: {exc.reason}") from exc

    if not raw:
        return None
    return json.loads(raw)


def init_env(
    env_id: str,
    *,
    base_url: str = "http://127.0.0.1:8123",
    token: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """初始化指定测试环境。"""
    payload = _request_json(
        "POST",
        "/api/mock/init_env",
        base_url=base_url,
        token=token,
        timeout=timeout,
        payload={"env_id": env_id},
    )
    if not isinstance(payload, dict):
        raise RuntimeError("init_env returned non-object response.")
    return payload


def restore_env(
    *,
    base_url: str = "http://127.0.0.1:8123",
    token: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """恢复到初始化前的原始环境快照。"""
    payload = _request_json(
        "POST",
        "/api/mock/original_env",
        base_url=base_url,
        token=token,
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("restore_env returned non-object response.")
    return payload


def _noop_callback(_: Any, __: str) -> None:
    """默认空回调：当外部未提供 func 时用于占位。"""
    return None


def run_six_envs(
    instruction: Any,
    func: Callable[[Any, str], Any] | None = None,
    *,
    base_url: str = "http://127.0.0.1:8123",
    token: str | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """循环执行六套环境：初始化 -> 回调 -> 恢复，并返回逐环境结果。"""
    callback = func or _noop_callback
    results: list[dict[str, Any]] = []

    for env_id in SIX_ENV_IDS:
        item: dict[str, Any] = {
            "env_id": env_id,
            "init_ok": False,
            "func_ok": False,
            "restore_ok": False,
            "func_result": None,
            "errors": [],
        }
        errors: list[str] = item["errors"]

        # 每个环境独立处理：初始化失败不影响后续环境。
        try:
            init_env(env_id, base_url=base_url, token=token, timeout=timeout)
            item["init_ok"] = True
            try:
                item["func_result"] = callback(instruction, env_id)
                item["func_ok"] = True
            except Exception as exc:  # noqa: BLE001
                # 回调失败只记录，不中断整个六环境流程。
                errors.append(f"callback failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"init_env failed: {exc}")
        finally:
            # 无论前面是否失败，都尽量恢复原始环境。
            try:
                restore_env(base_url=base_url, token=token, timeout=timeout)
                item["restore_ok"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"restore_env failed: {exc}")

        results.append(item)

    return results


if __name__ == "__main__":
    # 最小示例：使用默认 no-op 回调跑一轮并打印结果。
    demo_results = run_six_envs(instruction={"demo": True}, func=None)
    print(json.dumps(demo_results, ensure_ascii=False, indent=2))
