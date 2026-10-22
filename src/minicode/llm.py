"""将消息列表发送给 DeepSeek，返回本次回答。"""

import httpx

from minicode.config import Config


class ModelError(Exception):
    """可向用户展示的模型请求错误，不携带原始响应或密钥。"""


def ask_model(
    messages: list[dict[str, str]], config: Config, *, transport: httpx.BaseTransport | None = None
) -> str:
    """发送调用方提供的历史和当前问题；transport 用于离线测试替换网络。"""
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
                json={
                    "model": config.model,
                    "messages": messages,
                    "stream": False,
                    "thinking": {"type": "disabled"},
                    "max_tokens": 1024,
                },
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
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice["finish_reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError("模型响应格式异常，未获得有效回答。") from None
    if finish_reason == "length":
        raise ModelError("回答达到长度限制（1024 tokens），未完整返回，请缩小问题范围。")
    if finish_reason != "stop":
        raise ModelError("模型未正常完成回答，请稍后重试。")
    if not isinstance(content, str) or not content.strip():
        raise ModelError("模型返回了空回答或非文本回答，请稍后重试。")
    return content.strip()
