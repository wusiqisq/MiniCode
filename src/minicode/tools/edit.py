"""单文件修改预览与经应用确认的写入，不调用模型。"""

from dataclasses import dataclass
import difflib
import json
import os
import stat
import tempfile
from collections.abc import Callable
from typing import Literal
from pathlib import Path

from minicode.tools.files import FileReadError, MAX_READ_BYTES, _resolve_file_path, read_file


MAX_DIFF_BYTES = 128 * 1024


class EditPreviewError(ValueError):
    """可展示的预览错误，不包含文件正文或底层系统异常。"""


@dataclass(frozen=True)
class EditPreview:
    """一次只读预览的数据快照；它不是用户授权，也不能用于直接写入。

    relative_path：请求的相对文件路径。
    original_content / updated_content：完整修改前后文本，保留换行与空白。
    diff：带相对路径和行号的统一差异，不是整个修改后文件。
    project_root / target_path：解析后的根目录与目标路径，绑定快照所属项目。
    file_signature：文件身份与状态，不包含读取可能改变的访问时间。
    apply_edit 仍须重新校验路径、获得确认并检查外部修改。
    """

    relative_path: str
    original_content: str
    updated_content: str
    diff: str
    project_root: Path
    target_path: Path
    file_signature: tuple[int, ...]


def _text_size(
    text: str,
    field_name: str,
) -> int:
    """校验替换文本，并计算其 UTF-8 大小。

    输入：text 是替换参数或快照文本，field_name 是程序提供的字段名。
    输出：编码后的字节数；不改变文本的空白和换行。
    异常：非字符串、空字节、无效 Unicode 或超过 64 KiB 时抛出 EditPreviewError。
    空字符串由调用方按用途判断，new_text 为空可以表示删除一段内容。
    """
    if not isinstance(text, str) or "\x00" in text:
        raise EditPreviewError(f"{field_name} 必须是字符串，且不能包含空字符。")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise EditPreviewError(f"{field_name} 不能编码为有效 UTF-8 文本。") from None
    if size > MAX_READ_BYTES:
        raise EditPreviewError(f"{field_name} 超过文本上限（64 KiB）。")
    return size


def _make_diff(
    relative_path: str,
    original: str,
    updated: str,
) -> str:
    """比较修改前后文本，生成完整且大小受限的统一差异。

    输入：relative_path 用作展示标签；original / updated 是完整文本快照。
    输出：统一差异字符串，包含前三行/后三行上下文及缺少末尾换行的标记。
    异常：差异超过 128 KiB 时抛出 EditPreviewError，不返回截断预览。
    路径标签使用 JSON 引号转义，避免文件名中的换行伪装成差异内容。
    """
    changes = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=json.dumps(f"a/{relative_path}", ensure_ascii=False),
        tofile=json.dumps(f"b/{relative_path}", ensure_ascii=False),
        lineterm="\n",
    )
    parts = []
    size = 0
    for line in changes:
        # difflib 不会自动标记没有换行的末行；补上标记，防止删除行和新增行粘连。
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        size += len(line.encode("utf-8"))
        if size > MAX_DIFF_BYTES:
            raise EditPreviewError("修改差异超过预览上限（128 KiB），请缩小替换范围。")
        parts.append(line)
    return "".join(parts)


def preview_edit(
    project_root: str | Path,
    relative_path: str,
    old_text: str,
    new_text: str,
) -> EditPreview:
    """对项目内一个已有文件提出唯一文本替换，只生成预览。

    输入：
        project_root：程序指定的项目根目录；relative_path：项目内相对文件路径。
        old_text：要查找的非空原文本，按大小写、空白和换行精确匹配。
        new_text：替换文本，可以为空字符串；两个文本各不超过 64 KiB。
    输出：EditPreview，包含路径、原文、替换后的完整文本和统一差异。
    异常：路径/读取失败、匹配数不是一处、没有变化或结果超限时抛出 EditPreviewError。
    副作用：只读取文件；不写入、不发送网络请求、不修改 Session 或执行确认。
    预览只是读取时的快照，不证明文件此后保持不变，也不是写入授权。
    """
    _text_size(old_text, "old_text")
    _text_size(new_text, "new_text")
    if not old_text:
        raise EditPreviewError("old_text 不能为空；本步只支持替换已有内容。")
    if old_text == new_text:
        raise EditPreviewError("原文本与替换文本相同，没有产生修改。")

    try:
        root, target = _resolve_file_path(project_root, relative_path)
        before = _file_signature(target.stat())
        original = read_file(root, relative_path)
        _, checked_target = _resolve_file_path(root, relative_path)
        if target != checked_target or before != _file_signature(target.stat()):
            raise EditPreviewError("文件在生成预览时发生变化，请重新生成预览。")
    except FileReadError as error:
        raise EditPreviewError(str(error)) from None
    except OSError:
        raise EditPreviewError("无法获取文件状态，请检查权限或重新生成预览。") from None

    start = original.find(old_text)
    if start < 0:
        raise EditPreviewError("未找到原文本，请先读取文件并使用准确的原文本。")
    # 从起点加 1 继续查找，连重叠匹配也拒绝：aaa 中的 aa 有两个可选起点。
    if original.find(old_text, start + 1) >= 0:
        raise EditPreviewError("原文本匹配多处，请补充上下文以唯一定位修改位置。")

    updated = original[:start] + new_text + original[start + len(old_text):]
    if len(updated.encode("utf-8")) > MAX_READ_BYTES:
        raise EditPreviewError("修改后文件超过大小上限（64 KiB），请缩小修改范围。")
    diff = _make_diff(
        relative_path,
        original,
        updated,
    )
    return EditPreview(
        relative_path=relative_path,
        original_content=original,
        updated_content=updated,
        diff=diff,
        project_root=root,
        target_path=target,
        file_signature=before,
    )


class EditApplyError(ValueError):
    """可展示的写入错误，不包含文件正文、密钥或系统异常详情。"""


class EditConflictError(EditApplyError):
    """文件已变化，必须重新生成预览并重新确认。"""


@dataclass(frozen=True)
class EditApplyResult:
    """写入交接结果：applied 表示已替换，denied 表示本次确认未通过、没有写入。"""

    status: Literal["applied", "denied"]
    message: str


def _file_signature(metadata: os.stat_result) -> tuple[int, ...]:
    """从文件状态提取用于发现替换、改写和权限变化的比较值。

    输入：metadata 是 stat/fstat 的文件状态。
    输出：设备、inode、大小、修改/状态时间、权限、属主和链接数的整数元组。
    不包含读取本身可能改变的 atime；不访问磁盘或修改文件。
    """
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
    )


def _check_write_state(
    project_root: str | Path,
    preview: EditPreview,
) -> tuple[Path, os.stat_result]:
    """重新核对路径、文件身份和原文，拒绝已变化或不支持的目标。

    输入：project_root 是应用指定的根目录，preview 是待确认或已确认的快照。
    输出：(target, metadata)，分别是当前目标绝对路径与普通文件状态。
    异常：目标已变化抛出 EditConflictError；不支持的路径或读取失败抛出 EditApplyError。
    只读取；写入比预览更严格，拒绝 ..、任意路径段符号链接、硬链接和特殊权限文件。
    """
    try:
        root, target = _resolve_file_path(project_root, preview.relative_path)
        if root != preview.project_root or target != preview.target_path:
            raise EditConflictError("项目或目标路径与预览不一致，请重新生成预览。")
        cursor = root
        for part in Path(preview.relative_path).parts:
            if part == "..":
                raise EditApplyError("写入路径不能包含 ..，请使用直接的项目内相对路径。")
            cursor = cursor / part
            if cursor.is_symlink():
                raise EditApplyError("写入不支持符号链接，请直接使用普通文件路径重新预览。")
        metadata = target.stat()
        if metadata.st_nlink != 1:
            raise EditApplyError("写入不支持有多个硬链接的文件。")
        if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise EditApplyError("写入不支持带特殊权限位的文件。")
        if _file_signature(metadata) != preview.file_signature:
            raise EditConflictError("文件状态已变化，请重新生成预览并确认，未覆盖文件。")
        if read_file(root, preview.relative_path) != preview.original_content:
            raise EditConflictError("文件内容已变化，请重新生成预览并确认，未覆盖文件。")
        if not metadata.st_mode & 0o222 or not os.access(target, os.W_OK):
            raise EditApplyError("文件不可写，请检查权限。")
        return target, metadata
    except FileReadError as error:
        raise EditApplyError(str(error)) from None
    except OSError:
        raise EditApplyError("无法检查写入目标，请检查路径和权限。") from None


def apply_edit(
    project_root: str | Path,
    preview: EditPreview,
    *,
    confirm: Callable[[EditPreview], bool],
) -> EditApplyResult:
    """展示并逐次确认一个预览，通过冲突检查后用临时文件替换目标。

    输入：project_root 是应用指定的根目录，preview 是 preview_edit 返回的快照；
        confirm 是应用提供的确认函数，须展示项目、路径和差异，再返回 bool。
    输出：EditApplyResult，applied 表示已写入，denied 表示未获确认、没有写入。
    异常：冲突抛出 EditConflictError，其他校验/写入错误抛出 EditApplyError；中断向外传递。
    副作用：仅 confirm 返回严格的 True 后创建同目录临时文件并写入，最后替换原文件。
    保留普通权限位和文本字节；不保证所有权、ACL、扩展属性或磁盘崩溃后的目录持久性。
    最后检查与替换之间仍有竞争窗口，不构成抵御并发恶意修改的安全沙箱。
    """
    if not isinstance(preview, EditPreview) or not callable(confirm):
        raise EditApplyError("需要有效预览和应用提供的确认函数。")
    # 不直接相信预览的展示字段：再次核对大小并重算 diff，确认所见与实际写入一致。
    try:
        _text_size(preview.original_content, "original_content")
        _text_size(preview.updated_content, "updated_content")
        if preview.original_content == preview.updated_content:
            raise EditApplyError("预览没有产生修改。")
        expected_diff = _make_diff(
            preview.relative_path,
            preview.original_content,
            preview.updated_content,
        )
        if preview.diff != expected_diff:
            raise EditApplyError("预览差异与修改内容不一致，请重新生成预览。")
    except EditPreviewError as error:
        raise EditApplyError(str(error)) from None
    _check_write_state(project_root, preview)
    if confirm(preview) is not True:
        return EditApplyResult("denied", "用户未确认，本次未写入文件。")

    temporary_path = None
    try:
        target, metadata = _check_write_state(project_root, preview)
        # 同目录保证处于同一文件系统；原文件直到 replace 成功前都不会被截断。
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".minicode-edit-",
            dir=target.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            output = os.fdopen(descriptor, "wb")
        except BaseException:
            # fdopen 失败时还没有文件对象负责关闭底层描述符。
            os.close(descriptor)
            raise
        with output:
            output.write(preview.updated_content.encode("utf-8"))
            output.flush()
            os.fchmod(output.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fsync(output.fileno())
        # 等待确认和准备临时文件都可能耗时；替换前再检查一次，不把旧快照当成锁。
        _check_write_state(project_root, preview)
        os.replace(temporary_path, target)
        temporary_path = None
    except OSError:
        raise EditApplyError("写入未完成，请检查文件及目录权限或存储空间。") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                raise EditApplyError("写入未完成且临时文件清理失败，请检查目标目录。") from None
    return EditApplyResult("applied", "已确认并写入文件。")
