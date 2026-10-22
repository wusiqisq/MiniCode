"""读取可信项目中的小型 UTF-8 文件，不向模型发送内容。"""

import os
from pathlib import Path
import stat


MAX_READ_BYTES = 64 * 1024
_SENSITIVE_NAMES = {
    ".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube",
    ".netrc", ".npmrc", ".pypirc", "credentials", "credentials.json",
    "secrets", "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".keystore"}


class FileReadError(ValueError):
    """可展示的读取错误，不包含文件内容或底层系统异常。"""


def _check_sensitive(path: Path) -> None:
    for part in path.parts:
        name = part.casefold()
        if (
            name == ".env" or name.startswith(".env.")
            or name in _SENSITIVE_NAMES
            or Path(name).suffix in _SENSITIVE_SUFFIXES
        ):
            raise FileReadError("拒绝读取默认排除的敏感路径。")


def read_file(project_root: str | Path, relative_path: str) -> str:
    """返回至多 64 KiB 的完整 UTF-8 文本，保留原有空白。

    路径按调用时状态校验；这不是抵御并发恶意文件替换的沙箱。
    默认敏感路径列表也不能识别普通源码中硬编码的所有秘密。
    """
    if not isinstance(relative_path, str) or not relative_path.strip() or "\x00" in relative_path:
        raise FileReadError("文件路径必须是非空的相对路径，且不能包含空字符。")
    requested = Path(relative_path)
    if requested.is_absolute():
        raise FileReadError("只允许使用相对于项目根目录的文件路径。")
    _check_sensitive(requested)

    try:
        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise FileReadError("项目根目录必须是已存在的目录。")
        target = (root / requested).resolve(strict=True)
        if not target.is_relative_to(root):
            raise FileReadError("拒绝读取项目根目录之外的文件。")
        _check_sensitive(target.relative_to(root))
        if not target.is_file():
            raise FileReadError("只能读取普通文件，不能读取目录或特殊文件。")

        # 非阻塞打开避免文件被替换成 FIFO 后无限等待；拒绝最终路径的新符号链接。
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(target, flags), "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise FileReadError("只能读取普通文件，不能读取目录或特殊文件。")
            if metadata.st_size > MAX_READ_BYTES:
                raise FileReadError("文件超过读取上限（64 KiB），请缩小文件范围。")
            # 即便文件在检查后增长，也只读取上限加一个字节。
            content = source.read(MAX_READ_BYTES + 1)
    except FileNotFoundError:
        raise FileReadError("项目根目录或文件不存在，或符号链接目标不存在。") from None
    except FileReadError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise FileReadError("无法读取文件，请检查路径、权限和符号链接。") from None

    if len(content) > MAX_READ_BYTES:
        raise FileReadError("文件超过读取上限（64 KiB），请缩小文件范围。")
    if b"\x00" in content:
        raise FileReadError("文件包含空字节，不能作为 UTF-8 文本读取。")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        raise FileReadError("文件不是有效的 UTF-8 文本。") from None
