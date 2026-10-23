"""在可信项目内进行有界的字面文本搜索。"""

import json
import os
from pathlib import Path

from minicode.tools.files import FileReadError, _check_sensitive, read_file


MAX_ENTRIES = 2000
MAX_FILES = 200
MAX_MATCHES = 50
MAX_OUTPUT_BYTES = 16 * 1024
MAX_PREVIEW_CHARS = 300
_EXCLUDED_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class SearchError(ValueError):
    """可回传模型的搜索错误。"""


def search_project(
    project_root: str | Path,
    query: str,
    relative_path: str = ".",
) -> dict:
    """在项目目录内递归查找字面文本，返回匹配行及扫描状态。

    输入：project_root 是项目根目录；query 是区分大小写的非空单行搜索词，
        至多 200 字符；relative_path 是相对搜索目录，默认 . 表示整个项目。
    输出：字典包含 matches、scanned_files、skipped_entries、truncated 和 limit。
        matches 的每项提供路径、1 起始行号、匹配片段及片段裁剪标记。
    异常：参数或搜索目录无效时抛出 SearchError；单个文件读取失败则跳过计数。
    限制：不跟随遍历中遇到的符号链接；跳过敏感路径、依赖和缓存目录。
        skipped_entries 不展开被跳过目录；truncated 表示因资源上限提前停止。
    只读取文件，不改变项目或发送网络请求；无匹配不证明整个项目没有相关内容。
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 200 or any(c in query for c in "\x00\r\n"):
        raise SearchError("query 必须是 1 至 200 字符的非空单行文本，不能包含空字符。")
    if not isinstance(relative_path, str) or not relative_path.strip() or "\x00" in relative_path:
        raise SearchError("relative_path 必须是非空的相对目录路径。")
    requested = Path(relative_path)
    if requested.is_absolute():
        raise SearchError("搜索目录必须相对于项目根目录。")
    try:
        _check_sensitive(requested)
        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise SearchError("项目根目录必须是已存在的目录。")
        start = (root / requested).resolve(strict=True)
        if not start.is_relative_to(root):
            raise SearchError("拒绝搜索项目根目录之外的目录。")
        _check_sensitive(start.relative_to(root))
        if not start.is_dir():
            raise SearchError("搜索路径必须是已存在的目录。")
        if any(part.casefold() in _EXCLUDED_DIRS for part in requested.parts + start.relative_to(root).parts):
            raise SearchError("搜索目录属于默认排除的依赖或缓存目录。")
    except FileReadError as error:
        raise SearchError(str(error)) from None
    except SearchError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise SearchError("无法访问搜索目录，请检查路径、权限和符号链接。") from None

    result = {"matches": [], "scanned_files": 0, "skipped_entries": 0,
        "truncated": False, "limit": None}

    def stop(limit: str) -> dict:
        """为当前搜索结果标记提前停止的原因。

        输入：limit 是触发的资源上限名称，如 files、matches 或 output_bytes。
        输出：已经设置 truncated=True 和 limit 的结果字典。
        会修改外层正在积累的 result，但不会修改项目文件。
        """
        result.update(truncated=True, limit=limit)
        return result

    pending = [start]
    entries = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as children:
                for entry in children:
                    if entries >= MAX_ENTRIES:
                        return stop("entries")
                    entries += 1
                    path = Path(entry.path)
                    relative = path.relative_to(root)
                    try:
                        _check_sensitive(relative)
                        if entry.is_symlink():
                            result["skipped_entries"] += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.casefold() in _EXCLUDED_DIRS:
                                result["skipped_entries"] += 1
                            else:
                                pending.append(path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            result["skipped_entries"] += 1
                            continue
                        if result["scanned_files"] >= MAX_FILES:
                            return stop("files")
                        result["scanned_files"] += 1
                        content = read_file(root, relative.as_posix())
                    except (FileReadError, OSError):
                        result["skipped_entries"] += 1
                        continue
                    for number, line in enumerate(content.splitlines(), 1):
                        index = line.find(query)
                        if index < 0:
                            continue
                        if len(result["matches"]) >= MAX_MATCHES:
                            return stop("matches")
                        # 片段围绕首次命中，长行仍能看见搜索词。
                        offset = max(0, index - 50)
                        row = {"path": relative.as_posix(), "line": number,
                            "text": line[offset:offset + MAX_PREVIEW_CHARS],
                            "text_truncated": offset > 0 or len(line) > offset + MAX_PREVIEW_CHARS}
                        # 为最终计数与截断标记预留空间；按 JSON 编码后的字节计量。
                        candidate = {**result, "matches": result["matches"] + [row]}
                        if len(json.dumps(
                            candidate,
                            ensure_ascii=False,
                        ).encode("utf-8")) > MAX_OUTPUT_BYTES - 1024:
                            return stop("output_bytes")
                        result["matches"].append(row)
        except OSError:
            result["skipped_entries"] += 1
    return result
