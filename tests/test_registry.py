"""使用临时项目和模拟工具请求，不访问模型。"""

import json

import pytest

from minicode.tools import registry
from minicode.tools.registry import dispatch_tool


@pytest.mark.parametrize("content", ["", "# 中文\r\nprint('hi')\n"])
def test_dispatch_preserves_content_and_id(
    tmp_path,
    content,
):
    (tmp_path / "main.py").write_bytes(content.encode())
    result = dispatch_tool(
        tmp_path,
        "call_1",
        "read_file",
        '{"relative_path":"main.py"}',
    )
    assert (result.tool_call_id, result.status, result.content) == ("call_1", "success", content)


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("delete_file", '{}'), ("__import__", '{}'), (None, '{}'), ([], '{}'),
        ("read_file", None), ("read_file", {}), ("read_file", 'not-json private-data'),
        ("read_file", '[]'), ("read_file", 'null'), ("read_file", '"text"'),
        ("read_file", '{}'), ("read_file", '{"relative_path":null}'),
        ("read_file", '{"relative_path":123}'), ("read_file", '{"relative_path":true}'),
        ("read_file", '{"relative_path":[]}'), ("read_file", '{"relative_path":" "}'),
        ("read_file", '{"relative_path":"main.py","project_root":"/"}'),
        ("read_file", '{"relative_path":"main.py","extra":1}'),
        ("read_file", '{"relative_path":"main.py","relative_path":"other.py"}'),
        ("read_file", '[' * 2000 + ']' * 2000),
    ],
)
def test_invalid_requests_never_read(
    tmp_path,
    monkeypatch,
    name,
    arguments,
):
    def forbidden(*args):
        pytest.fail("无效请求不应调用文件读取函数")

    monkeypatch.setattr(
        registry,
        "read_file",
        forbidden,
    )
    result = dispatch_tool(
        tmp_path,
        "call_bad",
        name,
        arguments,
    )
    assert result.tool_call_id == "call_bad"
    assert result.status == "error"
    assert "private-data" not in result.content


@pytest.mark.parametrize("path", ["missing.py", ".env", "../outside.py"])
def test_read_failure_is_returned_with_id(
    tmp_path,
    path,
):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text("private-content")
    (tmp_path / "outside.py").write_text("private-content")
    result = dispatch_tool(
        root,
        "call_error",
        "read_file",
        json.dumps({"relative_path": path}),
    )
    assert result.tool_call_id == "call_error"
    assert result.status == "error"
    assert "private-content" not in result.content


@pytest.mark.parametrize("call_id", [None, "", "  ", 123])
def test_invalid_id_is_protocol_error(
    tmp_path,
    call_id,
):
    with pytest.raises(ValueError, match="请求标识"):
        dispatch_tool(
            tmp_path,
            call_id,
            "read_file",
            '{}',
        )


def test_failed_request_does_not_prevent_next_read(tmp_path):
    (tmp_path / "file.txt").write_text("ok")
    failed = dispatch_tool(
        tmp_path,
        "first",
        "unknown",
        '{}',
    )
    success = dispatch_tool(
        tmp_path,
        "second",
        "read_file",
        '{"relative_path":"file.txt"}',
    )
    assert failed.status == "error"
    assert success.status == "success"
    assert success.tool_call_id == "second"
    assert success.content == "ok"
