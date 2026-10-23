"""上下文字节计量与按整轮选择，不调用模型。"""

from copy import deepcopy
import json

import pytest

from minicode.context import ContextLimitError, context_bytes, select_context
from minicode.tools.registry import tool_definitions


def test_counts_utf8_json_tools_and_escaping():
    messages = [{"role": "user", "content": '中文😀"\n\\'}]
    expected = json.dumps(
        {"messages": messages, "tools": tool_definitions()},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert context_bytes(messages) == len(expected.encode("utf-8"))
    assert context_bytes(messages) > len(expected)
    assert context_bytes(messages) > len(json.dumps(messages).encode())


def test_exact_boundary_and_one_byte_over():
    current = [{"role": "user", "content": "问题"}]
    expected = [{"role": "system", "content": "规则"}] + current
    size = context_bytes(expected)
    messages, dropped = select_context(
        "规则",
        [],
        current,
        size,
    )
    assert messages == expected and dropped == 0
    with pytest.raises(ContextLimitError, match="未发送本次请求"):
        select_context(
            "规则",
            [],
            current,
            size - 1,
        )


def test_drops_oldest_complete_turn_and_counts_notice():
    history = [
        [{"role": "user", "content": "旧" * 1000}, {"role": "assistant", "content": "旧回答"}],
        [{"role": "user", "content": "新"}, {"role": "assistant", "content": "新回答"}],
    ]
    current = [{"role": "user", "content": "当前"}]
    before = deepcopy((history, current))
    expected, _ = select_context(
        "规则",
        history[1:],
        current,
        history_omitted=True,
    )
    messages, dropped = select_context(
        "规则",
        history,
        current,
        context_bytes(expected),
    )
    assert dropped == 1 and messages == expected
    assert "省略" in messages[0]["content"]
    assert context_bytes(messages) == context_bytes(expected)
    assert (history, current) == before


def test_keeps_contiguous_recent_suffix_even_if_older_small_turn_would_fit():
    history = [
        [{"role": "user", "content": "小"}, {"role": "assistant", "content": "小"}],
        [{"role": "user", "content": "大" * 1000}, {"role": "assistant", "content": "大"}],
    ]
    current = [{"role": "user", "content": "当前"}]
    expected, _ = select_context(
        "规则",
        [],
        current,
        history_omitted=True,
    )
    messages, dropped = select_context(
        "规则",
        history,
        current,
        context_bytes(expected),
    )
    assert dropped == 2 and messages == expected


def test_never_removes_or_truncates_current_tool_messages():
    current = [
        {"role": "user", "content": "读文件"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "one"}, {"id": "two"}]},
        {"role": "tool", "tool_call_id": "one", "content": "内容" * 1000},
        {"role": "tool", "tool_call_id": "two", "content": "完整内容"},
    ]
    original = deepcopy(current)
    expected, _ = select_context(
        "规则",
        [],
        current,
    )
    messages, dropped = select_context(
        "规则",
        [],
        current,
        context_bytes(expected),
    )
    assert messages[1:] == original and dropped == 0
    with pytest.raises(ContextLimitError):
        select_context(
            "规则",
            [],
            current,
            context_bytes(expected) - 1,
        )
    assert current == original


def test_system_and_tools_alone_can_exceed_budget():
    with pytest.raises(ContextLimitError):
        select_context(
            "规则",
            [],
            [],
            1,
        )


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5, None])
def test_invalid_limit(limit):
    with pytest.raises(ValueError, match="正整数"):
        select_context(
            "规则",
            [],
            [],
            limit,
        )
