# MiniCode

一个以学习为目标、使用 Python 逐步开发的终端 AI 编程助手。

项目目标是理解 Coding Agent 如何通过大模型、工具和反馈循环完成小型编程任务。核心 Agent Loop 自行实现，第一版不使用 LangChain、LangGraph 或 Agents SDK 封装完整工作流。

## 当前状态

已完成第七阶段项目搜索与上下文限制：终端通过 DeepSeek API 进行多轮对话，模型可以请求搜索、读取启动目录内的文件，程序校验并执行，再把结果交给模型继续回答。支持 `/clear` 清空历史，以及 `/exit`、EOF 或 Ctrl+C 退出。

成功整轮的问题、工具请求、工具结果和最终回答保存在本次运行的内存中，退出后不恢复。失败或达到限制时丢弃当前轮，保留此前成功历史。已通过离线模拟测试；助手未使用真实密钥进行 API 验证。搜索及按整轮裁剪旧历史的上下文限制已实现；编辑和命令工具尚未实现。

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

启动时会显示项目根目录和数据发送提示。普通输入会连同上下文限制内保留的成功问答、工具请求和工具结果（包括读取的文件内容和搜索片段）发送到配置的模型服务，并可能产生 API 费用。请仅在可信项目中使用；敏感路径排除不能识别所有秘密。缺少密钥、网络错误、超时、认证失败、余额不足、限流或服务错误会显示简短提示，然后继续等待输入；不自动重试，不输出原始错误响应或密钥。响应格式异常、空回答或未完整结束也会明确报错。失败的问题不会加入历史，之前成功的问答仍然保留。错误码说明见 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start/error_codes/)。

输入 `/exit` 退出，也可按 Ctrl+C 中断；Linux/macOS 下在空输入行按 Ctrl+D 可发送 EOF，表示输入结束。

### 多轮对话与清空历史

```text
你 > 请记住暗号：紫色河马739。
MiniCode > （模型回答）
你 > 我刚才告诉你的暗号是什么？
MiniCode > （模型根据随请求发送的历史回答）
你 > /clear
MiniCode > 已清空对话历史。
```

`/clear` 只清空本地历史，不读取密钥、不调用模型，也不会删除服务端已有的数据。清空后下一次请求只包含应用规则和新问题；退出重启也会从空历史开始。当前会按完整轮次裁剪较早历史以控制请求字节量，不计算 token；被移除的对话将不再提供给模型。仍可清空历史再提问。修改模型或接口配置时，如需开始独立会话，也请先执行 `/clear`。

### 代理配置报错

HTTPX 默认读取 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 及其小写形式。若提示不支持 `socks://`，错误发生在创建客户端时，请求尚未发送，不能据此判断 API 密钥是否有效。

- 使用 SOCKS 代理：将对应环境变量中的 `socks://` 改为 `socks5://`，并在当前虚拟环境执行 `python -m pip install 'httpx[socks]'`。
- 使用 HTTP 或混合代理端口：按代理软件的实际设置，将代理地址设为 `http://127.0.0.1:端口`。即使目标网站是 HTTPS，HTTP 代理地址通常仍以 `http://` 开头。

不要只改协议而忽略端口支持的实际协议。程序会提示客户端配置或依赖错误并继续等待输入，不会自动替换或绕过代理。参见 [HTTPX 代理说明](https://www.python-httpx.org/advanced/proxies/) 与 [环境变量说明](https://www.python-httpx.org/environment_variables/)。

## 受限文件读取

安装后可在 Python 中直接调用：

```python
from minicode.tools.files import FileReadError, read_file

try:
    text = read_file("/home/wusiqi/my-project/MiniCode", "src/minicode/session.py")
    print(text)
except FileReadError as error:
    print(error)
```

第一个参数显式指定项目根目录，第二个参数必须是相对路径。返回完整的 UTF-8 文本，保留换行和空白；空文件返回空字符串。文件最多 64 KiB（按字节计），超限直接拒绝，不返回截断内容。目录、特殊文件、含空字节或无效 UTF-8 的文件会报错；这不是通用文件类型检测器。

路径解析后必须仍在项目内；项目内的符号链接可以读取，指向项目外的链接会被拒绝。请求路径和解析后的路径均检查敏感名称：`.env` 及 `.env.*`（包括 `.env.example`）、`.git`、`.ssh`、`.aws`、`.azure`、`.gnupg`、`.kube`、常见凭据和 secrets 文件、常见 SSH 私钥名，以及 `.key`、`.pem`、`.p12`、`.pfx`、`.keystore` 后缀。完整名单见 `tools/files.py`。

该规则不能识别任意名称文件或普通源码中包含的全部秘密；路径检查也不构成抵御并发恶意替换文件或目录的沙箱，仅用于可信本地项目。`read_file()` 本身不会将读取内容写入对话历史或发送到模型服务；调用方把内容加入模型请求前，必须告知用户文件内容可能发送到配置的模型服务。

## 离线工具分发

```python
from minicode.tools.registry import dispatch_tool

result = dispatch_tool(
    "/home/wusiqi/my-project/MiniCode",
    "call_1",
    "read_file",
    '{"relative_path": "src/minicode/session.py"}',
)
print(result.tool_call_id, result.status, result.content)
```

注册表允许 `read_file` 和 `search_project`。读取参数必须是 JSON 对象且只包含非空字符串 `relative_path`；搜索参数见下节。程序指定项目根目录，工具参数不能修改它。结果包含请求标识、`success/error` 状态和完整文件文本或错误提示；非法请求不会执行读取。请求标识必须是非空字符串，否则抛出 `ValueError`。这是 Python 调用接口，不会自动发送文件内容。

## 受限项目搜索

模型可以调用 `search_project`，也可以在 Python 中直接使用：

```python
from minicode.tools.search import SearchError, search_project

result = search_project("/home/wusiqi/my-project/MiniCode", "record_messages", "src")
print(result)
```

`query` 是 1 至 200 字符的非空单行字面文本，区分大小写，不使用正则表达式；`relative_path` 是可选的相对目录路径，默认为 `"."`。根目录仍由程序指定。CLI 中可提问：“在 src 中搜索 record_messages，再读取找到的文件，解释它如何保存会话。”

返回值包括：

- `matches`：每个匹配行返回一次，包含相对路径 `path`、从 1 开始的行号 `line`、至多 300 字符的片段 `text` 和片段裁剪标记 `text_truncated`。
- `scanned_files`：尝试受限读取的文件数，包括读取失败的文件。
- `skipped_entries`：跳过的条目数，包括敏感路径、排除目录、符号链接、特殊文件，以及无法读取的目录或文件；跳过目录时不统计内部条目。
- `truncated` / `limit`：是否因扫描或输出上限提前停止，以及原因 `entries`、`files`、`matches` 或 `output_bytes`；未提前停止时为 `false` / `null`。

扫描上限是 2000 个目录条目和 200 个文件；返回至多 50 个匹配行，结果 JSON 编码后至多 16 KiB（注册表的外层消息封装另计）。匹配片段围绕该行首次命中位置。遍历顺序取决于文件系统，不保证排序；达到限制时可缩小搜索目录或使用更具体的搜索词。

搜索复用文件读取规则：敏感路径检查、项目真实路径边界、每文件 64 KiB 上限、UTF-8 和空字节检查。递归时不跟随符号链接；显式搜索目录按真实路径校验，允许解析到项目内的普通目录。默认跳过 `.venv`、`venv`、`node_modules`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache` 目录，不解析 `.gitignore`。无法读取或超过大小限制的文件会跳过，不当作工具整体失败。

没有匹配不等于整个项目不存在相关内容：请同时检查截断标记、跳过数和搜索范围。这些限制控制单次搜索工作量和结果；整轮消息及历史另外受下述上下文限制。搜索片段会作为工具结果发送到配置的模型服务；直接调用 Python 搜索函数本身不发送网络请求。安全边界与文件读取相同，仅用于可信项目。

## Agent Loop 与模型工具协议

CLI 调用 `run_agent(prompt, session, config, project_root)`。`request_model(..., use_tools=True)` 每次只请求一次，返回文本或工具请求；`dispatch_tool()` 独立校验、执行工具，模型本身不能直接执行函数。`ask_model()` 保留纯文本 Python 接口，CLI 已改用 Agent Loop。

一次读取任务的消息顺序为：用户问题 → assistant 工具请求 → 对应编号的 tool 结果 → assistant 最终回答。模型可以连续或一次请求多个文件，程序按顺序执行。工具结果 JSON 明确包含 `success/error` 和内容，空文件与失败可以区分；出错结果也会交给模型，以便调整请求。

例如，在项目根目录启动后输入：

```text
你 > 读取 src/minicode/session.py，解释它如何保存会话。
```

每轮默认最多请求模型 8 次、执行工具 16 次；连续 3 次工具错误即停止，工具成功重置失败计数。最后一次模型回复若仍要求工具，就停止而不读取；整批工具超过剩余预算时整批不执行。达到限制会显示“任务未完成”，不会当成成功回答。网络或协议错误立即停止，不自动重试；可以继续输入新问题。

成功整轮才加入 Session；失败或停止不保存当前轮，保留下来的成功工具消息会随后续问题发送。`/clear` 可清空这些本地历史，不能撤回已经发送给服务的数据。次数限制按每个问题重新计数，不是 token、费用或总耗时上限；每次请求另外受上下文预算约束，长对话会按整轮裁剪旧历史，也可用 `/clear` 重置。没有磁盘日志或退出恢复。

循环已用临时文件和模拟 HTTP 验证；真实模型服务的工具调用尚未验证。协议依据：[DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。工具说明和 system 消息不能替代程序的参数与路径校验。

## 上下文限制

CLI 的 Agent Loop 在**每次调用模型前**检查上下文，默认上限为 **128 KiB**。计量对象是 `{"messages": ..., "tools": ...}` 的紧凑 UTF-8 JSON，包含应用规则、用户问题、历史、工具请求、工具结果和工具说明；中文、JSON 转义及工具结果的外层封装都计入。这不是字符数、token 数或整个 HTTP 请求体的上限；模型名称等其他请求参数和模型响应不计入，也不保证一定满足服务端的 token 限额。

超限时从最早的成功轮次开始移除，保留最新的连续历史。一个轮次指一次用户问题、该轮全部 assistant/tool 消息及最终回答；不会留下缺少工具请求的结果，也不会只删掉一个结果。模型会收到旧历史已省略的提示，这段提示也计入预算。本轮的当前问题和工具消息始终完整保留，不截断文件或搜索结果来凑预算。

例如，旧问题 A、较新问题 B 和当前问题 C 一起超限时，先省略 A 的整轮。若工具执行后再次超限，再省略 B。若仅应用规则、工具说明和 C 的本轮消息就超限，程序在发出下一次请求前返回“任务未完成”，保留本地旧历史；已完成的读取不代表文件内容已成功发给模型。可缩短问题，或先在更小目录内搜索，减少本轮需要读取的内容。`/clear` 只能清空旧历史，不能解决当前轮本身过大的问题。

只有获得最终回答后，Session 才会同时移除本轮已省略的旧轮次并保存当前成功轮次；失败、达到限制或中断都不修改原历史。CLI 会报告省略轮次数。最终回答在请求结束后保存，因此本功能约束的是发送时的上下文，并非进程内存或 Session 保存字节的硬上限；下一轮请求会重新检查。

Python 调用 `run_agent(..., max_context_bytes=128 * 1024)` 可调整预算，只接受正整数；CLI 使用默认值，本次未增加 `.env` 配置。直接调用底层 `request_model()` / `ask_model()` 不自动裁剪，调用者需自行管理上下文。本阶段不引入 tokenizer、摘要压缩或真实模型请求。

## 测试

在已激活的虚拟环境中、项目根目录下执行：

```bash
python -m pytest
```

测试使用模拟输入、HTTPX MockTransport 和本地子进程；隔离本地 `.env` 和相关环境变量，并禁止进程内真实 HTTP 请求，不需要网络或密钥。首次安装依赖通常需要网络。

真实 API 验证由你填写密钥后单独进行：先测试简单问答，再使用上面的暗号示例测试跨轮回答，然后执行 `/clear` 并再次询问暗号，检查清空后的行为。Agent Loop 可用上面的 `session.py` 读取示例单独验证，再尝试读取不存在的文件，检查失败说明；请勿用真实敏感文件做测试。模型回答存在随机性，自动化测试还会直接检查 HTTP 请求中的消息列表，以确认历史确实被发送或清空。这一结果需与离线测试结果分开记录。

## 代码入口

- `pyproject.toml`：包配置、Python 版本要求和测试依赖。
- `src/minicode/__main__.py`：模块启动入口，调用 `cli.main()`。
- `src/minicode/cli.py`：输入循环，展示数据发送提示和项目根目录，协调配置、会话和 Agent Loop，处理清空与退出。
- `src/minicode/agent.py`：连接模型和工具，执行每轮停止限制，返回完成或停止结果。
- `src/minicode/session.py`：按成功轮次分组并深拷贝消息，成功时提交历史裁剪和当前轮，清空历史。
- `src/minicode/context.py`：计量消息及工具说明的 JSON 字节，选择完整历史轮次，拒绝当前轮超限。
- `src/minicode/config.py`：读取并校验配置，配置对象的文本表示隐藏密钥。
- `src/minicode/llm.py`：发送消息列表，解析文本或工具请求、转换错误提示，不执行工具或保存历史。
- `src/minicode/models.py`：模型回复与工具请求数据类，生成 assistant 消息。
- `src/minicode/tools/files.py`：校验读取范围、敏感路径及大小，返回 UTF-8 文本或 `FileReadError`。
- `src/minicode/tools/search.py`：受限目录遍历和字面搜索，返回匹配行、跳过数及截断标记。
- `src/minicode/tools/registry.py`：注册及描述工具，校验并顺序分发请求，将结果转换为带编号的 tool 消息。
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
