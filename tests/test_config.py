"""Unit tests for configuration loading and validation."""

from pathlib import Path
import pytest

from config import ConfigError, load_config


def test_load_config_valid():
    env = {
        "TELEGRAM_TOKEN": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        "TELEGRAM_ALLOWED_USER_IDS": "111, 222, 333",
        "DB_PATH": "custom/books.db",
        "IPFS_API_URL": "http://127.0.0.1:5001/",
        "MAX_CONCURRENT_DOWNLOADS": "5",
        "DOWNLOAD_TIMEOUT": "60",
        "AUTO_ACQUIRE": "false",
        "MIRROR_ANNA": "https://annas-archive.org,https://annas-archive.li",
        "MIRROR_LIBGEN": "https://libgen.is",
        "PUBLIC_GATEWAYS": "https://ipfs.io/ipfs",
    }
    cfg = load_config(env=env)
    assert cfg.telegram_token == "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    assert cfg.allowed_user_ids == frozenset({111, 222, 333})
    assert cfg.db_path == Path("custom/books.db")
    assert cfg.ipfs_api_url == "http://127.0.0.1:5001"
    assert cfg.max_concurrent_downloads == 5
    assert cfg.download_timeout == 60.0
    assert cfg.auto_acquire is False
    assert cfg.mirror_anna == ("https://annas-archive.org", "https://annas-archive.li")
    assert cfg.mirror_libgen == ("https://libgen.is",)
    assert cfg.public_gateways == ("https://ipfs.io/ipfs",)


def test_load_config_missing_token():
    env = {
        "TELEGRAM_ALLOWED_USER_IDS": "111",
    }
    with pytest.raises(ConfigError, match="TELEGRAM_TOKEN is required"):
        load_config(env=env)


def test_load_config_empty_token():
    env = {
        "TELEGRAM_TOKEN": "   ",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
    }
    with pytest.raises(ConfigError, match="TELEGRAM_TOKEN is required"):
        load_config(env=env)


def test_load_config_empty_whitelist():
    env = {
        "TELEGRAM_TOKEN": "valid_token",
        "TELEGRAM_ALLOWED_USER_IDS": "",
    }
    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_IDS is required"):
        load_config(env=env)


def test_load_config_missing_whitelist():
    env = {
        "TELEGRAM_TOKEN": "valid_token",
    }
    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_IDS is required"):
        load_config(env=env)


def test_load_config_bad_whitelist_int():
    env = {
        "TELEGRAM_TOKEN": "valid_token",
        "TELEGRAM_ALLOWED_USER_IDS": "111, not_a_number, 333",
    }
    with pytest.raises(ConfigError, match="non-integer ID"):
        load_config(env=env)


def test_load_config_bad_integer_field():
    env = {
        "TELEGRAM_TOKEN": "valid_token",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
        "MAX_CONCURRENT_DOWNLOADS": "abc",
    }
    with pytest.raises(ConfigError, match="MAX_CONCURRENT_DOWNLOADS must be a valid integer"):
        load_config(env=env)


def test_load_config_bad_ipfs_url():
    env = {
        "TELEGRAM_TOKEN": "valid_token",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
        "IPFS_API_URL": "not_a_valid_url",
    }
    with pytest.raises(ConfigError, match="IPFS_API_URL must be a valid http or https URL"):
        load_config(env=env)


def test_load_config_bad_mirror_url():
    env = {
        "TELEGRAM_TOKEN": "valid_token",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
        "MIRROR_ANNA": "ftp://bad-scheme.com",
    }
    with pytest.raises(ConfigError, match="MIRROR_ANNA must be a valid http or https URL"):
        load_config(env=env)


def test_load_config_search_cache_ttl_12h_default():
    env = {
        "TELEGRAM_TOKEN": "valid_token",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
    }
    cfg = load_config(env=env)
    assert cfg.search_cache_ttl == 43200.0  # 12 hours

