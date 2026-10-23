from pathlib import Path

import pytest

from minicode.config import ConfigError, load_config


def test_dotenv_and_defaults():
    Path(".env").write_text("DEEPSEEK_API_KEY=test-key\n", encoding="utf-8")
    config = load_config()
    assert config.api_key == "test-key"
    assert config.model == "deepseek-flash"
    assert config.base_url == "https://api.deepseek.com"
    assert config.timeout == 60
    assert "test-key" not in repr(config)


def test_environment_overrides_dotenv(monkeypatch):
    Path(".env").write_text(
        "DEEPSEEK_API_KEY=file-key\nDEEPSEEK_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "custom-model")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT", "12.5")
    config = load_config()
    assert config.api_key == "env-key"
    assert config.model == "custom-model"
    assert config.base_url == "https://api.deepseek.com/v1"
    assert config.timeout == 12.5


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key(
    monkeypatch,
    key,
):
    if key is not None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    with pytest.raises(ConfigError, match="缺少 DEEPSEEK_API_KEY"):
        load_config()


def test_parent_dotenv_not_loaded(
    tmp_path,
    monkeypatch,
):
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=parent-key\n", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    with pytest.raises(ConfigError, match="缺少"):
        load_config()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DEEPSEEK_TIMEOUT", "0"),
        ("DEEPSEEK_TIMEOUT", "-1"),
        ("DEEPSEEK_TIMEOUT", "nan"),
        ("DEEPSEEK_TIMEOUT", "inf"),
        ("DEEPSEEK_TIMEOUT", "oops"),
        ("DEEPSEEK_MODEL", ""),
        ("DEEPSEEK_BASE_URL", "http://api.deepseek.com"),
        ("DEEPSEEK_BASE_URL", "https://user:password@example.com"),
        ("DEEPSEEK_BASE_URL", "https://example.com?key=secret"),
        ("DEEPSEEK_BASE_URL", "https://[invalid"),
        ("DEEPSEEK_API_KEY", "secret\nkey"),
        ("DEEPSEEK_API_KEY", "中文密钥"),
    ],
)
def test_invalid_config(
    monkeypatch,
    name,
    value,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_config()


def test_invalid_utf8_dotenv():
    Path(".env").write_bytes(b"\xff")
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config()
