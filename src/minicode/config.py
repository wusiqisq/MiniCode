"""读取当前工作目录的 .env；已有环境变量优先。"""

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


class ConfigError(ValueError):
    """可向用户展示的配置错误。"""


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    timeout: float = 60.0


def load_config() -> Config:
    """只读取当前目录的 .env，不向父目录搜索或修改进程环境。"""
    try:
        file_values = dotenv_values(Path.cwd() / ".env", interpolate=False)
    except (OSError, UnicodeError):
        raise ConfigError("无法读取当前目录的 .env，请检查文件权限和 UTF-8 编码。") from None

    def value(name: str, default: str = "") -> str:
        return (os.environ.get(name, file_values.get(name) or default)).strip()

    api_key = value("DEEPSEEK_API_KEY")
    if not api_key:
        raise ConfigError("缺少 DEEPSEEK_API_KEY，请在当前目录的 .env 或环境变量中填写。")
    # 密钥会进入 HTTP 请求头，禁止换行和非 ASCII 字符。
    if not api_key.isascii() or any(char.isspace() for char in api_key):
        raise ConfigError("DEEPSEEK_API_KEY 格式不正确，请检查空白和非 ASCII 字符。")

    model = value("DEEPSEEK_MODEL", "deepseek-flash")
    if not model:
        raise ConfigError("DEEPSEEK_MODEL 不能为空。")
    base_url = value("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    try:
        url = urlsplit(base_url)
        valid_url = (
            url.scheme == "https" and bool(url.hostname) and url.port in (None, 443)
            and not url.username and not url.password and not url.query and not url.fragment
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ConfigError("DEEPSEEK_BASE_URL 必须是无凭据、查询参数或片段的 HTTPS 基础地址。")

    try:
        timeout = float(value("DEEPSEEK_TIMEOUT", "60"))
    except ValueError:
        raise ConfigError("DEEPSEEK_TIMEOUT 必须是大于 0 的有限秒数。") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("DEEPSEEK_TIMEOUT 必须是大于 0 的有限秒数。")
    return Config(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
