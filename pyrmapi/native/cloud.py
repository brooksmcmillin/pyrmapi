"""Cloud storage client for reMarkable Cloud API.

This module implements native Python file operations for the reMarkable Cloud API,
eliminating the need for the Go rmapi binary for storage operations.

Storage Operations:
- List documents and folders
- Create folders
- Upload documents (PDFs)
- Download documents
- Move/rename items
- Delete items
"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .auth import AuthClient, AuthError
from .models import (
    CloudItem,
    DeleteItem,
    ItemType,
    ServiceDiscoveryResponse,
    UpdateStatusItem,
    UploadRequestItem,
    UploadRequestResponse,
)

if TYPE_CHECKING:
    from typing import Self

# Service Discovery URLs
SERVICE_DISCOVERY_URL = (
    "https://service-manager-production-dot-remarkable-production.appspot.com"
    "/service/json/1/{service}?environment=production&apiVer={api_ver}"
)

# Default storage host (used as fallback if discovery fails)
DEFAULT_STORAGE_HOST = (
    "document-storage-production-dot-remarkable-production.appspot.com"
)

# API endpoints (relative to storage host)
LIST_DOCS_ENDPOINT = "/document-storage/json/2/docs"
UPLOAD_REQUEST_ENDPOINT = "/document-storage/json/2/upload/request"
UPDATE_STATUS_ENDPOINT = "/document-storage/json/2/upload/update-status"
DELETE_ENDPOINT = "/document-storage/json/2/delete"

# HTTP client settings
DEFAULT_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 300.0  # 5 minutes for uploads

# Root folder constant (empty string means root)
ROOT_FOLDER = ""


class CloudError(Exception):
    """Base exception for cloud storage errors."""

    pass


class ServiceDiscoveryError(CloudError):
    """Raised when service discovery fails."""

    pass


class ListItemsError(CloudError):
    """Raised when listing items fails."""

    pass


class CreateFolderError(CloudError):
    """Raised when folder creation fails."""

    pass


class UploadError(CloudError):
    """Raised when upload fails."""

    pass


class DownloadError(CloudError):
    """Raised when download fails."""

    pass


class MoveError(CloudError):
    """Raised when move/rename fails."""

    pass


class DeleteError(CloudError):
    """Raised when deletion fails."""

    pass


class ItemNotFoundError(CloudError):
    """Raised when an item is not found."""

    pass


class CloudClient:
    """Cloud storage client for reMarkable Cloud API.

    Handles file operations against the reMarkable cloud storage.

    Example:
        >>> auth = AuthClient.from_config()
        >>> cloud = CloudClient(auth)
        >>> items = cloud.list_items()
        >>> cloud.create_folder("My Folder")
        >>> cloud.upload_document("/path/to/file.pdf", "My Document")

    Attributes:
        auth_client: The authentication client for API authorization.
        storage_host: The discovered storage host URL.
    """

    def __init__(self, auth_client: AuthClient) -> None:
        """Initialize the cloud client.

        Args:
            auth_client: Authenticated AuthClient instance.
        """
        self.auth_client = auth_client
        self._storage_host: str | None = None
        self._items_cache: dict[str, CloudItem] | None = None

    @property
    def storage_host(self) -> str:
        """Get the storage host, discovering it if necessary."""
        if self._storage_host is None:
            self._storage_host = self.discover_storage_host()
        return self._storage_host

    def _get_storage_url(self, endpoint: str) -> str:
        """Build full URL for a storage endpoint."""
        return f"https://{self.storage_host}{endpoint}"

    def _get_auth_headers(self) -> dict[str, str]:
        """Get authorization headers with current user token."""
        self.auth_client.ensure_authenticated()
        if self.auth_client.tokens is None:
            raise AuthError("Not authenticated")
        return {"Authorization": f"Bearer {self.auth_client.tokens.user_token}"}

    def discover_storage_host(self) -> str:
        """Discover the storage host from the service manager.

        Returns:
            The storage host URL.

        Raises:
            ServiceDiscoveryError: If discovery fails.
        """
        url = SERVICE_DISCOVERY_URL.format(service="document-storage", api_ver="2")

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.get(url)

                if response.status_code != 200:
                    # Fall back to default host
                    return DEFAULT_STORAGE_HOST

                data = response.json()
                discovery = ServiceDiscoveryResponse.model_validate(data)

                if discovery.status != "OK":
                    return DEFAULT_STORAGE_HOST

                return discovery.host

        except httpx.HTTPError:
            # Fall back to default host on network errors
            return DEFAULT_STORAGE_HOST
        except Exception as e:
            raise ServiceDiscoveryError(f"Failed to discover storage host: {e}") from e

    async def discover_storage_host_async(self) -> str:
        """Discover the storage host from the service manager (async version).

        Returns:
            The storage host URL.

        Raises:
            ServiceDiscoveryError: If discovery fails.
        """
        url = SERVICE_DISCOVERY_URL.format(service="document-storage", api_ver="2")

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(url)

                if response.status_code != 200:
                    return DEFAULT_STORAGE_HOST

                data = response.json()
                discovery = ServiceDiscoveryResponse.model_validate(data)

                if discovery.status != "OK":
                    return DEFAULT_STORAGE_HOST

                return discovery.host

        except httpx.HTTPError:
            return DEFAULT_STORAGE_HOST
        except Exception as e:
            raise ServiceDiscoveryError(f"Failed to discover storage host: {e}") from e

    def list_items(
        self, *, with_blob: bool = False, refresh: bool = False
    ) -> list[CloudItem]:
        """List all items in the cloud storage.

        Args:
            with_blob: If True, includes download URLs for items.
            refresh: If True, bypasses cache and fetches fresh data.

        Returns:
            List of CloudItem objects.

        Raises:
            ListItemsError: If listing fails.
        """
        url = self._get_storage_url(LIST_DOCS_ENDPOINT)
        if with_blob:
            url += "?withBlob=true"

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.get(url, headers=self._get_auth_headers())

                if response.status_code != 200:
                    raise ListItemsError(
                        f"Failed to list items: {response.status_code} - "
                        f"{response.text}"
                    )

                data = response.json()

                # Handle empty response
                if not data:
                    return []

                items = [CloudItem.model_validate(item) for item in data]

                # Cache items by ID for path resolution
                self._items_cache = {item.id: item for item in items}

                return items

        except httpx.HTTPError as e:
            raise ListItemsError(f"HTTP error while listing items: {e}") from e

    async def list_items_async(
        self, *, with_blob: bool = False, refresh: bool = False
    ) -> list[CloudItem]:
        """List all items in the cloud storage (async version).

        Args:
            with_blob: If True, includes download URLs for items.
            refresh: If True, bypasses cache and fetches fresh data.

        Returns:
            List of CloudItem objects.

        Raises:
            ListItemsError: If listing fails.
        """
        if self._storage_host is None:
            self._storage_host = await self.discover_storage_host_async()

        url = self._get_storage_url(LIST_DOCS_ENDPOINT)
        if with_blob:
            url += "?withBlob=true"

        await self.auth_client.ensure_authenticated_async()
        if self.auth_client.tokens is None:
            raise AuthError("Not authenticated")

        headers = {"Authorization": f"Bearer {self.auth_client.tokens.user_token}"}

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(url, headers=headers)

                if response.status_code != 200:
                    raise ListItemsError(
                        f"Failed to list items: {response.status_code} - "
                        f"{response.text}"
                    )

                data = response.json()

                if not data:
                    return []

                items = [CloudItem.model_validate(item) for item in data]
                self._items_cache = {item.id: item for item in items}

                return items

        except httpx.HTTPError as e:
            raise ListItemsError(f"HTTP error while listing items: {e}") from e

    def get_item(self, item_id: str, *, with_blob: bool = False) -> CloudItem:
        """Get a specific item by ID.

        Args:
            item_id: UUID of the item.
            with_blob: If True, includes download URL.

        Returns:
            CloudItem for the requested item.

        Raises:
            ItemNotFoundError: If item is not found.
            ListItemsError: If request fails.
        """
        url = self._get_storage_url(LIST_DOCS_ENDPOINT)
        url += f"?doc={item_id}"
        if with_blob:
            url += "&withBlob=true"

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.get(url, headers=self._get_auth_headers())

                if response.status_code == 404:
                    raise ItemNotFoundError(f"Item not found: {item_id}")

                if response.status_code != 200:
                    raise ListItemsError(
                        f"Failed to get item: {response.status_code} - "
                        f"{response.text}"
                    )

                data = response.json()

                if not data:
                    raise ItemNotFoundError(f"Item not found: {item_id}")

                # API returns array even for single item
                if isinstance(data, list):
                    if len(data) == 0:
                        raise ItemNotFoundError(f"Item not found: {item_id}")
                    return CloudItem.model_validate(data[0])

                return CloudItem.model_validate(data)

        except httpx.HTTPError as e:
            raise ListItemsError(f"HTTP error while getting item: {e}") from e

    def find_item_by_path(self, path: str) -> CloudItem | None:
        """Find an item by its path.

        Args:
            path: Path like "/folder/subfolder/document".

        Returns:
            CloudItem if found, None otherwise.
        """
        # Refresh items cache
        items = self.list_items()

        # Normalize path
        path = path.strip("/")
        if not path:
            return None  # Root is not an item

        parts = path.split("/")

        # Build path lookup
        items_by_parent: dict[str, list[CloudItem]] = {}
        for item in items:
            parent = item.parent or ROOT_FOLDER
            if parent not in items_by_parent:
                items_by_parent[parent] = []
            items_by_parent[parent].append(item)

        # Traverse path
        current_parent = ROOT_FOLDER
        current_item: CloudItem | None = None

        for part in parts:
            children = items_by_parent.get(current_parent, [])
            found = None
            for child in children:
                if child.visible_name == part:
                    found = child
                    break

            if found is None:
                return None

            current_item = found
            current_parent = found.id

        return current_item

    def get_item_path(self, item: CloudItem) -> str:
        """Get the full path for an item.

        Args:
            item: CloudItem to get path for.

        Returns:
            Full path like "/folder/subfolder/document".
        """
        if self._items_cache is None:
            self.list_items()

        parts = [item.visible_name]
        current = item

        while current.parent and self._items_cache:
            parent = self._items_cache.get(current.parent)
            if parent is None:
                break
            parts.insert(0, parent.visible_name)
            current = parent

        return "/" + "/".join(parts)

    def create_folder(
        self, name: str, parent_id: str = ROOT_FOLDER
    ) -> CloudItem:
        """Create a new folder.

        Args:
            name: Name for the new folder.
            parent_id: UUID of parent folder (empty for root).

        Returns:
            CloudItem for the created folder.

        Raises:
            CreateFolderError: If folder creation fails.
        """
        folder_id = str(uuid.uuid4())

        # Step 1: Request upload slot
        upload_request = UploadRequestItem(
            id=folder_id,
            version=1,
            item_type=ItemType.COLLECTION,
        )

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                # Request upload
                response = client.put(
                    self._get_storage_url(UPLOAD_REQUEST_ENDPOINT),
                    headers=self._get_auth_headers(),
                    json=[upload_request.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise CreateFolderError(
                        f"Upload request failed: {response.status_code} - "
                        f"{response.text}"
                    )

                upload_responses = response.json()
                if not upload_responses or not upload_responses[0].get("Success", True):
                    msg = (
                        upload_responses[0].get("Message", "Unknown error")
                        if upload_responses
                        else "Empty response"
                    )
                    raise CreateFolderError(f"Upload request failed: {msg}")

                # Step 2: Update metadata to set name and parent
                update_item = UpdateStatusItem(
                    id=folder_id,
                    version=1,
                    parent=parent_id,
                    visible_name=name,
                    item_type=ItemType.COLLECTION,
                    modified_client=UpdateStatusItem.now_timestamp(),
                )

                response = client.put(
                    self._get_storage_url(UPDATE_STATUS_ENDPOINT),
                    headers=self._get_auth_headers(),
                    json=[update_item.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise CreateFolderError(
                        f"Metadata update failed: {response.status_code} - "
                        f"{response.text}"
                    )

                update_responses = response.json()
                if not update_responses:
                    raise CreateFolderError("Empty response from metadata update")

                # Invalidate cache
                self._items_cache = None

                return CloudItem.model_validate(update_responses[0])

        except httpx.HTTPError as e:
            raise CreateFolderError(f"HTTP error while creating folder: {e}") from e

    async def create_folder_async(
        self, name: str, parent_id: str = ROOT_FOLDER
    ) -> CloudItem:
        """Create a new folder (async version).

        Args:
            name: Name for the new folder.
            parent_id: UUID of parent folder (empty for root).

        Returns:
            CloudItem for the created folder.

        Raises:
            CreateFolderError: If folder creation fails.
        """
        if self._storage_host is None:
            self._storage_host = await self.discover_storage_host_async()

        await self.auth_client.ensure_authenticated_async()
        if self.auth_client.tokens is None:
            raise AuthError("Not authenticated")

        headers = {"Authorization": f"Bearer {self.auth_client.tokens.user_token}"}
        folder_id = str(uuid.uuid4())

        upload_request = UploadRequestItem(
            id=folder_id,
            version=1,
            item_type=ItemType.COLLECTION,
        )

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.put(
                    self._get_storage_url(UPLOAD_REQUEST_ENDPOINT),
                    headers=headers,
                    json=[upload_request.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise CreateFolderError(
                        f"Upload request failed: {response.status_code} - "
                        f"{response.text}"
                    )

                upload_responses = response.json()
                if not upload_responses or not upload_responses[0].get("Success", True):
                    msg = (
                        upload_responses[0].get("Message", "Unknown error")
                        if upload_responses
                        else "Empty response"
                    )
                    raise CreateFolderError(f"Upload request failed: {msg}")

                update_item = UpdateStatusItem(
                    id=folder_id,
                    version=1,
                    parent=parent_id,
                    visible_name=name,
                    item_type=ItemType.COLLECTION,
                    modified_client=UpdateStatusItem.now_timestamp(),
                )

                response = await client.put(
                    self._get_storage_url(UPDATE_STATUS_ENDPOINT),
                    headers=headers,
                    json=[update_item.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise CreateFolderError(
                        f"Metadata update failed: {response.status_code} - "
                        f"{response.text}"
                    )

                update_responses = response.json()
                if not update_responses:
                    raise CreateFolderError("Empty response from metadata update")

                self._items_cache = None

                return CloudItem.model_validate(update_responses[0])

        except httpx.HTTPError as e:
            raise CreateFolderError(f"HTTP error while creating folder: {e}") from e

    def _create_document_zip(self, pdf_path: Path) -> bytes:
        """Create a ZIP archive for document upload.

        The reMarkable API expects documents as ZIP files containing:
        - {uuid}.content - JSON metadata
        - {uuid}.pdf - The actual PDF file

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            ZIP archive as bytes.
        """
        buffer = io.BytesIO()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add the PDF
            zf.write(pdf_path, pdf_path.name)

            # Add minimal .content metadata
            content_json = """{
    "extraMetadata": {},
    "fileType": "pdf",
    "lastOpenedPage": 0,
    "lineHeight": -1,
    "margins": 100,
    "pageCount": 0,
    "textScale": 1,
    "transform": {}
}"""
            # Use a generic name - the actual UUID is in the metadata
            zf.writestr(".content", content_json)

        return buffer.getvalue()

    def upload_document(
        self,
        file_path: str | Path,
        name: str | None = None,
        parent_id: str = ROOT_FOLDER,
    ) -> CloudItem:
        """Upload a document (PDF) to the cloud.

        Args:
            file_path: Path to the PDF file.
            name: Display name (defaults to filename without extension).
            parent_id: UUID of parent folder (empty for root).

        Returns:
            CloudItem for the uploaded document.

        Raises:
            UploadError: If upload fails.
            FileNotFoundError: If file doesn't exist.
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if name is None:
            name = file_path.stem

        doc_id = str(uuid.uuid4())

        # Step 1: Request upload slot
        upload_request = UploadRequestItem(
            id=doc_id,
            version=1,
            item_type=ItemType.DOCUMENT,
        )

        try:
            with httpx.Client(timeout=UPLOAD_TIMEOUT) as client:
                # Request upload URL
                response = client.put(
                    self._get_storage_url(UPLOAD_REQUEST_ENDPOINT),
                    headers=self._get_auth_headers(),
                    json=[upload_request.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise UploadError(
                        f"Upload request failed: {response.status_code} - "
                        f"{response.text}"
                    )

                upload_responses = response.json()
                if not upload_responses:
                    raise UploadError("Empty response from upload request")

                upload_response = UploadRequestResponse.model_validate(
                    upload_responses[0]
                )

                if not upload_response.success:
                    raise UploadError(
                        f"Upload request failed: {upload_response.message}"
                    )

                if not upload_response.blob_url_put:
                    raise UploadError("No upload URL received")

                # Step 2: Upload the ZIP file to the blob URL
                zip_content = self._create_document_zip(file_path)

                # Note: The API requires empty or no Content-Type header
                upload_headers = {"Content-Type": ""}
                response = client.put(
                    upload_response.blob_url_put,
                    content=zip_content,
                    headers=upload_headers,
                )

                if response.status_code not in (200, 201):
                    raise UploadError(
                        f"Blob upload failed: {response.status_code} - "
                        f"{response.text}"
                    )

                # Step 3: Update metadata
                update_item = UpdateStatusItem(
                    id=doc_id,
                    version=1,
                    parent=parent_id,
                    visible_name=name,
                    item_type=ItemType.DOCUMENT,
                    modified_client=UpdateStatusItem.now_timestamp(),
                )

                response = client.put(
                    self._get_storage_url(UPDATE_STATUS_ENDPOINT),
                    headers=self._get_auth_headers(),
                    json=[update_item.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise UploadError(
                        f"Metadata update failed: {response.status_code} - "
                        f"{response.text}"
                    )

                update_responses = response.json()
                if not update_responses:
                    raise UploadError("Empty response from metadata update")

                # Invalidate cache
                self._items_cache = None

                return CloudItem.model_validate(update_responses[0])

        except httpx.HTTPError as e:
            raise UploadError(f"HTTP error during upload: {e}") from e

    async def upload_document_async(
        self,
        file_path: str | Path,
        name: str | None = None,
        parent_id: str = ROOT_FOLDER,
    ) -> CloudItem:
        """Upload a document (PDF) to the cloud (async version).

        Args:
            file_path: Path to the PDF file.
            name: Display name (defaults to filename without extension).
            parent_id: UUID of parent folder (empty for root).

        Returns:
            CloudItem for the uploaded document.

        Raises:
            UploadError: If upload fails.
            FileNotFoundError: If file doesn't exist.
        """
        if self._storage_host is None:
            self._storage_host = await self.discover_storage_host_async()

        await self.auth_client.ensure_authenticated_async()
        if self.auth_client.tokens is None:
            raise AuthError("Not authenticated")

        headers = {"Authorization": f"Bearer {self.auth_client.tokens.user_token}"}

        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if name is None:
            name = file_path.stem

        doc_id = str(uuid.uuid4())

        upload_request = UploadRequestItem(
            id=doc_id,
            version=1,
            item_type=ItemType.DOCUMENT,
        )

        try:
            async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
                response = await client.put(
                    self._get_storage_url(UPLOAD_REQUEST_ENDPOINT),
                    headers=headers,
                    json=[upload_request.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise UploadError(
                        f"Upload request failed: {response.status_code} - "
                        f"{response.text}"
                    )

                upload_responses = response.json()
                if not upload_responses:
                    raise UploadError("Empty response from upload request")

                upload_response = UploadRequestResponse.model_validate(
                    upload_responses[0]
                )

                if not upload_response.success:
                    raise UploadError(
                        f"Upload request failed: {upload_response.message}"
                    )

                if not upload_response.blob_url_put:
                    raise UploadError("No upload URL received")

                zip_content = self._create_document_zip(file_path)

                upload_headers = {"Content-Type": ""}
                response = await client.put(
                    upload_response.blob_url_put,
                    content=zip_content,
                    headers=upload_headers,
                )

                if response.status_code not in (200, 201):
                    raise UploadError(
                        f"Blob upload failed: {response.status_code} - "
                        f"{response.text}"
                    )

                update_item = UpdateStatusItem(
                    id=doc_id,
                    version=1,
                    parent=parent_id,
                    visible_name=name,
                    item_type=ItemType.DOCUMENT,
                    modified_client=UpdateStatusItem.now_timestamp(),
                )

                response = await client.put(
                    self._get_storage_url(UPDATE_STATUS_ENDPOINT),
                    headers=headers,
                    json=[update_item.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise UploadError(
                        f"Metadata update failed: {response.status_code} - "
                        f"{response.text}"
                    )

                update_responses = response.json()
                if not update_responses:
                    raise UploadError("Empty response from metadata update")

                self._items_cache = None

                return CloudItem.model_validate(update_responses[0])

        except httpx.HTTPError as e:
            raise UploadError(f"HTTP error during upload: {e}") from e

    def download_document(
        self, item_id: str, output_path: str | Path
    ) -> Path:
        """Download a document from the cloud.

        Args:
            item_id: UUID of the document to download.
            output_path: Path where the file should be saved.

        Returns:
            Path to the downloaded file.

        Raises:
            DownloadError: If download fails.
            ItemNotFoundError: If document is not found.
        """
        output_path = Path(output_path)

        # Get item with blob URL
        item = self.get_item(item_id, with_blob=True)

        if not item.blob_url_get:
            raise DownloadError(f"No download URL for item: {item_id}")

        try:
            with httpx.Client(timeout=UPLOAD_TIMEOUT) as client:
                # Download the ZIP file
                response = client.get(item.blob_url_get)

                if response.status_code != 200:
                    raise DownloadError(
                        f"Download failed: {response.status_code} - "
                        f"{response.text}"
                    )

                # Extract the PDF from the ZIP
                zip_buffer = io.BytesIO(response.content)

                with zipfile.ZipFile(zip_buffer, "r") as zf:
                    # Find PDF file in the archive
                    pdf_files = [n for n in zf.namelist() if n.endswith(".pdf")]

                    if not pdf_files:
                        # No PDF, save the whole ZIP
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_bytes(response.content)
                        return output_path

                    # Extract the first PDF
                    pdf_name = pdf_files[0]
                    pdf_content = zf.read(pdf_name)

                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(pdf_content)

                    return output_path

        except httpx.HTTPError as e:
            raise DownloadError(f"HTTP error during download: {e}") from e
        except zipfile.BadZipFile as e:
            raise DownloadError(f"Invalid ZIP file received: {e}") from e

    async def download_document_async(
        self, item_id: str, output_path: str | Path
    ) -> Path:
        """Download a document from the cloud (async version).

        Args:
            item_id: UUID of the document to download.
            output_path: Path where the file should be saved.

        Returns:
            Path to the downloaded file.

        Raises:
            DownloadError: If download fails.
            ItemNotFoundError: If document is not found.
        """
        if self._storage_host is None:
            self._storage_host = await self.discover_storage_host_async()

        await self.auth_client.ensure_authenticated_async()

        output_path = Path(output_path)

        # Get item with blob URL (sync call - could be made async)
        item = self.get_item(item_id, with_blob=True)

        if not item.blob_url_get:
            raise DownloadError(f"No download URL for item: {item_id}")

        try:
            async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
                response = await client.get(item.blob_url_get)

                if response.status_code != 200:
                    raise DownloadError(
                        f"Download failed: {response.status_code} - "
                        f"{response.text}"
                    )

                zip_buffer = io.BytesIO(response.content)

                with zipfile.ZipFile(zip_buffer, "r") as zf:
                    pdf_files = [n for n in zf.namelist() if n.endswith(".pdf")]

                    if not pdf_files:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_bytes(response.content)
                        return output_path

                    pdf_name = pdf_files[0]
                    pdf_content = zf.read(pdf_name)

                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(pdf_content)

                    return output_path

        except httpx.HTTPError as e:
            raise DownloadError(f"HTTP error during download: {e}") from e
        except zipfile.BadZipFile as e:
            raise DownloadError(f"Invalid ZIP file received: {e}") from e

    def move_item(
        self,
        item_id: str,
        new_parent_id: str = ROOT_FOLDER,
        new_name: str | None = None,
    ) -> CloudItem:
        """Move or rename an item.

        Args:
            item_id: UUID of the item to move.
            new_parent_id: UUID of new parent folder (empty for root).
            new_name: New name for the item (None to keep current name).

        Returns:
            Updated CloudItem.

        Raises:
            MoveError: If move fails.
            ItemNotFoundError: If item is not found.
        """
        # Get current item state
        item = self.get_item(item_id)

        name = new_name if new_name is not None else item.visible_name

        update_item = UpdateStatusItem(
            id=item_id,
            version=item.version + 1,
            parent=new_parent_id,
            visible_name=name,
            item_type=item.item_type,
            modified_client=UpdateStatusItem.now_timestamp(),
            bookmarked=item.bookmarked,
        )

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.put(
                    self._get_storage_url(UPDATE_STATUS_ENDPOINT),
                    headers=self._get_auth_headers(),
                    json=[update_item.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise MoveError(
                        f"Move failed: {response.status_code} - {response.text}"
                    )

                update_responses = response.json()
                if not update_responses:
                    raise MoveError("Empty response from move operation")

                result = CloudItem.model_validate(update_responses[0])

                if not result.success:
                    raise MoveError(f"Move failed: {result.message}")

                # Invalidate cache
                self._items_cache = None

                return result

        except httpx.HTTPError as e:
            raise MoveError(f"HTTP error during move: {e}") from e

    async def move_item_async(
        self,
        item_id: str,
        new_parent_id: str = ROOT_FOLDER,
        new_name: str | None = None,
    ) -> CloudItem:
        """Move or rename an item (async version).

        Args:
            item_id: UUID of the item to move.
            new_parent_id: UUID of new parent folder (empty for root).
            new_name: New name for the item (None to keep current name).

        Returns:
            Updated CloudItem.

        Raises:
            MoveError: If move fails.
            ItemNotFoundError: If item is not found.
        """
        if self._storage_host is None:
            self._storage_host = await self.discover_storage_host_async()

        await self.auth_client.ensure_authenticated_async()
        if self.auth_client.tokens is None:
            raise AuthError("Not authenticated")

        headers = {"Authorization": f"Bearer {self.auth_client.tokens.user_token}"}

        # Get current item state
        item = self.get_item(item_id)

        name = new_name if new_name is not None else item.visible_name

        update_item = UpdateStatusItem(
            id=item_id,
            version=item.version + 1,
            parent=new_parent_id,
            visible_name=name,
            item_type=item.item_type,
            modified_client=UpdateStatusItem.now_timestamp(),
            bookmarked=item.bookmarked,
        )

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.put(
                    self._get_storage_url(UPDATE_STATUS_ENDPOINT),
                    headers=headers,
                    json=[update_item.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise MoveError(
                        f"Move failed: {response.status_code} - {response.text}"
                    )

                update_responses = response.json()
                if not update_responses:
                    raise MoveError("Empty response from move operation")

                result = CloudItem.model_validate(update_responses[0])

                if not result.success:
                    raise MoveError(f"Move failed: {result.message}")

                self._items_cache = None

                return result

        except httpx.HTTPError as e:
            raise MoveError(f"HTTP error during move: {e}") from e

    def rename_item(self, item_id: str, new_name: str) -> CloudItem:
        """Rename an item.

        Args:
            item_id: UUID of the item to rename.
            new_name: New name for the item.

        Returns:
            Updated CloudItem.

        Raises:
            MoveError: If rename fails.
        """
        item = self.get_item(item_id)
        return self.move_item(item_id, new_parent_id=item.parent, new_name=new_name)

    async def rename_item_async(self, item_id: str, new_name: str) -> CloudItem:
        """Rename an item (async version).

        Args:
            item_id: UUID of the item to rename.
            new_name: New name for the item.

        Returns:
            Updated CloudItem.

        Raises:
            MoveError: If rename fails.
        """
        item = self.get_item(item_id)
        return await self.move_item_async(
            item_id, new_parent_id=item.parent, new_name=new_name
        )

    def delete_item(self, item_id: str) -> bool:
        """Delete an item.

        Args:
            item_id: UUID of the item to delete.

        Returns:
            True if deletion was successful.

        Raises:
            DeleteError: If deletion fails.
            ItemNotFoundError: If item is not found.
        """
        # Get current item to get version
        item = self.get_item(item_id)

        delete_item = DeleteItem(id=item_id, version=item.version)

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.put(
                    self._get_storage_url(DELETE_ENDPOINT),
                    headers=self._get_auth_headers(),
                    json=[delete_item.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise DeleteError(
                        f"Delete failed: {response.status_code} - {response.text}"
                    )

                delete_responses = response.json()
                if not delete_responses:
                    raise DeleteError("Empty response from delete operation")

                result = delete_responses[0]
                if not result.get("Success", True):
                    msg = result.get("Message", "Unknown error")
                    raise DeleteError(f"Delete failed: {msg}")

                # Invalidate cache
                self._items_cache = None

                return True

        except httpx.HTTPError as e:
            raise DeleteError(f"HTTP error during delete: {e}") from e

    async def delete_item_async(self, item_id: str) -> bool:
        """Delete an item (async version).

        Args:
            item_id: UUID of the item to delete.

        Returns:
            True if deletion was successful.

        Raises:
            DeleteError: If deletion fails.
            ItemNotFoundError: If item is not found.
        """
        if self._storage_host is None:
            self._storage_host = await self.discover_storage_host_async()

        await self.auth_client.ensure_authenticated_async()
        if self.auth_client.tokens is None:
            raise AuthError("Not authenticated")

        headers = {"Authorization": f"Bearer {self.auth_client.tokens.user_token}"}

        # Get current item to get version
        item = self.get_item(item_id)

        delete_item = DeleteItem(id=item_id, version=item.version)

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.put(
                    self._get_storage_url(DELETE_ENDPOINT),
                    headers=headers,
                    json=[delete_item.model_dump(by_alias=True)],
                )

                if response.status_code != 200:
                    raise DeleteError(
                        f"Delete failed: {response.status_code} - {response.text}"
                    )

                delete_responses = response.json()
                if not delete_responses:
                    raise DeleteError("Empty response from delete operation")

                result = delete_responses[0]
                if not result.get("Success", True):
                    msg = result.get("Message", "Unknown error")
                    raise DeleteError(f"Delete failed: {msg}")

                self._items_cache = None

                return True

        except httpx.HTTPError as e:
            raise DeleteError(f"HTTP error during delete: {e}") from e

    @classmethod
    def from_auth_client(cls, auth_client: AuthClient) -> Self:
        """Create a CloudClient from an existing AuthClient.

        Args:
            auth_client: Authenticated AuthClient instance.

        Returns:
            Configured CloudClient.
        """
        return cls(auth_client)

    @classmethod
    def from_config(cls, config_path: str | Path | None = None) -> Self:
        """Create a CloudClient using default configuration.

        Args:
            config_path: Path to config file. If None, uses default location.

        Returns:
            Configured CloudClient with authentication loaded.
        """
        auth_client = AuthClient.from_config(config_path)
        return cls(auth_client)
