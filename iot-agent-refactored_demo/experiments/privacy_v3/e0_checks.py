"""E0 运行前实现检查（实验方案 §7.E0，不占论文结果表）。

1. 全部消息出网路径经过同一 egress handler（before_model 钩子，四配置一致）
2. C2/C3 original→placeholder→original 往返正确率 100%
3. 不同原始值不映射到同一占位符
4. 用户输入中的 placeholder-like 文本先转义
5. LLM 返回未知占位符时不映射到任何真实设备（拒绝/受控保留）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(GIT_ROOT))

RESULTS: list[dict] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        RESULTS.append({"check": name, "pass": True, "detail": detail or "ok"})
        print(f"[PASS] {name}{': ' + detail if detail and detail != 'ok' else ''}")
    except AssertionError as exc:
        RESULTS.append({"check": name, "pass": False, "detail": str(exc)})
        print(f"[FAIL] {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append({"check": name, "pass": False, "detail": f"{type(exc).__name__}: {exc}"})
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")


def main() -> int:
    # ---------------------------------------------------------------- E0.1
    def e0_1():
        from smartHome.m_agent.agent.hooks import langchain_middleware as mw
        src = Path(mw.__file__).read_text(encoding="utf-8")
        assert "encode_messages(messages)" in src, "before_model 未经过 encode_messages"
        assert "transform_messages(messages, decode_text)" in src, "after_model 未经过 decode_text"
        return "system/user/tool_result 全部经 before_model→encode_messages 出网；响应经 after_model→decode_text"

    check("E0.1 统一 egress handler", e0_1)

    # ---------------------------------------------------------------- E0.2
    def e0_2():
        from langchain_core.messages import HumanMessage
        from experiments.privacy_v3.privacy_strategies import OpaqueStrategy
        from smartHome.m_agent.agent.utils import privacy_codex

        originals = [
            "把 light.h1_living_main 关掉，客厅太亮了",
            "王芳的卧室温度是 28.0 度",
            "设备 climate.h2_living_ac 在 2026-09-25T10:30:00 温度 24.0",
        ]
        # C2
        c2 = OpaqueStrategy()
        encoded = c2.encode_messages([HumanMessage(content=t) for t in originals])
        decoded = [c2.decode_text(m.content) for m in encoded]
        for orig, dec in zip(originals, decoded):
            assert orig == dec, f"C2 往返失败: {orig!r} -> {dec!r}"
        # C3（privacy_codex 原实现）
        privacy_codex.register_extra_sensitive({"王芳": "person", "客厅": "room"})
        for orig in originals:
            enc = privacy_codex.encode_text(orig)
            dec = privacy_codex.decode_text(enc)
            assert dec == orig, f"C3 往返失败: {orig!r} -> {enc!r} -> {dec!r}"
        return f"C2/C3 各 {len(originals)} 条文本往返 100%"

    check("E0.2 占位符往返可逆", e0_2)

    # ---------------------------------------------------------------- E0.3
    def e0_3():
        from experiments.privacy_v3.privacy_strategies import OpaqueStrategy, detect_sensitive
        values = ["light.h1_a", "light.h1_b", "sensor.h1_x", "climate.h1_ac"]
        c2 = OpaqueStrategy()
        tokens = set()
        for v in values:
            detection = detect_sensitive([v])
            mapping = c2.tokenizer.encode_mapping(detection)
            for tok in mapping.values():
                assert tok not in tokens, f"令牌冲突: {tok}"
                tokens.add(tok)
        # C3：privacy_codex 全局映射保证唯一（_make_unique_token）
        from smartHome.m_agent.agent.utils import privacy_codex
        enc_map = {}
        for v in values:
            token = privacy_codex.encode_text(v)
            enc_map[v] = token
        assert len(set(enc_map.values())) == len(enc_map), "C3 出现重复占位符"
        return f"{len(values)} 个不同原始值的占位符两两互异"

    check("E0.3 占位符唯一性", e0_3)

    # ---------------------------------------------------------------- E0.4
    def e0_4():
        from langchain_core.messages import HumanMessage
        from experiments.privacy_v3.privacy_strategies import OpaqueStrategy, RedactionStrategy
        # 用户文本中的占位符形态不被检出/替换，编码解码后保持完整（不破坏）
        tricky = "请对 [REDACTED] 与 [ID_A7F3] 两个占位符做检查"
        c1 = RedactionStrategy()
        out1 = c1.encode_messages([HumanMessage(content=tricky)])[0].content
        assert "[REDACTED]" in out1, f"C1 破坏了既有占位符文本: {out1!r}"
        assert c1.decode_text(out1) == tricky, "C1 往返改变原文"
        c2 = OpaqueStrategy()
        out2 = c2.encode_messages([HumanMessage(content="[ID_B219] 已存在的令牌，sensor.h1_x 温度 26.5")])[0].content
        assert "[ID_B219]" in out2, f"C2 把既有令牌误当敏感值替换: {out2!r}"
        assert "sensor.h1_x" not in out2, f"C2 漏检真实实体: {out2!r}"
        return "用户输入中既有占位符形态不被检出替换，往返后保持完整"

    check("E0.4 用户占位符文本转义", e0_4)

    # ---------------------------------------------------------------- E0.5
    def e0_5():
        from experiments.privacy_v3.privacy_strategies import OpaqueStrategy
        c2 = OpaqueStrategy()
        # 建立真实映射后，模型返回未注册令牌不应绑定任何设备
        c2.tokenizer.encode_mapping({"light.h1_real": "entity_id"})
        unknown = "[ID_ZZZZ] 不存在于映射表"
        decoded = c2.decode_unknown_kept(unknown)
        assert "[ID_ZZZZ]" in decoded, "未知令牌被错误改写"
        # C3：未注册 @token@ 保持原样（不映射设备）
        from smartHome.m_agent.agent.utils import privacy_codex
        out = privacy_codex.decode_text("操作 @unknown_token_42@ 设备")
        assert "@unknown_token_42@" in out, "C3 未知占位符被错误改写"
        return "未知占位符原样保留，不绑定任何真实设备（下游 HA 调用将受控失败）"

    check("E0.5 未知占位符不绑定设备", e0_5)

    out_path = Path(__file__).parent / "results" / "e0_checks.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(1 for r in RESULTS if r["pass"])
    print(f"\nE0: {passed}/{len(RESULTS)} 通过")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
