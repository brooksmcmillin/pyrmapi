"""Pydantic models for reMarkable Cloud API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


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


# =============================================================================
# Cloud Storage API Models
# =============================================================================


class ItemType(str, Enum):
    """Type of item in the reMarkable cloud storage."""

    DOCUMENT = "DocumentType"
    COLLECTION = "CollectionType"


class ServiceDiscoveryResponse(BaseModel):
    """Response from the service discovery endpoint."""

    status: str = Field(..., alias="Status")
    host: str = Field(..., alias="Host")

    model_config = {"populate_by_name": True}


class CloudItem(BaseModel):
    """Base model for items in the reMarkable cloud storage.

    This represents both documents (notebooks/PDFs) and collections (folders).
    """

    id: str = Field(..., alias="ID", description="UUID of the item")
    version: int = Field(..., alias="Version", description="Version number for sync")
    message: str = Field(default="", alias="Message", description="Status message")
    success: bool = Field(default=True, alias="Success", description="Operation status")
    blob_url_get: str = Field(
        default="",
        alias="BlobURLGet",
        description="Signed URL for downloading content",
    )
    blob_url_get_expires: str = Field(
        default="",
        alias="BlobURLGetExpires",
        description="Expiration time for download URL",
    )
    modified_client: str = Field(
        default="",
        alias="ModifiedClient",
        description="Last modification timestamp from client",
    )
    item_type: ItemType = Field(
        ...,
        alias="Type",
        description="Type of item (DocumentType or CollectionType)",
    )
    visible_name: str = Field(
        default="",
        alias="VissibleName",  # Note: API uses this spelling
        description="Display name of the item",
    )
    current_page: int = Field(
        default=0,
        alias="CurrentPage",
        description="Current page number (for documents)",
    )
    bookmarked: bool = Field(
        default=False,
        alias="Bookmarked",
        description="Whether the item is bookmarked",
    )
    parent: str = Field(
        default="",
        alias="Parent",
        description="UUID of parent folder (empty for root items)",
    )

    model_config = {"populate_by_name": True}

    @property
    def is_folder(self) -> bool:
        """Check if this item is a folder/collection."""
        return self.item_type == ItemType.COLLECTION

    @property
    def is_document(self) -> bool:
        """Check if this item is a document."""
        return self.item_type == ItemType.DOCUMENT


class UploadRequestItem(BaseModel):
    """Request item for initiating an upload."""

    id: str = Field(..., alias="ID", description="UUID for the new item")
    version: int = Field(..., alias="Version", description="Version number (1 for new)")
    item_type: ItemType = Field(..., alias="Type", description="Type of item")

    model_config = {"populate_by_name": True}


class UploadRequestResponse(BaseModel):
    """Response from upload request endpoint."""

    id: str = Field(..., alias="ID", description="UUID of the item")
    version: int = Field(..., alias="Version", description="Version number")
    message: str = Field(default="", alias="Message", description="Status message")
    success: bool = Field(default=True, alias="Success", description="Operation status")
    blob_url_put: str = Field(
        default="",
        alias="BlobURLPut",
        description="Signed URL for uploading content",
    )
    blob_url_put_expires: str = Field(
        default="",
        alias="BlobURLPutExpires",
        description="Expiration time for upload URL",
    )

    model_config = {"populate_by_name": True}


class UpdateStatusItem(BaseModel):
    """Request item for updating item metadata."""

    id: str = Field(..., alias="ID", description="UUID of the item")
    version: int = Field(..., alias="Version", description="Incremented version number")
    parent: str = Field(default="", alias="Parent", description="UUID of parent folder")
    visible_name: str = Field(
        ...,
        alias="VissibleName",  # Note: API uses this spelling
        description="Display name of the item",
    )
    item_type: ItemType = Field(..., alias="Type", description="Type of item")
    modified_client: str = Field(
        ...,
        alias="ModifiedClient",
        description="Current timestamp in ISO format",
    )
    bookmarked: bool = Field(
        default=False,
        alias="Bookmarked",
        description="Whether the item is bookmarked",
    )

    model_config = {"populate_by_name": True}

    @classmethod
    def now_timestamp(cls) -> str:
        """Get current UTC timestamp in the format expected by the API."""
        return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class DeleteItem(BaseModel):
    """Request item for deleting an item."""

    id: str = Field(..., alias="ID", description="UUID of the item to delete")
    version: int = Field(..., alias="Version", description="Current version of item")

    model_config = {"populate_by_name": True}
