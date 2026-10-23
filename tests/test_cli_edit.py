"""通过模拟 HTTP 和真实临时文件验证主 CLI 的编辑、拒绝、停止和中断。"""

import json

import httpx
import pytest

from minicode.cli import main
from minicode.config import Config
from minicode.llm import request_model
from minicode.models import ToolCall
from minicode.session import Session
from minicode.tools import edit


def response(
    *,
    edit_request=False,
    text="回答",
    extra_call=False,
):
    """输入是否编辑及最终回答文本，返回模型响应；可附加后续读取以验证拒绝整批。"""
    calls = None
    if edit_request:
        calls = [ToolCall(
            "edit",
            "edit_file",
            '{"relative_path":"text","old_text":"old","new_text":"new"}',
        ).as_dict()]
        if extra_call:
            calls.append(ToolCall(
                "read",
                "read_file",
                '{"relative_path":"text"}',
            ).as_dict())
    return {"choices": [{
        "message": {"content": None if calls else text, "tool_calls": calls},
        "finish_reason": "tool_calls" if calls else "stop",
    }]}


def install_model(
    monkeypatch,
    bodies,
):
    """输入 pytest 替换器和模拟响应序列，返回捕获请求列表；保留真实解析但禁止真实网络。"""
    requests = []
    replies = iter(bodies)

    def serve(request):
        """输入模拟 HTTP 请求，捕获消息并给出下一响应或指定异常。"""
        requests.append(json.loads(request.content))
        body = next(replies)
        if isinstance(body, BaseException):
            raise body
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, json=body)

    transport = httpx.MockTransport(serve)

    def request_once(
        messages,
        config,
        **kwargs,
    ):
        """输入 Agent 消息与配置，使用模拟传输请求真实协议层，返回 ModelReply。"""
        return request_model(
            messages,
            config,
            use_tools=True,
            transport=transport,
        )

    monkeypatch.setattr("minicode.agent.request_model", request_once)
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))
    return requests


def test_cli_write_then_error_followup_and_clear_do_not_undo_file(
    tmp_path,
    monkeypatch,
    capsys,
):
    """实际 CLI 经 yes 写入后遇 503，保留交接供下一问；/clear 只清历史不回滚文件。"""
    path = tmp_path / "text"
    path.write_text("old")
    inputs = iter(["修改 text", "yes", "下一问", "/clear", "新问题", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    requests = install_model(
        monkeypatch,
        [response(edit_request=True), 503, response(text="已看到失败交接"), response(text="新会话")],
    )
    main()
    output = capsys.readouterr().out
    assert "待修改文件" in output and "-old" in output and "+new" in output
    assert "替换文本和处理结果" in output and "不会自动回滚" in output
    assert "已确认写入" in output and "已看到失败交接" in output
    assert [m["role"] for m in requests[2]["messages"]] == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert "MiniCode 程序记录" in requests[2]["messages"][-2]["content"]
    assert [m["role"] for m in requests[3]["messages"]] == ["system", "user"]
    assert path.read_text() == "new"


def test_cli_refusal_stops_batch_and_next_question_sees_refusal(
    tmp_path,
    monkeypatch,
    capsys,
):
    """主 CLI 用户拒绝后不执行剩余请求、不自动续问模型，下一题可见明确拒绝记录。"""
    path = tmp_path / "text"
    path.write_text("old")
    inputs = iter(["修改", "n", "下一问", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    requests = install_model(
        monkeypatch,
        [response(edit_request=True, extra_call=True), response()],
    )
    main()
    tools = [json.loads(m["content"])["status"] for m in requests[1]["messages"] if m["role"] == "tool"]
    assert tools == ["denied", "skipped"]
    assert path.read_text() == "old" and len(requests) == 2
    assert "用户未确认编辑" in capsys.readouterr().out


@pytest.mark.parametrize("phase", ["staging", "next_model"])
def test_cli_interruption_reports_write_state_and_exits(
    tmp_path,
    monkeypatch,
    capsys,
    phase,
):
    """主 CLI 在批准后的不同阶段 Ctrl+C，展示已写入或结果未确认，并保留原退出行为。"""
    path = tmp_path / "text"
    path.write_text("old")
    inputs = iter(["修改", "yes"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    bodies = [response(edit_request=True)]
    if phase == "next_model":
        bodies.append(KeyboardInterrupt())
    else:
        def interrupt(descriptor):
            """输入临时文件描述符，模拟准备期间 Ctrl+C，无正常输出。"""
            raise KeyboardInterrupt

        monkeypatch.setattr(
            edit.os,
            "fsync",
            interrupt,
        )
    requests = install_model(monkeypatch, bodies)
    main()
    output = capsys.readouterr()
    assert "用户中断" in output.out and "已中断，程序退出" in output.out
    assert output.err == ""
    assert path.read_text() == ("old" if phase == "staging" else "new")
    assert ("写入结果未确认" if phase == "staging" else "已确认写入") in output.out
    assert len(requests) == (1 if phase == "staging" else 2)
    assert len(list(tmp_path.iterdir())) == 1


def test_confirmation_eof_saves_denial_before_cli_exits(
    tmp_path,
    monkeypatch,
    capsys,
):
    """确认输入关闭时拒绝并记录，随后输入循环正常 EOF 退出，不写临时文件。"""
    path = tmp_path / "text"
    path.write_text("old")
    session = Session()
    monkeypatch.setattr("minicode.cli.Session", lambda: session)
    count = 0

    def receive(prompt):
        """首次输入返回问题，之后均模拟 EOF；用于确认默认拒绝与主输入退出。"""
        nonlocal count
        count += 1
        if count == 1:
            return "修改 text"
        raise EOFError

    monkeypatch.setattr("builtins.input", receive)
    install_model(monkeypatch, [response(edit_request=True)])
    main()
    assert path.read_text() == "old" and len(session.history_turns()) == 1
    assert json.loads(session.history_turns()[0][2]["content"])["status"] == "denied"
    assert capsys.readouterr().out.endswith("再见！\n")
