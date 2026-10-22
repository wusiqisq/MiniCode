"""离线验证交互行为和已安装包的模块入口。"""

import subprocess
import sys
import os

import pytest

from minicode.cli import main
from minicode.config import Config
from minicode.llm import ModelError


def test_normal_input_keeps_waiting(monkeypatch, capsys):
    inputs = iter(["解释一个函数", "再解释一个", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))
    prompts = []

    def fake_ask(prompt, config):
        prompts.append(prompt)
        return "模拟模型回答"

    monkeypatch.setattr("minicode.cli.ask_model", fake_ask)

    main()

    output = capsys.readouterr().out
    assert "欢迎使用 MiniCode" in output
    assert output.count("MiniCode > 模拟模型回答") == 2
    assert prompts == ["解释一个函数", "再解释一个"]
    assert output.endswith("再见！\n")


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_input_is_skipped(monkeypatch, capsys, blank):
    inputs = iter([blank, "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    main()

    output = capsys.readouterr().out
    assert "正在等待模型回答" not in output
    assert "缺少 DEEPSEEK_API_KEY" not in output
    assert "再见！" in output


@pytest.mark.parametrize("command", ["/exit", "  /exit  "])
def test_exit_command(monkeypatch, capsys, command):
    monkeypatch.setattr("builtins.input", lambda prompt: command)

    main()

    output = capsys.readouterr().out
    assert "正在等待模型回答" not in output
    assert "缺少 DEEPSEEK_API_KEY" not in output
    assert output.endswith("再见！\n")


@pytest.mark.parametrize(
    ("exception", "message"),
    [(EOFError, "再见！"), (KeyboardInterrupt, "已中断，程序退出。")],
)
def test_input_termination(monkeypatch, capsys, exception, message):
    def interrupted_input(prompt):
        raise exception

    monkeypatch.setattr("builtins.input", interrupted_input)

    main()

    captured = capsys.readouterr()
    assert message in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("user_input", ["你好\n\n/exit\n", ""])
def test_module_entrypoint(tmp_path, user_input):
    # 在项目以外的目录启动，确认导入来自安装结果。
    result = subprocess.run(
        [sys.executable, "-m", "minicode"],
        input=user_input,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if not key.startswith("DEEPSEEK_")},
        timeout=5,
    )

    assert result.returncode == 0
    assert "欢迎使用 MiniCode" in result.stdout
    assert "你 > " in result.stdout
    assert "再见！" in result.stdout
    assert result.stdout.count("缺少 DEEPSEEK_API_KEY") == (1 if user_input else 0)
    assert result.stderr == ""


def test_model_error_allows_next_question(monkeypatch, capsys):
    inputs = iter(["问题一", "问题二", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))

    def fake_ask(prompt, config):
        if prompt == "问题一":
            raise ModelError("请求超时")
        return "恢复后的回答"

    monkeypatch.setattr("minicode.cli.ask_model", fake_ask)
    main()
    output = capsys.readouterr().out
    assert "请求超时" in output
    assert "恢复后的回答" in output
    assert output.endswith("再见！\n")


def test_interrupt_during_request(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "你好")
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))

    def interrupted_request(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("minicode.cli.ask_model", interrupted_request)
    main()
    captured = capsys.readouterr()
    assert "已中断，程序退出。" in captured.out
    assert captured.err == ""


def test_invalid_proxy_does_not_crash_cli(monkeypatch, capsys):
    inputs = iter(["你好", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7897/")

    main()

    captured = capsys.readouterr()
    assert "代理地址不支持 socks://" in captured.out
    assert captured.out.endswith("再见！\n")
    assert captured.err == ""
