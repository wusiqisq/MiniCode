"""校验并分发工具请求；不调用模型，不保存会话。"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

from minicode.models import ToolCall
from minicode.tools.files import FileReadError, read_file


@dataclass(frozen=True)
class ToolResult:
    """请求标识用于把执行结果对应到原始工具请求。"""

    tool_call_id: str
    status: Literal["success", "error"]
    content: str

    def as_message(self) -> dict[str, str]:
        """把状态和内容一起编码，避免把失败结果误当作文件内容。"""
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": json.dumps(
                {"status": self.status, "content": self.content}, ensure_ascii=False
            ),
        }


def _read_file(project_root: str | Path, arguments: dict) -> str:
    if set(arguments) != {"relative_path"}:
        raise FileReadError("read_file 参数必须且只能包含 relative_path。")
    path = arguments["relative_path"]
    if not isinstance(path, str) or not path.strip():
        raise FileReadError("relative_path 必须是非空字符串。")
    return read_file(project_root, path)


# 只有程序明确注册的函数可以执行；不使用 eval 或按名称动态导入。
_HANDLERS = {"read_file": _read_file}


def tool_definitions() -> list[dict]:
    """每次返回新的工具说明，外部修改不会影响后续请求。"""
    return [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定项目内至多 64 KiB 的 UTF-8 文本；拒绝越界和敏感路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "相对于项目根目录的文件路径"},
                },
                "required": ["relative_path"],
                "additionalProperties": False,
            },
        },
    }]


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复的 JSON 参数")
        result[key] = value
    return result


def dispatch_tool(
    project_root: str | Path, tool_call_id: str, name: str, arguments: str
) -> ToolResult:
    """执行一个请求。根目录来自调用方，不能通过模型参数更换。

    缺少有效请求标识是调用方的协议错误，抛出 ValueError；其他无效请求
    返回关联到该标识的错误结果。读取的成功与失败均不改变任何文件。
    """
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        raise ValueError("工具请求标识必须是非空字符串。")
    if not isinstance(name, str) or name not in _HANDLERS:
        return ToolResult(tool_call_id, "error", "未知工具，未执行。")
    if not isinstance(arguments, str):
        return ToolResult(tool_call_id, "error", "工具参数必须是 JSON 字符串。")
    try:
        parsed = json.loads(arguments, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return ToolResult(tool_call_id, "error", "工具参数必须是有效 JSON，且不能有重复字段。")
    if not isinstance(parsed, dict):
        return ToolResult(tool_call_id, "error", "工具参数必须是 JSON 对象。")
    try:
        content = _HANDLERS[name](project_root, parsed)
    except FileReadError as error:
        return ToolResult(tool_call_id, "error", str(error))
    return ToolResult(tool_call_id, "success", content)


def dispatch_tool_calls(
    project_root: str | Path, calls: tuple[ToolCall, ...]
) -> list[ToolResult]:
    """按顺序处理协议层已解析的请求；单个工具失败也产生对应结果。"""
    return [dispatch_tool(project_root, call.id, call.name, call.arguments) for call in calls]
