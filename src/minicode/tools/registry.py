"""校验并分发工具请求；不调用模型，不保存会话。"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

from minicode.models import ToolCall
from minicode.tools.files import FileReadError, read_file
from minicode.tools.search import SearchError, search_project


@dataclass(frozen=True)
class ToolResult:
    """请求标识用于把执行结果对应到原始工具请求。"""

    tool_call_id: str
    status: Literal["success", "error"]
    content: str

    def as_message(self) -> dict[str, str]:
        """将工具执行结果转换成可回传模型的一条 tool 消息。

        输入：无额外参数；读取自身请求编号、success/error 状态和文本内容。
        输出：包含 role、tool_call_id 和 JSON 字符串 content 的新字典。
        把状态与内容一起编码，避免将错误误当文件文本；本方法不发网络请求。
        """
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": json.dumps(
                {"status": self.status, "content": self.content},
                ensure_ascii=False,
            ),
        }


def _read_file(
    project_root: str | Path,
    arguments: dict,
) -> str:
    """检查 read_file 的参数对象，然后调用受限文件读取。

    输入：project_root 由程序指定；arguments 只能含字符串 relative_path。
    输出：read_file 返回的完整文件文本，空文件返回空字符串。
    异常：字段不符合要求或文件读取失败时抛出 FileReadError。
    """
    if set(arguments) != {"relative_path"}:
        raise FileReadError("read_file 参数必须且只能包含 relative_path。")
    path = arguments["relative_path"]
    if not isinstance(path, str) or not path.strip():
        raise FileReadError("relative_path 必须是非空字符串。")
    return read_file(project_root, path)


def _search_project(
    project_root: str | Path,
    arguments: dict,
) -> str:
    """检查搜索参数字段，并把搜索结果编码成文本。

    输入：project_root 由程序指定；arguments 必须含 query，可选 relative_path。
    输出：search_project 结果的 JSON 字符串，供 ToolResult 保存和回传。
    异常：字段不符合要求或搜索目录无效时抛出 SearchError。
    """
    if not {"query"} <= set(arguments) or not set(arguments) <= {"query", "relative_path"}:
        raise SearchError("search_project 需要 query，仅允许可选参数 relative_path。")
    result = search_project(
        project_root,
        arguments["query"],
        arguments.get(
            "relative_path",
            ".",
        ),
    )
    return json.dumps(result, ensure_ascii=False)


# 只有程序明确注册的函数可以执行；不使用 eval 或按名称动态导入。
_HANDLERS = {"read_file": _read_file, "search_project": _search_project}


def tool_definitions() -> list[dict]:
    """构造模型可见的工具说明和参数 JSON Schema。

    输入：无参数；工具说明由程序显式定义，模型不能添加可执行函数。
    输出：新的工具说明列表，目前包含 read_file 和 search_project。
    每次构造独立的嵌套对象；Schema 只指导模型，不能替代程序参数校验。
    """
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
    }, {
        "type": "function",
        "function": {
            "name": "search_project",
            "description": (
                "在项目目录递归搜索区分大小写的字面文本，返回相对路径、行号和片段。"
                "跳过敏感路径、符号链接、依赖和缓存目录；只读至多64 KiB的UTF-8文件。"
                "最多扫描2000条目、200文件，返回50匹配行和16 KiB内容；"
                "检查truncated、limit和skipped_entries，结果可能不完整。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 200,
                        "description": "非空单行字面文本，不是正则表达式"},
                    "relative_path": {"type": "string", "description": "相对目录路径，默认项目根目录 ."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }]


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """解析 JSON 字段时拒绝重复字段名。

    输入：pairs 是 json.loads 提供的有序 (字段名, 值) 列表。
    输出：不含重复字段的普通字典。
    异常：字段名重复时抛出 ValueError，避免后一个值悄悄覆盖前一个值。
    """
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复的 JSON 参数")
        result[key] = value
    return result


def dispatch_tool(
    project_root: str | Path,
    tool_call_id: str,
    name: str,
    arguments: str,
) -> ToolResult:
    """校验并执行一个工具请求，把可预期的工具失败转换成结果。

    输入：project_root 由程序指定；tool_call_id 是对应模型请求的编号；
        name 是注册工具名；arguments 是模型返回的原始 JSON 参数字符串。
    输出：ToolResult，携带相同请求编号、success/error 状态和文本内容。
    异常：请求编号无效属于协议错误，抛出 ValueError；其他无效请求返回 error。
    模型不能更换项目根目录；现有工具只读，不修改文件或自行调用模型。
    """
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        raise ValueError("工具请求标识必须是非空字符串。")
    if not isinstance(name, str) or name not in _HANDLERS:
        return ToolResult(
            tool_call_id,
            "error",
            "未知工具，未执行。",
        )
    if not isinstance(arguments, str):
        return ToolResult(
            tool_call_id,
            "error",
            "工具参数必须是 JSON 字符串。",
        )
    try:
        parsed = json.loads(arguments, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return ToolResult(
            tool_call_id,
            "error",
            "工具参数必须是有效 JSON，且不能有重复字段。",
        )
    if not isinstance(parsed, dict):
        return ToolResult(
            tool_call_id,
            "error",
            "工具参数必须是 JSON 对象。",
        )
    try:
        content = _HANDLERS[name](project_root, parsed)
    except (FileReadError, SearchError) as error:
        return ToolResult(
            tool_call_id,
            "error",
            str(error),
        )
    return ToolResult(
        tool_call_id,
        "success",
        content,
    )


def dispatch_tool_calls(
    project_root: str | Path,
    calls: tuple[ToolCall, ...],
) -> list[ToolResult]:
    """按模型给出的顺序执行一批已完成协议解析的工具请求。

    输入：project_root 是程序确定的根目录；calls 是 ToolCall 元组。
    输出：与请求顺序一致的 ToolResult 列表，一个普通工具失败不会阻止下一项。
    每项仍经过 dispatch_tool 校验；此辅助函数本身不实现 Agent Loop 的次数限制。
    """
    return [dispatch_tool(
        project_root,
        call.id,
        call.name,
        call.arguments,
    ) for call in calls]
