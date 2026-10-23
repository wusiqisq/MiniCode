"""校验并分发工具请求；不调用模型，不保存会话。"""

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

from minicode.models import ToolCall
from minicode.tools.edit import EditApplyError, EditApplyResult, EditPreview, EditPreviewError, apply_edit, preview_edit
from minicode.tools.files import FileReadError, read_file
from minicode.tools.search import SearchError, search_project


@dataclass(frozen=True)
class ToolResult:
    """请求标识用于把执行结果对应到原始工具请求。"""

    tool_call_id: str
    status: Literal["success", "error", "denied", "skipped"]
    content: str
    changed_path: str | None = None  # 应用内部记录实际写入，不接受模型设置。

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


def _edit_file(
    project_root: str | Path,
    arguments: dict,
    *,
    confirm: Callable[[EditPreview], bool] | None,
) -> EditApplyResult:
    """校验模型的三个编辑参数，生成预览并交给应用确认写入。

    输入：project_root 由程序指定；arguments 只允许 relative_path、old_text、new_text；
        confirm 是应用提供的可信确认函数，缺失时拒绝，不能由模型传入。
    输出：EditApplyResult；预览/写入错误由外层分发转成 error，中断向外传播。
    副作用：仅经过预览和确认后可能写入普通文件，不调用模型或更新会话。
    """
    if set(arguments) != {"relative_path", "old_text", "new_text"}:
        raise EditPreviewError("edit_file 必须且只能包含 relative_path、old_text、new_text。")
    if not all(isinstance(value, str) for value in arguments.values()):
        raise EditPreviewError("edit_file 的三个参数必须都是字符串。")
    if confirm is None:
        return EditApplyResult("denied", "应用未提供用户确认接口，编辑未执行。")
    preview = preview_edit(
        project_root,
        arguments["relative_path"],
        arguments["old_text"],
        arguments["new_text"],
    )
    return apply_edit(
        project_root,
        preview,
        confirm=confirm,
    )


# 只有程序明确注册的函数可以执行；不使用 eval 或按名称动态导入。
_HANDLERS = {"read_file": _read_file, "search_project": _search_project, "edit_file": _edit_file}


def tool_definitions() -> list[dict]:
    """构造模型可见的工具说明和参数 JSON Schema。

    输入：无参数；工具说明由程序显式定义，模型不能添加可执行函数。
    输出：新的工具说明列表，目前包含 read_file、search_project 和需确认的 edit_file。
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
    }, {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "对项目内已有普通UTF-8文件做一次精确文本替换，须先展示差异并逐次获用户确认。"
                "old_text非空且仅匹配一处，new_text可为空；参数及修改后文件最多64 KiB。"
                "拒绝敏感、越界、链接及文件冲突，不创建或删除文件。"
                "用户拒绝后不得重试或换工具绕过；只根据工具状态报告是否写入。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "相对于项目根目录的已有普通文件路径"},
                    "old_text": {"type": "string", "minLength": 1, "description": "原文件中唯一匹配的精确文本，保留空白和换行"},
                    "new_text": {"type": "string", "description": "替换文本，可为空字符串"},
                },
                "required": ["relative_path", "old_text", "new_text"],
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
    *,
    edit_confirm: Callable[[EditPreview], bool] | None = None,
) -> ToolResult:
    """校验并执行一个工具请求，把可预期的工具失败转换成结果。

    输入：project_root 由程序指定；tool_call_id 是对应模型请求的编号；
        name 是注册工具名；arguments 是模型返回的原始 JSON 参数字符串。
    输出：ToolResult，携带相同请求编号、success/error 状态和文本内容。
    异常：请求编号无效属于协议错误，抛出 ValueError；其他无效请求返回 error。
    edit_confirm 是应用传入的用户确认函数；无确认接口时拒绝编辑。
    模型不能更换根目录或传入授权；changed_path 仅在实际写入成功后由程序设置。
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
        if name == "edit_file":
            applied = _edit_file(
                project_root,
                parsed,
                confirm=edit_confirm,
            )
            return ToolResult(
                tool_call_id,
                "success" if applied.status == "applied" else "denied",
                json.dumps({"relative_path": parsed["relative_path"], "message": applied.message}, ensure_ascii=False),
                changed_path=parsed["relative_path"] if applied.status == "applied" else None,
            )
        content = _HANDLERS[name](project_root, parsed)
    except (FileReadError, SearchError, EditPreviewError, EditApplyError) as error:
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
    *,
    edit_confirm: Callable[[EditPreview], bool] | None = None,
) -> list[ToolResult]:
    """顺序执行一批请求；普通错误继续，用户拒绝则跳过剩余整批。

    输入：项目根目录、已解析的 ToolCall 元组和可选应用确认函数。
    输出：按请求顺序返回结果；拒绝后的请求为 skipped，不再执行。
    中断向外传播；本辅助接口不保存会话或实现 Agent Loop 的次数限制。
    """
    results = []
    denied = False
    for call in calls:
        if denied:
            result = ToolResult(
                call.id,
                "skipped",
                "用户未确认，剩余工具未执行。",
            )
        else:
            result = dispatch_tool(
                project_root,
                call.id,
                call.name,
                call.arguments,
                edit_confirm=edit_confirm,
            )
        results.append(result)
        denied = denied or result.status == "denied"
    return results
