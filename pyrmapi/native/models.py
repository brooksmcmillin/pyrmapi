"""Pydantic models for reMarkable Cloud API."""

from __future__ import annotations

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


class CloudItem(BaseModel):
    """Item in the reMarkable cloud storage.

    Represents both documents (notebooks/PDFs) and collections (folders)
    as returned by the sync v3 API tree walk.
    """

    id: str = Field(..., description="UUID of the item")
    hash: str = Field(default="", description="Content hash from sync tree")
    item_type: ItemType = Field(
        ...,
        description="Type of item (DocumentType or CollectionType)",
    )
    visible_name: str = Field(default="", description="Display name of the item")
    parent: str = Field(
        default="",
        description="UUID of parent folder (empty for root items)",
    )
    last_modified: str = Field(
        default="", description="Last modification timestamp (epoch ms)"
    )
    file_type: str = Field(default="", description="File type (pdf, epub, etc.)")

    model_config = {"populate_by_name": True}

    @property
    def is_folder(self) -> bool:
        """Check if this item is a folder/collection."""
        return self.item_type == ItemType.COLLECTION

    @property
    def is_document(self) -> bool:
        """Check if this item is a document."""
        return self.item_type == ItemType.DOCUMENT


class SyncRootResponse(BaseModel):
    """Response from the sync v4 root endpoint."""

    hash: str = Field(..., description="Root index hash")
    generation: int = Field(..., description="Generation number")
    schema_version: int = Field(..., alias="schemaVersion", description="Schema version")

    model_config = {"populate_by_name": True}


class IndexEntry(BaseModel):
    """Parsed entry from a sync v3 index file.

    Index format (schema v3):
        3
        {hash}:{type}:{id}:{subfiles}:{size}
        ...

    Type 80000000 = collection/index, 0 = file.
    """

    hash: str = Field(..., description="Content hash")
    entry_type: str = Field(..., description="Entry type (80000000=index, 0=file)")
    id: str = Field(..., description="UUID or filename")
    subfiles: int = Field(default=0, description="Number of sub-files")
    size: int = Field(default=0, description="Size in bytes")

    @property
    def is_index(self) -> bool:
        """Check if this entry is an index (collection/document container)."""
        return self.entry_type == "80000000"

    @property
    def is_file(self) -> bool:
        """Check if this entry is a plain file."""
        return self.entry_type == "0"


class UploadResponse(BaseModel):
    """Response from the v2 upload endpoint."""

    doc_id: str = Field(..., alias="docID", description="UUID of the uploaded document")
    hash: str = Field(..., description="Content hash")

    model_config = {"populate_by_name": True}


class DocumentMetadata(BaseModel):
    """Metadata JSON found in document .metadata blobs."""

    visible_name: str = Field(default="", alias="visibleName")
    parent: str = Field(default="")
    type: str = Field(default="")
    last_modified: str = Field(default="", alias="lastModified")
    deleted: bool = Field(default=False)
    metadatamodified: bool = Field(default=False)
    modified: bool = Field(default=False)
    pinned: bool = Field(default=False)
    synced: bool = Field(default=False)
    version: int = Field(default=0)

    model_config = {"populate_by_name": True}


class DocumentContent(BaseModel):
    """Content JSON found in document .content blobs."""

    file_type: str = Field(default="", alias="fileType")
    page_count: int = Field(default=0, alias="pageCount")

    model_config = {"populate_by_name": True}
