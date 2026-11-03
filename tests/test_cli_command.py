"""模拟模型与真实本地子进程验证主 CLI 命令授权、数据提示及停止交接。"""

import json
import os

import pytest

from minicode.cli import main
from minicode.session import Session
from minicode.tools import command
from test_agent_command import assert_closed, command_call, response
from test_cli_edit import install_model


pytestmark = pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")


def test_cli_approved_command_and_clear(
    tmp_path,
    monkeypatch,
    capsys,
):
    """真实 CLI 批准命令后展示应用交接，clear 仅清历史不撤销临时文件修改。"""
    inputs = iter(["运行", "yes", "/clear", "新问题", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    requests = install_model(
        monkeypatch,
        [response([command_call("open('marker','w').write('done'); print('ok')")]), response(), response()],
    )
    main()
    shown = capsys.readouterr().out
    assert "命令参数、标准输出和标准错误将发送到模型服务" in shown
    assert "命令参数：" in shown and "超时：30 秒" in shown and "工作目录：" in shown
    assert "命令交接" in shown and '"returncode": 0' in shown
    assert "不是沙箱" in shown and "不会撤销其副作用" in shown
    assert (tmp_path / "marker").read_text() == "done"
    assert [m["role"] for m in requests[2]["messages"]] == ["system", "user"]


@pytest.mark.parametrize("answer", ["n", ""])
def test_cli_denial_stops_batch_and_followup_sees_record(
    tmp_path,
    monkeypatch,
    capsys,
    answer,
):
    """终端拒绝阻止本批两条命令且不续问，下一问题携带 denied/skipped 完整记录。"""
    inputs = iter(["执行", answer, "下一问", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    requests = install_model(
        monkeypatch,
        [response([
            command_call("open('marker','w').write('bad')"),
            command_call(call_id="later"),
        ]), response()],
    )
    main()
    assert len(requests) == 2 and not (tmp_path / "marker").exists()
    statuses = [json.loads(m["content"])["status"] for m in requests[1]["messages"] if m["role"] == "tool"]
    assert statuses == ["denied", "skipped"]
    assert_closed(requests[1]["messages"])
    assert "用户未确认命令" in capsys.readouterr().out


def test_cli_command_then_model_failure_and_followup(
    tmp_path,
    monkeypatch,
    capsys,
):
    """批准后非零退出并遇模型 503，CLI 仍汇报退出码及副作用，下一问可见交接。"""
    inputs = iter(["运行", "yes", "下一问", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    requests = install_model(
        monkeypatch,
        [response([command_call("import sys; open('marker','w').write('done'); sys.exit(4)")]), 503, response()],
    )
    main()
    shown = capsys.readouterr().out
    assert "任务未完成" in shown and '"returncode": 4' in shown and "不会自动回滚" in shown
    assert (tmp_path / "marker").exists()
    assert "MiniCode 程序记录" in str(requests[2]["messages"])
    assert_closed(requests[2]["messages"])


@pytest.mark.parametrize("phase", ["confirmation", "running", "next_model"])
def test_cli_interrupt_reports_command_state_and_exits(
    tmp_path,
    monkeypatch,
    capsys,
    phase,
):
    """确认前中断没有交接，批准后运行中断报告 unknown，续问中断保留已知退出码。"""
    session = Session()
    monkeypatch.setattr("minicode.cli.Session", lambda: session)
    answers = iter(["运行", "yes"])

    def receive(prompt):
        """输入问题或授权；指定确认阶段在用户尚未批准时模拟 Ctrl+C。"""
        if phase == "confirmation" and "[y/N]" in prompt:
            raise KeyboardInterrupt
        return next(answers)

    monkeypatch.setattr("builtins.input", receive)
    bodies = [response([command_call("import time; time.sleep(20)" if phase == "running" else "print('ok')")])]
    if phase == "next_model":
        bodies.append(KeyboardInterrupt())
    if phase == "running":
        def interrupt(
            self,
            timeout=None,
        ):
            """接收选择器等待参数，模拟运行期间中断，无正常返回值。"""
            raise KeyboardInterrupt

        monkeypatch.setattr(command.selectors.DefaultSelector, "select", interrupt)
    install_model(monkeypatch, bodies)
    main()
    shown = capsys.readouterr()
    assert "已中断，程序退出" in shown.out and not shown.err
    if phase == "confirmation":
        assert not session.history_turns()
    else:
        assert "命令交接" in shown.out
        assert ('"outcome": "unknown"' if phase == "running" else '"returncode": 0') in shown.out
        assert_closed(session.messages_for("下一问"))


def test_cli_eof_during_confirmation_denies(
    tmp_path,
    monkeypatch,
    capsys,
):
    """终端确认处 EOF 默认拒绝并保存记录，主输入再次 EOF 时正常退出。"""
    session = Session()
    monkeypatch.setattr("minicode.cli.Session", lambda: session)
    calls = 0

    def receive(prompt):
        """首次输入问题，随后持续 EOF，模拟关闭标准输入，无授权。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            return "运行"
        raise EOFError

    monkeypatch.setattr("builtins.input", receive)
    install_model(monkeypatch, [response([command_call("open('marker','w').write('bad')")])])
    main()
    assert not (tmp_path / "marker").exists()
    assert "denied" in str(session.history_turns())
    assert capsys.readouterr().out.endswith("再见！\n")
