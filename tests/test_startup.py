"""验证命令行路径选择、配置优先级和快捷入口，不调用真实模型。"""

from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from minicode.cli import entrypoint
from minicode.config import ConfigError, load_config
from minicode.llm import request_model
from test_agent_command import response
from test_repair_demo import edit_call


def test_project_config_does_not_use_launch_directory(
    tmp_path,
):
    """tmp_path 为启动目录，目标目录有另一份配置；默认读取目标 .env，不改变进程目录。"""
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=launch-key\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("DEEPSEEK_API_KEY=project-key\n")
    assert load_config(project_root=project).api_key == "project-key"
    assert Path.cwd() == tmp_path


def test_explicit_config_is_exclusive_and_environment_wins(
    tmp_path,
    monkeypatch,
):
    """显式配置替代默认文件而非合并；环境变量仍优先，空环境值不能回退文件。"""
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=default-key\nDEEPSEEK_MODEL=default-model\n")
    config_file = tmp_path / "shared-config"
    config_file.write_text("DEEPSEEK_API_KEY=chosen-key\n")
    config = load_config(env_file=config_file)
    assert config.api_key == "chosen-key" and config.model == "deepseek-flash"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    assert load_config(env_file=config_file).api_key == "environment-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    with pytest.raises(ConfigError, match="缺少"):
        load_config(env_file=config_file)


def test_missing_default_config_can_use_environment(
    tmp_path,
    monkeypatch,
):
    """没有默认 .env 时允许仅配置环境变量；没有任何来源时报告缺少密钥。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    assert load_config(project_root=tmp_path).api_key == "environment-key"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(ConfigError, match="缺少"):
        load_config(project_root=tmp_path)


@pytest.mark.parametrize("explicit", [False, True])
def test_selected_file_reloaded_on_each_question(
    tmp_path,
    explicit,
):
    """默认项目配置和显式配置每次加载都重新读取；改文件后下一次返回新值，无缓存。"""
    path = tmp_path / ("chosen-config" if explicit else ".env")
    options = {"env_file": path} if explicit else {"project_root": tmp_path}
    path.write_text("DEEPSEEK_API_KEY=first-key\n")
    assert load_config(**options).api_key == "first-key"
    path.write_text("DEEPSEEK_API_KEY=second-key\n")
    assert load_config(**options).api_key == "second-key"


def test_removed_explicit_file_does_not_switch_to_default(tmp_path):
    """选中的文件被外部删除后明确失败，不能悄悄改用项目默认配置。"""
    path = tmp_path / "chosen-config"
    path.write_text("DEEPSEEK_API_KEY=chosen-key\n")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=default-key\n")
    assert load_config(env_file=path).api_key == "chosen-key"
    path.unlink()
    with pytest.raises(ConfigError, match="不存在"):
        load_config(env_file=path)


@pytest.mark.parametrize("kind", ["missing", "directory", "encoding", "permission"])
def test_explicit_config_errors_do_not_fall_back(
    tmp_path,
    monkeypatch,
    kind,
):
    """显式文件缺失、目录、编码或权限错误均失败，不能回退默认配置或环境变量掩盖错误。"""
    path = tmp_path / "chosen"
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=fallback-key\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    if kind == "directory":
        path.mkdir()
    elif kind == "encoding":
        path.write_bytes(b"\xff")
    elif kind == "permission":
        path.write_text("DEEPSEEK_API_KEY=test-key\n")

        def denied(
            *args,
            **kwargs,
        ):
            """接收文件读取参数并模拟权限错误，无正常返回值，避免依赖宿主用户权限。"""
            raise PermissionError

        monkeypatch.setattr("minicode.config.dotenv_values", denied)
    with pytest.raises(ConfigError):
        load_config(env_file=path)


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_help_and_version_do_not_enter_session(
    monkeypatch,
    capsys,
    flag,
):
    """帮助和版本即便附带缺失项目也应直接退出，不初始化会话、不读取配置。"""
    def forbidden(
        *args,
        **kwargs,
    ):
        """若帮助流程误入主循环或配置读取，则直接使测试失败，无正常返回值。"""
        pytest.fail("帮助或版本不应初始化会话")

    monkeypatch.setattr("minicode.cli.main", forbidden)
    monkeypatch.setattr("minicode.cli.load_config", forbidden)
    with pytest.raises(SystemExit) as error:
        entrypoint(["--project", "missing", flag])
    assert error.value.code == 0
    shown = capsys.readouterr().out
    assert "--env-file" in shown if flag == "--help" else version("minicode") in shown


@pytest.mark.parametrize("arguments", [
    ["--project", "missing"], ["--project", "not-a-directory"], ["--project", ""],
    ["--env-file", "missing"], ["--env-file", "."], ["--env-file", ""],
    ["--project"], ["--env-file"], ["--unknown"],
])
def test_invalid_arguments_fail_before_prompt(
    tmp_path,
    monkeypatch,
    capsys,
    arguments,
):
    """非法路径、缺参数或未知选项在交互前以 2 退出，不请求输入或暴露异常堆栈。"""
    (tmp_path / "not-a-directory").write_text("plain file")

    def forbidden(prompt):
        """非法启动参数不应进入输入流程，若进入则使测试失败。"""
        pytest.fail("参数错误不应开始会话")

    monkeypatch.setattr("builtins.input", forbidden)
    with pytest.raises(SystemExit) as error:
        entrypoint(arguments)
    assert error.value.code == 2
    shown = capsys.readouterr()
    assert "欢迎" not in shown.out and "Traceback" not in shown.err


def test_relative_paths_and_symlink_resolve_from_launch_directory(
    tmp_path,
    monkeypatch,
    capsys,
):
    """项目链接解析真实根目录，配置的相对路径仍以启动目录为基准，含空格和中文路径也可用。"""
    project = tmp_path / "项目 with spaces"
    project.mkdir()
    (tmp_path / "alias").symlink_to(project, target_is_directory=True)
    config_file = tmp_path / "共享 配置"
    config_file.write_text("DEEPSEEK_API_KEY=test-key\n")
    captured = []

    def capture(
        *,
        project_root,
        env_file,
    ):
        """捕获入口传给主循环的两个绝对路径，不读取密钥或执行操作，无返回值。"""
        captured.append((project_root, env_file))

    monkeypatch.setattr("minicode.cli.main", capture)
    assert entrypoint(["--project", "alias", "--env-file", "共享 配置"]) == 0
    assert captured == [(project, config_file)]
    assert Path.cwd() == tmp_path


def test_tilde_paths_expand_before_startup(
    tmp_path,
    monkeypatch,
):
    """显式 ~ 路径由入口展开；用 Path.expanduser 替身隔离真实主目录，不读取用户文件。"""
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "config"
    config.write_text("DEEPSEEK_API_KEY=test-key\n")
    captured = []

    def expand(path):
        """将测试的 ~ 路径映射到临时目录，返回 Path，不修改系统主目录变量。"""
        return tmp_path / str(path).removeprefix("~/")

    def capture(**options):
        """捕获解析后的绝对路径，无返回值，不进入交互会话。"""
        captured.append(options)

    monkeypatch.setattr(Path, "expanduser", expand)
    monkeypatch.setattr("minicode.cli.main", capture)
    assert entrypoint(["--project", "~/project", "--env-file", "~/config"]) == 0
    assert captured == [{"project_root": project, "env_file": config}]


@pytest.mark.parametrize("explicit_config", [False, True])
@pytest.mark.parametrize("repair_mode", [False, True])
def test_selected_project_reaches_tools_and_config(
    tmp_path,
    monkeypatch,
    capsys,
    explicit_config,
    repair_mode,
):
    """普通问答真实读取/修改目标文件、修复模式真实运行目标检查；HTTP 捕获所选配置，不操作启动目录同名文件。"""
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "calculator.py").write_text("launch file")
    (project / "calculator.py").write_text("return a - b")
    (project / ".env").write_text("DEEPSEEK_API_KEY=project-key\nDEEPSEEK_MODEL=project-model\n")
    shared = tmp_path / "chosen-config"
    shared.write_text("DEEPSEEK_API_KEY=chosen-key\nDEEPSEEK_MODEL=chosen-model\n")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=launch-key\n")
    captured = []
    replies = iter([response([edit_call("fix")]), response()])

    def serve(request):
        """捕获所用配置的测试值；普通问答返回编辑，修复模式基线通过不应请求模型。"""
        captured.append((request.headers["Authorization"], json.loads(request.content)["model"]))
        return httpx.Response(200, json=next(replies))

    def request_once(
        messages,
        config,
        **options,
    ):
        """透传普通模型工具参数，使用离线传输验证配置选择和实际工具路径。"""
        options["transport"] = httpx.MockTransport(serve)
        return request_model(messages, config, **options)

    if repair_mode:
        (project / "check.py").write_text("from pathlib import Path\nassert Path('calculator.py').read_text() == 'return a - b'\n")
        inputs = ["/repair", "检查加法", '["calculator.py"]',
                  json.dumps([sys.executable, "-B", "check.py"]), "yes", "/exit"]
    else:
        inputs = ["修改加法", "yes", "/exit"]
    answers = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("minicode.agent.request_model", request_once)
    arguments = ["--project", str(project)]
    if explicit_config:
        arguments += ["--env-file", str(shared)]
    assert entrypoint(arguments) == 0
    assert Path.cwd() == tmp_path
    assert (tmp_path / "calculator.py").read_text() == "launch file"
    if repair_mode:
        assert not captured and "指定检查通过" in capsys.readouterr().out
    else:
        expected = ("Bearer chosen-key", "chosen-model") if explicit_config else ("Bearer project-key", "project-model")
        assert captured == [expected, expected]
        assert (project / "calculator.py").read_text() == "return a + b"


@pytest.mark.parametrize("arguments,expected", [
    (["--help"], "--project"), (["--version"], "MiniCode"),
    ([], "欢迎使用"),
])
def test_real_module_startup(
    tmp_path,
    arguments,
    expected,
):
    """从临时目录启动真实 python -m 入口，验证帮助、版本、原始无参数启动都能无密钥退出。"""
    process = subprocess.run(
        [sys.executable, "-m", "minicode", *arguments],
        input="/exit\n",
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=5,
    )
    assert process.returncode == 0 and expected in process.stdout and not process.stderr


def test_installed_console_script_matches_module_version(tmp_path):
    """当前环境重新安装后生成的 minicode 脚本应可直接运行，版本与包元数据一致，无需密钥。"""
    executable = Path(sys.executable).with_name("minicode")
    process = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=5,
    )
    assert process.returncode == 0
    assert process.stdout.strip() == f"MiniCode {version('minicode')}"
    assert not process.stderr
