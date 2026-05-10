from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 两套正常环境共用同一组设备，只是初始温度组合不同。
# - te_normal_pair_a_v1：正常模式，使用 pair_a 初始温度组合。
# - te_normal_pair_b_v1：正常模式，使用 pair_b 初始温度组合。
TWO_NORMAL_ENV_IDS: tuple[str, ...] = (
    "te_normal_pair_a_v1",
    "te_normal_pair_b_v1",
)


def _build_headers(token: str | None = None) -> dict[str, str]:
    """构建通用 JSON 请求头；传入 token 时额外附带 Bearer 鉴权。"""
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
    """统一发送 HTTP JSON 请求，并将底层网络异常包装成 RuntimeError。"""
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
    """调用 mock API 初始化指定测试环境；只返回接口响应，不做业务校验。"""
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
    """调用 mock API 恢复原始环境快照；只返回接口响应，不做业务校验。"""
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


def _noop_callback(_: str) -> None:
    """默认空回调：即使 func=None，也让双环境切换与恢复流程完整执行。"""
    return None


def _run_privacy_home_agent(instruction: str) -> Any:
    """执行隐私保护版 HomeAgent，并在调用前固定 LangSmith 项目名。"""
    # 懒导入会触发 GLOBALCONFIG 初始化，可能先把项目名重置回默认值。
    from smartHome.m_agent.agent.codec_home_agent import run_easy_codec_Agent

    os.environ["LANGSMITH_PROJECT"] = "HomeAgent- privacy"
    return run_easy_codec_Agent(instruction)


def _is_failed_result(result: dict[str, Any]) -> bool:
    """任一阶段未成功或存在错误时，将该环境视为失败。"""
    return (
        not result.get("init_ok", False)
        or not result.get("func_ok", False)
        or not result.get("restore_ok", False)
        or bool(result.get("errors"))
    )


def build_two_env_report(
    instruction: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """将逐环境结果封装为可直接落盘的报告对象。"""
    failed_envs = [result["env_id"] for result in results if _is_failed_result(result)]
    return {
        "instruction": instruction,
        "summary": {
            "total_envs": len(results),
            "init_ok": sum(1 for result in results if result.get("init_ok", False)),
            "func_ok": sum(1 for result in results if result.get("func_ok", False)),
            "restore_ok": sum(1 for result in results if result.get("restore_ok", False)),
            "failed_envs": failed_envs,
        },
        "results": results,
    }


def save_two_env_report(report: dict[str, Any]) -> Path:
    """覆盖写入 two_normal_env_results.json，并返回文件路径。"""
    output_path = Path(__file__).with_name("two_normal_env_results.json")
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def run_two_normal_envs(
    instruction: str,
    func: Callable[[str], Any] | None = None,
    *,
    base_url: str = "http://127.0.0.1:8123",
    token: str | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """循环执行两套正常环境：初始化 -> 回调 -> 恢复，并返回逐环境结果。"""
    callback = func or _noop_callback
    results: list[dict[str, Any]] = []

    for env_id in TWO_NORMAL_ENV_IDS:
        item: dict[str, Any] = {
            "env_id": env_id,
            "init_ok": False,
            "func_ok": False,
            "restore_ok": False,
            "func_result": None,
            "errors": [],
        }
        errors: list[str] = item["errors"]

        try:
            init_env(env_id, base_url=base_url, token=token, timeout=timeout)
            item["init_ok"] = True
            try:
                item["func_result"] = callback(instruction)
                item["func_ok"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"callback failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"init_env failed: {exc}")
        finally:
            try:
                restore_env(base_url=base_url, token=token, timeout=timeout)
                item["restore_ok"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"restore_env failed: {exc}")

        results.append(item)

    return results


if __name__ == "__main__":
    instruction = "把空调温度调高2度"
    demo_results = run_two_normal_envs(instruction=instruction, func=_run_privacy_home_agent)
    report = build_two_env_report(instruction=instruction, results=demo_results)
    save_two_env_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
