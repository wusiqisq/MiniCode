# 0.2.0 本地发布准备

这是学习预览版，面向用户监督下的可信项目。用户选择暂不指定许可证；
本仓库和安装包不声明开源许可证。此次只构建和验证本地安装包，不自动上传 PyPI、创建 GitHub Release 或推送 Git。

## 环境与依赖

- 声明 Python 3.11+；当前实际验证 Linux/Python 3.12.3，其他版本和系统未验证。
- 命令工具依赖 POSIX 进程组，不支持 Windows 命令执行。
- 运行依赖为 HTTPX、python-dotenv；pytest 和 build 仅是开发工具。
- 安装依赖和构建隔离环境可能访问包索引；帮助、版本、退出与离线测试无需真实模型或密钥。

## 从源码安装

以下命令在解压的源码包或仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
minicode --help
minicode --version
python -m pytest -q
```

源码包包含 README、更新记录、设计/使用文档、错误练习、测试和无密钥 `.env.example`。
wheel 包含应用代码与包元数据；不包含测试、练习项目或用户配置。

## 构建 wheel 和源码包

```bash
python -m build
```

产物为 `dist/minicode-0.2.0-py3-none-any.whl` 与 `dist/minicode-0.2.0.tar.gz`。
本次同时生成 `dist/SHA256SUMS` 供核对产物校验和；重新构建后应重新计算。
构建默认先生成源码包，再从源码包构建 wheel，因此可以发现漏打包的源码。
`dist/`、虚拟环境、密钥、Git 元数据和缓存不提交。构建后应检查两个归档的成员，
仅允许根目录模板 `.env.example`，不得出现真实 `.env`、其他 `.env.*` 或私钥。

## 在独立环境检查安装

仍在仓库根目录，先记录绝对路径，再建立独立目录：

```bash
wheel_path="$PWD/dist/minicode-0.2.0-py3-none-any.whl"
release_check_dir=$(mktemp -d /tmp/minicode-install-XXXXXX)
python3 -m venv "$release_check_dir/venv"
"$release_check_dir/venv/bin/python" -m pip install "$wheel_path"
cd "$release_check_dir"
"$release_check_dir/venv/bin/minicode" --version
"$release_check_dir/venv/bin/minicode" --help
"$release_check_dir/venv/bin/python" -m minicode
```

最后一次启动后输入 `/help`、`/clear`、`/exit`，无需配置真实密钥。
确认输出版本为 0.2.0、模块来自新环境的 site-packages，而非仓库的 src。
进一步验证时，可将源码包解压到独立目录，在该目录运行测试；测试所需 pytest 需单独安装。
不要在此过程中复制真实密钥。

## 使用

```bash
minicode --project /你的/项目 --env-file /你的/配置/.env
```

两个相对路径都以启动目录为基准；未指定配置文件时读取目标项目 `.env`，环境变量优先。
工具只以项目目录作为文件范围；配置文件在项目之外不扩大工具权限。
目标项目的测试命令应使用安装了目标依赖的 Python。
模型、工具、确认、上下文与修复规则见 `docs/design.md`；运行中输入 `/help`。

## 已知限制与发布边界

- 不做真实模型验收；离线测试证明协议和程序规则，不保证模型能修好任意项目。
- 非流式输出、无退出恢复/磁盘审计、无自动回滚，编辑工具只能替换已有文本。
- 工作目录和 shell=False 不是沙箱；批准的源码、命令与测试可访问外部文件或网络。
- 读取最多 64 KiB，上下文按 128 KiB 字节而非 token 限制；搜索可跳过或截断。
- `/repair` 最多三轮，每轮默认模型 8 次、工具 16 次；普通自然语言模式仍按单轮调用上限。
- 许可证未指定；正式对外发布和平台扩展另行决定。

本次实际构建、测试和环境结果记录在 `docs/progress.md`，测试数量以该次运行输出为准。
