import ast
import json
import re
from typing import Any


class LLMPrivacyHandler:
    """使用 LLM 为文本生成可逆的隐私占位符。"""

    _DATE_LIKE_PATTERN = re.compile(r"^\d{2,4}\s*-\s*\d{1,2}\s*-\s*\d{1,2}$")
    _TOKEN_PATTERN = re.compile(r"@[^@]+@")
    _ARITHMETIC_ALLOWED_CHARS = frozenset("0123456789.+-*/() \t\n\r")
    _ALLOWED_BIN_OPS = {
        ast.Add: lambda left, right: left + right,
        ast.Sub: lambda left, right: left - right,
        ast.Mult: lambda left, right: left * right,
        ast.Div: lambda left, right: left / right,
    }
    _ALLOWED_UNARY_OPS = {
        ast.UAdd: lambda value: value,
        ast.USub: lambda value: -value,
    }

    def __init__(self, llm: Any):
        if llm is None:
            raise ValueError("llm 不能为空")

        self.llm = llm
        self.encode_map: dict[str, str] = {}
        self.decode_map: dict[str, str] = {}

    def encode_text(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("text 必须是字符串")
        if not text:
            self.encode_map = {}
            self.decode_map = {}
            return text

        messages = self._build_encode_prompt(text)
        response = self.llm.invoke(messages)
        response_text = getattr(response, "content", response)
        mapping = self._parse_llm_mapping(str(response_text))

        self.encode_map = mapping
        self.decode_map = {encoded: original for original, encoded in mapping.items()}
        return self._replace_from_map(text, self.encode_map)

    def decode_text(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("text 必须是字符串")
        if not text:
            return text
        if self._TOKEN_PATTERN.search(text) and not self.decode_map:
            raise ValueError("decode_map 为空；请先在同一个实例上调用 encode_text")

        decoded_text = self._replace_from_map(text, self.decode_map)
        return self._evaluate_arithmetic_expressions(decoded_text)

    def _build_encode_prompt(self, text: str) -> list[dict[str, str]]:
        """构造发送给 LLM 的提示词，要求其仅返回脱敏映射表。"""
        system_prompt = """
你是隐私信息处理助手。你的任务是识别文本中的敏感信息，并为每个敏感值生成可逆的语义化占位符。

输出规则：
1. 只返回 JSON，不要添加解释、Markdown、代码块或其他文字。
2. JSON 格式必须严格为 {"encoded_text": {"原始值": "semantic_name"}}。
3. semantic_name 只能包含字母、数字、下划线，优先使用语义化命名。
4. 如果同类型出现多个值，请追加编号，如 entity_id_01、entity_id_02。
5. 不要改写原文，不要翻译，不要总结，只返回映射表。
6. 已经形如 @token@ 的内容不要重复处理。

优先识别但不限于以下隐私类型：
- entity_id
- IP 地址
- WiFi SSID
- 唯一标识符或上下文 ID
- 时间戳
- 敏感状态值

以下内容默认不视为隐私，除非上下文明确要求：
- friendly_name
- 普通描述性文本
""".strip()

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

    def _parse_llm_mapping(self, response_text: str) -> dict[str, str]:
        """解析 LLM 返回的 JSON，并规范化为 @token@ 形式的映射。"""
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if not json_match:
            raise ValueError(f"LLM 返回内容中不包含 JSON：{response_text}")

        payload = json.loads(json_match.group())
        encoded_text = payload.get("encoded_text")
        if not isinstance(encoded_text, dict):
            raise ValueError(f"LLM 返回内容必须包含 'encoded_text' 对象：{payload}")

        normalized_mapping: dict[str, str] = {}
        used_semantic_names: dict[str, int] = {}

        for original_value, semantic_name in encoded_text.items():
            original = str(original_value).strip()
            semantic = str(semantic_name).strip().strip("@")
            if not original or not semantic:
                continue
            if self._TOKEN_PATTERN.fullmatch(original):
                continue

            # 仅保留协议允许的字符，避免生成无法安全替换的占位符名称。
            semantic = re.sub(r"[^0-9A-Za-z_]+", "_", semantic)
            semantic = re.sub(r"_+", "_", semantic).strip("_")
            if not semantic:
                continue
            if semantic[0].isdigit():
                semantic = f"value_{semantic}"

            occurrence = used_semantic_names.get(semantic, 0) + 1
            used_semantic_names[semantic] = occurrence
            unique_semantic = semantic if occurrence == 1 else f"{semantic}_{occurrence:02d}"

            normalized_mapping[original] = f"@{unique_semantic}@"

        return normalized_mapping

    def _evaluate_arithmetic_expressions(self, text: str) -> str:
        """在完成反向替换后，尝试计算文本中独立的四则运算表达式。"""
        if not text:
            return text

        pieces: list[str] = []
        start = 0
        text_length = len(text)

        while start < text_length:
            if text[start] not in self._ARITHMETIC_ALLOWED_CHARS:
                pieces.append(text[start])
                start += 1
                continue

            end = start
            while end < text_length and text[end] in self._ARITHMETIC_ALLOWED_CHARS:
                end += 1

            segment = text[start:end]
            previous_char = text[start - 1] if start > 0 else ""
            next_char = text[end] if end < text_length else ""
            pieces.append(self._maybe_evaluate_segment(segment, previous_char, next_char))
            start = end

        return "".join(pieces)

    def _maybe_evaluate_segment(self, segment: str, previous_char: str, next_char: str) -> str:
        """仅在片段看起来像独立算式时才求值，避免误伤日期和时间戳。"""
        stripped_segment = segment.strip()
        if not stripped_segment:
            return segment
        if not any(operator in stripped_segment for operator in "+-*/"):
            return segment
        if not any(char.isdigit() for char in stripped_segment):
            return segment
        if self._DATE_LIKE_PATTERN.fullmatch(stripped_segment):
            return segment
        if self._looks_like_timestamp_fragment(previous_char, next_char):
            return segment

        try:
            result = self._safe_eval_expression(stripped_segment)
        except Exception:
            return segment

        formatted_result = self._format_numeric_result(result)
        leading_whitespace_length = len(segment) - len(segment.lstrip())
        trailing_whitespace_length = len(segment) - len(segment.rstrip())
        leading = segment[:leading_whitespace_length]
        trailing = segment[len(segment) - trailing_whitespace_length:] if trailing_whitespace_length else ""
        return f"{leading}{formatted_result}{trailing}"

    def _looks_like_timestamp_fragment(self, previous_char: str, next_char: str) -> bool:
        """识别可能位于时间戳中的片段，避免把它们误当成算式。"""
        if next_char in {"T", "t"}:
            return True
        if previous_char == ":" and next_char == ":":
            return True
        return False

    def _safe_eval_expression(self, expression: str) -> float:
        """使用 AST 安全求值，仅允许受控的算术表达式。"""
        parsed = ast.parse(expression, mode="eval")
        return self._eval_ast_node(parsed.body)

    def _eval_ast_node(self, node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Num):
            return float(node.n)
        if isinstance(node, ast.BinOp):
            operator = self._ALLOWED_BIN_OPS.get(type(node.op))
            if operator is None:
                raise ValueError("不支持的算术运算符")
            return operator(self._eval_ast_node(node.left), self._eval_ast_node(node.right))
        if isinstance(node, ast.UnaryOp):
            operator = self._ALLOWED_UNARY_OPS.get(type(node.op))
            if operator is None:
                raise ValueError("不支持的一元运算符")
            return operator(self._eval_ast_node(node.operand))
        raise ValueError("不支持的算术表达式")

    def _format_numeric_result(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return format(value, ".15g")

    def _replace_from_map(self, text: str, replace_map: dict[str, str]) -> str:
        """按原始值长度倒序替换，避免短字符串提前替换影响长字符串。"""
        replaced_text = text
        for original_value in sorted(replace_map.keys(), key=len, reverse=True):
            replaced_text = replaced_text.replace(original_value, replace_map[original_value])
        return replaced_text


__all__ = ["LLMPrivacyHandler"]
