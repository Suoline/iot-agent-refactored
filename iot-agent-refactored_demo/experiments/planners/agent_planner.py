from __future__ import annotations

from dataclasses import dataclass, field
import configparser
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Optional
import urllib.error
import urllib.request

from pydantic import BaseModel, Field, ValidationError, model_validator

from experiments.memory.schemas import SearchResultPackage


def _redact_error(exc: Exception) -> str:
    text = str(exc).strip()
    return text[:300] if text else type(exc).__name__


class AgentActionModel(BaseModel):
    service: str
    entity_id: str
    args: dict[str, Any] = Field(default_factory=dict)


class AgentPlanPayload(BaseModel):
    action: Optional[AgentActionModel] = None
    actions: list[AgentActionModel] = Field(default_factory=list)
    should_ask_user: bool = False
    reason: str = ""

    @model_validator(mode="after")
    def _merge_single_action(self) -> "AgentPlanPayload":
        if self.action is not None and not self.actions:
            self.actions = [self.action]
        return self


@dataclass
class AgentPlannerDecision:
    action: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    should_ask_user: bool = False
    reason: str | None = None
    raw_output: str | None = None
    structured_output: dict[str, Any] | None = None
    backend: str = "heuristic_fallback"
    provider: str | None = None
    model: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    failure_type: str | None = None


class ExternalLLMClient:
    def __init__(self):
        provider, model, base_url, api_key = self._load_runtime_config()
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self._transport = "http"
        self._model = None
        try:
            from langchain.chat_models import init_chat_model
        except Exception as exc:  # pragma: no cover - runtime specific
            self._init_error = f"langchain_import_failed:{type(exc).__name__}"
        else:
            self._transport = "langchain"
            self._model = init_chat_model(
                model=model,
                model_provider="openai",
                api_key=api_key,
                base_url=base_url,
                temperature=0,
                max_retries=0,
                timeout=20,
            )
            self._init_error = None

    @staticmethod
    def _load_runtime_config() -> tuple[str, str, str, str]:
        config_path = (
            Path(__file__).resolve().parents[2]
            / "smartHome"
            / "m_agent"
            / "common"
            / "llm_config.ini"
        )
        parser = configparser.ConfigParser()
        if not parser.read(config_path, encoding="utf-8"):
            raise RuntimeError("llm_config_missing")
        provider = os.environ.get(
            "EXPERIMENT_AGENT_PROVIDER",
            parser.get("base", "selected_llm_provider"),
        )
        model = os.environ.get("EXPERIMENT_AGENT_MODEL", parser.get(provider, "model"))
        base_url = os.environ.get("EXPERIMENT_AGENT_BASE_URL", parser.get(provider, "base_url"))
        api_key = os.environ.get("EXPERIMENT_AGENT_API_KEY", parser.get(provider, "api_key"))
        return provider, model, base_url, api_key

    def invoke(self, prompt: str) -> dict[str, Any]:
        if self._transport == "http":
            return self._invoke_http(prompt)
        started = time.perf_counter()
        message = self._model.invoke(prompt)
        latency_ms = (time.perf_counter() - started) * 1000
        response_metadata = _coerce_mapping(getattr(message, "response_metadata", None))
        usage = _coerce_usage(message, response_metadata)
        return {
            "raw_output": _render_message_content(getattr(message, "content", message)),
            "tool_calls": _coerce_tool_calls(getattr(message, "tool_calls", None)),
            "usage": usage,
            "latency_ms": latency_ms,
            "model": response_metadata.get("model_name", self.model),
            "provider": response_metadata.get("model_provider", self.provider),
            "response_metadata": response_metadata,
        }

    def _invoke_http(self, prompt: str) -> dict[str, Any]:
        started = time.perf_counter()
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self._chat_completions_url(),
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"http_error:{exc.code}:{body[:200]}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"url_error:{_redact_error(exc)}") from exc
        latency_ms = (time.perf_counter() - started) * 1000
        try:
            response_json = json.loads(raw_body)
        except json.JSONDecodeError as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"invalid_json:{raw_body[:200]}") from exc
        choice = ((response_json.get("choices") or [{}])[0]).get("message") or {}
        usage = _coerce_mapping(response_json.get("usage"))
        model = response_json.get("model", self.model)
        return {
            "raw_output": _render_message_content(choice.get("content", "")),
            "tool_calls": _coerce_tool_calls(choice.get("tool_calls")),
            "usage": usage,
            "latency_ms": latency_ms,
            "model": model,
            "provider": self.provider,
            "response_metadata": {"model_name": model, "transport": self._transport},
        }

    def _chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _coerce_usage(message: Any, response_metadata: Mapping[str, Any]) -> dict[str, Any]:
    usage = _coerce_mapping(getattr(message, "usage_metadata", None))
    if usage:
        return usage
    token_usage = response_metadata.get("token_usage")
    return dict(token_usage) if isinstance(token_usage, Mapping) else {}


def _coerce_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [dict(item) if isinstance(item, Mapping) else {"raw": item} for item in value]
    return [{"raw": value}]


def _render_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                if isinstance(item.get("text"), str):
                    chunks.append(item["text"])
                else:
                    chunks.append(json.dumps(item, ensure_ascii=False))
            else:
                chunks.append(str(item))
        return "\n".join(chunks)
    return str(content)


def _extract_json_payload(raw_output: str) -> dict[str, Any]:
    candidates = [raw_output.strip()]
    if "```" in raw_output:
        for block in raw_output.split("```"):
            block = block.strip()
            if not block:
                continue
            if block.startswith("json"):
                block = block[4:].strip()
            candidates.append(block)
    start = raw_output.find("{")
    end = raw_output.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw_output[start : end + 1].strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("json_parse_failed")


def _build_safety_execution_hint(package: SearchResultPackage) -> dict[str, Any]:
    if package.task_type != "safety":
        return {"direct_execution_allowed": False, "reason": "not_safety_task"}
    if package.should_ask_user:
        return {"direct_execution_allowed": False, "reason": package.ask_reason or "retrieval_requested_clarification"}
    candidates = _best_candidates(package)
    if not candidates:
        return {"direct_execution_allowed": False, "reason": "no_candidate_devices"}
    top = candidates[0]
    if len(candidates) > 1 and (top.score - candidates[1].score) < 0.10:
        return {"direct_execution_allowed": False, "reason": "ambiguous_top_candidates"}
    high_worth_memories = [
        item for item in top.matched_memories
        if item.in_usable_set and item.memory_worth > 0.80 and item.system_status == "active"
    ]
    if not high_worth_memories:
        return {"direct_execution_allowed": False, "reason": "missing_high_memory_worth_grounding"}
    single_action_services = [service for service in top.available_services if service.startswith("lock.")]
    if not single_action_services:
        return {"direct_execution_allowed": False, "reason": "no_lock_service_grounding"}
    return {
        "direct_execution_allowed": True,
        "reason": "single_action_high_memory_worth_grounding",
        "entity_id": top.entity_id,
        "entity_type": top.entity_type,
        "available_services": single_action_services,
        "memory_ids": [item.memory_id for item in high_worth_memories],
        "max_memory_worth": max(item.memory_worth for item in high_worth_memories),
    }


def _build_plan_prompt(task: str, package: SearchResultPackage) -> str:
    usable = [item for item in package.matched_memories if item.in_usable_set]
    grounding = [item for item in package.matched_memories if item.in_usable_set or item.in_grounding_set]
    answerable_memories = [
        {
            "memory_id": item.memory_id,
            "memory_type": item.memory_type,
            "text": item.text,
            "score": item.score,
            "effective_confidence": item.effective_confidence,
        }
        for item in usable
    ]
    safety_execution_hint = _build_safety_execution_hint(package)
    prompt_payload = {
        "task": task,
        "task_type": package.task_type,
        "retrieval_should_ask_user": package.should_ask_user,
        "retrieval_ask_reason": package.ask_reason,
        "candidate_devices": [item.model_dump(mode="json") for item in package.candidate_devices],
        "global_constraints": [item.model_dump(mode="json") for item in package.global_constraints],
        "usable_memories": [item.model_dump(mode="json") for item in usable],
        "grounding_memories": [item.model_dump(mode="json") for item in grounding],
        "answerable_memories": answerable_memories,
        "retrieval_metadata": package.retrieval_metadata,
        "safety_execution_hint": safety_execution_hint,
    }
    return (
        "你是实验环境中的 smart-home 规划器，只负责输出可审计的 JSON 计划，不能执行动作。\n"
        "必须遵守以下规则：\n"
        "1. 只能使用 candidate_devices 里已有的 entity_id 和 available_services。\n"
        "2. task_type=query 时，如果 answerable_memories 足以回答问题，返回一个动作："
        '{"service":"memory.answer","entity_id":"<memory_id>","args":{}}。\n'
        "3. task_type=automation 且当前规则不可执行时，返回 should_ask_user=false 且 actions=[]，不要因为规则过期而发起澄清。\n"
        "4. 如果记忆检索结果本身要求澄清、候选不唯一、或安全风险无法确认，返回 should_ask_user=true 且 actions=[]。\n"
        "5. task_type=safety 时，涉及 routine.run、多动作联动、或缺少强 grounding 的高风险动作，优先澄清。\n"
        "6. 但如果 safety_execution_hint.direct_execution_allowed=true，且你能给出唯一、单个、与 available_services 精确匹配的动作，则应直接返回该动作，不要额外澄清。\n"
        "7. 不要输出额外解释文字，只返回一个 JSON 对象。\n"
        "8. JSON schema: "
        '{"actions":[{"service":"...", "entity_id":"...", "args":{}}], "should_ask_user": false, "reason": "..."}\n'
        f"上下文如下：\n{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}"
    )


def _best_candidates(package: SearchResultPackage) -> list[Any]:
    return sorted(package.candidate_devices, key=lambda item: (-item.score, item.entity_id))


def _apply_post_guards(
    package: SearchResultPackage,
    plan: AgentPlanPayload,
) -> AgentPlanPayload:
    actions = list(plan.actions)
    if package.task_type == "automation" and not actions and not plan.should_ask_user:
        return AgentPlanPayload(
            actions=[],
            should_ask_user=False,
            reason=plan.reason or "automation_no_action",
        )
    if package.task_type == "query":
        answer_ids = {item.memory_id for item in package.matched_memories if item.in_usable_set}
        if len(actions) == 1 and actions[0].service == "memory.answer" and actions[0].entity_id in answer_ids:
            return AgentPlanPayload(
                actions=actions,
                should_ask_user=False,
                reason=plan.reason or "query_answer",
            )
        if plan.should_ask_user:
            return AgentPlanPayload(
                actions=[],
                should_ask_user=True,
                reason=plan.reason or "query_requires_clarification",
            )
        if answer_ids:
            memory_id = sorted(answer_ids)[0]
            return AgentPlanPayload(
                actions=[AgentActionModel(service="memory.answer", entity_id=memory_id, args={})],
                should_ask_user=False,
                reason=plan.reason or "query_answer",
            )
        return AgentPlanPayload(
            actions=[],
            should_ask_user=True,
            reason=plan.reason or "query_requires_clarification",
        )
    if plan.should_ask_user:
        return AgentPlanPayload(
            actions=[],
            should_ask_user=True,
            reason=plan.reason or "model_requested_clarification",
        )
    usable_reflections = [
        item for item in package.matched_memories
        if item.memory_type == "reflection" and item.in_usable_set
    ]
    if usable_reflections:
        return AgentPlanPayload(
            actions=[],
            should_ask_user=True,
            reason="reflection_requires_clarification",
        )
    if package.should_ask_user:
        return AgentPlanPayload(
            actions=[],
            should_ask_user=True,
            reason=package.ask_reason or "retrieval_requested_clarification",
        )
    if package.task_type == "safety":
        if len(actions) > 1:
            return AgentPlanPayload(
                actions=[],
                should_ask_user=True,
                reason="safety_multi_action_requires_confirmation",
            )
        if any(item.service == "routine.run" for item in actions):
            return AgentPlanPayload(
                actions=[],
                should_ask_user=True,
                reason="safety_routine_requires_confirmation",
            )
    best = _best_candidates(package)
    if len(actions) == 1 and len(best) > 1 and (best[0].score - best[1].score) < 0.10:
        selected = {item.entity_id for item in actions}
        if best[0].entity_id in selected or best[1].entity_id in selected:
            return AgentPlanPayload(
                actions=[],
                should_ask_user=True,
                reason="ambiguous_top_candidates",
            )
    if not actions and not plan.should_ask_user:
        return AgentPlanPayload(
            actions=[],
            should_ask_user=True,
            reason=plan.reason or "no_action_generated",
        )
    return AgentPlanPayload(
        actions=actions,
        should_ask_user=plan.should_ask_user,
        reason=plan.reason,
    )


class AgentPlanner:
    """Experiment-side agent planner with optional real LLM planning and explicit fallback."""

    def __init__(self, client_factory: Callable[[], Any] | None = None):
        self._client_factory = client_factory or ExternalLLMClient
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def decide(self, package: SearchResultPackage, task: str) -> AgentPlannerDecision:
        if os.environ.get("EXPERIMENT_AGENT_BACKEND") == "external":
            external = self._decide_external(package, task)
            if external is not None:
                return external
        return self._heuristic_fallback(package)

    def _decide_external(self, package: SearchResultPackage, task: str) -> AgentPlannerDecision | None:
        try:
            client = self._get_client()
        except Exception as exc:
            return self._heuristic_fallback(package, failure_type=f"external_init_failed:{type(exc).__name__}", raw_output=_redact_error(exc))

        try:
            response = client.invoke(_build_plan_prompt(task, package))
        except Exception as exc:
            return self._heuristic_fallback(package, failure_type=f"external_call_failed:{type(exc).__name__}", raw_output=_redact_error(exc))

        raw_output = str(response.get("raw_output", ""))
        tool_calls = _coerce_tool_calls(response.get("tool_calls"))
        usage = dict(response.get("usage", {})) if isinstance(response.get("usage"), Mapping) else {}
        latency_ms = float(response.get("latency_ms", 0.0) or 0.0)
        model = response.get("model")
        provider = response.get("provider")

        try:
            payload = _extract_json_payload(raw_output)
            parsed = AgentPlanPayload.model_validate(payload)
            guarded = _apply_post_guards(package, parsed)
        except (ValueError, ValidationError) as exc:
            return self._heuristic_fallback(
                package,
                failure_type=f"external_parse_failed:{type(exc).__name__}",
                raw_output=raw_output or _redact_error(exc),
                model=model,
                provider=provider,
                tool_calls=tool_calls,
                usage=usage,
                latency_ms=latency_ms,
            )

        actions = [item.model_dump(mode="json") for item in guarded.actions]
        structured_output = {
            "actions": actions,
            "should_ask_user": guarded.should_ask_user,
            "reason": guarded.reason,
            "raw_model_output": raw_output,
            "model": model,
            "provider": provider,
            "tool_calls": tool_calls,
            "backend": "external_llm",
        }
        return AgentPlannerDecision(
            action=actions[0] if actions else None,
            actions=actions,
            should_ask_user=guarded.should_ask_user,
            reason=guarded.reason,
            raw_output=raw_output,
            structured_output=structured_output,
            backend="external_llm",
            provider=provider,
            model=model,
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=latency_ms,
        )

    def _heuristic_fallback(
        self,
        package: SearchResultPackage,
        *,
        failure_type: str | None = None,
        raw_output: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None,
        latency_ms: float = 0.0,
    ) -> AgentPlannerDecision:
        usable = [item for item in package.matched_memories if item.in_usable_set]
        if package.task_type == "query":
            if usable:
                best = max(usable, key=lambda item: (item.score, item.effective_confidence, item.memory_id))
                action = {"service": "memory.answer", "entity_id": best.memory_id, "args": {}}
                return AgentPlannerDecision(
                    action=action,
                    actions=[action],
                    raw_output=raw_output,
                    reason="heuristic_query_answer",
                    failure_type=failure_type,
                    model=model,
                    provider=provider,
                    tool_calls=tool_calls or [],
                    usage=usage or {},
                    latency_ms=latency_ms,
                )
            return AgentPlannerDecision(
                should_ask_user=True,
                raw_output=raw_output,
                reason="no_answerable_memory",
                failure_type=failure_type,
                model=model,
                provider=provider,
                tool_calls=tool_calls or [],
                usage=usage or {},
                latency_ms=latency_ms,
            )
        if package.task_type == "automation" and not usable:
            return AgentPlannerDecision(
                action=None,
                actions=[],
                should_ask_user=False,
                raw_output=raw_output,
                reason="automation_no_action",
                failure_type=failure_type,
                model=model,
                provider=provider,
                tool_calls=tool_calls or [],
                usage=usage or {},
                latency_ms=latency_ms,
            )
        routines = [item for item in usable if item.memory_type == "routine"]
        reflections = [item for item in usable if item.memory_type == "reflection"]
        if reflections:
            return AgentPlannerDecision(
                should_ask_user=True,
                raw_output=raw_output,
                reason="reflection_requires_clarification",
                failure_type=failure_type,
                model=model,
                provider=provider,
                tool_calls=tool_calls or [],
                usage=usage or {},
                latency_ms=latency_ms,
            )
        if routines:
            routine_devices = [
                candidate
                for candidate in package.candidate_devices
                if candidate.entity_id.startswith("routine.")
            ]
            top_candidate = None
            if package.candidate_devices:
                top_candidate = max(package.candidate_devices, key=lambda item: (item.score, item.confidence, item.entity_id))
            if (
                routine_devices
                and package.task_type != "safety"
                and top_candidate is not None
                and top_candidate.entity_id.startswith("routine.")
            ):
                action = {
                    "service": "routine.run",
                    "entity_id": routine_devices[0].entity_id,
                    "args": {},
                }
                return AgentPlannerDecision(
                    action=action,
                    actions=[action],
                    raw_output=raw_output,
                    reason="heuristic_routine",
                    failure_type=failure_type,
                    model=model,
                    provider=provider,
                    tool_calls=tool_calls or [],
                    usage=usage or {},
                    latency_ms=latency_ms,
                )
            if package.candidate_devices:
                best = max(package.candidate_devices, key=lambda item: (item.score, item.confidence, item.entity_id))
                action = {"service": "planner.select", "entity_id": best.entity_id, "args": {}}
                return AgentPlannerDecision(
                    action=action,
                    actions=[action],
                    raw_output=raw_output,
                    reason="heuristic_routine_grounded_select",
                    failure_type=failure_type,
                    model=model,
                    provider=provider,
                    tool_calls=tool_calls or [],
                    usage=usage or {},
                    latency_ms=latency_ms,
                )
            return AgentPlannerDecision(
                should_ask_user=True,
                raw_output=raw_output,
                reason="routine_requires_clarification",
                failure_type=failure_type,
                model=model,
                provider=provider,
                tool_calls=tool_calls or [],
                usage=usage or {},
                latency_ms=latency_ms,
            )
        if package.should_ask_user or not package.candidate_devices:
            return AgentPlannerDecision(
                should_ask_user=True,
                raw_output=raw_output,
                reason=package.ask_reason or "no_candidate_devices",
                failure_type=failure_type,
                model=model,
                provider=provider,
                tool_calls=tool_calls or [],
                usage=usage or {},
                latency_ms=latency_ms,
            )
        best = max(package.candidate_devices, key=lambda item: item.score)
        if package.task_type == "safety":
            grounding = best.matched_memories[0] if best.matched_memories else None
            if grounding is None or grounding.memory_worth <= 0.8:
                return AgentPlannerDecision(
                    should_ask_user=True,
                    raw_output=raw_output,
                    reason="safety_grounding_insufficient",
                    failure_type=failure_type,
                    model=model,
                    provider=provider,
                    tool_calls=tool_calls or [],
                    usage=usage or {},
                    latency_ms=latency_ms,
                )
        action = {"service": "planner.select", "entity_id": best.entity_id, "args": {}}
        return AgentPlannerDecision(
            action=action,
            actions=[action],
            raw_output=raw_output,
            reason="heuristic_select",
            failure_type=failure_type,
            model=model,
            provider=provider,
            tool_calls=tool_calls or [],
            usage=usage or {},
            latency_ms=latency_ms,
        )
