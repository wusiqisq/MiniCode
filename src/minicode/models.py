"""模型回复与工具请求的数据结构，不执行工具。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    """模型提出的请求：id 用于对应结果，name 是工具名，arguments 是未解析的 JSON 文本。"""

    id: str
    name: str
    arguments: str

    def as_dict(self) -> dict:
        """将当前 ToolCall 转成模型协议需要的工具请求字典。

        输入：无额外参数；读取自身的 id、name 和原始 JSON 参数字符串 arguments。
        输出：带 id、type=function 和 function 字段的新字典。
        只转换数据，不解析参数、不执行工具；每次返回独立的嵌套字典。
        """
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
        """将当前模型回复转成一条 assistant 消息。

        输入：无额外参数；读取自身文本 content 和工具请求元组 tool_calls。
        输出：新的消息字典；有工具请求时附上按原顺序转换的 tool_calls。
        不会修改当前对象，也不会执行工具。
        """
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [call.as_dict() for call in self.tool_calls]
        return message
