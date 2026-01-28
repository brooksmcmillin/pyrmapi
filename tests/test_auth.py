"""Tests for the native authentication module."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import yaml

from pyrmapi.native import (
    AuthClient,
    AuthError,
    AuthTokens,
    ConfigError,
    DeviceRegistrationError,
    TokenRefreshError,
)
from pyrmapi.native.auth import (
    CONFIG_FILE_MODE,
    DEFAULT_CONFIG_NAME,
    DEVICE_TOKEN_URL,
    USER_TOKEN_URL,
)


class TestAuthTokens:
    """Tests for AuthTokens model."""

    def test_create_with_both_tokens(self) -> None:
        """Test creating AuthTokens with both tokens."""
        tokens = AuthTokens(device_token="dev123", user_token="user456")
        assert tokens.device_token == "dev123"
        assert tokens.user_token == "user456"

    def test_create_with_device_token_only(self) -> None:
        """Test creating AuthTokens with only device token."""
        tokens = AuthTokens(device_token="dev123")
        assert tokens.device_token == "dev123"
        assert tokens.user_token == ""

    def test_create_with_aliases(self) -> None:
        """Test creating AuthTokens using YAML-compatible aliases."""
        tokens = AuthTokens.model_validate(
            {"devicetoken": "dev123", "usertoken": "user456"}
        )
        assert tokens.device_token == "dev123"
        assert tokens.user_token == "user456"


class TestAuthClientConfig:
    """Tests for AuthClient configuration handling."""

    def test_default_config_path(self, tmp_path: Path) -> None:
        """Test default config path resolution."""
        with patch("pyrmapi.native.auth.Path.home", return_value=tmp_path):
            client = AuthClient()
            assert client.config_path == tmp_path / DEFAULT_CONFIG_NAME

    def test_custom_config_path(self, tmp_path: Path) -> None:
        """Test custom config path."""
        custom_path = tmp_path / "custom_config"
        client = AuthClient(config_path=custom_path)
        assert client.config_path == custom_path

    def test_env_config_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test config path from environment variable."""
        env_path = tmp_path / "env_config"
        monkeypatch.setenv("RMAPI_CONFIG", str(env_path))
        client = AuthClient()
        assert client.config_path == env_path

    def test_load_tokens_file_not_exists(self, tmp_path: Path) -> None:
        """Test loading tokens when config file doesn't exist."""
        client = AuthClient(config_path=tmp_path / "nonexistent")
        result = client.load_tokens()
        assert result is None
        assert client.tokens is None

    def test_load_tokens_success(self, tmp_path: Path) -> None:
        """Test successfully loading tokens from config file."""
        config_file = tmp_path / ".rmapi"
        config_file.write_text(
            yaml.safe_dump({"devicetoken": "dev123", "usertoken": "user456"})
        )

        client = AuthClient(config_path=config_file)
        tokens = client.load_tokens()

        assert tokens is not None
        assert tokens.device_token == "dev123"
        assert tokens.user_token == "user456"
        assert client.tokens == tokens

    def test_load_tokens_empty_file(self, tmp_path: Path) -> None:
        """Test loading tokens from empty config file."""
        config_file = tmp_path / ".rmapi"
        config_file.write_text("")

        client = AuthClient(config_path=config_file)
        result = client.load_tokens()
        assert result is None

    def test_load_tokens_invalid_yaml(self, tmp_path: Path) -> None:
        """Test loading tokens from invalid YAML file."""
        config_file = tmp_path / ".rmapi"
        config_file.write_text("invalid: yaml: content:")

        client = AuthClient(config_path=config_file)
        with pytest.raises(ConfigError, match="Failed to parse config"):
            client.load_tokens()

    def test_save_tokens_success(self, tmp_path: Path) -> None:
        """Test successfully saving tokens to config file."""
        config_file = tmp_path / ".rmapi"
        tokens = AuthTokens(device_token="dev123", user_token="user456")

        client = AuthClient(config_path=config_file)
        client.save_tokens(tokens)

        assert config_file.exists()
        assert config_file.stat().st_mode & 0o777 == CONFIG_FILE_MODE

        content = yaml.safe_load(config_file.read_text())
        assert content["devicetoken"] == "dev123"
        assert content["usertoken"] == "user456"

    def test_save_tokens_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Test that save_tokens creates parent directories."""
        config_file = tmp_path / "subdir" / "nested" / ".rmapi"
        tokens = AuthTokens(device_token="dev123", user_token="user456")

        client = AuthClient(config_path=config_file)
        client.save_tokens(tokens)

        assert config_file.exists()

    def test_save_tokens_no_tokens_error(self, tmp_path: Path) -> None:
        """Test that save_tokens raises error when no tokens available."""
        client = AuthClient(config_path=tmp_path / ".rmapi")
        with pytest.raises(ConfigError, match="No tokens to save"):
            client.save_tokens()

    def test_from_config_loads_tokens(self, tmp_path: Path) -> None:
        """Test from_config class method loads tokens."""
        config_file = tmp_path / ".rmapi"
        config_file.write_text(
            yaml.safe_dump({"devicetoken": "dev123", "usertoken": "user456"})
        )

        client = AuthClient.from_config(config_path=config_file)
        assert client.tokens is not None
        assert client.tokens.device_token == "dev123"


class TestDeviceRegistration:
    """Tests for device registration."""

    def test_register_device_success(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test successful device registration."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=DEVICE_TOKEN_URL,
            method="POST",
            text="new_device_token_12345",
        )

        client = AuthClient(config_path=tmp_path / ".rmapi")
        tokens = client.register_device("one-time-code")

        assert tokens.device_token == "new_device_token_12345"
        assert tokens.user_token == ""
        assert client.tokens == tokens

    def test_register_device_http_error(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test device registration with HTTP error."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=DEVICE_TOKEN_URL,
            method="POST",
            status_code=401,
            text="Invalid code",
        )

        client = AuthClient(config_path=tmp_path / ".rmapi")
        with pytest.raises(DeviceRegistrationError, match="401"):
            client.register_device("invalid-code")

    def test_register_device_empty_response(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test device registration with empty response."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=DEVICE_TOKEN_URL,
            method="POST",
            text="",
        )

        client = AuthClient(config_path=tmp_path / ".rmapi")
        with pytest.raises(DeviceRegistrationError, match="empty device token"):
            client.register_device("code")

    def test_register_device_network_error(self, tmp_path: Path) -> None:
        """Test device registration with network error."""
        with patch("httpx.Client.post", side_effect=httpx.ConnectError("Network error")):
            client = AuthClient(config_path=tmp_path / ".rmapi")
            with pytest.raises(DeviceRegistrationError, match="HTTP error"):
                client.register_device("code")


class TestTokenRefresh:
    """Tests for user token refresh."""

    def test_refresh_user_token_success(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test successful user token refresh."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=USER_TOKEN_URL,
            method="POST",
            text="new_user_token_67890",
        )

        client = AuthClient(config_path=tmp_path / ".rmapi")
        client.tokens = AuthTokens(device_token="dev123", user_token="")

        user_token = client.refresh_user_token()

        assert user_token == "new_user_token_67890"
        assert client.tokens.user_token == "new_user_token_67890"
        assert client.tokens.device_token == "dev123"

    def test_refresh_user_token_no_device_token(self, tmp_path: Path) -> None:
        """Test refresh fails without device token."""
        client = AuthClient(config_path=tmp_path / ".rmapi")
        with pytest.raises(AuthError, match="No device token"):
            client.refresh_user_token()

    def test_refresh_user_token_http_error(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test token refresh with HTTP error."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=USER_TOKEN_URL,
            method="POST",
            status_code=401,
            text="Invalid token",
        )

        client = AuthClient(config_path=tmp_path / ".rmapi")
        client.tokens = AuthTokens(device_token="dev123", user_token="")

        with pytest.raises(TokenRefreshError, match="401"):
            client.refresh_user_token()

    def test_refresh_user_token_empty_response(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test token refresh with empty response."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=USER_TOKEN_URL,
            method="POST",
            text="",
        )

        client = AuthClient(config_path=tmp_path / ".rmapi")
        client.tokens = AuthTokens(device_token="dev123", user_token="")

        with pytest.raises(TokenRefreshError, match="empty user token"):
            client.refresh_user_token()


class TestEnsureAuthenticated:
    """Tests for ensure_authenticated method."""

    def test_ensure_authenticated_success(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test ensure_authenticated with valid config."""
        config_file = tmp_path / ".rmapi"
        config_file.write_text(
            yaml.safe_dump({"devicetoken": "dev123", "usertoken": "old_user"})
        )

        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=USER_TOKEN_URL,
            method="POST",
            text="new_user_token",
        )

        client = AuthClient(config_path=config_file)
        tokens = client.ensure_authenticated()

        assert tokens.device_token == "dev123"
        assert tokens.user_token == "new_user_token"

        # Verify tokens were saved
        saved = yaml.safe_load(config_file.read_text())
        assert saved["usertoken"] == "new_user_token"

    def test_ensure_authenticated_not_registered(self, tmp_path: Path) -> None:
        """Test ensure_authenticated when not registered."""
        client = AuthClient(config_path=tmp_path / ".rmapi")
        with pytest.raises(AuthError, match="Not authenticated"):
            client.ensure_authenticated()


class TestHttpClient:
    """Tests for HTTP client creation."""

    def test_get_http_client_success(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test getting authenticated HTTP client."""
        config_file = tmp_path / ".rmapi"
        config_file.write_text(
            yaml.safe_dump({"devicetoken": "dev123", "usertoken": "old_user"})
        )

        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=USER_TOKEN_URL,
            method="POST",
            text="user_token_abc",
        )

        client = AuthClient(config_path=config_file)
        http_client = client.get_http_client()

        assert isinstance(http_client, httpx.Client)
        assert http_client.headers["Authorization"] == "Bearer user_token_abc"
        http_client.close()

    def test_get_async_http_client_not_authenticated(self, tmp_path: Path) -> None:
        """Test getting async client without authentication."""
        client = AuthClient(config_path=tmp_path / ".rmapi")
        with pytest.raises(AuthError, match="No user token"):
            client.get_async_http_client()

    def test_get_async_http_client_success(self, tmp_path: Path) -> None:
        """Test getting async HTTP client when authenticated."""
        client = AuthClient(config_path=tmp_path / ".rmapi")
        client.tokens = AuthTokens(device_token="dev123", user_token="user456")

        async_client = client.get_async_http_client()

        assert isinstance(async_client, httpx.AsyncClient)
        assert async_client.headers["Authorization"] == "Bearer user456"


class TestContextManager:
    """Tests for context manager functionality."""

    def test_context_manager(self, tmp_path: Path) -> None:
        """Test AuthClient as context manager."""
        config_file = tmp_path / ".rmapi"

        with AuthClient(config_path=config_file) as client:
            assert isinstance(client, AuthClient)

    def test_context_manager_closes_http_client(self, tmp_path: Path) -> None:
        """Test that context manager closes HTTP client."""
        config_file = tmp_path / ".rmapi"

        client = AuthClient(config_path=config_file)
        client._http_client = httpx.Client()

        client.__exit__(None, None, None)

        assert client._http_client is None


@pytest.mark.asyncio
class TestAsyncMethods:
    """Tests for async authentication methods."""

    async def test_register_device_async_success(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test async device registration."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=DEVICE_TOKEN_URL,
            method="POST",
            text="async_device_token",
        )

        client = AuthClient(config_path=tmp_path / ".rmapi")
        tokens = await client.register_device_async("code")

        assert tokens.device_token == "async_device_token"

    async def test_refresh_user_token_async_success(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test async token refresh."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=USER_TOKEN_URL,
            method="POST",
            text="async_user_token",
        )

        client = AuthClient(config_path=tmp_path / ".rmapi")
        client.tokens = AuthTokens(device_token="dev123", user_token="")

        user_token = await client.refresh_user_token_async()

        assert user_token == "async_user_token"

    async def test_ensure_authenticated_async_success(
        self, tmp_path: Path, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test async ensure_authenticated."""
        config_file = tmp_path / ".rmapi"
        config_file.write_text(
            yaml.safe_dump({"devicetoken": "dev123", "usertoken": "old"})
        )

        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=USER_TOKEN_URL,
            method="POST",
            text="async_refreshed_token",
        )

        client = AuthClient(config_path=config_file)
        tokens = await client.ensure_authenticated_async()

        assert tokens.user_token == "async_refreshed_token"
