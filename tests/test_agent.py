"""通过模拟 HTTP 与真实受限读取验证循环，不访问真实模型。"""

from copy import deepcopy
import json

import httpx
import pytest

from minicode.agent import SYSTEM_PROMPT, run_agent
from minicode.config import Config
from minicode.models import ToolCall
from minicode.session import Session
from minicode.tools import registry


def read_call(
    call_id="one",
    path="a.txt",
    name="read_file",
):
    """构造模拟读取请求：输入编号、路径和工具名，返回协议字典，不执行读取。"""
    return ToolCall(
        call_id,
        name,
        json.dumps({"relative_path": path}),
    ).as_dict()


def completion(
    calls=None,
    text="完成",
):
    """构造模拟模型响应：输入工具请求或文本，返回带结束标志的响应字典。"""
    return {"choices": [{
        "message": {"content": None if calls else text, "tool_calls": calls},
        "finish_reason": "tool_calls" if calls else "stop",
    }]}


def execute(
    tmp_path,
    bodies,
    *,
    session=None,
    **limits,
):
    """运行离线循环：输入临时目录、模拟响应和上限，返回结果、会话与捕获的请求列表。"""
    payloads = []
    responses = iter(bodies)

    def handler(request):
        payloads.append(json.loads(request.content))
        body = next(responses)
        if isinstance(body, BaseException):
            raise body
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, json=body)

    session = session if session is not None else Session()
    result = run_agent(
        "查看项目",
        session,
        Config(api_key="test-key"),
        tmp_path,
        transport=httpx.MockTransport(handler),
        **limits,
    )
    return result, session, payloads


def test_multiple_steps_and_full_history(tmp_path):
    (tmp_path / "a.txt").write_text("你好")
    (tmp_path / "b.txt").write_text("")
    calls = [read_call(), read_call("two", "b.txt")]
    result, session, payloads = execute(
        tmp_path,
        [
            completion(calls), completion([read_call("three")]), completion(),
        ],
    )
    assert (result.status, result.message, result.model_calls, result.tool_calls) == ("completed", "完成", 3, 3)
    assert all(p["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT} for p in payloads)
    assert all(p["tool_choice"] == "auto" for p in payloads)
    messages = payloads[2]["messages"][1:]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "tool", "assistant", "tool"]
    assert messages[1]["tool_calls"] == calls
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == ["one", "two", "three"]
    assert json.loads(messages[2]["content"]) == {"status": "success", "content": "你好"}
    assert json.loads(messages[3]["content"]) == {"status": "success", "content": ""}
    assert session.messages_for("下一题") == messages + [
        {"role": "assistant", "content": "完成"}, {"role": "user", "content": "下一题"},
    ]
    # 下一轮真正的 HTTP 请求也必须包含整轮工具历史，不能重复保存旧历史。
    old = session.messages_for("查看项目")[:-1]
    result, _, followup = execute(
        tmp_path,
        [completion(text="继续")],
        session=session,
    )
    assert followup[0]["messages"][1:] == old + [{"role": "user", "content": "查看项目"}]
    assert session.messages_for("结束")[:-1] == old + [
        {"role": "user", "content": "查看项目"}, {"role": "assistant", "content": "继续"},
    ]
    session.clear()
    assert session.messages_for("新题") == [{"role": "user", "content": "新题"}]


def test_tool_error_is_sent_and_can_recover(tmp_path):
    result, _, payloads = execute(
        tmp_path,
        [completion([read_call()]), completion(text="文件不存在")],
    )
    assert result.status == "completed"
    assert json.loads(payloads[1]["messages"][-1]["content"])["status"] == "error"


def test_failure_threshold_stops_mid_batch(
    tmp_path,
    monkeypatch,
):
    reads = []
    original = registry.read_file

    def read(
        root,
        path,
    ):
        reads.append(path)
        return original(root, path)

    monkeypatch.setattr(
        registry,
        "read_file",
        read,
    )
    calls = [read_call(str(i)) for i in range(5)]
    result, session, payloads = execute(tmp_path, [completion(calls)])
    assert result.status == "stopped" and "连续工具失败" in result.message
    assert result.model_calls == 1 and result.tool_calls == 3
    assert len(reads) == 3 and len(payloads) == 1
    assert session.messages_for("新题") == [{"role": "user", "content": "新题"}]


def test_success_resets_failure_counter_across_batches(tmp_path):
    (tmp_path / "good").write_text("ok")
    result, _, _ = execute(
        tmp_path,
        [
            completion([read_call("1"), read_call("2"), read_call("3", "good")]),
            completion([read_call("4"), read_call("5")]), completion(),
        ],
    )
    assert result.status == "completed" and result.tool_calls == 5


def test_failure_count_continues_across_batches(tmp_path):
    result, _, payloads = execute(
        tmp_path,
        [
            completion([read_call("1"), read_call("2")]), completion([read_call("3")]),
        ],
    )
    assert result.status == "stopped" and result.tool_calls == 3
    assert len(payloads) == 2


def test_model_limit_does_not_execute_last_batch(tmp_path):
    (tmp_path / "a.txt").write_text("ok")
    result, _, payloads = execute(
        tmp_path,
        [completion([read_call()])] * 2,
        max_model_calls=2,
    )
    assert result.status == "stopped" and "模型调用上限（2 次）" in result.message
    assert result.model_calls == 2 and result.tool_calls == 1 and len(payloads) == 2


def test_final_answer_at_model_limit_is_success(tmp_path):
    result, _, _ = execute(
        tmp_path,
        [completion()],
        max_model_calls=1,
    )
    assert result.status == "completed" and result.model_calls == 1


def test_tool_budget_rejects_entire_batch_but_allows_final_request(tmp_path):
    (tmp_path / "a.txt").write_text("ok")
    result, _, _ = execute(
        tmp_path,
        [
            completion([read_call()]), completion(),
        ],
        max_tool_calls=1,
    )
    assert result.status == "completed" and result.tool_calls == 1
    result, _, _ = execute(
        tmp_path,
        [
            completion([read_call()]), completion([read_call("two")]),
        ],
        max_tool_calls=1,
    )
    assert result.status == "stopped" and result.tool_calls == 1
    result, _, _ = execute(
        tmp_path,
        [
            completion([read_call(), read_call("two")]),
        ],
        max_tool_calls=1,
    )
    assert result.status == "stopped" and result.tool_calls == 0
    assert "本批工具未执行" in result.message


@pytest.mark.parametrize("error", [503, httpx.ReadTimeout("hidden"), {"choices": []}])
def test_model_failure_after_tool_discards_turn_and_preserves_old_history(
    tmp_path,
    error,
):
    (tmp_path / "a.txt").write_text("ok")
    session = Session()
    session.record_turn("旧问题", "旧回答")
    old = session.messages_for("检查")
    result, _, payloads = execute(
        tmp_path,
        [completion([read_call()]), error],
        session=session,
    )
    assert result.status == "stopped" and "任务未完成" in result.message
    assert "hidden" not in result.message
    assert result.model_calls == 2 and result.tool_calls == 1
    assert len(payloads) == 2 and session.messages_for("检查") == old


def test_interrupt_preserves_history(tmp_path):
    session = Session()
    session.record_turn("旧", "回答")
    old = session.messages_for("新")
    with pytest.raises(KeyboardInterrupt):
        execute(
            tmp_path,
            [KeyboardInterrupt()],
            session=session,
        )
    assert session.messages_for("新") == old


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
@pytest.mark.parametrize(
    "name",
    ["max_model_calls", "max_tool_calls", "max_consecutive_failures"],
)
def test_invalid_limits(
    tmp_path,
    name,
    limit,
):
    with pytest.raises(ValueError, match="正整数"):
        execute(
            tmp_path,
            [],
            **{name: limit},
        )


def test_nested_session_data_is_independent():
    session = Session()
    turn = [{"role": "assistant", "content": None, "tool_calls": [read_call()]}]
    expected = deepcopy(turn)
    session.record_messages(turn)
    turn[0]["tool_calls"][0]["function"]["name"] = "changed"
    request = session.messages_for("新题")
    assert request[:-1] == expected
    request[0]["tool_calls"].clear()
    assert session.messages_for("新题")[:-1] == expected


def test_search_then_read_round_trip(tmp_path):
    (tmp_path / "a.txt").write_text("目标内容")
    search_call = ToolCall(
        "search",
        "search_project",
        '{"query":"目标"}',
    ).as_dict()
    result, session, payloads = execute(
        tmp_path,
        [
            completion([search_call]), completion([read_call(
                "read",
                "a.txt",
            )]), completion(),
        ],
    )
    assert result.status == "completed" and result.tool_calls == 2
    names = {t["function"]["name"] for t in payloads[0]["tools"]}
    assert names == {"read_file", "search_project"}
    search_result = json.loads(payloads[1]["messages"][-1]["content"])
    assert search_result["status"] == "success"
    assert json.loads(search_result["content"])["matches"][0] == {
        "path": "a.txt", "line": 1, "text": "目标内容", "text_truncated": False,
    }
    assert json.loads(payloads[2]["messages"][-1]["content"])["content"] == "目标内容"
    assert [m["role"] for m in session.messages_for("继续")] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant", "user",
    ]


def test_context_too_small_sends_no_request_and_preserves_history(tmp_path):
    session = Session()
    session.record_turn("旧问题", "回答")
    old = session.history_turns()
    result, _, payloads = execute(
        tmp_path,
        [],
        session=session,
        max_context_bytes=1,
    )
    assert result.status == "stopped" and "上下文上限" in result.message
    assert result.model_calls == result.tool_calls == 0 and payloads == []
    assert session.history_turns() == old


def test_context_trim_preserves_whole_tool_turn_and_commits_only_on_success(tmp_path):
    from minicode.context import context_bytes, select_context

    session = Session()
    session.record_turn("最早" * 1000, "旧回答")
    tool_turn = [
        {"role": "user", "content": "读取文件"},
        {"role": "assistant", "content": None, "tool_calls": [read_call()]},
        {"role": "tool", "tool_call_id": "one", "content": '{"status":"success","content":"正文"}'},
        {"role": "assistant", "content": "解释"},
    ]
    session.record_messages(tool_turn)
    current = [{"role": "user", "content": "查看项目"}]
    expected, _ = select_context(
        SYSTEM_PROMPT,
        [tool_turn],
        current,
        history_omitted=True,
    )
    limit = context_bytes(expected)
    result, _, payloads = execute(
        tmp_path,
        [completion()],
        session=session,
        max_context_bytes=limit,
    )
    assert result.status == "completed" and result.omitted_history_turns == 1
    assert payloads[0]["messages"] == expected
    assert session.history_turns() == [tool_turn, current + [{"role": "assistant", "content": "完成"}]]
    assert context_bytes(payloads[0]["messages"]) <= limit


@pytest.mark.parametrize("error", [503, {"choices": []}, KeyboardInterrupt()])
def test_trim_is_not_committed_on_failure_or_interrupt(
    tmp_path,
    error,
):
    from minicode.context import context_bytes, select_context

    session = Session()
    session.record_turn("旧" * 1000, "回答")
    old = session.history_turns()
    current = [{"role": "user", "content": "查看项目"}]
    minimal, _ = select_context(
        SYSTEM_PROMPT,
        [],
        current,
        history_omitted=True,
    )
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            execute(
                tmp_path,
                [error],
                session=session,
                max_context_bytes=context_bytes(minimal),
            )
    else:
        result, _, payloads = execute(
            tmp_path,
            [error],
            session=session,
            max_context_bytes=context_bytes(minimal),
        )
        assert result.status == "stopped" and result.omitted_history_turns == 1
        assert payloads[0]["messages"] == minimal
    assert session.history_turns() == old


def test_large_tool_results_stop_before_next_http_request(tmp_path):
    (tmp_path / "a.txt").write_text("中" * 10000)
    session = Session()
    session.record_turn("旧", "回答")
    old = session.history_turns()
    result, _, payloads = execute(
        tmp_path,
        [completion([read_call()])],
        session=session,
        max_context_bytes=5000,
    )
    assert result.status == "stopped" and "上下文上限" in result.message
    assert result.model_calls == 1 and result.tool_calls == 1 and len(payloads) == 1
    assert session.history_turns() == old
    assert "中" * 10 not in result.message


def test_history_can_be_trimmed_after_tool_execution_without_orphan_messages(tmp_path):
    from minicode.context import context_bytes

    (tmp_path / "a.txt").write_text("中" * 1500)
    session = Session()
    session.record_turn("旧" * 1500, "旧回答")
    # 首次请求能容纳历史；工具结果返回后必须移除该完整旧轮次。
    result, _, payloads = execute(
        tmp_path,
        [completion([read_call()]), completion()],
        session=session,
        max_context_bytes=8000,
    )
    assert result.status == "completed" and result.omitted_history_turns == 1
    assert "旧回答" in str(payloads[0]["messages"])
    assert "旧回答" not in str(payloads[1]["messages"])
    assert [m["role"] for m in payloads[1]["messages"]] == ["system", "user", "assistant", "tool"]
    assert payloads[1]["messages"][-1]["tool_call_id"] == "one"
    assert len(session.history_turns()) == 1
    assert all(context_bytes(p["messages"]) <= 8000 for p in payloads)


def test_invalid_context_limit(tmp_path):
    for limit in (True, 0, -1, 1.5):
        with pytest.raises(ValueError, match="正整数"):
            execute(
                tmp_path,
                [],
                max_context_bytes=limit,
            )


def test_default_context_limit_rejects_two_full_size_read_results(tmp_path):
    from minicode.context import MAX_CONTEXT_BYTES, context_bytes
    from minicode.tools.files import MAX_READ_BYTES

    (tmp_path / "a.txt").write_text("x" * MAX_READ_BYTES)
    result, session, payloads = execute(
        tmp_path,
        [completion([read_call(), read_call("two")])],
    )
    assert result.status == "stopped" and "上下文上限" in result.message
    assert (result.model_calls, result.tool_calls) == (1, 2)
    assert len(payloads) == 1 and context_bytes(payloads[0]["messages"]) <= MAX_CONTEXT_BYTES
    assert session.history_turns() == []


def test_omitted_turns_never_reappear_in_later_requests(tmp_path):
    from minicode.context import context_bytes

    (tmp_path / "a.txt").write_text("x")
    session = Session()
    session.record_turn("旧" * 3000, "不应重现")
    result, _, payloads = execute(
        tmp_path,
        [
            completion([read_call()]), completion([read_call("two")]), completion(),
        ],
        session=session,
        max_context_bytes=5000,
    )
    assert result.status == "completed" and result.omitted_history_turns == 1
    assert all("不应重现" not in str(p["messages"]) for p in payloads)
    assert all("省略" in p["messages"][0]["content"] for p in payloads)
    assert all(context_bytes(p["messages"]) <= 5000 for p in payloads)
    assert len(session.history_turns()) == 1
    result, _, followup = execute(
        tmp_path,
        [completion()],
        session=session,
    )
    assert "不应重现" not in str(followup[0]["messages"])
    assert len(session.history_turns()) == 2
