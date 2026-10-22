"""模型回复与工具请求的数据结构，不执行工具。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class ModelReply:
    """普通回复含文本；工具回复还包含按模型返回顺序排列的请求。"""

    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()

    def as_message(self) -> dict:
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [call.as_dict() for call in self.tool_calls]
        return message
