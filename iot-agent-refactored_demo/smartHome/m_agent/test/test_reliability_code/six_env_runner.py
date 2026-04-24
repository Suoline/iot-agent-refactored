from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from smartHome.m_agent.agent.retryValidate_home_agent import run_validate_Agent
from smartHome.m_agent.common.global_config import GLOBALCONFIG
from smartHome.m_agent.common.logger import setup_dynamic_indent_logger

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


def _noop_callback(_: str) -> str:
    """默认空执行函数：当外部未提供 run_func 时用于占位。"""
    return ""


def _sanitize_tag(value: str) -> str:
    """将字符串清洗成仅含字母数字下划线的安全标签。"""
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_")
    return cleaned or "unknown"


def _init_global_env(env_id: str, instruction: str) -> None:
    """初始化每个环境执行前的全局日志与 LangSmith 项目名。"""
    safe_env_id = _sanitize_tag(env_id)
    safe_model = _sanitize_tag(GLOBALCONFIG.model)
    logger_name = f"six_env_runner_{safe_model}_{safe_env_id}"
    log_file_path = f"logs/test_reliability/six_env_runner/{safe_model}/{safe_env_id}.log"

    GLOBALCONFIG.nested_logger = setup_dynamic_indent_logger(
        logger_name=logger_name,
        log_file_path=log_file_path,
    )
    os.environ["LANGSMITH_PROJECT"] = f"shuaSmartHomeReliabilityTest_{GLOBALCONFIG.model}"
    GLOBALCONFIG.nested_logger.info(
        f"[six_env_runner] initialized global env for env_id={env_id}, instruction={instruction}",
        extra={"indent": ""},
    )


def _save_six_env_results(results: list[dict[str, Any]], instruction: str) -> Path:
    """把当前运行结果覆盖写入脚本同目录 six_env_results.json。"""
    output_path = Path(__file__).resolve().parent / "six_env_results.json"

    failed_envs = [
        item["env_id"]
        for item in results
        if (not item.get("init_ok", False))
        or (not item.get("func_ok", False))
        or (not item.get("restore_ok", False))
        or bool(item.get("errors"))
    ]
    payload = {
        "instruction": instruction,
        "summary": {
            "total_envs": len(results),
            "init_ok": sum(1 for item in results if item.get("init_ok", False)),
            "func_ok": sum(1 for item in results if item.get("func_ok", False)),
            "restore_ok": sum(1 for item in results if item.get("restore_ok", False)),
            "failed_envs": failed_envs,
        },
        "results": results,
    }

    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def run_six_envs(
    instruction: str,
    run_func: Callable[[str], str] | None = None,
    *,
    base_url: str = "http://127.0.0.1:8123",
    token: str | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """
    循环执行六套环境：初始化 -> 执行函数 -> 恢复，并返回逐环境结果。
    """
    # 统一约束执行函数签名为：输入字符串指令，输出字符串结果。
    run_func = run_func or _noop_callback
    results: list[dict[str, Any]] = []

    for env_id in SIX_ENV_IDS:
        # 每个环境都独立记录三段状态，便于后续定位到底卡在初始化、执行还是恢复。
        item: dict[str, Any] = {
            "env_id": env_id,
            "init_ok": False,
            "func_ok": False,
            "restore_ok": False,
            "func_result": None,
            "errors": [],
        }
        errors: list[str] = item["errors"]

        # 容错原则：单环境失败只记录，不影响后续环境继续跑，保证一次批跑数据尽量完整。
        try:
            init_env(env_id, base_url=base_url, token=token, timeout=timeout)
            _init_global_env(env_id=env_id, instruction=instruction)
            item["init_ok"] = True

            try:
                item["func_result"] = run_func(instruction)
                item["func_ok"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"run_func failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"init_env failed: {exc}")
        finally:
            # 无论前面是否失败，都尽量恢复原始环境，降低环境污染对后续结果的干扰。
            try:
                restore_env(base_url=base_url, token=token, timeout=timeout)
                item["restore_ok"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"restore_env failed: {exc}")

        results.append(item)
        _save_six_env_results(results=results, instruction=instruction)

    _save_six_env_results(results=results, instruction=instruction)
    return results


if __name__ == "__main__":
    # 最小示例：执行后结果会写入当前目录的 six_env_results.json。
    demo_results = run_six_envs(instruction="把客厅空调温度调到17度", run_func=run_validate_Agent)
    print(json.dumps(demo_results, ensure_ascii=False, indent=2))
