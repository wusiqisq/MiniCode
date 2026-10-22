"""将消息列表发送给 DeepSeek，解析文本或工具请求，不执行工具。"""

import httpx

from minicode.config import Config
from minicode.models import ModelReply, ToolCall
from minicode.tools.registry import tool_definitions


class ModelError(Exception):
    """可向用户展示的模型请求错误，不携带原始响应或密钥。"""


def ask_model(
    messages: list[dict[str, str]], config: Config, *, transport: httpx.BaseTransport | None = None
) -> str:
    """保留 CLI 的纯文本接口，不向模型开放工具。"""
    reply = request_model(messages, config, transport=transport)
    # 未开放工具时，响应解析器保证返回非空文本。
    assert reply.content is not None
    return reply.content


def request_model(
    messages: list[dict], config: Config, *, use_tools: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> ModelReply:
    """只发出一次请求；工具需显式开启，调用方负责告知用户数据流向。"""
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "thinking": {"type": "disabled"},
        "max_tokens": 1024,
    }
    if use_tools:
        payload["tools"] = tool_definitions()
        payload["tool_choice"] = "auto"
    try:
        client = httpx.Client(timeout=config.timeout, transport=transport)
    except ValueError:
        raise ModelError(
            "HTTP 客户端配置无效，请检查 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY"
            "（含小写变量）。代理地址不支持 socks://；请按代理实际协议使用 "
            "http:// 或 socks5://。"
        ) from None
    except ImportError:
        raise ModelError(
            "SOCKS 代理缺少依赖，请在当前虚拟环境执行 "
            "python -m pip install 'httpx[socks]'。"
        ) from None

    try:
        with client:
            response = client.post(
                f"{config.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {config.api_key}"},
                json=payload,
            )
    except httpx.TimeoutException:
        raise ModelError("请求超时，请稍后重试，或调整 DEEPSEEK_TIMEOUT。") from None
    except httpx.RequestError:
        raise ModelError("无法连接模型服务，请检查网络和 DEEPSEEK_BASE_URL。") from None

    if not response.is_success:
        error_messages = {
            400: "请求格式错误，请检查模型名称和接口配置。",
            401: "身份验证失败，请检查 DEEPSEEK_API_KEY。",
            402: "DeepSeek 账户余额不足，请检查账户。",
            422: "请求参数无效，请检查模型名称和接口配置。",
            429: "请求受到限流，请稍后重试。",
        }
        message = error_messages.get(response.status_code, "模型服务请求失败，请稍后重试。")
        raise ModelError(f"{message}（HTTP {response.status_code}）")

    try:
        body = response.json()
    except ValueError:
        raise ModelError("模型响应格式异常，未获得有效回答。") from None
    return _parse_reply(body, use_tools=use_tools)


def _parse_reply(body: object, *, use_tools: bool) -> ModelReply:
    try:
        choices = body["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        choice = choices[0]
        message = choice["message"]
        content = message["content"]
        finish_reason = choice["finish_reason"]
        if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
            raise ValueError
        calls = message.get("tool_calls")
        if calls is not None and not isinstance(calls, list):
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError("模型响应格式异常，未获得有效回答。") from None
    if finish_reason == "length":
        raise ModelError("回答达到长度限制（1024 tokens），未完整返回，请缩小问题范围。")
    if finish_reason == "tool_calls":
        if not use_tools:
            raise ModelError("当前请求未开放工具，不能处理模型工具请求。")
        if not calls or (content is not None and not isinstance(content, str)):
            raise ModelError("模型工具请求格式异常，未执行工具。")
        parsed = []
        ids = set()
        try:
            for call in calls:
                call_id = call["id"]
                function = call["function"]
                name = function["name"]
                arguments = function["arguments"]
                if (
                    call["type"] != "function"
                    or not isinstance(call_id, str) or not call_id.strip() or call_id in ids
                    or not isinstance(name, str) or not name.strip()
                    or not isinstance(arguments, str)
                ):
                    raise ValueError
                ids.add(call_id)
                parsed.append(ToolCall(call_id, name, arguments))
        except (ValueError, KeyError, TypeError):
            raise ModelError("模型工具请求格式异常，未执行工具。") from None
        return ModelReply(content, tuple(parsed))
    if finish_reason != "stop":
        raise ModelError("模型未正常完成回答，请稍后重试。")
    if calls:
        raise ModelError("模型结束标志与工具请求不一致，未执行工具。")
    if not isinstance(content, str) or not content.strip():
        raise ModelError("模型返回了空回答或非文本回答，请稍后重试。")
    return ModelReply(content.strip())
