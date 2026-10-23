"""临时项目与模拟 HTTP 验证模型编辑接入，不访问真实模型服务。"""

import json

import httpx
import pytest

from minicode.agent import AgentInterrupted, SYSTEM_PROMPT, run_agent
from minicode.config import Config
from minicode.context import context_bytes
from minicode.models import ToolCall
from minicode.session import Session
from minicode.tools import edit
from minicode.tools.registry import dispatch_tool, dispatch_tool_calls, tool_definitions


def edit_call(
    call_id="edit",
    old="old",
    new="new",
    path="text",
):
    """输入请求编号、原/新文本和路径，返回 edit_file 协议字典，不执行编辑。"""
    return ToolCall(
        call_id,
        "edit_file",
        json.dumps({"relative_path": path, "old_text": old, "new_text": new}),
    ).as_dict()


def read_call(call_id="read"):
    """输入编号，返回读取 text 的协议字典；此函数本身不读取文件。"""
    return ToolCall(
        call_id,
        "read_file",
        '{"relative_path":"text"}',
    ).as_dict()


def reply(
    calls=None,
    text="完成",
):
    """输入工具请求或最终回答文本，返回模拟 HTTP 响应体，不访问服务。"""
    return {"choices": [{
        "message": {"content": None if calls else text, "tool_calls": calls},
        "finish_reason": "tool_calls" if calls else "stop",
    }]}


def run_sequence(
    root,
    replies,
    *,
    session=None,
    confirm=None,
    **limits,
):
    """输入临时根目录、响应序列、会话及确认/限制，返回任务结果、会话和实际请求。"""
    requests = []
    bodies = iter(replies)

    def serve(request):
        """输入模拟请求，捕获 JSON 后返回下一响应或抛指定异常，用于检查完整网络协议。"""
        requests.append(json.loads(request.content))
        body = next(bodies)
        if isinstance(body, BaseException):
            raise body
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, json=body)

    session = session if session is not None else Session()
    result = run_agent(
        "修改 text",
        session,
        Config(api_key="test-key"),
        root,
        edit_confirm=confirm,
        transport=httpx.MockTransport(serve),
        **limits,
    )
    return result, session, requests


def assert_closed(messages):
    """输入消息列表，断言每批 assistant 工具请求后都有按编号对应的完整结果，无返回值。"""
    pending = []
    for message in messages:
        if message["role"] == "tool":
            assert pending and message["tool_call_id"] == pending.pop(0)
        else:
            assert not pending
            pending = [call["id"] for call in message.get("tool_calls", [])]
    assert not pending


def test_schema_and_dispatch_approved_edit(tmp_path):
    """临时文件验证新 schema、真实确认写入及程序内部 changed_path，不靠模型声称成功。"""
    path = tmp_path / "text"
    path.write_text("old")
    schema = next(item["function"] for item in tool_definitions() if item["function"]["name"] == "edit_file")
    assert schema["parameters"]["required"] == ["relative_path", "old_text", "new_text"]
    assert schema["parameters"]["additionalProperties"] is False
    call = edit_call()["function"]
    result = dispatch_tool(
        tmp_path,
        "edit",
        call["name"],
        call["arguments"],
        edit_confirm=lambda preview: True,
    )
    assert result.status == "success" and result.changed_path == "text"
    assert path.read_text() == "new"
    assert json.loads(result.as_message()["content"])["status"] == "success"


@pytest.mark.parametrize("arguments", [
    '{}', '[]', '{"relative_path":"text","old_text":"old","new_text":"new","confirmed":true}',
    '{"relative_path":"text","old_text":"old","new_text":"new","project_root":"/"}',
    '{"relative_path":"text","old_text":1,"new_text":"new"}',
    '{"relative_path":"text","old_text":"old","old_text":"x","new_text":"new"}',
])
def test_model_cannot_supply_authorization_or_root(
    tmp_path,
    arguments,
):
    """模型注入字段、重复字段及错误类型不能授权或更换根目录，临时文件不变。"""
    path = tmp_path / "text"
    path.write_text("old")

    def forbidden(preview):
        """输入非法请求产生的预览即使测试失败，无正常输出。"""
        pytest.fail("无效参数不能进入确认")

    result = dispatch_tool(
        tmp_path,
        "edit",
        "edit_file",
        arguments,
        edit_confirm=forbidden,
    )
    assert result.status == "error" and path.read_text() == "old"


def test_batch_helper_stops_after_refusal(tmp_path):
    """Python 批量分发也须遵守拒绝停止，剩余请求给 skipped 而非偷偷执行。"""
    path = tmp_path / "text"
    path.write_text("old")
    first = edit_call()["function"]
    calls = (
        ToolCall(
            "edit",
            "edit_file",
            first["arguments"],
        ),
        ToolCall(
            "second",
            "read_file",
            '{"relative_path":"text"}',
        ),
    )
    results = dispatch_tool_calls(
        tmp_path,
        calls,
        edit_confirm=lambda preview: False,
    )
    assert [r.status for r in results] == ["denied", "skipped"]
    assert path.read_text() == "old"


def test_model_edit_read_answer_and_followup_history(tmp_path):
    """模拟模型依次编辑、读取并回答；临时文件实际写入且后续请求携带完整历史。"""
    path = tmp_path / "text"
    path.write_text("old")
    result, session, requests = run_sequence(
        tmp_path,
        [reply([edit_call()]), reply([read_call()]), reply()],
        confirm=lambda preview: True,
    )
    assert result.status == "completed" and result.written_paths == ("text",)
    assert result.history_saved and result.tool_calls == 2
    assert path.read_text() == "new"
    assert json.loads(requests[2]["messages"][-1]["content"])["content"] == "new"
    assert_closed(session.messages_for("下一问"))
    _, _, followup = run_sequence(
        tmp_path,
        [reply()],
        session=session,
    )
    assert any(m["role"] == "tool" for m in followup[0]["messages"])
    assert_closed(followup[0]["messages"])


@pytest.mark.parametrize("confirm", [None, lambda preview: False])
def test_refusal_stops_model_and_closes_pending_batch(
    tmp_path,
    confirm,
):
    """无确认接口或用户拒绝均停止本轮；保存拒绝并补齐未执行批次，模型不获重试机会。"""
    path = tmp_path / "text"
    path.write_text("old")
    result, session, requests = run_sequence(
        tmp_path,
        [reply([edit_call(), read_call()])],
        confirm=confirm,
    )
    assert result.status == "stopped" and result.tool_calls == 1
    assert len(requests) == 1 and path.read_text() == "old"
    assert result.history_saved and result.written_paths == ()
    tools = [json.loads(m["content"])["status"] for m in session.history_turns()[0] if m["role"] == "tool"]
    assert tools == ["denied", "skipped"]
    assert session.history_turns()[0][-1]["content"].startswith("MiniCode 程序记录：")
    assert_closed(session.messages_for("下一问"))


def test_second_edit_needs_new_approval_and_preserves_first_on_denial(tmp_path):
    """同一批两次编辑分别确认，第二次拒绝不回滚第一次，也不能执行余下读取。"""
    path = tmp_path / "text"
    path.write_text("old")
    seen = []

    def decide(preview):
        """输入每次预览并记录；第一次批准，第二次拒绝，不缓存此前授权。"""
        seen.append(preview)
        return len(seen) == 1

    result, session, _ = run_sequence(
        tmp_path,
        [reply([edit_call(), edit_call(
            "second",
            "new",
            "third",
        ), read_call()])],
        confirm=decide,
    )
    assert len(seen) == 2 and result.tool_calls == 2
    assert result.written_paths == ("text",) and path.read_text() == "new"
    assert "不会自动回滚" in result.message
    assert_closed(session.messages_for("下一问"))


@pytest.mark.parametrize("failure", [503, httpx.ReadTimeout("hidden"), {"choices": []}])
def test_written_edit_survives_model_failure_with_complete_history(
    tmp_path,
    failure,
):
    """批准写入后模型失败时保留文件与程序交接，旧历史不裁剪，下一轮消息协议仍完整。"""
    path = tmp_path / "text"
    path.write_text("old")
    session = Session()
    session.record_turn("旧" * 10000, "旧回答")
    old = session.history_turns()[0]
    result, _, requests = run_sequence(
        tmp_path,
        [reply([edit_call()]), failure],
        session=session,
        confirm=lambda preview: True,
        max_context_bytes=8000,
    )
    assert result.status == "stopped" and result.history_saved
    assert result.omitted_history_turns == 1 and session.history_turns()[0] == old
    assert len(session.history_turns()) == 2 and path.read_text() == "new"
    assert "hidden" not in result.message and "不会自动回滚" in result.message
    assert_closed(session.messages_for("下一问"))
    _, _, followup = run_sequence(
        tmp_path,
        [reply()],
        session=session,
    )
    assert "MiniCode 程序记录" in str(followup[0]["messages"])
    assert_closed(followup[0]["messages"])


@pytest.mark.parametrize("limit", ["model", "tools", "failures"])
def test_limits_after_write_keep_closed_records(
    tmp_path,
    limit,
):
    """模型/工具/连续失败上限在写入后触发时，未执行工具必须补结果，已写入文件保留。"""
    path = tmp_path / "text"
    path.write_text("old")
    limits = {}
    if limit == "model":
        limits["max_model_calls"] = 2
        next_calls = [read_call()]
    elif limit == "tools":
        limits["max_tool_calls"] = 1
        next_calls = [read_call(), read_call("later")]
    else:
        next_calls = [ToolCall(
            str(i),
            "unknown",
            "{}",
        ).as_dict() for i in range(4)]
    result, session, _ = run_sequence(
        tmp_path,
        [reply([edit_call()]), reply(next_calls)],
        confirm=lambda preview: True,
        **limits,
    )
    assert result.status == "stopped" and result.history_saved and path.read_text() == "new"
    assert any(m.get("role") == "tool" and json.loads(m["content"])["status"] == "skipped" for m in session.history_turns()[0])
    assert_closed(session.messages_for("下一问"))


def test_context_limit_after_write_preserves_actual_effect(tmp_path):
    """大替换文本写入后使下一请求超限时不能丢弃写入交接，也不能发送超预算请求。"""
    path = tmp_path / "text"
    path.write_text("old")
    minimal = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "修改 text"}]
    result, session, requests = run_sequence(
        tmp_path,
        [reply([edit_call(new="中" * 2000)])],
        confirm=lambda preview: True,
        max_context_bytes=context_bytes(minimal) + 200,
    )
    assert result.status == "stopped" and result.history_saved and "上下文上限" in result.message
    assert len(requests) == 1 and path.read_text() == "中" * 2000
    assert_closed(session.messages_for("下一问"))


def test_interruption_before_approval_leaves_old_history(tmp_path):
    """确认前 Ctrl+C 没有写入影响，原文件与既有历史保持不变。"""
    path = tmp_path / "text"
    path.write_text("old")
    session = Session()
    session.record_turn("旧", "答")
    old = session.history_turns()

    def interrupt(preview):
        """输入待确认预览后模拟用户中断，无正常返回值。"""
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt) as error:
        run_sequence(
            tmp_path,
            [reply([edit_call()])],
            session=session,
            confirm=interrupt,
        )
    assert not isinstance(error.value, AgentInterrupted)
    assert path.read_text() == "old" and session.history_turns() == old


@pytest.mark.parametrize("phase", ["fsync", "replace", "next_model"])
def test_interruption_after_approval_reports_known_or_uncertain_effect(
    tmp_path,
    monkeypatch,
    phase,
):
    """批准后在准备、替换后或续问时中断，分别保留不确定/已知写入记录并补齐协议。"""
    path = tmp_path / "text"
    path.write_text("old")
    session = Session()
    responses = [reply([edit_call(), read_call()])]
    if phase == "next_model":
        responses.append(KeyboardInterrupt())
    else:
        operation = getattr(edit.os, phase)

        def interrupt(
            *args,
            **kwargs,
        ):
            """输入系统调用参数；替换阶段先实际替换再中断，同步阶段直接中断。"""
            if phase == "replace":
                operation(*args, **kwargs)
            raise KeyboardInterrupt

        monkeypatch.setattr(
            edit.os,
            phase,
            interrupt,
        )
    with pytest.raises(AgentInterrupted) as error:
        run_sequence(
            tmp_path,
            responses,
            session=session,
            confirm=lambda preview: True,
        )
    result = error.value.result
    assert result.history_saved and "用户中断" in result.message
    assert path.read_text() == ("old" if phase == "fsync" else "new")
    if phase == "next_model":
        assert result.written_paths == ("text",) and not result.uncertain_paths
    else:
        assert result.uncertain_paths == ("text",) and "写入结果未确认" in result.message
    assert_closed(session.messages_for("下一问"))
    assert len(list(tmp_path.iterdir())) == 1


def test_invalid_edit_can_be_corrected_before_single_confirmation(tmp_path):
    """模型先用错误原文后纠正时，只有有效预览会询问一次，错误请求不写入。"""
    path = tmp_path / "text"
    path.write_text("old")
    confirmations = []

    def approve(preview):
        """输入有效预览并记录，返回 True；用于验证错误请求不会进入确认。"""
        confirmations.append(preview)
        return True

    result, session, requests = run_sequence(
        tmp_path,
        [reply([edit_call(old="missing")]), reply([edit_call("corrected")]), reply()],
        confirm=approve,
    )
    assert result.status == "completed" and len(confirmations) == 1
    assert json.loads(requests[1]["messages"][-1]["content"])["status"] == "error"
    assert path.read_text() == "new" and result.written_paths == ("text",)
    assert_closed(session.messages_for("下一问"))


def test_conflict_is_returned_to_model_without_writing(tmp_path):
    """用户确认期间外部修改时，模型收到 error 而非成功，外部内容保留。"""
    path = tmp_path / "text"
    path.write_text("old")

    def save_then_approve(preview):
        """输入预览，模拟外部保存后返回 True；应用必须在写入前检测冲突。"""
        path.write_text("external")
        return True

    result, _, requests = run_sequence(
        tmp_path,
        [reply([edit_call()]), reply(text="检测到冲突，未完成编辑")],
        confirm=save_then_approve,
    )
    assert not result.written_paths and path.read_text() == "external"
    assert json.loads(requests[1]["messages"][-1]["content"])["status"] == "error"
