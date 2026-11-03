# MiniCode

一个以学习为目标、使用 Python 逐步开发的终端 AI 编程助手。

项目目标是理解 Coding Agent 如何通过大模型、工具和反馈循环完成小型编程任务。核心 Agent Loop 自行实现，第一版不使用 LangChain、LangGraph 或 Agents SDK 封装完整工作流。

## 当前状态

已完成第十阶段测试与有限修复的实现及离线验证：终端通过 DeepSeek API 进行多轮对话，模型可以请求搜索、读取、经确认修改文件及运行命令，程序校验并执行，再把结果交给模型继续回答。支持 `/repair` 固定测试与最多三轮修复、`/clear` 清空历史，以及 `/exit`、EOF 或 Ctrl+C 退出。

成功整轮保存在本次运行内存中；发生实际写入、命令执行、批准后结果未确认或用户拒绝的停止轮次，也保留完整工具结果和程序停止说明。纯只读失败仍丢弃当前轮，退出不恢复。已通过离线模拟测试；助手未使用真实密钥进行 API 验证。搜索及按整轮裁剪旧历史的上下文限制已实现；已提供 Python 修改预览与逐次确认写入接口；主 CLI 已接入逐次确认编辑及命令执行，命令当前仅支持 POSIX，已在 Linux 验证。

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

在提供 Python 标准库 `readline` 的交互终端中，左右方向键可移动光标，上下方向键可切换本次运行输入过的内容（包括确认输入）。输入历史不写入磁盘，退出后不恢复；它与发送给模型的会话历史不同，`/clear` 只清空模型会话。没有 `readline` 的平台仍可使用基本输入，但不保证方向键编辑。修改代码后需退出并重新启动 MiniCode 才会生效。

启动时会显示项目根目录和数据发送提示。普通输入会连同上下文限制内保留的问答、程序交接、工具请求和工具结果（包括读取的文件内容和搜索片段）发送到配置的模型服务，并可能产生 API 费用。请仅在可信项目中使用；敏感路径排除不能识别所有秘密。缺少密钥、网络错误、超时、认证失败、余额不足、限流或服务错误会显示简短提示，然后继续等待输入；不自动重试，不输出原始错误响应或密钥。响应格式异常、空回答或未完整结束也会明确报错。纯只读失败不会加入历史；有写入影响或拒绝的停止会保留交接，之前的历史仍然保留。错误码说明见 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start/error_codes/)。

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

注册表允许 `read_file`、`search_project` 和经确认的 `edit_file`。读取参数必须是 JSON 对象且只包含非空字符串 `relative_path`；搜索参数见下节。程序指定项目根目录，工具参数不能修改它。结果包含请求标识、`success/error/denied/skipped` 状态和内容。`denied` 表示未获授权，`skipped` 表示停止后的请求未继续执行；非法请求不执行文件操作。请求标识必须是非空字符串，否则抛出 `ValueError`。这是 Python 调用接口，不会自动发送文件内容。

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

成功整轮加入 Session；纯只读失败不保存，有写入影响或拒绝的停止保留工具与程序交接，保留下来的消息会随后续问题发送。`/clear` 可清空这些本地历史，不能撤回已经发送给服务的数据。次数限制按每个问题重新计数，不是 token、费用或总耗时上限；每次请求另外受上下文预算约束，长对话会按整轮裁剪旧历史，也可用 `/clear` 重置。没有磁盘日志或退出恢复。

循环已用临时文件和模拟 HTTP 验证；真实模型服务的工具调用尚未验证。协议依据：[DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。工具说明和 system 消息不能替代程序的参数与路径校验。

## 上下文限制

CLI 的 Agent Loop 在**每次调用模型前**检查上下文，默认上限为 **128 KiB**。计量对象是 `{"messages": ..., "tools": ...}` 的紧凑 UTF-8 JSON，包含应用规则、用户问题、历史、工具请求、工具结果和工具说明；中文、JSON 转义及工具结果的外层封装都计入。这不是字符数、token 数或整个 HTTP 请求体的上限；模型名称等其他请求参数和模型响应不计入，也不保证一定满足服务端的 token 限额。

超限时从最早的成功轮次开始移除，保留最新的连续历史。一个轮次指一次用户问题、该轮全部 assistant/tool 消息及最终回答；不会留下缺少工具请求的结果，也不会只删掉一个结果。模型会收到旧历史已省略的提示，这段提示也计入预算。本轮的当前问题和工具消息始终完整保留，不截断文件或搜索结果来凑预算。

例如，旧问题 A、较新问题 B 和当前问题 C 一起超限时，先省略 A 的整轮。若工具执行后再次超限，再省略 B。若仅应用规则、工具说明和 C 的本轮消息就超限，程序在发出下一次请求前返回“任务未完成”，保留本地旧历史；已完成的读取不代表文件内容已成功发给模型。可缩短问题，或先在更小目录内搜索，减少本轮需要读取的内容。`/clear` 只能清空旧历史，不能解决当前轮本身过大的问题。

获得最终回答后，Session 同时移除本轮已省略的旧轮次并保存当前成功轮次。纯只读失败、达到限制或中断不修改历史；有写入影响或用户拒绝的停止则补齐完整工具结果并保存程序交接，但不裁剪旧历史。CLI 会报告省略轮次数。最终回答在请求结束后保存，因此本功能约束的是发送时的上下文，并非进程内存或 Session 保存字节的硬上限；下一轮请求会重新检查。

Python 调用 `run_agent(..., max_context_bytes=128 * 1024)` 可调整预算，只接受正整数；CLI 使用默认值，本次未增加 `.env` 配置。直接调用底层 `request_model()` / `ask_model()` 不自动裁剪，调用者需自行管理上下文。本阶段不引入 tokenizer、摘要压缩或真实模型请求。

## 文件修改预览（Python 接口）

`preview_edit()` 本身仅生成单文件修改预览；CLI 和模型现已通过下面的 edit_file 流程接入确认写入。例如可以预览给会话初始化增加一条注释：

```python
from minicode.tools.edit import EditPreviewError, preview_edit

try:
    preview = preview_edit(
        project_root="/home/wusiqi/my-project/MiniCode",
        relative_path="src/minicode/session.py",
        old_text="self._turns: list[list[dict]] = []",
        new_text="self._turns: list[list[dict]] = []  # 按成功轮次保存历史",
    )
    print(preview.diff)
except EditPreviewError as error:
    print(error)
```

`preview_edit()` 依次检查文本参数、受限读取原文件、唯一定位原文本、在内存中替换并生成统一差异。返回只读的 `EditPreview` 数据对象：`relative_path` 是请求路径，`original_content` 和 `updated_content` 是完整修改前后文本，`diff` 是带行号和上下文的差异。快照还绑定解析后的项目和目标路径，以及文件身份、大小、时间、权限等状态。`-` 行表示原内容，`+` 行表示建议新增的内容；路径标签用 JSON 引号转义，没有末尾换行时明确标记。

匹配区分大小写、空白和换行，支持跨行文本，不使用正则表达式。`old_text` 不能为空，且必须恰好匹配一处，重叠匹配也算多处；`new_text` 可以为空，表示删除这段内容，不代表删除文件。未找到、多处匹配、原文与替换文本相同，都明确失败。

沿用受限读取的项目边界、敏感路径、普通 UTF-8 文本及 64 KiB 限制，项目内符号链接可以只读预览；替换参数各至多 64 KiB，修改后文件也至多 64 KiB。完整差异最多 128 KiB，超限直接报错，不截断。例如接近文件上限的单行即使只改一个字符，差异也可能因为同时包含旧新两行而超限。

此接口不创建或写入文件、不发送网络请求、不更新会话，也不注册为模型工具。预览只是读取时的快照，不能作为用户授权或未来无冲突的证明；写入必须经下面的 `apply_edit()` 重新校验路径、展示差异、逐次确认并检查外部修改。暂不支持创建文件、多文件修改或批量替换。

## 确认与安全写入（Python 接口）

`apply_edit(project_root, preview, confirm=confirm_edit)` 串联确认与写入。这里传入的是**确认函数**，不是模型提供的 `confirmed=true`：`apply_edit()` 决定何时请求授权，`permissions.confirm_edit()` 负责展示项目根目录、文件相对路径和完整差异，再等待终端用户回答。

下面示例只操作自动清理的临时目录，可以独立试验拒绝和批准：

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.permissions import confirm_edit
from minicode.tools.edit import EditApplyError, EditPreviewError, apply_edit, preview_edit

with TemporaryDirectory() as directory:
    root = Path(directory)
    path = root / "demo.py"
    path.write_text("value = 1\n", encoding="utf-8")
    try:
        preview = preview_edit(
            root,
            "demo.py",
            "value = 1",
            "value = 2",
        )
        result = apply_edit(
            root,
            preview,
            confirm=confirm_edit,
        )
        print(result.message)
        print(path.read_text(encoding="utf-8"))
    except (EditPreviewError, EditApplyError) as error:
        print(error)
```

只接受终端输入 `y` 或 `yes`（忽略大小写和两端空白）；空输入、EOF 或其他回答都拒绝，返回 `EditApplyResult(status="denied", ...)`，不创建临时文件。Ctrl+C 向外传递。每次调用重新询问，文件或模型输出中的“同意”只是数据。终端展示会转义 ESC、回车和双向格式字符等控制内容，实际写入文本保持原字节。

程序先验证预览内容、重新生成差异，确保展示与实际建议写入一致；随后核对项目和目标路径、文件身份和原文。确认后再次检查，在同目录创建 `.minicode-edit-*` 临时文件，完整写入 UTF-8、保留普通权限位并同步，替换前再检查一次，最后用 `os.replace()` 替换目标。成功返回 `status="applied"`。普通准备或替换失败不会先截断原文件，并清理临时文件；若清理也失败，会明确报错提示检查目录。

检测到外部修改、相同内容的文件被替换、权限变化或项目不一致时拒绝，通常抛出 `EditConflictError`（`EditApplyError` 的子类）；目标消失等路径/读写错误也抛出 `EditApplyError`。需要重新生成预览并重新确认，不能复用成功写入后的旧预览。所有错误提示都不包含文件正文或底层系统错误详情。

写入只支持已有、可写的普通单文件；拒绝相对路径中的 `..`、任意相对路径段的符号链接、多个硬链接和特殊权限位。只读预览仍允许项目内符号链接。空的新内容会保留一个空文件，不删除文件。初版不保留属主、ACL 或扩展属性，不保证崩溃后目录项已经持久化；最后检查和替换之间仍有竞争窗口，因此只面向受监督的可信项目，不是安全沙箱。

独立调用 `apply_edit()` 不更新 Session；主 CLI 的模型编辑工具已接入下面的记录与交接策略。

## 模型编辑与停止交接

在项目根目录执行 `python -m minicode` 后，可提出：“读取某个非敏感源码文件，把其中唯一的一段原文本改成新文本；先展示差异，等我确认。”模型通过 `edit_file` 请求 `relative_path`、`old_text` 和 `new_text` 三个字符串。程序独立校验，生成快照和差异，再调用终端确认；模型不能设置 `confirmed`、项目根目录或确认函数。

每次编辑都重新询问，批准只对当前预览有效。工具 `success` 仅在写入接口返回 applied 后产生；拒绝是 `denied`，冲突/非法参数/写入失败是 `error`。程序使用内部 `changed_path` 记录实际成功写入，不以模型的口头回答为证据。没有应用确认接口时编辑默认拒绝。Python 使用 `run_agent(..., edit_confirm=confirm_edit)` 接入确认，底层 `dispatch_tool(..., edit_confirm=...)` 同样需要应用显式提供。

**拒绝立即停止本轮**，不再请求模型，也不执行整批中的剩余工具；剩余结果补为 `skipped`。再次编辑需要用户开启新的问题并重新确认。单个普通工具错误仍可回传模型纠正，但不能绕过用户拒绝。

文件写入后，模型续问仍可能失败或达到限制。此时程序保留实际完成的工具结果，为未完成的请求补齐结果，并追加以 `MiniCode 程序记录：` 开头的本地停止说明；它是应用交接，不是模型生成的最终回答。此前旧历史不裁剪，下一轮可继续看到完整请求/结果配对。CLI 会说明已写入的路径以及“修改不会自动回滚”。单独使用 `dispatch_tool_calls()` 时只返回结果列表、不管理会话，调用方负责交接；它也会在拒绝后跳过余下整批。

批准后若在编辑调用内部 Ctrl+C，不能可靠区分中断发生在替换前还是替换后：程序保守保存 `error` 结果并提示“写入结果未确认，请重新读取检查”，不声称没有写入。如果已明确获得 applied，之后模型请求才中断，则按已知写入交接。`AgentInterrupted` 仍是 `KeyboardInterrupt`，CLI 打印交接后退出。确认之前中断且无此前写入影响时，沿用原有不保存当前轮的行为。

这些记录只在进程内存中；`/clear`、成功后的历史裁剪、退出或崩溃可能移除本地记录，都不会撤回已写入文件或模型服务已收到的数据。没有磁盘审计日志、自动回滚、自动重试或文件删除；模型命令接入及交接见下一节。本阶段使用模拟 HTTP 配合临时文件验证了实际编辑，真实服务的 edit_file 请求仍需用非敏感文件单独验证。

## 经确认的命令执行（模型与 Python 接口）

模型工具 `run_command(argv, relative_cwd=".", timeout_seconds=30)` 已接入 Agent Loop；主 CLI 传入 `confirm_command`，执行前逐次询问。程序仍独立校验模型参数，不接受模型传入 confirmed、project_root 或其他额外字段。当前仅支持 POSIX，已在 Linux 验证。底层 Python 接口如下：

```python
import sys
from pathlib import Path

from minicode.permissions import confirm_command
from minicode.tools.command import run_command

result = run_command(
    Path.cwd(),
    [sys.executable, "-c", "print('hello MiniCode')"],
    relative_cwd=".",
    timeout_seconds=30,
    confirm=confirm_command,
)
print(result.status, result.returncode)
print(result.stdout)
print(result.stderr)
```

输入经过校验后转换成不可变的 `CommandRequest`，展示解析后的工作目录、JSON 参数列表和超时。只有当次输入 y/yes 才执行；无回调、空输入或 EOF 均拒绝。应用自定义回调必须严格返回 `True`。参数列表最多 128 项、合计 16 KiB UTF-8；工作目录必须在项目内，拒绝 `..`、绝对路径及默认敏感路径，确认后再次检查目录解析结果。

使用 `Popen(shell=False)`，不解析管道、重定向或通配符；stdin 关闭，运行非交互命令。超时默认 30 秒，可指定 1–120 的整数秒。双流并行收集，合计保留前 32 KiB 原始字节，超限仍排空管道防止死锁；`output_truncated` 标记丢弃。UTF-8 无效或被截断的字符替换为 `�`，因此解码后重新编码的字节数可能略大于原始保留量。

`status` 为 `denied`、`exited` 或 `timed_out`；`exited` 只代表进程退出，需结合 `returncode == 0` 判断命令是否成功。拒绝时退出码为 `None`。超时强制终止进程组并回收直接子进程，返回截至截止时已收集的输出；中断和收集错误也清理进程组。正常结束也清理仍留在同组的后代。不自动重试。参数、目录和启动等错误抛出 `CommandError`；Ctrl+C 清理后继续向外传播。

命令继承当前环境，可能修改文件、读取项目外路径或访问网络；不能在参数中放密钥。目录校验只约束启动工作目录，不能限制命令参数及程序行为。`shell=False` 不构成沙箱，也不阻止显式运行解释器或 shell。进程组不能捕获主动另建会话逃离的后代；目录检查仍有并发替换窗口。只用于用户监督下的可信项目。超时、中断或失败不撤销命令副作用。直接调用底层接口的应用自行处理交接；Agent Loop 的策略如下。

`ToolResult` 只有在正常退出且退出码为 0 时返回 success，非零退出、超时或收集失败为 error，拒绝为 denied。输出回传包括实际 argv、工作目录、超时、outcome、returncode、stdout、stderr 和 output_truncated；这部分数据可能发给模型。普通错误仍可由模型继续分析，但下一条命令必须重新获得授权。命令拒绝立即停止本轮，不续问模型，剩余批次补 skipped。

`CommandRecord` 是程序生成的内部交接，`AgentResult.commands` 返回本轮有执行影响的命令；不会推断命令究竟修改了哪些文件。正常退出为 exited，超时为 timed_out，启动后收集失败或批准后中断而未取得明确结果为 unknown。批准前中断与明确启动失败不记录为已执行；批准后在启动窗口中断则保守报告 unknown，不能断言没有执行。

已有命令记录后，即使遇模型网络/协议错误、上下文或次数限制、中断，仍保存整轮工具协议及程序停止说明，此前历史不裁剪。中断补齐当前请求的 error 和剩余请求的 skipped，CLI 展示交接后退出。成功轮次按原规则提交历史裁剪；CLI 另行展示程序生成的命令结果，模型最终回答不等同于命令或测试成功。记录仅在内存，clear/裁剪/退出可能移除记录，不撤销副作用。

可以在可信的小型示例项目中启动 `python -m minicode`，输入“请运行 Python 打印 hello，不修改文件”。先检查模型提出的实际参数、目录和超时，再选择 y/yes 或拒绝。模型请求和最终表述不确定；验收使用模拟模型和真实临时目录子进程，未请求真实 API。完整的固定测试与有限修复见下节及[修复练习](examples/repair_demo/README.md)。

## 固定测试与有限修复

在待修复项目目录启动 MiniCode，输入 `/repair`，按提示依次提供：

```text
修复目标 > 修复加法，不修改检查脚本
允许编辑的文件（JSON 字符串列表） > ["calculator.py"]
测试命令（JSON 参数列表） > ["/home/wusiqi/my-project/MiniCode/.venv/bin/python", "-B", "check_calculator.py"]
```

上例使用仓库提供的练习项目；自己的项目请替换文件和测试命令。
命令的每个参数分别写为 JSON 字符串，不写 shell 管道或 `&&`。
应使用安装了目标项目测试依赖的 Python，可与运行 MiniCode 的解释器不同。

- 程序先确认并运行基线检查，通过则直接汇报，无需调用模型或修改文件。
- 非零退出后开始修复。模型只开放读取、搜索和允许文件的编辑；程序独立拦截其他文件编辑和模型命令请求。
- 每轮模型结束后重新确认、执行同一条检查；即使模型没有修改或声称成功，也必须实际复查。
- 默认最多修复 3 轮（首次加最多 2 次重试），最多检查 4 次；每轮模型最多 8 次、模型工具最多 16 次、连续工具失败最多 3 次。达到任一限制立即停止整体修复，不开启新轮绕过。
- CLI 固定项目根目录为工作目录、每次检查超时 30 秒；每次编辑和检查分别确认，空输入/EOF 默认拒绝。拒绝、超时、启动/收集错误、模型故障或中断均停止，不自动重试异常操作。
- 最终结论由程序根据实际检查生成：命令、轮数、文件、逐次状态和退出码、最后一次有界输出。`passed` 仅表示指定命令正常退出且退出码为零，不表示测试充分或所有需求完成；非零退出也可能是依赖或命令错误。

只把源码放进允许编辑名单，不包括检查脚本。该名单限制模型的编辑工具，不是沙箱；用户批准的测试程序仍可能修改文件或访问外部资源，模型提出的源码也要检查差异。
命令和允许路径由程序冻结，模型不能用另一条命令替代验证；超时和停止不回滚已经发生的影响。

Python 接口为 `minicode.repair.run_repair()`：`editable_paths` 使用非空相对路径元组，`test_argv` 使用参数列表或元组；可指定 `relative_cwd`、`timeout_seconds`、`max_repair_attempts` 和每轮资源上限。确认接口与普通 Agent 相同，缺失默认拒绝。返回 `RepairResult`；Ctrl+C 抛 `RepairInterrupted` 并携带同样的交接结果。

修复在独立工作会话中请求模型，期间可临时省略旧历史；结束时把完整流程合并成原会话的一轮，不删除原有历史。下一次普通问答仍可能按整轮裁剪。上下文字节检查保守计入全部工具说明，实际修复请求仅发送三个工具说明；不计算 token，也不承诺内存硬上限。普通自然语言问答继续使用原 Agent Loop，三轮修复规则仅在 `/repair` 中生效。

## 测试

在已激活的虚拟环境中、项目根目录下执行：

```bash
python -m pytest
```

测试使用模拟输入、HTTPX MockTransport 和本地子进程；隔离本地 `.env` 和相关环境变量，并禁止进程内真实 HTTP 请求，不需要网络或密钥。首次安装依赖通常需要网络。

单独运行修复样例的集成验收：`python -m pytest -q tests/test_repair_demo.py`。
其中模型回复为模拟数据，编辑和检查命令在临时项目中真实执行；原始错误样例保持不变。
有限修复控制器与终端入口验收：`python -m pytest -q tests/test_repair.py tests/test_cli_repair.py`。

真实 API 验证由你填写密钥后单独进行：先测试简单问答，再使用上面的暗号示例测试跨轮回答，然后执行 `/clear` 并再次询问暗号，检查清空后的行为。Agent Loop 可用上面的 `session.py` 读取示例单独验证，再尝试读取不存在的文件，检查失败说明；请勿用真实敏感文件做测试。模型回答存在随机性，自动化测试还会直接检查 HTTP 请求中的消息列表，以确认历史确实被发送或清空。这一结果需与离线测试结果分开记录。

## 代码入口

- `pyproject.toml`：包配置、Python 版本要求和测试依赖。
- `src/minicode/__main__.py`：模块启动入口，调用 `cli.main()`。
- `src/minicode/cli.py`：输入循环，展示数据发送提示和项目根目录，协调配置、会话和 Agent Loop，处理清空与退出。
- `src/minicode/agent.py`：连接模型、工具及应用确认，执行停止限制，保留文件、命令副作用或拒绝的交接。
- `src/minicode/repair.py`：冻结检查命令和允许编辑路径，控制修复轮数、实际复查、程序汇报与中断交接。
- `src/minicode/session.py`：按完整轮次保存问答/工具与停止交接，深拷贝消息，提交裁剪或清空历史。
- `src/minicode/context.py`：计量消息及工具说明的 JSON 字节，选择完整历史轮次，拒绝当前轮超限。
- `src/minicode/config.py`：读取并校验配置，配置对象的文本表示隐藏密钥。
- `src/minicode/llm.py`：发送消息列表，解析文本或工具请求、转换错误提示，不执行工具或保存历史。
- `src/minicode/models.py`：模型回复与工具请求数据类，生成 assistant 消息。
- `src/minicode/tools/files.py`：校验读取范围、敏感路径及大小，返回 UTF-8 文本或 `FileReadError`。
- `src/minicode/tools/search.py`：受限目录遍历和字面搜索，返回匹配行、跳过数及截断标记。
- `src/minicode/tools/edit.py`：唯一文本替换预览、文件快照、授权后冲突检查和临时文件替换。
- `src/minicode/tools/command.py`：命令请求和执行交接、参数/目录校验、进程组清理及有界双流收集。
- `src/minicode/permissions.py`：安全展示编辑差异或命令参数，逐次收集终端确认，不直接执行操作。
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
