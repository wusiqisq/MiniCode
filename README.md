# MiniCode

一个以学习为目标、使用 Python 逐步开发的终端 AI 编程助手。

项目目标是理解 Coding Agent 如何通过大模型、工具和反馈循环完成小型编程任务。核心 Agent Loop 自行实现，第一版不使用 LangChain、LangGraph 或 Agents SDK 封装完整工作流。

## 当前状态

已完成第四阶段的受限文件读取，以及第五阶段的工具注册与模型工具协议；终端仍通过 DeepSeek API 进行进程内多轮纯文本对话。支持 `/clear` 清空历史、跳过空白，以及通过 `/exit`、EOF 或 Ctrl+C 退出（包括等待模型时中断）。

每次发送此前成功的问答和当前问题；只有模型成功返回完整回答，才将这一问一答加入历史。历史仅保存在本次运行的内存中，退出后不恢复。文件工具及协议目前只供 Python 调用，尚未接入 CLI 自动执行；Agent Loop、编辑和命令工具尚未实现。当前实现已通过离线模拟测试，助手未使用真实密钥进行 API 验证。

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

普通输入会连同此前成功的问答发送到配置的模型服务，并可能产生 API 费用。缺少密钥、网络错误、超时、认证失败、余额不足、限流或服务错误会显示简短提示，然后继续等待输入；不自动重试，不输出原始错误响应或密钥。响应格式异常、空回答或未完整结束也会明确报错。失败的问题不会加入历史，之前成功的问答仍然保留。错误码说明见 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start/error_codes/)。

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

`/clear` 只清空本地历史，不读取密钥、不调用模型，也不会删除服务端已有的数据。清空后下一次请求仅包含新问题；退出重启也会从空历史开始。当前不会裁剪历史或计算 token，长对话可能增加请求开销或触及上下文限制，此时可以清空历史再提问。修改模型或接口配置时，如需开始独立会话，也请先执行 `/clear`。

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

注册表目前只允许 `read_file`，参数必须是 JSON 对象且只包含非空字符串 `relative_path`。程序指定项目根目录，工具参数不能修改它。结果包含请求标识、`success/error` 状态和完整文件文本或错误提示；非法请求不会执行读取。请求标识必须是非空字符串，否则抛出 `ValueError`。这是 Python 调用接口，不会自动发送文件内容。

## 模型工具协议（尚未接入 CLI）

`request_model(messages, config, use_tools=True)` 会向配置的服务发送一次请求，并提供 `read_file` 的工具说明。它返回 `ModelReply`：可能包含普通文本，也可能包含有序的 `ToolCall`；它只解析响应，不执行工具。默认 `use_tools=False`，CLI 使用的 `ask_model()` 仍只接收文本回答。

调用方可把 `reply.tool_calls` 交给 `dispatch_tool_calls(project_root, reply.tool_calls)`，获得有序结果。构造下一次消息列表的顺序是：原消息、`reply.as_message()`、各个 `result.as_message()`。每个结果是带 `tool_call_id` 的 tool 消息，内容用 JSON 编码 `status` 和 `content`，读取失败和空文件可以清楚区分。只有调用方显式再次调用 `request_model()` 才会发送这些结果；本阶段没有自动循环，也不把工具消息写入 Session。

**文件内容一旦被加入真实模型请求，就会发送到配置的模型服务。调用方必须在发送前明确告知用户这一数据流向。** 现有 CLI 尚未开放文件工具。协议链已通过临时文件和模拟 HTTP 响应验证，真实服务的工具调用尚未验证。后续将在第六阶段加入项目根目录入口、交互提示、Agent Loop 与停止限制。

协议依据：[DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。工具说明不能替代程序的参数和路径校验。

## 测试

在已激活的虚拟环境中、项目根目录下执行：

```bash
python -m pytest
```

测试使用模拟输入、HTTPX MockTransport 和本地子进程；隔离本地 `.env` 和相关环境变量，并禁止进程内真实 HTTP 请求，不需要网络或密钥。首次安装依赖通常需要网络。

真实 API 验证由你填写密钥后单独进行：先测试简单问答，再使用上面的暗号示例测试跨轮回答，然后执行 `/clear` 并再次询问暗号，检查清空后的行为。模型回答存在随机性，自动化测试还会直接检查 HTTP 请求中的消息列表，以确认历史确实被发送或清空。这一结果需与离线测试结果分开记录。

## 代码入口

- `pyproject.toml`：包配置、Python 版本要求和测试依赖。
- `src/minicode/__main__.py`：模块启动入口，调用 `cli.main()`。
- `src/minicode/cli.py`：输入循环，协调配置、会话和模型调用，处理清空与退出。
- `src/minicode/session.py`：构造包含历史的请求副本，保存成功的问答，清空历史。
- `src/minicode/config.py`：读取并校验配置，配置对象的文本表示隐藏密钥。
- `src/minicode/llm.py`：发送消息列表，解析文本或工具请求、转换错误提示，不执行工具或保存历史。
- `src/minicode/models.py`：模型回复与工具请求数据类，生成 assistant 消息。
- `src/minicode/tools/files.py`：校验读取范围、敏感路径及大小，返回 UTF-8 文本或 `FileReadError`。
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
