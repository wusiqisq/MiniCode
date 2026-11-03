"""固定错误样例配合模拟 HTTP，验证真实文件修改、测试命令及失败交接。"""

import json
import os
from pathlib import Path
import shutil
import sys

import httpx
import pytest

from minicode.agent import run_agent
from minicode.config import Config
from minicode.models import ToolCall
from minicode.session import Session
from test_agent_command import assert_closed, response
from test_cli_edit import install_model


pytestmark = pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")
DEMO_ROOT = Path(__file__).resolve().parents[1] / "examples" / "repair_demo"


@pytest.fixture
def repair_project(tmp_path):
    """输入 pytest 临时目录，复制两个无密钥样例文件并返回项目路径，不修改原始样例。"""
    for name in ("calculator.py", "check_calculator.py"):
        shutil.copyfile(DEMO_ROOT / name, tmp_path / name)
    return tmp_path


def tool_call(
    call_id,
    name,
    **arguments,
):
    """输入编号、工具名和参数，返回符合模型协议的请求字典，不执行工具。"""
    return ToolCall(call_id, name, json.dumps(arguments)).as_dict()


def check_call(call_id):
    """输入请求编号，返回用当前解释器执行样例检查的命令请求，不启动进程。"""
    return tool_call(
        call_id,
        "run_command",
        argv=[sys.executable, "-B", "check_calculator.py"],
    )


def edit_call(
    call_id,
    old_text="return a - b",
    new_text="return a + b",
):
    """输入编号及替换文本，返回只修改 calculator.py 的请求，尚未授权写入。"""
    return tool_call(
        call_id,
        "edit_file",
        relative_path="calculator.py",
        old_text=old_text,
        new_text=new_text,
    )


def run_demo(
    root,
    bodies,
    *,
    decisions=None,
    **limits,
):
    """输入临时项目、模拟响应及逐次批准序列，运行真实循环并返回结果、会话、请求与授权顺序。

    文件和命令工具真实执行；HTTP 使用 MockTransport，不连接模型服务。
    limits 透传现有循环上限；批准序列耗尽会使测试失败，防止额外操作被悄悄批准。
    """
    requests = []
    approvals = []
    replies = iter(bodies)
    choices = iter(decisions) if decisions is not None else None
    session = Session()

    def serve(request):
        """捕获实际 HTTP 请求并返回预设响应；超出预设序列即失败，不访问网络。"""
        requests.append(json.loads(request.content))
        body = next(replies)
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, json=body)

    def approve_edit(preview):
        """记录真实编辑快照中的差异并返回本次批准决定，不继承其他操作的授权。"""
        approvals.append(("edit", preview.diff))
        return next(choices) if choices is not None else True

    def approve_command(request):
        """记录命令参数并返回本次批准决定；只允许固定检查命令在临时根目录执行。"""
        assert request.argv == (sys.executable, "-B", "check_calculator.py")
        assert request.cwd == root
        approvals.append(("command", request.argv))
        return next(choices) if choices is not None else True

    result = run_agent(
        "读取两个文件，运行检查，只修复 calculator.py，再检查并如实汇报。",
        session,
        Config(api_key="test-key"),
        root,
        edit_confirm=approve_edit,
        command_confirm=approve_command,
        transport=httpx.MockTransport(serve),
        **limits,
    )
    for request in requests:
        assert_closed(request["messages"])
    assert_closed(session.messages_for("下一问"))
    return result, session, requests, approvals


def outputs(requests):
    """输入已捕获请求，按编号去重提取命令工具的真实回传结果，返回结果列表。"""
    found = {}
    for request in requests:
        for message in request["messages"]:
            if message["role"] == "tool":
                payload = json.loads(message["content"])
                content = payload["content"]
                if content.startswith("{"):
                    data = json.loads(content)
                    if "returncode" in data:
                        found[message["tool_call_id"]] = data
    return list(found.values())


@pytest.mark.parametrize("first_fix_wrong", [False, True])
def test_failed_check_then_repair_and_recheck(
    repair_project,
    first_fix_wrong,
):
    """临时样例真实失败后修复并通过；可先修错一次，验证连续失败反馈与逐次授权、原测试不变。"""
    checks_before = (repair_project / "check_calculator.py").read_bytes()
    bodies = [response([
        tool_call("source", "read_file", relative_path="calculator.py"),
        tool_call("checks", "read_file", relative_path="check_calculator.py"),
    ]), response([check_call("baseline")])]
    if first_fix_wrong:
        bodies.extend([
            response([edit_call("wrong", new_text="return a * b")]),
            response([check_call("still_failed")]),
            response([edit_call("correct", old_text="return a * b")]),
        ])
    else:
        bodies.append(response([edit_call("correct")]))
    bodies.extend([
        response([check_call("passed")]),
        response(text="已修改 calculator.py；3 项检查通过，退出码 0；未修改检查脚本。"),
    ])
    result, session, requests, approvals = run_demo(repair_project, bodies)
    assert result.status == "completed" and result.written_paths == ("calculator.py",)
    assert len(session.history_turns()) == 1
    assert [record.returncode for record in result.commands] == ([1, 1, 0] if first_fix_wrong else [1, 0])
    actual = outputs(requests)
    assert "FAILED (failures=2)" in actual[0]["stderr"]
    assert actual[0]["returncode"] == 1
    assert actual[-1]["returncode"] == 0 and "Ran 3 tests" in actual[-1]["stderr"]
    assert actual[-1]["stderr"].rstrip().endswith("OK")
    assert "return a + b" in (repair_project / "calculator.py").read_text()
    assert (repair_project / "check_calculator.py").read_bytes() == checks_before
    assert [kind for kind, _ in approvals] == (
        ["command", "edit", "command", "edit", "command"]
        if first_fix_wrong else ["command", "edit", "command"]
    )
    assert all("+    return a" in diff for kind, diff in approvals if kind == "edit")


@pytest.mark.parametrize("denied_phase", ["baseline", "edit", "recheck"])
def test_repair_denial_stops_without_bypassing(
    repair_project,
    denied_phase,
):
    """分别拒绝初始检查、修复、复查；断言不续问、不执行剩余批次，已批准写入保留。"""
    bodies = [response([check_call("baseline")])]
    choices = [False]
    if denied_phase != "baseline":
        bodies.append(response([edit_call("fix")]))
        choices = [True, False]
    if denied_phase == "recheck":
        bodies.append(response([check_call("recheck"), edit_call("forbidden")]))
        choices = [True, True, False]
    result, session, requests, approvals = run_demo(
        repair_project,
        bodies,
        decisions=choices,
    )
    assert result.status == "stopped" and result.history_saved
    assert len(requests) == len(bodies) and len(approvals) == len(choices)
    expected = "return a + b" if denied_phase == "recheck" else "return a - b"
    assert expected in (repair_project / "calculator.py").read_text()
    assert len(result.commands) == (0 if denied_phase == "baseline" else 1)
    statuses = [json.loads(m["content"])["status"] for m in session.history_turns()[0] if m["role"] == "tool"]
    assert "denied" in statuses
    if denied_phase == "recheck":
        assert statuses[-2:] == ["denied", "skipped"]


@pytest.mark.parametrize("stop", ["model_error", "model_limit"])
def test_repair_stopped_before_recheck_keeps_unverified_change(
    repair_project,
    stop,
):
    """修复写入后服务失败或达到模型上限时，保留实际文件和失败记录，不把未运行的复查当通过。"""
    bodies = [response([check_call("baseline")]), response([edit_call("fix")])]
    limits = {}
    if stop == "model_error":
        bodies.append(503)
    else:
        bodies.append(response([check_call("not_executed")]))
        limits["max_model_calls"] = 3
    result, session, requests, approvals = run_demo(
        repair_project,
        bodies,
        **limits,
    )
    assert result.status == "stopped" and result.history_saved
    assert result.written_paths == ("calculator.py",)
    assert [record.returncode for record in result.commands] == [1]
    assert [kind for kind, _ in approvals] == ["command", "edit"]
    assert "return a + b" in (repair_project / "calculator.py").read_text()
    assert "任务未完成" in result.message and "不会自动回滚" in result.message
    assert "MiniCode 程序记录" in session.history_turns()[0][-1]["content"]
    assert len(requests) == 3


def test_cli_repair_demo_confirms_each_step(
    repair_project,
    monkeypatch,
    capsys,
):
    """临时样例经真实 CLI 三次确认完成失败、编辑、通过；检查授权展示和实际文件，模型为模拟 HTTP。"""
    from minicode.cli import main

    answers = iter(["修复加法并检查", "yes", "yes", "yes", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    requests = install_model(
        monkeypatch,
        [
            response([check_call("baseline")]),
            response([edit_call("fix")]),
            response([check_call("recheck")]),
            response(text="已修改 calculator.py；3 项检查通过，退出码 0；未修改检查脚本。"),
        ],
    )
    main()
    shown = capsys.readouterr().out
    assert shown.count("命令参数：") == 2
    assert "-    return a - b" in shown and "+    return a + b" in shown
    assert '已确认写入：["calculator.py"]' in shown
    assert '"returncode": 1' in shown and '"returncode": 0' in shown
    assert "3 项检查通过" in shown
    assert "return a + b" in (repair_project / "calculator.py").read_text()
    assert [data["returncode"] for data in outputs(requests)] == [1, 0]
