"""在可信 POSIX 项目中逐次确认执行命令；不注册模型工具，不提供沙箱。"""

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Literal

from minicode.tools.files import _check_sensitive


MAX_OUTPUT_BYTES = 32 * 1024
MAX_TIMEOUT_SECONDS = 120


class CommandError(ValueError):
    """可展示的命令错误；started 标记启动后收集失败，供上层保留副作用交接。"""

    def __init__(
        self,
        message: str,
        *,
        started: bool = False,
    ) -> None:
        """输入错误说明和程序内部启动标记，初始化异常，无返回值或文件副作用。"""
        super().__init__(message)
        self.started = started


@dataclass(frozen=True)
class CommandRequest:
    """确认快照：不可变参数、解析后的项目根目录、实际工作目录及超时秒数。"""

    argv: tuple[str, ...]
    project_root: Path
    cwd: Path
    timeout_seconds: int


@dataclass(frozen=True)
class CommandRecord:
    """应用交接：已执行或批准后结果未确认的命令，不保存输出副本或推断修改文件。"""

    request: CommandRequest
    outcome: Literal["exited", "timed_out", "unknown"]
    returncode: int | None = None

    def as_dict(self) -> dict:
        """将自身转为可保存的 JSON 数据，包含实际参数、目录、超时及状态；不执行命令。"""
        return {
            "argv": list(self.request.argv),
            "cwd": str(self.request.cwd),
            "timeout_seconds": self.request.timeout_seconds,
            "outcome": self.outcome,
            "returncode": self.returncode,
        }


@dataclass(frozen=True)
class CommandResult:
    """执行交接：拒绝时无退出码；超时也可能已有副作用，截断仅影响保留输出。"""

    status: Literal["exited", "denied", "timed_out"]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False


def _prepare_request(
    project_root: str | Path,
    argv: list[str] | tuple[str, ...],
    relative_cwd: str,
    timeout_seconds: int,
) -> CommandRequest:
    """校验参数列表和项目内目录，返回供用户确认的不可变快照。

    argv 限 128 项、合计 16 KiB UTF-8；目录拒绝绝对路径、.. 和敏感路径。
    超时须为 1 到 120 的整数秒。非法输入抛出 CommandError，不启动进程。
    """
    if os.name != "posix":
        raise CommandError("当前命令执行接口仅支持 POSIX 系统。")
    if not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 128:
        raise CommandError("argv 必须是包含 1 到 128 项的参数列表。")
    if any(not isinstance(arg, str) or "\x00" in arg for arg in argv) or not argv[0].strip():
        raise CommandError("命令名称必须非空，所有参数必须是无空字符的字符串。")
    try:
        if sum(len(arg.encode("utf-8")) for arg in argv) > 16 * 1024:
            raise CommandError("命令参数超过 16 KiB 上限。")
    except UnicodeEncodeError:
        raise CommandError("命令参数必须能编码为 UTF-8。") from None
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise CommandError("超时必须是 1 到 120 的整数秒。")
    if not isinstance(relative_cwd, str) or not relative_cwd.strip() or "\x00" in relative_cwd:
        raise CommandError("工作目录必须是非空相对路径。")
    requested = Path(relative_cwd)
    if requested.is_absolute() or ".." in requested.parts:
        raise CommandError("工作目录不能为绝对路径或包含 ..。")
    try:
        _check_sensitive(requested)
        root = Path(project_root).resolve(strict=True)
        cwd = (root / requested).resolve(strict=True)
        if not root.is_dir() or not cwd.is_dir() or not cwd.is_relative_to(root):
            raise CommandError("工作目录必须是项目内已有目录。")
        _check_sensitive(cwd.relative_to(root))
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        if isinstance(error, CommandError):
            raise
        raise CommandError("无法使用该项目或工作目录（缺失、越界或敏感路径）。") from None
    return CommandRequest(
        tuple(argv),
        root,
        cwd,
        timeout_seconds,
    )


def _kill_group(process: subprocess.Popen) -> None:
    """输入本接口创建的进程，强制终止同组进程；组已消失则忽略，无返回值。

    即便直接子进程已经退出，后代仍可能持有输出管道，因此始终尝试终止进程组。
    主动另建会话的后代可逃离进程组，本接口不提供对抗性进程隔离。
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_command(
    project_root: str | Path,
    argv: list[str] | tuple[str, ...],
    *,
    relative_cwd: str = ".",
    timeout_seconds: int = 30,
    confirm: Callable[[CommandRequest], bool] | None = None,
) -> CommandResult:
    """校验并逐次确认命令，返回退出码及有界输出；拒绝时不启动。

    输入为项目目录、参数列表、相对工作目录、整数超时和应用提供的确认回调。
    只有回调严格返回 True 才执行；无回调默认拒绝。确认后重新检查目录解析结果。
    stdout/stderr 合计保留前 32 KiB，继续排空多余数据避免阻塞，按 UTF-8 替换坏字节。
    副作用：获准命令继承环境，可修改文件或访问网络；stdin 关闭，不运行交互命令。
    参数/启动/收集失败抛 CommandError；中断清理进程组后向外传播，不回滚副作用。
    """
    request = _prepare_request(
        project_root,
        argv,
        relative_cwd,
        timeout_seconds,
    )
    if confirm is None or confirm(request) is not True:
        return CommandResult("denied")
    # 不再使用调用者可能在确认期间修改的列表，也不允许目录链接改指其他目标。
    current = _prepare_request(
        project_root,
        request.argv,
        relative_cwd,
        timeout_seconds,
    )
    if current != request:
        raise CommandError("确认期间项目或工作目录发生变化，请重新请求。")
    try:
        process = subprocess.Popen(
            request.argv,
            cwd=request.cwd,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError):
        raise CommandError("命令启动失败，请检查可执行程序和工作目录。") from None

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    retained = 0
    truncated = False
    timed_out = False
    deadline = time.monotonic() + request.timeout_seconds
    try:
        with selectors.DefaultSelector() as selector:
            for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            # 管道 EOF 与主进程退出都满足才算完成；后代持管道也受同一超时限制。
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    kept = chunk[:MAX_OUTPUT_BYTES - retained]
                    buffers[key.data].extend(kept)
                    retained += len(kept)
                    truncated = truncated or len(kept) < len(chunk)
        if timed_out:
            _kill_group(process)
        process.wait()
    except OSError:
        raise CommandError(
            "收集命令输出失败；已尝试终止进程组，命令可能已有副作用。",
            started=True,
        ) from None
    finally:
        # 正常退出时也清理仍在同组运行但已关闭管道的后代；不等待后代持有的管道。
        _kill_group(process)
        process.wait()
        process.stdout.close()
        process.stderr.close()
    return CommandResult(
        "timed_out" if timed_out else "exited",
        process.returncode,
        bytes(buffers["stdout"]).decode("utf-8", errors="replace"),
        bytes(buffers["stderr"]).decode("utf-8", errors="replace"),
        truncated,
    )
