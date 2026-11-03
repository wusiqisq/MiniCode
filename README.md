# MiniCode

一个用于学习和实践 Coding Agent 的 Python 终端编程助手，接入 DeepSeek，帮助你理解项目、修改代码并运行测试。

## 主要功能

- 多轮对话，读取和搜索项目文件。
- 展示修改差异，经确认后写入文件或执行命令。
- 固定测试与有限修复：最多修复 3 轮，根据实际结果汇报。
- 支持项目目录、配置文件选择，以及终端帮助和进度提示。

## 快速开始

需要 Python 3.11+，当前在 Linux 上验证。在仓库目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp -n .env.example .env
```

在 `.env` 中填写自己的 `DEEPSEEK_API_KEY`，然后启动：

```bash
minicode --project /你的/项目 --env-file .env
```

输入 `/help` 查看帮助，`/repair` 启动有限修复，`/clear` 清空对话，`/exit` 退出。

## 说明

当前为 0.2.0 学习预览版，会话退出后不恢复。问题、读取内容和命令输出会发送到配置的模型服务，请仅在可信项目中使用。许可证暂不指定。

更多信息：[安装与构建](docs/release.md) · [修复练习](examples/repair_demo/README.md) · [更新记录](CHANGELOG.md)
