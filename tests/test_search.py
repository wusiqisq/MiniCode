"""临时目录内验证搜索，无网络或真实密钥。"""

import json
import os

import pytest

from minicode.tools import search
from minicode.tools.files import MAX_READ_BYTES
from minicode.tools.registry import dispatch_tool, tool_definitions
from minicode.tools.search import SearchError, search_project


def test_literal_chinese_lines_and_scope(tmp_path):
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "a.py").write_text("中文.*中文\n中文\nABC\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("中文")
    result = search_project(
        tmp_path,
        "中文",
        "src",
    )
    assert result["matches"] == [
        {"path": "src/a.py", "line": 1, "text": "中文.*中文", "text_truncated": False},
        {"path": "src/a.py", "line": 2, "text": "中文", "text_truncated": False},
    ]
    assert result["scanned_files"] == 1
    assert result["truncated"] is False and result["limit"] is None
    assert len(search_project(
        tmp_path,
        ".*",
        "src",
    )["matches"]) == 1
    assert search_project(
        tmp_path,
        "abc",
        "src",
    )["matches"] == []


@pytest.mark.parametrize(
    "query",
    [None, 1, True, [], "", "  ", "a\nb", "a\rb", "a\x00b", "中" * 201],
)
def test_invalid_query(
    tmp_path,
    query,
):
    with pytest.raises(SearchError, match="query"):
        search_project(tmp_path, query)


@pytest.mark.parametrize(
    "path",
    [None, 1, "", " ", "/", "../", ".env", "missing", "a.txt", "x\x00y", "node_modules"],
)
def test_invalid_scope(
    tmp_path,
    path,
):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".env").mkdir()
    (tmp_path / "node_modules").mkdir()
    with pytest.raises(SearchError):
        search_project(
            tmp_path,
            "x",
            path,
        )


def test_invalid_roots(tmp_path):
    file = tmp_path / "a"
    file.write_text("x")
    for root in (file, tmp_path / "missing"):
        with pytest.raises(SearchError):
            search_project(root, "x")


def test_exclusions_symlinks_and_unreadable_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for name in (".env", ".ENV.local", "key.pem", "credentials.json"):
        (root / name).write_text("needle")
    for name in (".git", ".venv", "node_modules", "__pycache__", "secrets"):
        (root / name).mkdir()
        (root / name / "hidden.txt").write_text("needle")
    (root / "a.txt").write_text("needle")
    (root / "alias").symlink_to(root / ".env")
    (root / "internal").symlink_to(root / "a.txt")
    (root / "loop").symlink_to(root, target_is_directory=True)
    (root / "broken").symlink_to(root / "missing")
    (tmp_path / "outside").write_text("needle")
    (root / "outside").symlink_to(tmp_path / "outside")
    (root / "binary").write_bytes(b"needle\x00")
    (root / "bad_utf8").write_bytes(b"needle\xff")
    (root / "huge").write_bytes(b"n" * (MAX_READ_BYTES + 1))
    os.mkfifo(root / "fifo")
    result = search_project(root, "needle")
    assert [r["path"] for r in result["matches"]] == ["a.txt"]
    assert result["skipped_entries"] == 18
    assert result["truncated"] is False
    # 指定范围也不能绕过真实路径或敏感目录校验。
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    (root / "secret_alias").symlink_to(root / "secrets", target_is_directory=True)
    for path in ("escape", "secret_alias"):
        with pytest.raises(SearchError):
            search_project(
                root,
                "needle",
                path,
            )


def test_long_line_preview_contains_match(tmp_path):
    (tmp_path / "a").write_text("前" * 1000 + "needle" + "后" * 1000)
    row = search_project(tmp_path, "needle")["matches"][0]
    assert "needle" in row["text"] and len(row["text"]) == 300
    assert row["text_truncated"] is True


@pytest.mark.parametrize(
    "constant,limit",
    [("MAX_ENTRIES", "entries"), ("MAX_FILES", "files"), ("MAX_MATCHES", "matches")],
)
def test_limits_are_explicit(
    tmp_path,
    monkeypatch,
    constant,
    limit,
):
    for i in range(3):
        (tmp_path / str(i)).write_text("x\nx\nx")
    monkeypatch.setattr(
        search,
        constant,
        1,
    )
    result = search_project(tmp_path, "x")
    assert result["truncated"] is True and result["limit"] == limit
    if constant == "MAX_MATCHES":
        assert len(result["matches"]) == 1
    else:
        assert result["scanned_files"] == 1


def test_output_limit_uses_encoded_json_bytes(tmp_path):
    (tmp_path / "a").write_text(("中" * 300 + "\n") * 50)
    result = search_project(tmp_path, "中")
    assert result["truncated"] is True and result["limit"] == "output_bytes"
    assert len(json.dumps(
        result,
        ensure_ascii=False,
    ).encode()) <= search.MAX_OUTPUT_BYTES
    assert 0 < len(result["matches"]) < 50


def test_empty_and_exact_limit_are_complete(
    tmp_path,
    monkeypatch,
):
    assert search_project(tmp_path, "x")["matches"] == []
    (tmp_path / "a").write_text("x")
    monkeypatch.setattr(
        search,
        "MAX_MATCHES",
        1,
    )
    monkeypatch.setattr(
        search,
        "MAX_FILES",
        1,
    )
    monkeypatch.setattr(
        search,
        "MAX_ENTRIES",
        1,
    )
    assert search_project(tmp_path, "x")["truncated"] is False


def test_read_and_directory_errors_are_counted(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "a").write_text("x")
    def denied(*args):
        raise search.FileReadError("无法读取")
    monkeypatch.setattr(
        search,
        "read_file",
        denied,
    )
    result = search_project(tmp_path, "x")
    assert result["matches"] == [] and result["skipped_entries"] == 1
    def no_scan(*args):
        raise PermissionError()
    monkeypatch.setattr(
        search.os,
        "scandir",
        no_scan,
    )
    result = search_project(tmp_path, "x")
    assert result["matches"] == [] and result["skipped_entries"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        '{}', '{"query":null}', '{"query":true}', '{"query":""}',
        '{"query":"x","relative_path":1}', '{"query":"x","project_root":"/"}',
        '{"query":"x","query":"y"}', '[]', 'bad-json',
    ],
)
def test_dispatch_rejects_invalid_search(
    tmp_path,
    arguments,
):
    result = dispatch_tool(
        tmp_path,
        "s",
        "search_project",
        arguments,
    )
    assert result.status == "error" and result.tool_call_id == "s"


def test_dispatch_and_schema(tmp_path):
    (tmp_path / "a").write_text("needle")
    result = dispatch_tool(
        tmp_path,
        "s",
        "search_project",
        '{"query":"needle"}',
    )
    assert result.status == "success"
    assert json.loads(result.content)["matches"][0]["path"] == "a"
    definitions = {d["function"]["name"]: d["function"] for d in tool_definitions()}
    assert definitions["search_project"]["parameters"]["required"] == ["query"]
    assert definitions["search_project"]["parameters"]["additionalProperties"] is False
