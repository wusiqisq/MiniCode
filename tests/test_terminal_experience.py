"""离线验证本地帮助、进度顺序、中文停止原因及显示回调不改变授权。"""

import os

import httpx
import pytest

from minicode.agent import AgentInterrupted, run_agent
from minicode.cli import _progress, main
from minicode.config import Config
from minicode.repair import RepairInterrupted, run_repair
from minicode.session import Session
from minicode.tools.command import CommandError, CommandResult
from test_agent_command import command_call, response
from test_cli_edit import install_model
from test_cli_repair import install, repair_inputs
from test_repair_demo import edit_call, repair_project, tool_call


def test_local_help_and_unknown_commands_do_not_load_config(
    monkeypatch,
    capsys,
):
    """多次 /help 与未知命令均不读配置、不进历史；无密钥时仍可查看帮助并退出。"""
    answers = iter(["/help", "/missing", "/help", "/exit"])
    session = Session()

    def forbidden():
        """帮助或拼错命令若触发配置读取则使测试失败，无正常返回值。"""
        pytest.fail("本地帮助不应读取配置")

    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("minicode.cli.Session", lambda: session)
    monkeypatch.setattr("minicode.cli.load_config", forbidden)
    main()
    output = capsys.readouterr().out
    assert output.count("/help：显示本帮助") == 2
    assert "未知会话命令" in output and "不要输入密钥" in output
    assert "每轮模型最多 8 次" in output and "最多修复 3 轮" in output
    assert not session.history_turns()


def test_help_does_not_enter_model_history(
    monkeypatch,
):
    """两次问答之间查看帮助或拼错命令，不污染下一次实际 HTTP 请求的用户历史。"""
    answers = iter(["第一问", "/help", "/oops", "第二问", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    requests = install_model(monkeypatch, [response(), response()])
    main()
    assert len(requests) == 2
    users = [m["content"] for m in requests[1]["messages"] if m["role"] == "user"]
    assert users == ["第一问", "第二问"]


def test_model_and_tool_progress_order(
    tmp_path,
    monkeypatch,
    capsys,
):
    """真实读取临时文件时进度先于工具执行，随后显示第二次模型请求，文件内容不作为进度输出。"""
    (tmp_path / "note.txt").write_text("file-content-marker")
    answers = iter(["读取文件", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    install_model(monkeypatch, [response([tool_call("read", "read_file", relative_path="note.txt")]), response()])
    main()
    shown = capsys.readouterr().out
    assert shown.index("本轮第 1/8 次") < shown.index("准备处理工具 read_file") < shown.index("本轮第 2/8 次")
    assert "file-content-marker" not in shown


@pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")
def test_repair_progress_tracks_real_checks(
    repair_project,
    monkeypatch,
    capsys,
):
    """临时错误样例真实执行失败、修复和通过，进度显示基线与轮次，最终报告为中文状态。"""
    install(
        monkeypatch,
        repair_inputs() + ["yes", "yes", "yes", "/exit"],
        [response([edit_call("fix")]), response()],
    )
    main()
    shown = capsys.readouterr().out
    assert shown.index("准备运行基线检查") < shown.index("固定检查 0：未通过") < shown.index("开始第 1/3 轮修复")
    assert shown.index("开始第 1/3 轮修复") < shown.index("第 1 轮修复后的复查") < shown.index("固定检查 1：通过")
    assert "检查 1：通过，退出码：0" in shown and "检查 1：passed" not in shown


@pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")
@pytest.mark.parametrize("kind,reason", [
    ("denied", "用户拒绝固定检查"), ("timed_out", "固定检查超时"),
    ("error", "固定检查执行错误"), ("unknown", "固定检查结果未确认"),
])
def test_stop_reason_describes_actual_failure(
    repair_project,
    monkeypatch,
    kind,
    reason,
):
    """用命令替身注入不同停止状态，验证报告只描述实际原因，不混写所有可能性。"""
    def outcome(
        *args,
        **kwargs,
    ):
        """接收固定检查参数，返回指定模拟结果或抛模拟命令错误，不执行子进程。"""
        if kind in {"error", "unknown"}:
            raise CommandError("模拟命令错误", started=kind == "unknown")
        return CommandResult(kind)

    monkeypatch.setattr("minicode.repair.run_command", outcome)
    import sys

    result = run_repair(
        "检查",
        Session(),
        Config(api_key="test-key"),
        repair_project,
        editable_paths=("calculator.py",),
        test_argv=[sys.executable, "-B", "check_calculator.py"],
    )
    assert result.status == "stopped" and reason in result.message


@pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")
def test_progress_return_value_cannot_authorize_command(tmp_path):
    """即使进度回调返回 True，无命令确认接口仍必须拒绝，不能创建标记文件。"""
    messages = []
    result = run_agent(
        "运行命令",
        Session(),
        Config(api_key="test-key"),
        tmp_path,
        on_progress=lambda message: messages.append(message) or True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response([
            command_call("open('marker','w').write('bad')"),
        ]))),
    )
    assert result.status == "stopped" and "用户未确认命令" in result.message
    assert messages and not (tmp_path / "marker").exists()


def test_interrupt_from_progress_before_request_preserves_history(tmp_path):
    """进度展示时的 Ctrl+C 发生在模型请求前；保留旧历史，不假计为一次已发送请求。"""
    session = Session()
    session.record_turn("之前", "回答")
    old = session.history_turns()

    def interrupted(message):
        """接收进度并模拟用户中断，无正常返回值。"""
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_agent("问题", session, Config(api_key="test-key"), tmp_path, on_progress=interrupted)
    assert session.history_turns() == old


@pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")
def test_repair_progress_interrupt_before_check_cannot_execute(repair_project):
    """基线检查展示阶段中断应交接为批准前中断，不执行命令或启动修复。"""
    import sys

    def interrupted(message):
        """接收程序阶段文字后模拟 Ctrl+C，无正常返回值。"""
        raise KeyboardInterrupt

    with pytest.raises(RepairInterrupted) as error:
        run_repair(
            "修复",
            Session(),
            Config(api_key="test-key"),
            repair_project,
            editable_paths=("calculator.py",),
            test_argv=[sys.executable, "-B", "check_calculator.py"],
            on_progress=interrupted,
        )
    assert error.value.result.verifications[0].outcome == "interrupted"
    assert error.value.result.repair_attempts == 0


def test_progress_escapes_terminal_controls(capsys):
    """模拟工具名含控制字符，展示时必须转义 ESC，普通中文保持可读，不形成终端指令。"""
    _progress("准备处理工具\x1b[31m中文")
    shown = capsys.readouterr().out
    assert "\x1b" not in shown and "\\x1b" in shown and "中文" in shown


@pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")
def test_progress_interrupt_after_check_approval_is_unknown(repair_project):
    """批准后启动提示处中断时保守报告 unknown；不能误称命令未执行或检查已通过。"""
    import sys

    def interrupted(message):
        """仅在已批准固定检查的启动窗口中断，其余进度正常展示，无返回值。"""
        if message.startswith("已批准固定检查"):
            raise KeyboardInterrupt

    with pytest.raises(RepairInterrupted) as error:
        run_repair(
            "检查",
            Session(),
            Config(api_key="test-key"),
            repair_project,
            editable_paths=("calculator.py",),
            test_argv=[sys.executable, "-B", "check_calculator.py"],
            command_confirm=lambda request: True,
            on_progress=interrupted,
        )
    assert error.value.result.verifications[0].outcome == "unknown"


def test_progress_interrupt_after_edit_approval_keeps_uncertain(repair_project):
    """编辑批准后的进度中断沿用 Agent 的 uncertain 交接，不把尚未返回的写入声明为成功。"""
    def interrupted(message):
        """仅在编辑批准后的准备阶段模拟 Ctrl+C，其余进度不处理，无返回值。"""
        if message.startswith("已批准编辑"):
            raise KeyboardInterrupt

    with pytest.raises(AgentInterrupted) as error:
        run_agent(
            "修改",
            Session(),
            Config(api_key="test-key"),
            repair_project,
            edit_confirm=lambda preview: True,
            on_progress=interrupted,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response([edit_call("fix")]))),
        )
    assert error.value.result.uncertain_paths == ("calculator.py",)
    assert "return a - b" in (repair_project / "calculator.py").read_text()
