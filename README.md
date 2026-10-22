# MiniCode

一个以学习为目标、使用 Python 逐步开发的终端 AI 编程助手。

项目目标是理解 Coding Agent 如何通过大模型、工具和反馈循环完成小型编程任务。核心 Agent Loop 自行实现，第一版不使用 LangChain、LangGraph 或 Agents SDK 封装完整工作流。

## 当前状态

已实现第二阶段：在终端通过 DeepSeek API 进行单次问答。支持跳过空白，以及通过 `/exit`、EOF 或 Ctrl+C 退出（包括等待模型时中断）。

每次仅发送当前问题，不保留对话历史。Agent Loop、文件和命令工具尚未实现。当前实现已通过离线模拟测试，尚未使用真实密钥进行 API 验证。

## 安装与运行

要求 Python 3.11+。当前实际验证环境为 Python 3.12.3；其他版本尚未实际运行验证。

在项目根目录执行（以下命令适用于 Linux/macOS 的 Bash）：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

`-e` 表示以可编辑方式安装，修改 `src/minicode/` 后即可使用新代码；`[dev]` 会同时安装测试依赖 pytest。运行依赖为 HTTPX（同步 HTTP 请求）和 python-dotenv（读取 `.env`）。已有虚拟环境时，激活后重新执行安装命令即可更新依赖。

### 配置 DeepSeek

如果还没有 `.env`，先在项目根目录复制模板（`-n` 避免覆盖已有文件）：

```bash
cp -n .env.example .env
```

用编辑器打开 `.env`，自行填写密钥：

```dotenv
DEEPSEEK_API_KEY=填写你自己的DeepSeek密钥
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT=60
```

程序只读取**启动时当前工作目录**的 `.env`，不会搜索父目录；已有同名环境变量优先于 `.env`（包括空值）。`.env` 已被 Git 忽略，模板 `.env.example` 可以提交；不要将真实密钥写入模板或发到聊天中。修改 `.env` 后，下一次提问会重新读取配置。

默认模型和接口依据 [DeepSeek 官方快速开始](https://api-docs.deepseek.com/) 与 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。请求使用非流式模式、关闭思考模式，最大输出为 1024 tokens。模型名称可在 `.env` 修改，不在程序中限制可选名称。

`DEEPSEEK_TIMEOUT` 必须是大于 0 的有限秒数，控制连接、读取等 HTTP 网络操作的超时，**不是整次请求的总时限**。基础地址要求 HTTPS，不包含凭据、查询参数或片段；通常保留模板值即可。

在项目根目录启动：

```bash
python -m minicode
```

普通输入会发送到配置的模型服务，并可能产生 API 费用。缺少密钥、网络错误、超时、认证失败、余额不足、限流或服务错误会显示简短提示，然后继续等待输入；不自动重试，不输出原始错误响应或密钥。响应格式异常、空回答或未完整结束也会明确报错。错误码说明见 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start/error_codes/)。

输入 `/exit` 退出，也可按 Ctrl+C 中断；Linux/macOS 下在空输入行按 Ctrl+D 可发送 EOF，表示输入结束。

### 代理配置报错

HTTPX 默认读取 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 及其小写形式。若提示不支持 `socks://`，错误发生在创建客户端时，请求尚未发送，不能据此判断 API 密钥是否有效。

- 使用 SOCKS 代理：将对应环境变量中的 `socks://` 改为 `socks5://`，并在当前虚拟环境执行 `python -m pip install 'httpx[socks]'`。
- 使用 HTTP 或混合代理端口：按代理软件的实际设置，将代理地址设为 `http://127.0.0.1:端口`。即使目标网站是 HTTPS，HTTP 代理地址通常仍以 `http://` 开头。

不要只改协议而忽略端口支持的实际协议。程序会提示客户端配置或依赖错误并继续等待输入，不会自动替换或绕过代理。参见 [HTTPX 代理说明](https://www.python-httpx.org/advanced/proxies/) 与 [环境变量说明](https://www.python-httpx.org/environment_variables/)。

## 测试

在已激活的虚拟环境中、项目根目录下执行：

```bash
python -m pytest
```

测试使用模拟输入、HTTPX MockTransport 和本地子进程；隔离本地 `.env` 和相关环境变量，并禁止进程内真实 HTTP 请求，不需要网络或密钥。首次安装依赖通常需要网络。

真实 API 验证由你填写密钥后单独进行：启动程序，输入一个简单问题，确认显示真实回答后输入 `/exit`。这一结果需与离线测试结果分开记录。

## 代码入口

- `pyproject.toml`：包配置、Python 版本要求和测试依赖。
- `src/minicode/__main__.py`：模块启动入口，调用 `cli.main()`。
- `src/minicode/cli.py`：欢迎提示、输入循环和退出处理。
- `src/minicode/config.py`：读取并校验配置，配置对象的文本表示隐藏密钥。
- `src/minicode/llm.py`：发送单次模型请求、校验回答、转换错误提示。
- `.env.example`：不含密钥的配置模板。
- `tests/`：配置、HTTP 请求响应、交互和模块入口的离线测试。

## 第一版目标

- 在终端进行自然语言交互，保留当前会话的多轮对话。
- 接入一个支持工具调用的模型服务。
- 在指定项目内读取、搜索和编辑文件。
- 经用户确认后写入文件、执行命令和运行测试。
- 根据工具结果继续行动，并在次数限制内完成小型修改任务。
- 汇报修改内容、验证结果和未完成事项。

第一版暂缓多 Agent、长期记忆、插件系统、复杂全屏界面，以及自动 Git 提交和推送。

## 文档入口

- [开发与 AI 协作规则](AGENTS.md)
- [初步设计与开发里程碑](docs/design.md)
- [当前进度与下一步](docs/progress.md)

## 开发方式

每次只实现一个可独立验证的小功能：先明确目标和验收条件，再解释方案、实现、测试、阅读改动，最后更新进度并提交。

每次完成后，在进度文档中记录实际执行的验证与遗留事项。

## 开源许可

许可证尚未选定。公开仓库本身不等于已经授予开源许可；项目会在选定许可证后添加 `LICENSE`。
