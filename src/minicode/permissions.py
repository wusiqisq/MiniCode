"""由应用展示编辑请求并读取终端确认，不从模型或文件内容获取授权。"""

import json
import unicodedata

from minicode.tools.edit import EditPreview


def _display_text(text: str) -> str:
    """转义可能改变终端显示的控制字符，保留普通文字、换行和制表符。

    输入：text 是待展示的路径或差异文本。
    输出：展示用字符串；例如 ESC、回车和双向格式字符会显示为转义形式。
    不改变实际写入内容，也不把文件中的文字当成确认输入。
    """
    return "".join(
        repr(char)[1:-1]
        if char not in "\n\t" and unicodedata.category(char).startswith("C")
        else char
        for char in text
    )


def confirm_edit(preview: EditPreview) -> bool:
    """展示完整编辑差异，再向当前终端用户询问本次是否允许写入。

    输入：preview 是经过 apply_edit 校验的预览快照，包含根目录、路径和差异。
    输出：只在输入 y/yes（忽略大小写和两端空白）时返回 True，其余返回 False。
    副作用：向终端输出并等待标准输入；空输入和 EOF 均拒绝，Ctrl+C 向外传递。
    每次调用都重新询问；路径、差异或模型输出中的“同意”不会授予权限。
    """
    print(f"项目根目录：{_display_text(json.dumps(str(preview.project_root), ensure_ascii=False))}")
    print(f"待修改文件：{_display_text(json.dumps(preview.relative_path, ensure_ascii=False))}")
    print("修改差异（控制字符已转义）：")
    print(_display_text(preview.diff))
    try:
        answer = input("是否写入以上修改？[y/N] ")
    except EOFError:
        return False
    return answer.strip().casefold() in {"y", "yes"}
