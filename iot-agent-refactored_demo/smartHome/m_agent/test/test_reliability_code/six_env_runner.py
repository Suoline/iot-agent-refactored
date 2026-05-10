from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 六套环境共用同一组设备，只是初始温度组合和故障模式不同。
# 共享设备与实体映射如下：
# - device.test_living_room_ac_main -> climate.test_living_room_ac_main
# - device.test_living_room_temperature_sensor_main -> sensor.test_living_room_temperature_main
# - device.test_bedroom_ac_main -> climate.test_bedroom_ac_main
# - device.test_bedroom_temperature_sensor_main -> sensor.test_bedroom_temperature_main
#
# 环境差异说明：
# - pair_a / pair_b：两组环境设备相同，但空调设定温度和温度传感器初始值不同。
# - normal：服务调用按正常逻辑执行。
# - one_shot_network_error：指定空调的首次 set_temperature 调用模拟网络错误。
# - fake_success：指定空调的 set_temperature 调用返回成功，但不会真正改动状态。
#
# 六个 env_id 的含义：
# - te_normal_pair_a_v1：正常模式，使用 pair_a 初始温度组合。
# - te_normal_pair_b_v1：正常模式，使用 pair_b 初始温度组合。
# - te_one_shot_network_error_pair_a_v1：单次网络错误模式，使用 pair_a 初始温度组合。
# - te_one_shot_network_error_pair_b_v1：单次网络错误模式，使用 pair_b 初始温度组合。
# - te_fake_success_pair_a_v1：伪成功模式，使用 pair_a 初始温度组合。
# - te_fake_success_pair_b_v1：伪成功模式，使用 pair_b 初始温度组合。
#
# 固定的六套测试环境按该顺序循环执行。
SIX_ENV_IDS: tuple[str, ...] = (
    "te_normal_pair_a_v1",
    "te_normal_pair_b_v1",
    "te_one_shot_network_error_pair_a_v1",
    "te_one_shot_network_error_pair_b_v1",
    "te_fake_success_pair_a_v1",
    "te_fake_success_pair_b_v1",
)
# 一共有 4 个设备：
#
# device.test_living_room_ac_main
# 设备名：客厅空调设备
# 位置：room.living_room，也就是客厅
#
# device.test_living_room_temperature_sensor_main
# 设备名：客厅温度传感器设备
# 位置：room.living_room，也就是客厅
#
# device.test_bedroom_ac_main
# 设备名：卧室空调设备
# 位置：room.bedroom，也就是卧室
#
# device.test_bedroom_temperature_sensor_main
# 设备名：卧室温度传感器设备
# 位置：room.bedroom，也就是卧室


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
    """默认空回调：即使 func=None，也让六环境切换与恢复流程完整执行。"""
    return None


def _is_failed_result(result: dict[str, Any]) -> bool:
    """任一阶段未成功或存在错误时，将该环境视为失败。"""
    return (
        not result.get("init_ok", False)
        or not result.get("func_ok", False)
        or not result.get("restore_ok", False)
        or bool(result.get("errors"))
    )


def build_six_env_report(
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


def save_six_env_report(report: dict[str, Any]) -> Path:
    """覆盖写入 six_env_results.json，并返回文件路径。"""
    output_path = Path(__file__).with_name("six_env_results.json")
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def run_six_envs(
    instruction: str,
    func: Callable[[str], Any] | None = None,
    *,
    base_url: str = "http://127.0.0.1:8123",
    token: str | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """循环执行六套环境：初始化 -> 回调 -> 恢复，并返回逐环境结果。

    func 的签名约定为 func(instruction: str)。

    返回列表中的每个元素都对应一个环境，字段含义如下：
    - env_id：当前执行的环境 ID。
    - init_ok：环境初始化是否成功。
    - func_ok：回调是否未抛出异常；这不等价于业务语义正确。
    - restore_ok：环境恢复是否成功。
    - func_result：回调原样返回的结果。
    - errors：初始化、回调、恢复阶段收集到的错误信息。

    该函数只负责流程编排与异常收集，不负责判断业务是否真正“做对了”。
    """
    callback = func or _noop_callback
    results: list[dict[str, Any]] = []

    for env_id in SIX_ENV_IDS:
        # item 是单个环境的一次执行快照，便于上层统一汇总结果。
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
                item["func_result"] = callback(instruction)
                # func_ok=True 仅表示回调没有抛异常，不代表业务结果一定正确。
                item["func_ok"] = True
            except Exception as exc:  # noqa: BLE001
                # 回调失败只记录，不中断整个六环境流程。
                errors.append(f"callback failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"init_env failed: {exc}")
        finally:
            # 无论前面是否失败，都尽量恢复原始环境，避免跨环境污染。
            try:
                restore_env(base_url=base_url, token=token, timeout=timeout)
                item["restore_ok"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"restore_env failed: {exc}")

        results.append(item)

    return results


if __name__ == "__main__":
    from smartHome.m_agent.agent.retryValidate_home_agent import run_validate_Agent, run_easy_validate_Agent

    # 直接运行脚本时，落盘完整报告并打印同一份内容，避免终端与文件不一致。
    instruction = "把空调温度调高一些"
    demo_results = run_six_envs(instruction=instruction, func=run_easy_validate_Agent)
    report = build_six_env_report(instruction=instruction, results=demo_results)
    save_six_env_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
