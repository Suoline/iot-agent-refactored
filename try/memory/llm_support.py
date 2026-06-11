from __future__ import annotations

from typing import Any, Protocol


class StructuredOutputInvoker(Protocol):
    def invoke(self, schema: type, messages: list[dict[str, str]]) -> Any:
        ...


class LangChainStructuredInvoker:
    def __init__(self, llm_factory) -> None:
        self.llm_factory = llm_factory

    def invoke(self, schema: type, messages: list[dict[str, str]]) -> Any:
        llm = self.llm_factory()
        structured_llm = llm.with_structured_output(schema)
        return structured_llm.invoke(messages)

