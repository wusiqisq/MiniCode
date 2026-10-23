"""仅在临时项目中验证读取和安全边界，不读取用户文件。"""

import os
import pytest

from minicode.tools.files import FileReadError, MAX_READ_BYTES, read_file


def test_read_text_and_preserve_whitespace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_bytes("# 中文\r\nprint('hi')\n".encode())
    assert read_file(tmp_path, "src/main.py") == "# 中文\r\nprint('hi')\n"


@pytest.mark.parametrize("size", [0, MAX_READ_BYTES])
def test_empty_and_exact_size_limit(
    tmp_path,
    size,
):
    (tmp_path / "file.txt").write_bytes(b"a" * size)
    assert read_file(str(tmp_path), "file.txt") == "a" * size


def test_size_limit_counts_bytes(tmp_path):
    (tmp_path / "large.txt").write_bytes(b"a" * (MAX_READ_BYTES + 1))
    with pytest.raises(FileReadError, match="64 KiB"):
        read_file(tmp_path, "large.txt")


def test_bounded_read_even_if_size_check_is_stale(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "growing.txt").write_bytes(b"a" * (MAX_READ_BYTES + 1))
    actual_fstat = os.fstat

    def stale_size(fd):
        values = list(actual_fstat(fd))
        values[6] = 0  # 模拟检查大小后文件增长；仍须限制实际读取。
        return os.stat_result(values)

    monkeypatch.setattr(
        os,
        "fstat",
        stale_size,
    )
    with pytest.raises(FileReadError, match="64 KiB"):
        read_file(tmp_path, "growing.txt")


@pytest.mark.parametrize("path", ["", "  ", "a\x00b", None, 123])
def test_invalid_path(
    tmp_path,
    path,
):
    with pytest.raises(FileReadError, match="非空的相对路径"):
        read_file(tmp_path, path)


def test_absolute_path_even_inside_root_is_rejected(tmp_path):
    with pytest.raises(FileReadError, match="相对于项目"):
        read_file(tmp_path, str(tmp_path / "file.txt"))


def test_parent_traversal_and_similar_prefix_are_rejected(tmp_path):
    root = tmp_path / "project"
    sibling = tmp_path / "project-other"
    root.mkdir()
    sibling.mkdir()
    (sibling / "file.txt").write_text("outside")
    with pytest.raises(FileReadError, match="之外"):
        read_file(root, "../project-other/file.txt")


def test_internal_parent_component_is_allowed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "file.txt").write_text("inside")
    assert read_file(tmp_path, "src/../file.txt") == "inside"


@pytest.mark.parametrize(
    "name",
    [
        ".env", ".env.local", ".env.example", ".ENV", ".git/config", ".ssh/config",
        ".aws/credentials", "private.pem", "server.key", "secrets.toml", "id_ed25519",
    ],
)
def test_sensitive_paths_rejected(
    tmp_path,
    name,
):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("private-content")
    with pytest.raises(FileReadError, match="敏感") as error:
        read_file(tmp_path, name)
    assert "private-content" not in str(error.value)


def test_symlink_inside_project_allowed(tmp_path):
    (tmp_path / "file.txt").write_text("inside")
    (tmp_path / "alias").symlink_to("file.txt")
    assert read_file(tmp_path, "alias") == "inside"


def test_symlink_outside_project_rejected(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "outside").write_text("outside")
    (root / "alias").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FileReadError, match="之外"):
        read_file(root, "alias/outside")


def test_symlink_to_sensitive_file_rejected(tmp_path):
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "alias").symlink_to(".env")
    with pytest.raises(FileReadError, match="敏感"):
        read_file(tmp_path, "alias")


def test_sensitive_alias_to_normal_file_rejected(tmp_path):
    (tmp_path / "normal").write_text("text")
    (tmp_path / ".env").symlink_to("normal")
    with pytest.raises(FileReadError, match="敏感"):
        read_file(tmp_path, ".env")


@pytest.mark.parametrize("path", ["missing", "broken", "loop"])
def test_missing_and_broken_or_looping_links(
    tmp_path,
    path,
):
    (tmp_path / "broken").symlink_to("missing")
    (tmp_path / "loop").symlink_to("loop")
    with pytest.raises(FileReadError):
        read_file(tmp_path, path)


def test_directory_and_invalid_root(tmp_path):
    (tmp_path / "file").write_text("text")
    with pytest.raises(FileReadError, match="普通文件"):
        read_file(tmp_path, ".")
    with pytest.raises(FileReadError, match="根目录"):
        read_file(tmp_path / "file", "anything")
    with pytest.raises(FileReadError, match="不存在"):
        read_file(tmp_path / "missing", "anything")


@pytest.mark.parametrize("content", [b"\xff\xfe", b"text\x00binary"])
def test_non_text_rejected(
    tmp_path,
    content,
):
    (tmp_path / "binary").write_bytes(content)
    with pytest.raises(FileReadError, match="文本"):
        read_file(tmp_path, "binary")


def test_permission_error_is_sanitized(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "file").write_text("text")

    def denied(
        *args,
        **kwargs,
    ):
        raise PermissionError("private-system-details")

    monkeypatch.setattr(
        os,
        "open",
        denied,
    )
    with pytest.raises(FileReadError, match="权限") as error:
        read_file(tmp_path, "file")
    assert "private-system-details" not in str(error.value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="系统不支持 FIFO")
def test_fifo_rejected_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(FileReadError, match="普通文件"):
        read_file(tmp_path, "pipe")
