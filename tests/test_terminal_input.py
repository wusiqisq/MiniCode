"""通过真实伪终端验证方向键，不访问模型或读取用户配置。"""

import importlib.util
import json
import os
import select
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(
    os.name != "posix" or importlib.util.find_spec("readline") is None,
    reason="需要 POSIX 伪终端与 readline",
)
@pytest.mark.parametrize("text", ["hello", "你好"])
def test_terminal_editing_and_history(
    tmp_path,
    text,
):
    """用 tmp_path 隔离目录，真实发送左右键编辑、上下键回调；返回原始控制码或错误文本即失败。"""
    import pty

    master, slave = pty.openpty()
    env = dict(os.environ, TERM="xterm", INPUTRC="/dev/null", PYTHONIOENCODING="utf-8")
    # 导入 CLI 与正常启动一样加载行编辑，子进程只收集输入，不调用模型。
    script = (
        "import json; import minicode.cli; "
        "a = input('FIRST> '); print('VALUE=' + json.dumps(a)); "
        "b = input('SECOND> '); print('VALUE=' + json.dumps(b))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env=env,
    )
    os.close(slave)
    output = bytearray()

    def receive_until(marker: bytes) -> None:
        """输入预期字节标记，限时收集伪终端输出到 output；无返回值，缺失标记则使测试失败。"""
        deadline = time.monotonic() + 5
        while marker not in output:
            remaining = deadline - time.monotonic()
            assert remaining > 0, bytes(output)
            ready, _, _ = select.select([master], [], [], remaining)
            assert ready, bytes(output)
            output.extend(os.read(master, 4096))

    try:
        receive_until(b"FIRST> ")
        # 在 X 前插入 !，再向右并删除 X，检验光标移动后的实际输入值。
        os.write(master, text.encode() + b"X\x1b[D!\x1b[C\x7f\n")
        receive_until(b"SECOND> ")
        # 上键取回前一行，下键回到空行，再上键重新取回并提交。
        os.write(master, b"\x1b[A\x1b[B\x1b[A\n")
        expected = ("VALUE=" + json.dumps(text + "!")).encode()
        receive_until(expected + b"\r\n")
        while bytes(output).count(expected) < 2:
            ready, _, _ = select.select([master], [], [], 5)
            assert ready, bytes(output)
            output.extend(os.read(master, 4096))
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        os.close(master)
