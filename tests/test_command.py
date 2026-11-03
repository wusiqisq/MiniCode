"""命令接口使用临时项目和真实本地 Python 子进程，不访问模型或用户配置。"""

import os
import signal
import sys
import time

import pytest

from minicode.permissions import confirm_command
from minicode.tools.command import MAX_OUTPUT_BYTES, CommandError, run_command


pytestmark = pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")


def approve(request):
    """输入已校验命令快照，返回明确批准；用于本地测试命令，无其他副作用。"""
    return True


def test_exit_output_cwd_and_closed_stdin(tmp_path):
    """临时子目录执行真实 Python，验证目录、双流、关闭 stdin 和非零退出码。"""
    (tmp_path / "sub").mkdir()
    result = run_command(
        tmp_path,
        [sys.executable, "-c", "import os,sys; print(os.getcwd()); print(repr(sys.stdin.read())); print('error',file=sys.stderr); sys.exit(7)"],
        relative_cwd="sub",
        confirm=approve,
    )
    assert result.status == "exited" and result.returncode == 7
    assert result.stdout == f"{tmp_path / 'sub'}\n''\n"
    assert result.stderr == "error\n" and not result.output_truncated


@pytest.mark.parametrize("answer", [False, None, 1, "yes"])
def test_denial_has_no_side_effects(
    tmp_path,
    answer,
):
    """临时项目中模拟非严格批准；不存在标记文件证明没有启动命令。"""
    result = run_command(
        tmp_path,
        [sys.executable, "-c", "open('marker','w').write('ran')"],
        confirm=lambda request: answer,
    )
    assert result.status == "denied" and result.returncode is None
    assert not (tmp_path / "marker").exists()


def test_no_callback_denies(tmp_path):
    """无应用确认回调时默认拒绝，哪怕可执行程序不存在也不尝试启动。"""
    assert run_command(tmp_path, ["nonexistent-minicode-command"]).status == "denied"


@pytest.mark.parametrize("argv,cwd,timeout", [
    ("echo test", ".", 1), ([], ".", 1), ([""], ".", 1),
    (["python", 1], ".", 1), (["a\x00b"], ".", 1),
    (["a" * 16385], ".", 1), (["a"] * 129, ".", 1),
    (["a\ud800"], ".", 1), (["a"], "..", 1), (["a"], "/tmp", 1),
    (["a"], "", 1), (["a"], ".git", 1), (["a"], "missing", 1),
    (["a"], ".", True), (["a"], ".", 0), (["a"], ".", 121),
    (["a"], ".", 1.5),
])
def test_invalid_request_never_asks(
    tmp_path,
    argv,
    cwd,
    timeout,
):
    """参数化非法输入配合临时项目；校验失败须早于询问和启动。"""
    def unexpected(request):
        """若非法参数进入确认即使测试失败；没有正常返回值。"""
        pytest.fail("非法请求不得询问")

    with pytest.raises(CommandError):
        run_command(
            tmp_path,
            argv,
            relative_cwd=cwd,
            timeout_seconds=timeout,
            confirm=unexpected,
        )


def test_directory_boundaries_and_aliases(tmp_path):
    """临时内外链接和敏感别名验证解析后的工作目录仍须在允许范围内。"""
    (tmp_path / "outside").symlink_to(tmp_path.parent, target_is_directory=True)
    (tmp_path / ".git").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / ".git", target_is_directory=True)
    for cwd in ("outside", "alias"):
        with pytest.raises(CommandError):
            run_command(tmp_path, ["unused"], relative_cwd=cwd, confirm=approve)


def test_confirmation_cannot_change_command_or_cwd(tmp_path):
    """确认回调修改原列表不改变快照；改指目录链接则拒绝执行。"""
    argv = [sys.executable, "-c", "print('original')"]

    def mutate(request):
        """输入快照后修改调用者列表，返回批准，用于验证执行参数独立。"""
        argv[-1] = "print('changed')"
        return True

    assert run_command(tmp_path, argv, confirm=mutate).stdout == "original\n"
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "one", target_is_directory=True)

    def retarget(request):
        """确认时将目录链接改指另一个项目内目录，返回批准以触发复核。"""
        link.unlink()
        link.symlink_to(tmp_path / "two", target_is_directory=True)
        return True

    with pytest.raises(CommandError, match="变化"):
        run_command(tmp_path, argv, relative_cwd="link", confirm=retarget)


def test_shell_metacharacters_are_literal(tmp_path):
    """真实子进程接收元字符原文，shell=False 不解析分号和重定向。"""
    value = "; touch marker $(echo injected) > marker"
    result = run_command(
        tmp_path,
        [sys.executable, "-c", "import sys; print(sys.argv[1])", value],
        confirm=approve,
    )
    assert result.stdout == value + "\n"
    assert not (tmp_path / "marker").exists()


def test_large_output_is_drained_and_bounded(tmp_path):
    """双流各输出超过管道容量，验证没有死锁且合计保留字节受限。"""
    result = run_command(
        tmp_path,
        [sys.executable, "-c", "import os; os.write(1,b'x'*200000); os.write(2,b'y'*200000)"],
        confirm=approve,
    )
    assert result.status == "exited" and result.returncode == 0
    assert len(result.stdout.encode()) + len(result.stderr.encode()) == MAX_OUTPUT_BYTES
    assert result.output_truncated


def test_invalid_utf8_is_replaced(tmp_path):
    """真实子进程输出坏字节，返回可显示文本而非抛解码错误。"""
    result = run_command(
        tmp_path,
        [sys.executable, "-c", "import os; os.write(1,b'\\xff')"],
        confirm=approve,
    )
    assert result.stdout == "\ufffd"


def test_timeout_kills_descendants_with_inherited_pipes(tmp_path):
    """主进程退出、后代持有管道时仍须超时杀组，后代不能延迟写入标记。"""
    child = "import time; time.sleep(1.8); open('late','w').write('bad')"
    code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}]); print('started',flush=True)"
    started = time.monotonic()
    result = run_command(
        tmp_path,
        [sys.executable, "-c", code],
        timeout_seconds=1,
        confirm=approve,
    )
    assert result.status == "timed_out" and "started" in result.stdout
    assert time.monotonic() - started < 3
    time.sleep(1)
    assert not (tmp_path / "late").exists()


def test_running_process_timeout(tmp_path):
    """持续运行的真实进程到时被终止并回收，返回负退出码。"""
    result = run_command(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(20)"],
        timeout_seconds=1,
        confirm=approve,
    )
    assert result.status == "timed_out" and result.returncode == -signal.SIGKILL


def test_start_failure_is_clear(tmp_path):
    """批准不存在的程序后返回可读启动异常，不伪造退出成功。"""
    with pytest.raises(CommandError, match="启动失败"):
        run_command(tmp_path, ["nonexistent-minicode-command"], confirm=approve)


def test_interrupt_cleans_up_process(
    tmp_path,
    monkeypatch,
):
    """选择器等待时模拟 Ctrl+C；真实子进程必须已回收且中断继续向外传递。"""
    import minicode.tools.command as module

    original = module.subprocess.Popen
    children = []

    def track(
        *args,
        **kwargs,
    ):
        """透传启动参数，记录真实 Popen 对象供中断后验证回收，返回进程。"""
        child = original(*args, **kwargs)
        children.append(child)
        return child

    def interrupt(
        self,
        timeout=None,
    ):
        """模拟执行期间用户中断，无正常返回值。"""
        raise KeyboardInterrupt

    monkeypatch.setattr(module.subprocess, "Popen", track)
    monkeypatch.setattr(module.selectors.DefaultSelector, "select", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_command(
            tmp_path,
            [sys.executable, "-c", "import time; time.sleep(20)"],
            confirm=approve,
        )
    assert children[0].poll() == -signal.SIGKILL


@pytest.mark.parametrize("answer,expected", [("yes", True), (" Y ", True), ("", False), ("n", False)])
def test_terminal_confirmation(
    tmp_path,
    monkeypatch,
    capsys,
    answer,
    expected,
):
    """临时项目和输入替身验证先展示目录/参数/超时；控制字符不会改写终端。"""
    def respond(prompt):
        """检查输入前已展示完整请求，再返回指定用户答案。"""
        shown = capsys.readouterr().out
        assert str(tmp_path) in shown and "30 秒" in shown and "[y/N]" in prompt
        assert "\\u001b" in shown and "\x1b" not in shown and "不构成沙箱" in shown
        return answer

    monkeypatch.setattr("builtins.input", respond)
    result = run_command(
        tmp_path,
        [sys.executable, "-c", "print('\\x1b')", "\x1b"],
        confirm=confirm_command,
    )
    assert (result.status == "exited") is expected


@pytest.mark.parametrize("exception", [EOFError, KeyboardInterrupt])
def test_confirmation_eof_and_interrupt(
    tmp_path,
    monkeypatch,
    exception,
):
    """终端 EOF 默认拒绝，确认前中断直接传播，两者均不启动标记命令。"""
    def terminate(prompt):
        """接收确认提示并模拟输入关闭或中断，无正常返回值。"""
        raise exception

    monkeypatch.setattr("builtins.input", terminate)
    argv = [sys.executable, "-c", "open('marker','w').write('bad')"]
    if exception is EOFError:
        assert run_command(tmp_path, argv, confirm=confirm_command).status == "denied"
    else:
        with pytest.raises(KeyboardInterrupt):
            run_command(tmp_path, argv, confirm=confirm_command)
    assert not (tmp_path / "marker").exists()


@pytest.mark.parametrize("size,truncated", [(0, False), (MAX_OUTPUT_BYTES, False), (MAX_OUTPUT_BYTES + 1, True)])
def test_exact_output_boundary(
    tmp_path,
    size,
    truncated,
):
    """真实子进程验证空输出、恰好上限及多一字节时的截断标记和成功退出。"""
    result = run_command(
        tmp_path,
        [sys.executable, "-c", f"import os; os.write(1,b'x'*{size})"],
        confirm=approve,
    )
    assert result.returncode == 0 and result.status == "exited"
    assert len(result.stdout) == min(size, MAX_OUTPUT_BYTES)
    assert result.output_truncated is truncated


def test_each_run_requires_new_confirmation(tmp_path):
    """连续两次相同命令分别确认；第一次批准不使第二次拒绝失效。"""
    approvals = iter([True, False])
    seen = []

    def respond(request):
        """记录每次独立请求并依次返回批准和拒绝，用于验证不缓存授权。"""
        seen.append(request)
        return next(approvals)

    argv = [sys.executable, "-c", "print('ok')"]
    first = run_command(tmp_path, argv, confirm=respond)
    second = run_command(tmp_path, argv, confirm=respond)
    assert first.status == "exited" and second.status == "denied"
    assert len(seen) == 2


def test_output_collection_failure_reaps_process(
    tmp_path,
    monkeypatch,
):
    """选择器模拟读取系统错误；实际进程被回收，错误不伪装成成功。"""
    import minicode.tools.command as module

    original = module.subprocess.Popen
    children = []

    def track(
        *args,
        **kwargs,
    ):
        """启动真实进程并保留句柄，返回句柄供接口正常执行。"""
        child = original(*args, **kwargs)
        children.append(child)
        return child

    def fail(
        self,
        timeout=None,
    ):
        """接收等待参数并模拟系统收集失败，无正常返回值。"""
        raise OSError("simulated failure")

    monkeypatch.setattr(module.subprocess, "Popen", track)
    monkeypatch.setattr(module.selectors.DefaultSelector, "select", fail)
    with pytest.raises(CommandError, match="收集"):
        run_command(
            tmp_path,
            [sys.executable, "-c", "import time; time.sleep(20)"],
            confirm=approve,
        )
    assert children[0].poll() == -signal.SIGKILL
