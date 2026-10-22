"""离线验证交互行为和已安装包的模块入口。"""

import subprocess
import sys

import pytest

from minicode.cli import main


def test_normal_input_keeps_waiting(monkeypatch, capsys):
    inputs = iter(["解释一个函数", "再解释一个", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    main()

    output = capsys.readouterr().out
    assert "欢迎使用 MiniCode" in output
    assert output.count("已收到输入，模型功能尚未接入。") == 2
    assert output.endswith("再见！\n")


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_input_is_skipped(monkeypatch, capsys, blank):
    inputs = iter([blank, "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    main()

    output = capsys.readouterr().out
    assert "已收到输入" not in output
    assert "再见！" in output


@pytest.mark.parametrize("command", ["/exit", "  /exit  "])
def test_exit_command(monkeypatch, capsys, command):
    monkeypatch.setattr("builtins.input", lambda prompt: command)

    main()

    output = capsys.readouterr().out
    assert "已收到输入" not in output
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
        timeout=5,
    )

    assert result.returncode == 0
    assert "欢迎使用 MiniCode" in result.stdout
    assert "你 > " in result.stdout
    assert "再见！" in result.stdout
    assert result.stdout.count("已收到输入") == (1 if user_input else 0)
    assert result.stderr == ""
