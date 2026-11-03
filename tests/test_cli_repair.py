"""实际 CLI 输入与确认函数驱动有限修复，模型使用模拟 HTTP，不读取真实密钥。"""

import json
import os
import subprocess
import sys
import textwrap

import httpx
import pytest

from minicode.cli import main
from minicode.config import Config
from minicode.llm import request_model
from minicode.session import Session
from test_agent_command import assert_closed, response
from test_repair_demo import edit_call, repair_project


pytestmark = pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")


def install(
    monkeypatch,
    answers,
    bodies=(),
):
    """输入终端回答与模型响应，安装模拟传输并返回会话和请求列表；实际文件及确认流程保留。"""
    inputs = iter(answers)
    replies = iter(bodies)
    requests = []
    session = Session()

    def receive(prompt):
        """输入提示并取下一终端回答；异常值模拟 Ctrl+C 或 EOF，不额外批准。"""
        value = next(inputs)
        if isinstance(value, BaseException):
            raise value
        return value

    def serve(request):
        """记录请求并返回下一预设 HTTP 响应，禁止额外请求或真实网络。"""
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))

    def request_once(
        messages,
        config,
        **options,
    ):
        """透传修复模式的工具范围，使用模拟传输调用真实模型协议解析器并返回 ModelReply。"""
        options["transport"] = httpx.MockTransport(serve)
        return request_model(messages, config, **options)

    monkeypatch.setattr("builtins.input", receive)
    monkeypatch.setattr("minicode.agent.request_model", request_once)
    monkeypatch.setattr("minicode.cli.Session", lambda: session)
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))
    return session, requests


def repair_inputs():
    """返回有效 /repair 参数输入列表，使用当前解释器和固定检查脚本，不执行命令。"""
    return ["/repair", "修复加法，不改测试", '["calculator.py"]',
            json.dumps([sys.executable, "-B", "check_calculator.py"])]


def test_cli_repair_pass_and_followup_history(
    repair_project,
    monkeypatch,
    capsys,
):
    """实际 CLI 三次 yes 完成基线、编辑、复查，程序生成结论；下一问携带完整结果，clear 不回滚。"""
    session, requests = install(
        monkeypatch,
        repair_inputs() + ["yes", "yes", "yes", "汇报刚才的检查", "/clear", "/exit"],
        [response([edit_call("fix")]), response(text="模型猜测没有修好。"), response()],
    )
    main()
    shown = capsys.readouterr().out
    assert shown.count("命令参数：") == 2
    assert "修复轮数：1/3" in shown and "指定检查通过。" in shown
    assert "检查 0：未通过，退出码：1" in shown and "检查 1：通过，退出码：0" in shown
    assert "Ran 3 tests" in shown and "+    return a + b" in shown
    assert "固定验证结果" in str(requests[2]["messages"])
    assert "指定检查通过" in str(requests[2]["messages"])
    assert_closed(requests[2]["messages"])
    assert "run_command" not in {t["function"]["name"] for t in requests[0]["tools"]}
    assert not session.history_turns()
    assert "return a + b" in (repair_project / "calculator.py").read_text()


@pytest.mark.parametrize("answer", ["", "n", EOFError()])
def test_cli_repair_baseline_denied(
    repair_project,
    monkeypatch,
    capsys,
    answer,
):
    """基线确认回车、n 或 EOF 默认拒绝，不请求模型、不修改文件，CLI 可继续退出。"""
    session, requests = install(monkeypatch, repair_inputs() + [answer, "/exit"])
    main()
    shown = capsys.readouterr().out
    assert "检查 0：用户拒绝" in shown and "任务未完成" in shown
    assert not requests and len(session.history_turns()) == 1
    assert "return a - b" in (repair_project / "calculator.py").read_text()


@pytest.mark.parametrize("paths,argv", [
    ("not json", '["python"]'),
    ('["calculator.py"]', "not json"),
    ('{"path":"calculator.py"}', '["python"]'),
    ('["calculator.py"]', '"python"'),
    ('[]', '["python"]'),
    ('["calculator.py"]', '[1]'),
    ('["../calculator.py"]', '["python"]'),
])
def test_cli_repair_invalid_input_keeps_loop_usable(
    repair_project,
    monkeypatch,
    capsys,
    paths,
    argv,
):
    """非法 JSON、容器、元素和路径不执行命令；提示原因后仍可退出，历史不改变。"""
    answers = ["/repair", "修复", paths]
    # 第一组 JSON 解析失败时，程序不会再询问命令。
    if paths != "not json":
        answers.append(argv)
    session, requests = install(monkeypatch, answers + ["/exit"])
    main()
    shown = capsys.readouterr().out
    assert "修复输入或配置无效" in shown and "再见" in shown
    assert "命令参数：" not in shown and not requests and not session.history_turns()


def test_cli_repair_interrupt_after_edit_reports_unverified_file(
    repair_project,
    monkeypatch,
    capsys,
):
    """复查尚未批准就中断时，CLI 输出已写文件及未完成结论并退出，不回滚或请求更多模型。"""
    session, requests = install(
        monkeypatch,
        repair_inputs() + ["yes", "yes", KeyboardInterrupt()],
        [response([edit_call("fix")]), response()],
    )
    main()
    shown = capsys.readouterr().out
    assert "已中断，程序退出" in shown and "任务未完成" in shown
    assert '已确认写入：["calculator.py"]' in shown and "检查 1：用户中断" in shown
    assert len(requests) == 2 and len(session.history_turns()) == 1
    assert_closed(session.messages_for("下一问"))
    assert "return a + b" in (repair_project / "calculator.py").read_text()


@pytest.mark.parametrize("edit_answer", ["yes", ""])
def test_repair_real_process_stdin(
    repair_project,
    edit_answer,
):
    """真实 CLI 子进程读取标准输入的批准或空行拒绝；模型用进程内模拟传输，临时文件核对实际结果。"""
    script = textwrap.dedent('''
        import json
        import sys
        import httpx
        import minicode.agent as agent
        import minicode.cli as cli
        from minicode.config import Config
        from minicode.llm import request_model

        replies = iter(json.loads(sys.argv[1]))

        def serve(request):
            """接收实际请求并返回预设模型响应，不访问网络。"""
            return httpx.Response(200, json=next(replies))

        def request_once(
            messages,
            config,
            **options,
        ):
            """用模拟传输替换网络，保留真实模型协议解析及工具范围。"""
            options["transport"] = httpx.MockTransport(serve)
            return request_model(messages, config, **options)

        cli.load_config = lambda: Config(api_key="test-key")
        agent.request_model = request_once
        cli.main()
    ''')
    answers = repair_inputs() + ["yes", edit_answer]
    if edit_answer:
        answers.append("yes")
    answers.append("/exit")
    process = subprocess.run(
        [sys.executable, "-c", script, json.dumps([response([edit_call("fix")]), response()])],
        input="\n".join(answers) + "\n",
        text=True,
        capture_output=True,
        cwd=repair_project,
        timeout=10,
    )
    assert process.returncode == 0 and "Traceback" not in process.stderr
    assert "是否写入以上修改？[y/N]" in process.stdout
    assert ("return a + b" in (repair_project / "calculator.py").read_text()) == bool(edit_answer)
    assert ("指定检查通过。" if edit_answer else "任务未完成。") in process.stdout
