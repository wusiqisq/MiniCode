# MiniCode 修复练习

这是一个故意含有错误的项目：`calculator.py` 的 `add()` 使用了减法。
`check_calculator.py` 固定检查三个加法行为；只修源码，不改检查条件。
无需安装第三方依赖。

先在终端复制一份到新的临时目录，避免修改仓库中的原始错误样例：

```bash
cd /home/wusiqi/my-project/MiniCode
demo_dir=$(mktemp -d /tmp/minicode-repair-XXXXXX)
cp examples/repair_demo/calculator.py examples/repair_demo/check_calculator.py "$demo_dir/"
cp -n .env.example "$demo_dir/.env"
cd "$demo_dir"
```

自行编辑新目录的 `.env` 填写密钥；不要发送或提交密钥。
然后先在终端运行基线检查：

```bash
/home/wusiqi/my-project/MiniCode/.venv/bin/python -B check_calculator.py
```

预期：运行 3 项检查，其中 2 项失败，退出码非零。`-B` 避免生成字节码缓存，
使快速修改后重新运行直接读取源码。然后启动 MiniCode：

```bash
/home/wusiqi/my-project/MiniCode/.venv/bin/python -m minicode
```

在 `你 >` 后输入 `/repair`。接下来的三次输入依次为：

```text
修复 calculator.py 的加法错误，不修改检查脚本。
["calculator.py"]
["/home/wusiqi/my-project/MiniCode/.venv/bin/python", "-B", "check_calculator.py"]
```

每次核对差异、命令和目录后才输入 `y`。输入 `n` 或回车应停止本轮，
拒绝前已经发生的修改仍保留。最终应为 3 项检查全部通过、退出码 0，
并且 `calculator.py` 使用 `return a + b`，检查脚本保持不变。
如果本机需代理，请按主 README 配置；上述命令不会自动修改代理。

程序先运行基线检查，再把失败结果交给模型；每轮修改后重新运行相同命令。
最多修复 3 轮，仍失败就停止。最后由程序根据实际退出码生成结论，不采信模型声称通过。
模型不能通过编辑工具修改名单外的检查脚本，也不能执行其他命令；检查源码差异后再批准。
每轮仍受模型 8 次、模型工具 16 次及连续失败 3 次等上限约束，任一限制触发即停止整体流程。

离线测试会复制样例到 pytest 临时目录，通过模拟 HTTP 驱动真实 Agent Loop、编辑和本地子进程；
不使用 `.env` 或真实 API。`tests/test_repair_demo.py` 验证普通 Agent 的基础链路，
`tests/test_repair.py` 与 `tests/test_cli_repair.py` 验证有限修复控制器和真实终端输入。
它们能证明程序链路与限制，不保证真实模型每次都能找到正确修复。
