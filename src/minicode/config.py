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
    """模型配置：api_key 是密钥（不参与对象打印），其余字段是模型、地址和超时秒数。"""

    api_key: str = field(repr=False)
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    timeout: float = 60.0


def load_config() -> Config:
    """读取、校验模型配置，返回可供模型客户端使用的 Config。

    输入：无显式参数；读取当前目录 .env 和进程环境变量，环境变量优先。
    输出：包含密钥、模型、HTTPS 基础地址和超时秒数的 Config 对象。
    异常：读取失败、缺少密钥或配置格式错误时抛出 ConfigError。
    不会修改环境变量，也不会发送网络请求或输出密钥。
    """
    try:
        file_values = dotenv_values(Path.cwd() / ".env", interpolate=False)
    except (OSError, UnicodeError):
        raise ConfigError("无法读取当前目录的 .env，请检查文件权限和 UTF-8 编码。") from None

    def value(
        name: str,
        default: str = "",
    ) -> str:
        """按优先级取一个配置项，并返回去掉两端空白的字符串。

        输入：name 是配置名，default 是未配置时的默认字符串。
        输出：环境变量优先，其次 .env，最后默认值；显式空环境变量仍优先。
        读取外层已加载的 file_values，不修改任何配置。
        """
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
    return Config(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )
