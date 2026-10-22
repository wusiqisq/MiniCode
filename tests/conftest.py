"""测试隔离：不读取用户 .env，不允许真实 HTTP 访问。"""

import httpx
import pytest


@pytest.fixture(autouse=True)
def isolate_configuration_and_network(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "DEEPSEEK_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)

    def reject_network(*args, **kwargs):
        raise AssertionError("自动化测试禁止访问真实 HTTP 服务")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_network)
