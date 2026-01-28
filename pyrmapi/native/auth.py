"""Authentication client for reMarkable Cloud API.

This module implements native Python authentication for the reMarkable Cloud API,
eliminating the need for the Go rmapi binary for authentication operations.

Authentication Flow:
1. User obtains a one-time code from https://my.remarkable.com/device/desktop/connect
2. Client exchanges code for a device token (long-lived)
3. Client exchanges device token for a user token (short-lived, auto-refreshed)
"""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import yaml

from .models import AuthTokens, DeviceRegistrationRequest

if TYPE_CHECKING:
    from typing import Self

# reMarkable Cloud API endpoints
DEVICE_TOKEN_URL = "https://my.remarkable.com/token/json/2/device/new"
USER_TOKEN_URL = "https://my.remarkable.com/token/json/2/user/new"

# Default config locations
DEFAULT_CONFIG_NAME = ".rmapi"
XDG_CONFIG_NAME = "rmapi/rmapi.conf"

# File permissions (owner read/write only)
CONFIG_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0o600

# HTTP client settings
DEFAULT_TIMEOUT = 10.0


class AuthError(Exception):
    """Base exception for authentication errors."""

    pass


class DeviceRegistrationError(AuthError):
    """Raised when device registration fails."""

    pass


class TokenRefreshError(AuthError):
    """Raised when token refresh fails."""

    pass


class ConfigError(AuthError):
    """Raised when configuration loading/saving fails."""

    pass


def _get_default_config_path() -> Path:
    """Determine the default configuration file path.

    Checks in order:
    1. RMAPI_CONFIG environment variable
    2. ~/.rmapi (home directory)
    3. ~/.config/rmapi/rmapi.conf (XDG config)

    Returns:
        Path to the configuration file.
    """
    # Check environment variable first
    env_config = os.environ.get("RMAPI_CONFIG")
    if env_config:
        return Path(env_config).expanduser()

    # Check home directory
    home_config = Path.home() / DEFAULT_CONFIG_NAME
    if home_config.exists():
        return home_config

    # Check XDG config directory
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        xdg_config = Path(xdg_config_home) / XDG_CONFIG_NAME
    else:
        xdg_config = Path.home() / ".config" / XDG_CONFIG_NAME

    if xdg_config.exists():
        return xdg_config

    # Default to home directory location
    return home_config


class AuthClient:
    """Authentication client for reMarkable Cloud API.

    Handles device registration, token management, and persistence.

    Example:
        >>> client = AuthClient()
        >>> # First time: register device with code from my.remarkable.com
        >>> await client.register_device("your-one-time-code")
        >>> # Get authenticated HTTP client for API calls
        >>> http_client = client.get_http_client()

    Attributes:
        config_path: Path to the configuration file.
        tokens: Current authentication tokens (if loaded/registered).
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Initialize the authentication client.

        Args:
            config_path: Path to config file. If None, uses default location.
        """
        if config_path is None:
            self.config_path = _get_default_config_path()
        else:
            self.config_path = Path(config_path).expanduser()

        self.tokens: AuthTokens | None = None
        self._http_client: httpx.Client | None = None

    def load_tokens(self) -> AuthTokens | None:
        """Load tokens from the configuration file.

        Returns:
            AuthTokens if config exists and is valid, None otherwise.

        Raises:
            ConfigError: If config file exists but cannot be parsed.
        """
        if not self.config_path.exists():
            return None

        try:
            content = self.config_path.read_text()
            data = yaml.safe_load(content)

            if not data:
                return None

            self.tokens = AuthTokens.model_validate(data)
            return self.tokens

        except yaml.YAMLError as e:
            raise ConfigError(f"Failed to parse config file: {e}") from e
        except Exception as e:
            raise ConfigError(f"Failed to load config: {e}") from e

    def save_tokens(self, tokens: AuthTokens | None = None) -> None:
        """Save tokens to the configuration file.

        Args:
            tokens: Tokens to save. If None, saves current tokens.

        Raises:
            ConfigError: If tokens cannot be saved.
        """
        tokens = tokens or self.tokens
        if tokens is None:
            raise ConfigError("No tokens to save")

        try:
            # Ensure parent directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # Serialize with aliases for compatibility with Go rmapi
            data = {
                "devicetoken": tokens.device_token,
                "usertoken": tokens.user_token,
            }

            content = yaml.safe_dump(data, default_flow_style=False)
            self.config_path.write_text(content)

            # Set restrictive permissions
            self.config_path.chmod(CONFIG_FILE_MODE)

            self.tokens = tokens

        except Exception as e:
            raise ConfigError(f"Failed to save config: {e}") from e

    def register_device(self, code: str) -> AuthTokens:
        """Register a new device with the reMarkable Cloud.

        Args:
            code: One-time code from my.remarkable.com/device/desktop/connect

        Returns:
            AuthTokens with the new device token.

        Raises:
            DeviceRegistrationError: If registration fails.
        """
        device_id = str(uuid.uuid4())

        request = DeviceRegistrationRequest(
            code=code,
            device_id=device_id,
        )

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.post(
                    DEVICE_TOKEN_URL,
                    json=request.model_dump(by_alias=True),
                )

                if response.status_code != 200:
                    raise DeviceRegistrationError(
                        f"Registration failed: {response.status_code} - "
                        f"{response.text}"
                    )

                device_token = response.text.strip()

                if not device_token:
                    raise DeviceRegistrationError("Received empty device token")

                self.tokens = AuthTokens(device_token=device_token, user_token="")
                return self.tokens

        except httpx.HTTPError as e:
            raise DeviceRegistrationError(f"HTTP error during registration: {e}") from e

    async def register_device_async(self, code: str) -> AuthTokens:
        """Register a new device with the reMarkable Cloud (async version).

        Args:
            code: One-time code from my.remarkable.com/device/desktop/connect

        Returns:
            AuthTokens with the new device token.

        Raises:
            DeviceRegistrationError: If registration fails.
        """
        device_id = str(uuid.uuid4())

        request = DeviceRegistrationRequest(
            code=code,
            device_id=device_id,
        )

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    DEVICE_TOKEN_URL,
                    json=request.model_dump(by_alias=True),
                )

                if response.status_code != 200:
                    raise DeviceRegistrationError(
                        f"Registration failed: {response.status_code} - "
                        f"{response.text}"
                    )

                device_token = response.text.strip()

                if not device_token:
                    raise DeviceRegistrationError("Received empty device token")

                self.tokens = AuthTokens(device_token=device_token, user_token="")
                return self.tokens

        except httpx.HTTPError as e:
            raise DeviceRegistrationError(f"HTTP error during registration: {e}") from e

    def refresh_user_token(self) -> str:
        """Refresh the user token using the device token.

        Returns:
            The new user token.

        Raises:
            TokenRefreshError: If token refresh fails.
            AuthError: If no device token is available.
        """
        if self.tokens is None or not self.tokens.device_token:
            raise AuthError("No device token available. Register device first.")

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.post(
                    USER_TOKEN_URL,
                    headers={"Authorization": f"Bearer {self.tokens.device_token}"},
                )

                if response.status_code != 200:
                    raise TokenRefreshError(
                        f"Token refresh failed: {response.status_code} - "
                        f"{response.text}"
                    )

                user_token = response.text.strip()

                if not user_token:
                    raise TokenRefreshError("Received empty user token")

                self.tokens = AuthTokens(
                    device_token=self.tokens.device_token,
                    user_token=user_token,
                )
                return user_token

        except httpx.HTTPError as e:
            raise TokenRefreshError(f"HTTP error: {e}") from e

    async def refresh_user_token_async(self) -> str:
        """Refresh the user token using the device token (async version).

        Returns:
            The new user token.

        Raises:
            TokenRefreshError: If token refresh fails.
            AuthError: If no device token is available.
        """
        if self.tokens is None or not self.tokens.device_token:
            raise AuthError("No device token available. Register device first.")

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    USER_TOKEN_URL,
                    headers={"Authorization": f"Bearer {self.tokens.device_token}"},
                )

                if response.status_code != 200:
                    raise TokenRefreshError(
                        f"Token refresh failed: {response.status_code} - "
                        f"{response.text}"
                    )

                user_token = response.text.strip()

                if not user_token:
                    raise TokenRefreshError("Received empty user token")

                self.tokens = AuthTokens(
                    device_token=self.tokens.device_token,
                    user_token=user_token,
                )
                return user_token

        except httpx.HTTPError as e:
            raise TokenRefreshError(f"HTTP error: {e}") from e

    def ensure_authenticated(self) -> AuthTokens:
        """Ensure the client has valid authentication tokens.

        Loads tokens from config, refreshes user token if needed.

        Returns:
            Valid authentication tokens.

        Raises:
            AuthError: If authentication cannot be established.
        """
        # Try to load existing tokens
        if self.tokens is None:
            self.load_tokens()

        if self.tokens is None:
            raise AuthError(
                "Not authenticated. Register device first with a code from "
                "https://my.remarkable.com/device/desktop/connect"
            )

        # Refresh user token (they expire, so always refresh for safety)
        self.refresh_user_token()
        self.save_tokens()

        return self.tokens

    async def ensure_authenticated_async(self) -> AuthTokens:
        """Ensure the client has valid authentication tokens (async version).

        Loads tokens from config, refreshes user token if needed.

        Returns:
            Valid authentication tokens.

        Raises:
            AuthError: If authentication cannot be established.
        """
        # Try to load existing tokens
        if self.tokens is None:
            self.load_tokens()

        if self.tokens is None:
            raise AuthError(
                "Not authenticated. Register device first with a code from "
                "https://my.remarkable.com/device/desktop/connect"
            )

        # Refresh user token (they expire, so always refresh for safety)
        await self.refresh_user_token_async()
        self.save_tokens()

        return self.tokens

    def get_http_client(self) -> httpx.Client:
        """Get an authenticated HTTP client for API calls.

        The client includes the Authorization header with the user token.

        Returns:
            Configured httpx.Client with authentication headers.

        Raises:
            AuthError: If not authenticated.
        """
        tokens = self.ensure_authenticated()

        return httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            headers={"Authorization": f"Bearer {tokens.user_token}"},
        )

    def get_async_http_client(self) -> httpx.AsyncClient:
        """Get an authenticated async HTTP client for API calls.

        Note: Call ensure_authenticated_async() before using this in async contexts.

        Returns:
            Configured httpx.AsyncClient with authentication headers.

        Raises:
            AuthError: If tokens are not available.
        """
        if self.tokens is None or not self.tokens.user_token:
            raise AuthError(
                "No user token available. Call ensure_authenticated_async() first."
            )

        return httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            headers={"Authorization": f"Bearer {self.tokens.user_token}"},
        )

    @classmethod
    def from_config(cls, config_path: str | Path | None = None) -> Self:
        """Create an AuthClient and load existing tokens.

        Args:
            config_path: Path to config file. If None, uses default location.

        Returns:
            AuthClient with tokens loaded (if available).
        """
        client = cls(config_path)
        client.load_tokens()
        return client

    def __enter__(self) -> Self:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Context manager exit - close HTTP client if open."""
        if self._http_client is not None:
            self._http_client.close()
            self._http_client = None
