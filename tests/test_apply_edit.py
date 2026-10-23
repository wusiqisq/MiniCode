"""使用临时项目与受控确认函数验证写入，不接入模型或网络。"""

from dataclasses import replace
import os
import stat

import pytest

from minicode.tools import edit
from minicode.tools.edit import EditApplyError, EditConflictError, apply_edit, preview_edit


@pytest.fixture
def prepared(tmp_path):
    """输入临时目录 fixture；创建带 CRLF 的文件，返回根目录、路径和预览供每项测试独立使用。"""
    path = tmp_path / "main.py"
    path.write_bytes(b"value = 1\r\n")
    preview = preview_edit(
        tmp_path,
        "main.py",
        "value = 1",
        "value = 2",
    )
    return tmp_path, path, preview


def test_approved_write_preserves_bytes_and_mode(prepared):
    """临时文件确认后应写入新文本、保留 CRLF 和执行位，且不残留临时文件。"""
    root, path, _ = prepared
    path.chmod(0o750)
    preview = preview_edit(
        root,
        "main.py",
        "1",
        "2",
    )
    seen = []

    def approve(candidate):
        """输入待确认预览，记录其内容；确认前原文件必须未变，返回 True。"""
        assert path.read_bytes() == b"value = 1\r\n"
        seen.append(candidate)
        return True

    result = apply_edit(
        root,
        preview,
        confirm=approve,
    )
    assert result.status == "applied" and seen == [preview]
    assert path.read_bytes() == b"value = 2\r\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o750
    assert sorted(p.name for p in root.iterdir()) == ["main.py"]


@pytest.mark.parametrize("answer", [False, None, "yes", 1])
def test_rejection_or_non_boolean_confirmation_creates_nothing(
    prepared,
    monkeypatch,
    answer,
):
    """用临时预览和禁止创建临时文件的替身，验证严格 True 之外均拒绝且原文件未变。"""
    root, path, preview = prepared

    def forbidden(
        *args,
        **kwargs,
    ):
        """输入任意创建参数；未获确认时调用此函数即使测试失败，无正常输出。"""
        pytest.fail("拒绝后不应创建临时文件")

    monkeypatch.setattr(
        edit.tempfile,
        "mkstemp",
        forbidden,
    )
    result = apply_edit(
        root,
        preview,
        confirm=lambda candidate: answer,
    )
    assert result.status == "denied" and path.read_bytes() == b"value = 1\r\n"
    assert len(list(root.iterdir())) == 1


def test_denial_is_not_cached_and_each_call_confirms_again(prepared):
    """同一临时预览先拒绝再单独批准，两次均调用应用确认，不绕过首次拒绝。"""
    root, path, preview = prepared
    calls = []

    def decide(candidate):
        """输入预览并记录本次请求；第一次返回 False，第二次返回 True。"""
        calls.append(candidate)
        return len(calls) == 2

    assert apply_edit(
        root,
        preview,
        confirm=decide,
    ).status == "denied"
    assert path.read_bytes() == b"value = 1\r\n"
    assert apply_edit(
        root,
        preview,
        confirm=decide,
    ).status == "applied"
    assert len(calls) == 2


def test_existing_conflict_is_rejected_before_confirmation(prepared):
    """预览后外部保存临时文件时应先报冲突，不询问过期差异或覆盖新内容。"""
    root, path, preview = prepared
    path.write_text("external")

    def forbidden(candidate):
        """输入过期预览时使测试失败，无正常返回值。"""
        pytest.fail("已过期预览不应进入确认")

    with pytest.raises(EditConflictError):
        apply_edit(
            root,
            preview,
            confirm=forbidden,
        )
    assert path.read_text() == "external"


def test_change_while_waiting_for_approval_is_not_overwritten(prepared):
    """确认函数模拟等待期间的外部保存，确认后的检查必须保留外部内容。"""
    root, path, preview = prepared

    def approve_after_external_save(candidate):
        """输入预览，模拟外部保存后返回 True，供确认后的冲突检查拒绝。"""
        path.write_text("external")
        return True

    with pytest.raises(EditConflictError):
        apply_edit(
            root,
            preview,
            confirm=approve_after_external_save,
        )
    assert path.read_text() == "external" and len(list(root.iterdir())) == 1


def test_same_content_replacement_is_detected_by_file_identity(prepared):
    """使用同字节文件替换临时目标，内容相同也应因 inode 改变而拒绝旧预览。"""
    root, path, preview = prepared
    replacement = root / "replacement"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(EditConflictError):
        apply_edit(
            root,
            preview,
            confirm=lambda candidate: True,
        )
    assert path.read_bytes() == b"value = 1\r\n"


def test_content_is_compared_even_if_metadata_looks_unchanged(
    prepared,
    monkeypatch,
):
    """模拟文件状态信息未变，原文字节检查仍应拒绝已修改目标，不能只依赖时间戳。"""
    root, path, preview = prepared
    path.write_bytes(b"value = 9\r\n")
    monkeypatch.setattr(
        edit,
        "_file_signature",
        lambda metadata: preview.file_signature,
    )
    with pytest.raises(EditConflictError, match="内容已变化"):
        apply_edit(
            root,
            preview,
            confirm=lambda candidate: True,
        )
    assert path.read_bytes() == b"value = 9\r\n"


@pytest.mark.parametrize("change", ["delete", "directory", "mode"])
def test_missing_or_changed_target_is_not_recreated(
    prepared,
    change,
):
    """临时文件删除、变目录或权限变化后均拒绝，不能用旧预览重新创建或覆盖。"""
    root, path, preview = prepared
    if change == "mode":
        path.chmod(0o400)
    else:
        path.unlink()
        if change == "directory":
            path.mkdir()
    with pytest.raises(EditApplyError):
        apply_edit(
            root,
            preview,
            confirm=lambda candidate: True,
        )
    if change == "delete":
        assert not path.exists()
    elif change == "directory":
        assert path.is_dir() and list(path.iterdir()) == []
    else:
        assert path.read_bytes() == b"value = 1\r\n"


def test_preview_cannot_be_applied_to_different_project(
    prepared,
    tmp_path,
):
    """另一项目即便路径和文本相同，也必须拒绝原项目的快照。"""
    root, path, preview = prepared
    other = tmp_path / "other"
    other.mkdir()
    (other / "main.py").write_bytes(path.read_bytes())
    with pytest.raises(EditConflictError):
        apply_edit(
            other,
            preview,
            confirm=lambda candidate: True,
        )
    assert path.read_bytes() == (other / "main.py").read_bytes() == b"value = 1\r\n"


@pytest.mark.parametrize("tamper", ["diff", "updated_content", "relative_path"])
def test_altered_preview_cannot_hide_what_will_be_written(
    prepared,
    tamper,
):
    """用 dataclasses.replace 改动展示、正文或路径，写入校验必须拒绝。"""
    root, path, preview = prepared
    forged = replace(preview, **{tamper: "forged"})
    with pytest.raises(EditApplyError):
        apply_edit(
            root,
            forged,
            confirm=lambda candidate: True,
        )
    assert path.read_bytes() == b"value = 1\r\n"


def test_symlink_target_is_readable_but_not_writable(tmp_path):
    """内部符号链接可预览，写入阶段必须拒绝而不是替换链接或改写其目标。"""
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
    with pytest.raises(EditApplyError, match="符号链接"):
        apply_edit(
            tmp_path,
            preview,
            confirm=lambda candidate: True,
        )
    assert alias.is_symlink() and target.read_text() == "old"


def test_symlink_parent_is_rejected_for_writing(tmp_path):
    """路径父目录是项目内链接时，预览可读但不能经该路径写入。"""
    (tmp_path / "src").mkdir()
    path = tmp_path / "src/text"
    path.write_text("old")
    (tmp_path / "alias").symlink_to(tmp_path / "src", target_is_directory=True)
    preview = preview_edit(
        tmp_path,
        "alias/text",
        "old",
        "new",
    )
    with pytest.raises(EditApplyError, match="符号链接"):
        apply_edit(
            tmp_path,
            preview,
            confirm=lambda candidate: True,
        )
    assert path.read_text() == "old"


def test_hardlinked_file_is_rejected(tmp_path):
    """目标拥有多个硬链接时拒绝写入，避免破坏链接语义。"""
    path = tmp_path / "text"
    path.write_text("old")
    os.link(path, tmp_path / "alias")
    preview = preview_edit(
        tmp_path,
        "text",
        "old",
        "new",
    )
    with pytest.raises(EditApplyError, match="硬链接"):
        apply_edit(
            tmp_path,
            preview,
            confirm=lambda candidate: True,
        )
    assert path.read_text() == (tmp_path / "alias").read_text() == "old"


def test_state_change_during_staging_cleans_up_and_preserves_external_edit(
    prepared,
    monkeypatch,
):
    """模拟临时文件准备期间外部保存，最后一次检查拒绝替换并清理临时文件。"""
    root, path, preview = prepared
    original_fsync = edit.os.fsync

    def fsync_then_external_save(descriptor):
        """输入临时文件描述符，同步后模拟外部保存，返回 None。"""
        original_fsync(descriptor)
        path.write_text("external")

    monkeypatch.setattr(
        edit.os,
        "fsync",
        fsync_then_external_save,
    )
    with pytest.raises(EditConflictError):
        apply_edit(
            root,
            preview,
            confirm=lambda candidate: True,
        )
    assert path.read_text() == "external" and len(list(root.iterdir())) == 1


@pytest.mark.parametrize("operation", ["mkstemp", "fsync", "replace"])
def test_write_failures_keep_original_and_cleanup(
    prepared,
    monkeypatch,
    operation,
):
    """模拟创建、同步或替换失败，原文件完整、临时文件清理，底层错误详情不能泄露。"""
    root, path, preview = prepared

    def fail(
        *args,
        **kwargs,
    ):
        """接受任意系统调用参数，抛出带敏感详情的模拟存储错误，无正常输出。"""
        raise OSError("private-system-error")

    owner = edit.tempfile if operation == "mkstemp" else edit.os
    monkeypatch.setattr(
        owner,
        operation,
        fail,
    )
    with pytest.raises(EditApplyError) as error:
        apply_edit(
            root,
            preview,
            confirm=lambda candidate: True,
        )
    assert "private-system-error" not in str(error.value)
    assert path.read_bytes() == b"value = 1\r\n" and len(list(root.iterdir())) == 1


def test_fdopen_failure_closes_descriptor_and_removes_temporary_file(
    prepared,
    monkeypatch,
):
    """模拟文件对象创建失败，验证底层描述符关闭、临时文件清理且原文件未变。"""
    root, path, preview = prepared
    original_fdopen = edit.os.fdopen
    failed = []

    def fail_only_for_write(
        descriptor,
        mode,
    ):
        """读取沿用真实 fdopen；记录并拒绝写入模式，便于断言描述符已关闭。"""
        if mode == "wb":
            failed.append(descriptor)
            raise OSError("cannot attach stream")
        return original_fdopen(descriptor, mode)

    monkeypatch.setattr(
        edit.os,
        "fdopen",
        fail_only_for_write,
    )
    with pytest.raises(EditApplyError):
        apply_edit(
            root,
            preview,
            confirm=lambda candidate: True,
        )
    assert len(failed) == 1
    with pytest.raises(OSError):
        os.fstat(failed[0])
    assert path.read_bytes() == b"value = 1\r\n" and len(list(root.iterdir())) == 1


@pytest.mark.parametrize("phase", ["confirm", "staging"])
def test_interrupt_propagates_without_replacing_original(
    prepared,
    monkeypatch,
    phase,
):
    """在确认或临时文件准备阶段模拟 Ctrl+C，必须保留原文件并清理临时文件。"""
    root, path, preview = prepared

    def interrupt(
        *args,
        **kwargs,
    ):
        """输入任意确认或系统调用参数，模拟用户中断，无正常返回值。"""
        raise KeyboardInterrupt

    confirmation = interrupt if phase == "confirm" else lambda candidate: True
    if phase == "staging":
        monkeypatch.setattr(
            edit.os,
            "fsync",
            interrupt,
        )
    with pytest.raises(KeyboardInterrupt):
        apply_edit(
            root,
            preview,
            confirm=confirmation,
        )
    assert path.read_bytes() == b"value = 1\r\n" and len(list(root.iterdir())) == 1


def test_second_apply_of_successful_preview_requires_new_preview(prepared):
    """成功写入后的旧预览应过期，重复调用不能复用原确认再次写入。"""
    root, path, preview = prepared
    assert apply_edit(
        root,
        preview,
        confirm=lambda candidate: True,
    ).status == "applied"
    with pytest.raises(EditConflictError):
        apply_edit(
            root,
            preview,
            confirm=lambda candidate: True,
        )
    assert path.read_bytes() == b"value = 2\r\n"


def test_empty_replacement_keeps_an_empty_file(tmp_path):
    """批准删除全部文本后应保留空的普通文件，不能将文本删除变成文件删除。"""
    path = tmp_path / "text"
    path.write_text("old")
    preview = preview_edit(
        tmp_path,
        "text",
        "old",
        "",
    )
    assert apply_edit(
        tmp_path,
        preview,
        confirm=lambda candidate: True,
    ).status == "applied"
    assert path.is_file() and path.read_bytes() == b""


def test_readonly_file_is_rejected_even_when_directory_allows_replacement(tmp_path):
    """预览一个已只读的文件后仍不得借助目录写权限绕过文件本身的只读标记。"""
    path = tmp_path / "text"
    path.write_text("old")
    path.chmod(0o400)
    preview = preview_edit(
        tmp_path,
        "text",
        "old",
        "new",
    )
    with pytest.raises(EditApplyError, match="不可写"):
        apply_edit(
            tmp_path,
            preview,
            confirm=lambda candidate: True,
        )
    assert path.read_text() == "old"


def test_cleanup_failure_is_reported_without_overwriting_original(
    prepared,
    monkeypatch,
):
    """模拟同步失败后又无法清理临时文件，错误应明确提示残留且保留原文件。"""
    from pathlib import Path

    root, path, preview = prepared
    real_unlink = Path.unlink

    def fail_sync(descriptor):
        """输入临时描述符，抛出同步错误以触发清理，无正常返回值。"""
        raise OSError("sync failed")

    def fail_temporary_unlink(
        candidate,
        *args,
        **kwargs,
    ):
        """输入待删除路径，拒绝临时文件清理，其余路径交给真实 unlink。"""
        if candidate.name.startswith(".minicode-edit-"):
            raise PermissionError("cannot clean up")
        return real_unlink(
            candidate,
            *args,
            **kwargs,
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            edit.os,
            "fsync",
            fail_sync,
        )
        patch.setattr(
            Path,
            "unlink",
            fail_temporary_unlink,
        )
        with pytest.raises(EditApplyError, match="临时文件清理失败"):
            apply_edit(
                root,
                preview,
                confirm=lambda candidate: True,
            )
    assert path.read_bytes() == b"value = 1\r\n"
    leftovers = list(root.glob(".minicode-edit-*"))
    assert len(leftovers) == 1
    leftovers[0].unlink()
