"""离线验证交互行为和已安装包的模块入口。"""

import subprocess
import sys
import os
import json

import httpx
import pytest

from minicode.cli import main
from minicode.config import Config, ConfigError
from minicode.llm import ModelError, ask_model


def test_normal_input_keeps_waiting(monkeypatch, capsys):
    inputs = iter(["解释一个函数", "再解释一个", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))
    prompts = []

    def fake_ask(messages, config):
        prompts.append(messages)
        return "模拟模型回答"

    monkeypatch.setattr("minicode.cli.ask_model", fake_ask)

    main()

    output = capsys.readouterr().out
    assert "欢迎使用 MiniCode" in output
    assert output.count("MiniCode > 模拟模型回答") == 2
    assert prompts == [
        [{"role": "user", "content": "解释一个函数"}],
        [
            {"role": "user", "content": "解释一个函数"},
            {"role": "assistant", "content": "模拟模型回答"},
            {"role": "user", "content": "再解释一个"},
        ],
    ]
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


@pytest.mark.parametrize("user_input", ["你好\n\n/exit\n", "/clear\n/exit\n", ""])
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
    assert result.stdout.count("缺少 DEEPSEEK_API_KEY") == (1 if "你好" in user_input else 0)
    if "/clear" in user_input:
        assert "已清空对话历史。" in result.stdout
    assert result.stderr == ""


def test_model_error_allows_next_question(monkeypatch, capsys):
    inputs = iter(["问题一", "问题二", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))

    def fake_ask(messages, config):
        if messages[-1]["content"] == "问题一":
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


@pytest.mark.parametrize("clear_command", ["/clear", "  /clear  "])
def test_clear_needs_no_config_or_request(monkeypatch, capsys, clear_command):
    inputs = iter([clear_command, clear_command, "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    def unexpected_call(*args):
        pytest.fail("清空命令不应加载模型配置或发送请求")

    monkeypatch.setattr("minicode.cli.load_config", unexpected_call)
    monkeypatch.setattr("minicode.cli.ask_model", unexpected_call)
    main()
    output = capsys.readouterr().out
    assert output.count("已清空对话历史。") == 2
    assert output.endswith("再见！\n")


def test_history_failure_and_clear_through_http(monkeypatch, capsys):
    inputs = iter(["记住暗号739", "失败问题", "暗号是什么", "/clear", "新问题", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))
    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload["messages"][-1]["content"] == "失败问题":
            return httpx.Response(503, text="service unavailable")
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "模拟回答"}, "finish_reason": "stop"
        }]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "minicode.cli.ask_model",
        lambda messages, config: ask_model(messages, config, transport=transport),
    )
    main()

    saved_turn = [
        {"role": "user", "content": "记住暗号739"},
        {"role": "assistant", "content": "模拟回答"},
    ]
    assert [payload["messages"] for payload in payloads] == [
        [{"role": "user", "content": "记住暗号739"}],
        saved_turn + [{"role": "user", "content": "失败问题"}],
        saved_turn + [{"role": "user", "content": "暗号是什么"}],
        [{"role": "user", "content": "新问题"}],
    ]
    captured = capsys.readouterr()
    assert "HTTP 503" in captured.out
    assert "已清空对话历史" in captured.out
    assert captured.err == ""


def test_configuration_failure_is_not_saved(monkeypatch, capsys):
    inputs = iter(["缺密钥的问题", "有效问题", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    # 第一次保持缺少密钥，第二次模拟用户补好配置。
    configs = iter([ConfigError("缺少密钥"), Config(api_key="test-key")])

    def config_for_turn():
        config = next(configs)
        if isinstance(config, ConfigError):
            raise config
        return config

    captured_messages = []
    monkeypatch.setattr("minicode.cli.load_config", config_for_turn)

    def fake_ask(messages, config):
        captured_messages.append(messages)
        return "回答"

    monkeypatch.setattr("minicode.cli.ask_model", fake_ask)
    main()
    assert captured_messages == [[{"role": "user", "content": "有效问题"}]]
    assert "缺少密钥" in capsys.readouterr().out


def test_new_cli_run_does_not_restore_history(monkeypatch, capsys):
    inputs = iter(["旧问题", "/exit", "新问题", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr("minicode.cli.load_config", lambda: Config(api_key="test-key"))
    requests = []

    def fake_ask(messages, config):
        requests.append(messages)
        return "模拟回答"

    monkeypatch.setattr("minicode.cli.ask_model", fake_ask)
    main()
    main()
    assert requests == [
        [{"role": "user", "content": "旧问题"}],
        [{"role": "user", "content": "新问题"}],
    ]
    assert capsys.readouterr().out.count("欢迎使用 MiniCode") == 2
