"""只在临时项目验证修改预览，所有路径均不执行写入。"""

from dataclasses import FrozenInstanceError

import pytest

from minicode.tools import edit
from minicode.tools.edit import EditPreviewError, preview_edit
from minicode.tools.files import MAX_READ_BYTES
from minicode.tools.registry import tool_definitions


def test_preview_contains_snapshot_diff_and_never_writes(tmp_path):
    """用临时源码验证路径、快照和差异；预览后原文件字节必须保持不变。"""
    path = tmp_path / "main.py"
    before = "# 示例\nvalue = 1\nprint(value)\n".encode()
    path.write_bytes(before)
    preview = preview_edit(
        tmp_path,
        "main.py",
        "value = 1",
        "value = 2",
    )
    assert preview.relative_path == "main.py"
    assert preview.original_content == before.decode()
    assert preview.updated_content == "# 示例\nvalue = 2\nprint(value)\n"
    assert preview.diff == (
        '--- "a/main.py"\n+++ "b/main.py"\n@@ -1,3 +1,3 @@\n'
        ' # 示例\n-value = 1\n+value = 2\n print(value)\n'
    )
    assert path.read_bytes() == before
    with pytest.raises(FrozenInstanceError):
        preview.updated_content = "不允许修改快照"


@pytest.mark.parametrize(
    "original,old,new,updated",
    [
        ("一\n二\n三\n", "一\n二", "壹\n贰", "壹\n贰\n三\n"),
        ("  value = 1\r\nend\r\n", "value = 1", "value = 2", "  value = 2\r\nend\r\n"),
        ("a.*b", ".*", "?", "a?b"),
        ("one", "one", "", ""),
        ("a  b", "  ", " ", "a b"),
        ("x", "x", "x\n", "x\n"),
    ],
)
def test_exact_replacement_preserves_other_text(
    tmp_path,
    original,
    old,
    new,
    updated,
):
    """临时文本覆盖多行、CRLF、字面匹配、删除和空白；结果与原文件分别检查。"""
    path = tmp_path / "text"
    path.write_bytes(original.encode())
    preview = preview_edit(
        tmp_path,
        "text",
        old,
        new,
    )
    assert preview.updated_content == updated
    assert preview.original_content == original
    assert path.read_bytes() == original.encode()


def test_missing_final_newline_is_visible(tmp_path):
    """无末尾换行的临时文件应有独立差异行和明确标记，避免旧新文本粘连。"""
    (tmp_path / "text").write_text("old", encoding="utf-8")
    preview = preview_edit(
        tmp_path,
        "text",
        "old",
        "new",
    )
    assert "-old\n\\ No newline at end of file\n+new\n" in preview.diff
    assert preview.diff.count("\\ No newline at end of file") == 2


@pytest.mark.parametrize(
    "original,old,new,reason",
    [
        ("abc", "x", "y", "未找到"),
        ("abc abc", "abc", "x", "匹配多处"),
        ("aaa", "aa", "b", "匹配多处"),
        ("abc", "", "x", "不能为空"),
        ("abc", "abc", "abc", "没有产生修改"),
        ("", "x", "y", "未找到"),
        ("ABC", "abc", "x", "未找到"),
    ],
)
def test_ambiguous_missing_or_noop_requests_fail_without_writing(
    tmp_path,
    original,
    old,
    new,
    reason,
):
    """临时文件验证匹配与无变化边界；失败不能泄露正文或改变文件。"""
    path = tmp_path / "text"
    path.write_bytes(original.encode())
    with pytest.raises(EditPreviewError, match=reason):
        preview_edit(
            tmp_path,
            "text",
            old,
            new,
        )
    assert path.read_bytes() == original.encode()


@pytest.mark.parametrize("value", [None, 123, True, [], "x\x00y", "\ud800", "中" * 22000])
@pytest.mark.parametrize("field", ["old_text", "new_text"])
def test_invalid_text_never_reads(
    tmp_path,
    monkeypatch,
    value,
    field,
):
    """通过 monkeypatch 禁止读取，验证两种文本字段的无效参数会先被拒绝。"""
    def forbidden(
        *args,
        **kwargs,
    ):
        """输入任意调用参数；若发生读取则让测试失败，无正常返回值。"""
        pytest.fail("无效文本不应读取文件")

    monkeypatch.setattr(
        edit,
        "read_file",
        forbidden,
    )
    arguments = {"old_text": "old", "new_text": "new", field: value}
    with pytest.raises(EditPreviewError, match=field):
        preview_edit(
            tmp_path,
            "text",
            **arguments,
        )


@pytest.mark.parametrize("relative", ["../outside", ".env", "missing", ".", "", "/"])
def test_path_and_read_errors_are_wrapped(
    tmp_path,
    relative,
):
    """临时项目检查越界、敏感、缺失及非法路径，统一返回预览错误并保持文件不变。"""
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "outside").write_text("private-content")
    (root / ".env").write_text("private-content")
    with pytest.raises(EditPreviewError) as error:
        preview_edit(
            root,
            relative,
            "private",
            "public",
        )
    assert "private-content" not in str(error.value)
    assert (root / ".env").read_text() == "private-content"
    assert (tmp_path / "outside").read_text() == "private-content"


@pytest.mark.parametrize("target", ["outside", ".env"])
def test_unsafe_symlink_is_rejected(
    tmp_path,
    target,
):
    """临时别名指向项目外或敏感文件时应被受限读取拒绝，目标内容保持不变。"""
    root = tmp_path / "project"
    root.mkdir()
    destination = tmp_path / "outside" if target == "outside" else root / ".env"
    destination.write_text("old")
    (root / "alias").symlink_to(destination)
    with pytest.raises(EditPreviewError):
        preview_edit(
            root,
            "alias",
            "old",
            "new",
        )
    assert destination.read_text() == "old"


def test_internal_symlink_preview_does_not_write_target(tmp_path):
    """项目内链接沿用读取规则可生成预览，但链接和目标均不得改变。"""
    target = tmp_path / "text"
    target.write_text("old")
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    preview = preview_edit(
        tmp_path,
        "alias",
        "old",
        "new",
    )
    assert preview.relative_path == "alias" and preview.updated_content == "new"
    assert alias.is_symlink() and target.read_text() == "old"


@pytest.mark.parametrize("content", [b"\xff", b"old\x00", b"a" * (MAX_READ_BYTES + 1)])
def test_invalid_source_is_rejected(
    tmp_path,
    content,
):
    """临时文件的无效 UTF-8、空字节及超大原文必须拒绝且不改变字节。"""
    path = tmp_path / "text"
    path.write_bytes(content)
    with pytest.raises(EditPreviewError):
        preview_edit(
            tmp_path,
            "text",
            "a",
            "b",
        )
    assert path.read_bytes() == content


def test_updated_size_counts_utf8_bytes_and_accepts_exact_limit(tmp_path):
    """临时文件替换后恰好 64 KiB 可预览，多一个中文字符超限时拒绝且原文件不变。"""
    path = tmp_path / "text"
    original = "a" * (MAX_READ_BYTES - 4) + "\n中"
    path.write_bytes(original.encode())
    preview = preview_edit(
        tmp_path,
        "text",
        "中",
        "文",
    )
    assert len(preview.updated_content.encode()) == MAX_READ_BYTES
    with pytest.raises(EditPreviewError, match="修改后文件"):
        preview_edit(
            tmp_path,
            "text",
            "中",
            "文字",
        )
    assert path.read_bytes() == original.encode()


def test_diff_limit_is_explicit_and_never_truncates(
    tmp_path,
    monkeypatch,
):
    """通过 monkeypatch 缩小差异预算，超限必须报错而非交付不完整预览，文件不变。"""
    (tmp_path / "text").write_text("old")
    monkeypatch.setattr(
        edit,
        "MAX_DIFF_BYTES",
        10,
    )
    with pytest.raises(EditPreviewError, match="预览上限"):
        preview_edit(
            tmp_path,
            "text",
            "old",
            "new",
        )
    assert (tmp_path / "text").read_text() == "old"


def test_filename_newlines_are_escaped_in_diff_headers(tmp_path):
    """临时文件名含换行时，差异头必须转义，不能伪装成额外标题。"""
    name = "file\nname"
    (tmp_path / name).write_text("old")
    preview = preview_edit(
        tmp_path,
        name,
        "old",
        "new",
    )
    assert preview.diff.splitlines()[:2] == ['--- "a/file\\nname"', '+++ "b/file\\nname"']


def test_only_guarded_edit_is_exposed_as_model_tool():
    """无需 fixture；模型只获得受确认保护的 edit_file，不能直接调用预览或 apply_edit。"""
    names = {item["function"]["name"] for item in tool_definitions()}
    assert names == {"read_file", "search_project", "edit_file"}


def test_default_diff_budget_can_reject_a_valid_large_single_line(tmp_path):
    """合法大小的超长单行会让差异同时容纳旧新两行，超过默认预算时必须拒绝。"""
    path = tmp_path / "text"
    original = "a" * (MAX_READ_BYTES - 1) + "x"
    path.write_text(original)
    with pytest.raises(EditPreviewError, match="预览上限"):
        preview_edit(
            tmp_path,
            "text",
            "x",
            "y",
        )
    assert path.read_text() == original


def test_preview_is_a_snapshot_not_a_future_conflict_check(tmp_path):
    """临时文件在预览后被外部修改时，旧快照不变，预览不能被视为未来写入许可。"""
    path = tmp_path / "text"
    path.write_text("old")
    preview = preview_edit(
        tmp_path,
        "text",
        "old",
        "new",
    )
    path.write_text("external change")
    assert preview.original_content == "old" and preview.updated_content == "new"
    assert path.read_text() == "external change"


def test_read_permission_error_is_sanitized(
    tmp_path,
    monkeypatch,
):
    """模拟底层读取失败；预览只回传可展示错误，不泄露系统异常详情。"""
    import os

    path = tmp_path / "text"
    path.write_text("old")

    def denied(
        *args,
        **kwargs,
    ):
        """接受任意打开参数并抛出模拟权限错误，无正常返回值。"""
        raise PermissionError("private-system-details")

    with monkeypatch.context() as patch:
        patch.setattr(
            os,
            "open",
            denied,
        )
        with pytest.raises(EditPreviewError, match="权限") as error:
            preview_edit(
                tmp_path,
                "text",
                "old",
                "new",
            )
    assert "private-system-details" not in str(error.value)
    assert path.read_text() == "old"


def test_file_changed_during_preview_is_rejected(
    tmp_path,
    monkeypatch,
):
    """读取替身模拟生成快照期间的外部保存，预览必须失败并保留外部修改。"""
    path = tmp_path / "text"
    path.write_text("old")
    original_read = edit.read_file

    def read_then_change(
        root,
        relative,
    ):
        """输入读取路径，返回旧内容前模拟外部保存，供状态检查发现变化。"""
        content = original_read(root, relative)
        path.write_text("external")
        return content

    monkeypatch.setattr(
        edit,
        "read_file",
        read_then_change,
    )
    with pytest.raises(EditPreviewError, match="生成预览时发生变化"):
        preview_edit(
            tmp_path,
            "text",
            "old",
            "new",
        )
    assert path.read_text() == "external"
