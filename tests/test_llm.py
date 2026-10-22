import json

import httpx
import pytest

from minicode.config import Config
from minicode.llm import ModelError, ask_model


def completion(content="你好", finish_reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


def test_request_and_supplied_history():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completion(" 模拟回答 "))

    config = Config(api_key="test-key")
    transport = httpx.MockTransport(handler)
    first = [{"role": "user", "content": "第一个问题"}]
    second = first + [
        {"role": "assistant", "content": "模拟回答"},
        {"role": "user", "content": "第二个问题"},
    ]
    for messages in (first, second):
        assert ask_model(messages, config, transport=transport) == "模拟回答"
    for request, messages in zip(requests, (first, second)):
        assert request.method == "POST"
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload == {
            "model": "deepseek-flash", "messages": messages,
            "stream": False, "thinking": {"type": "disabled"}, "max_tokens": 1024,
        }
        assert request.extensions["timeout"]["read"] == 60


@pytest.mark.parametrize("status", [302, 400, 401, 402, 422, 429, 500, 503])
def test_http_error_is_sanitized(status):
    def handler(request):
        return httpx.Response(status, text="sensitive-response test-key")

    with pytest.raises(ModelError, match=f"HTTP {status}") as error:
        ask_model(
            [{"role": "user", "content": "你好"}],
            Config(api_key="test-key"),
            transport=httpx.MockTransport(handler),
        )
    assert "sensitive-response" not in str(error.value)
    assert "test-key" not in str(error.value)


@pytest.mark.parametrize(
    ("error_type", "message"),
    [(httpx.ReadTimeout, "超时"), (httpx.ConnectError, "无法连接")],
)
def test_network_errors(error_type, message):
    def handler(request):
        raise error_type("sensitive-details", request=request)

    with pytest.raises(ModelError, match=message) as error:
        ask_model(
            [{"role": "user", "content": "你好"}],
            Config(api_key="test-key"),
            transport=httpx.MockTransport(handler),
        )
    assert "sensitive-details" not in str(error.value)


@pytest.mark.parametrize(
    "body",
    [None, [], {}, {"choices": []}, {"choices": [None]}, completion(None),
     completion(""), completion("   "), completion(123), completion("部分回答", "length"),
     completion("部分回答", "content_filter"), completion("部分回答", "aborted")],
)
def test_invalid_or_incomplete_response(body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    with pytest.raises(ModelError):
        ask_model([{"role": "user", "content": "你好"}], Config(api_key="test-key"), transport=transport)


def test_non_json_response():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="not json"))
    with pytest.raises(ModelError, match="格式异常"):
        ask_model([{"role": "user", "content": "你好"}], Config(api_key="test-key"), transport=transport)


@pytest.mark.parametrize("name", ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"])
def test_invalid_environment_proxy_is_sanitized(monkeypatch, name):
    monkeypatch.setenv(name, "socks://user:private-password@127.0.0.1:7897/")
    # 不使用 MockTransport：需要实际经过 HTTPX 创建客户端和解析环境代理的路径。
    with pytest.raises(ModelError, match="代理地址不支持 socks://") as error:
        ask_model([{"role": "user", "content": "你好"}], Config(api_key="test-key"))
    assert "private-password" not in str(error.value)
    assert "test-key" not in str(error.value)


def test_missing_socks_dependency(monkeypatch):
    def missing_dependency(**kwargs):
        raise ImportError("sensitive-details")

    monkeypatch.setattr(httpx, "Client", missing_dependency)
    with pytest.raises(ModelError, match="httpx\\[socks\\]") as error:
        ask_model([{"role": "user", "content": "你好"}], Config(api_key="test-key"))
    assert "sensitive-details" not in str(error.value)
