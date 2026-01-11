"""Pydantic models for reMarkable Cloud API."""

from pydantic import BaseModel, Field


class DeviceRegistrationRequest(BaseModel):
    """Request body for device registration."""

    code: str = Field(..., description="One-time registration code")
    device_desc: str = Field(
        default="desktop-linux",
        alias="deviceDesc",
        description="Device description",
    )
    device_id: str = Field(
        ...,
        alias="deviceID",
        description="Unique device identifier (UUID)",
    )

    model_config = {"populate_by_name": True}


class AuthTokens(BaseModel):
    """Authentication tokens for reMarkable Cloud API.

    Stored in the .rmapi config file in YAML format.
    """

    device_token: str = Field(
        ...,
        alias="devicetoken",
        description="Long-lived device token",
    )
    user_token: str = Field(
        default="",
        alias="usertoken",
        description="Short-lived user token",
    )

    model_config = {"populate_by_name": True}
