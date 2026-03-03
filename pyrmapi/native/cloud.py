"""Cloud storage client for reMarkable Cloud API.

This module implements native Python file operations for the reMarkable Cloud API
using the v2 upload API and v3/v4 sync API.

Storage Operations:
- List documents and folders (sync v3/v4 tree walk)
- Create folders (v2 API)
- Upload documents (v2 API)
- Download documents (sync v3 blob fetch)
- Move/rename/delete items (not yet implemented - require tree mutation)
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .auth import AuthClient, AuthError
from .models import (
    CloudItem,
    DocumentContent,
    DocumentMetadata,
    IndexEntry,
    ItemType,
    SyncRootResponse,
    UploadResponse,
)

if TYPE_CHECKING:
    from typing import Self

# v2 Upload API
UPLOAD_HOST = "https://internal.cloud.remarkable.com"
UPLOAD_ENDPOINT = "/doc/v2/files"

# Sync v3/v4 API
SYNC_HOST = "https://eu.tectonic.remarkable.com"
SYNC_ROOT_ENDPOINT = "/sync/v4/root"
SYNC_FILES_ENDPOINT = "/sync/v3/files"

# HTTP client settings
DEFAULT_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 300.0  # 5 minutes for uploads

# Root folder constant (empty string means root)
ROOT_FOLDER = ""


class CloudError(Exception):
    """Base exception for cloud storage errors."""

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

    Uses the v2 upload API and v3/v4 sync API.

    Example:
        >>> auth = AuthClient.from_config()
        >>> cloud = CloudClient(auth)
        >>> items = cloud.list_items()
        >>> cloud.create_folder("My Folder")
        >>> cloud.upload_document("/path/to/file.pdf", "My Document")
    """

    def __init__(self, auth_client: AuthClient) -> None:
        self.auth_client = auth_client
        self._items_cache: dict[str, CloudItem] | None = None

    def _get_auth_headers(self) -> dict[str, str]:
        """Get authorization headers with current user token."""
        self.auth_client.ensure_authenticated()
        if self.auth_client.tokens is None:
            raise AuthError("Not authenticated")
        return {"Authorization": f"Bearer {self.auth_client.tokens.user_token}"}

    async def _get_auth_headers_async(self) -> dict[str, str]:
        """Get authorization headers with current user token (async)."""
        await self.auth_client.ensure_authenticated_async()
        if self.auth_client.tokens is None:
            raise AuthError("Not authenticated")
        return {"Authorization": f"Bearer {self.auth_client.tokens.user_token}"}

    # =========================================================================
    # Sync v3/v4 helpers
    # =========================================================================

    def _get_root_hash(self) -> SyncRootResponse:
        """Fetch the root hash from the sync v4 API."""
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.get(
                    f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
                    headers=self._get_auth_headers(),
                )
                if response.status_code != 200:
                    raise ListItemsError(
                        f"Failed to get root hash: {response.status_code} - "
                        f"{response.text}"
                    )
                return SyncRootResponse.model_validate(response.json())
        except httpx.HTTPError as e:
            raise ListItemsError(f"HTTP error getting root hash: {e}") from e

    async def _get_root_hash_async(self) -> SyncRootResponse:
        """Fetch the root hash from the sync v4 API (async)."""
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
                    headers=await self._get_auth_headers_async(),
                )
                if response.status_code != 200:
                    raise ListItemsError(
                        f"Failed to get root hash: {response.status_code} - "
                        f"{response.text}"
                    )
                return SyncRootResponse.model_validate(response.json())
        except httpx.HTTPError as e:
            raise ListItemsError(f"HTTP error getting root hash: {e}") from e

    def _fetch_hash(self, hash_value: str) -> bytes:
        """Download a blob by its hash from the sync v3 API."""
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.get(
                    f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{hash_value}",
                    headers=self._get_auth_headers(),
                )
                if response.status_code != 200:
                    raise ListItemsError(
                        f"Failed to fetch hash {hash_value}: "
                        f"{response.status_code} - {response.text}"
                    )
                return response.content
        except httpx.HTTPError as e:
            raise ListItemsError(
                f"HTTP error fetching hash {hash_value}: {e}"
            ) from e

    async def _fetch_hash_async(self, hash_value: str) -> bytes:
        """Download a blob by its hash from the sync v3 API (async)."""
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{hash_value}",
                    headers=await self._get_auth_headers_async(),
                )
                if response.status_code != 200:
                    raise ListItemsError(
                        f"Failed to fetch hash {hash_value}: "
                        f"{response.status_code} - {response.text}"
                    )
                return response.content
        except httpx.HTTPError as e:
            raise ListItemsError(
                f"HTTP error fetching hash {hash_value}: {e}"
            ) from e

    @staticmethod
    def _parse_index(data: bytes) -> list[IndexEntry]:
        """Parse an index file into IndexEntry objects.

        Index format (schema v3):
            3
            {hash}:{type}:{id}:{subfiles}:{size}
            ...
        """
        lines = data.decode("utf-8").strip().split("\n")
        if not lines:
            return []

        # First line is schema version, skip it
        entries = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) < 5:
                continue
            entries.append(
                IndexEntry(
                    hash=parts[0],
                    entry_type=parts[1],
                    id=parts[2],
                    subfiles=int(parts[3]),
                    size=int(parts[4]),
                )
            )
        return entries

    # =========================================================================
    # List items
    # =========================================================================

    def list_items(self, *, refresh: bool = False) -> list[CloudItem]:
        """List all items by walking the sync v3/v4 tree.

        Fetches root hash, then walks index entries to collect metadata
        for each document and folder.

        Args:
            refresh: If True, bypasses cache and fetches fresh data.

        Returns:
            List of CloudItem objects.

        Raises:
            ListItemsError: If listing fails.
        """
        # Get root hash
        root = self._get_root_hash()

        # Fetch root index
        root_index_data = self._fetch_hash(root.hash)
        root_entries = self._parse_index(root_index_data)

        items: list[CloudItem] = []

        for entry in root_entries:
            if not entry.is_index:
                continue

            # Each root-level index entry is a document or folder
            try:
                doc_index_data = self._fetch_hash(entry.hash)
                doc_entries = self._parse_index(doc_index_data)
            except ListItemsError:
                continue

            # Find .metadata and .content blobs
            metadata: DocumentMetadata | None = None
            content: DocumentContent | None = None

            for doc_entry in doc_entries:
                if doc_entry.id.endswith(".metadata"):
                    try:
                        meta_data = self._fetch_hash(doc_entry.hash)
                        metadata = DocumentMetadata.model_validate(
                            json.loads(meta_data)
                        )
                    except (ListItemsError, json.JSONDecodeError):
                        pass
                elif doc_entry.id.endswith(".content"):
                    try:
                        content_data = self._fetch_hash(doc_entry.hash)
                        content = DocumentContent.model_validate(
                            json.loads(content_data)
                        )
                    except (ListItemsError, json.JSONDecodeError):
                        pass

            if metadata is None:
                continue

            # Skip deleted items
            if metadata.deleted:
                continue

            # Determine item type from metadata
            if metadata.type == "CollectionType":
                item_type = ItemType.COLLECTION
            else:
                item_type = ItemType.DOCUMENT

            cloud_item = CloudItem(
                id=entry.id,
                hash=entry.hash,
                item_type=item_type,
                visible_name=metadata.visible_name,
                parent=metadata.parent,
                last_modified=metadata.last_modified,
                file_type=content.file_type if content else "",
            )
            items.append(cloud_item)

        # Cache items by ID for path resolution
        self._items_cache = {item.id: item for item in items}

        return items

    async def list_items_async(self, *, refresh: bool = False) -> list[CloudItem]:
        """List all items by walking the sync v3/v4 tree (async).

        Args:
            refresh: If True, bypasses cache and fetches fresh data.

        Returns:
            List of CloudItem objects.

        Raises:
            ListItemsError: If listing fails.
        """
        root = await self._get_root_hash_async()
        root_index_data = await self._fetch_hash_async(root.hash)
        root_entries = self._parse_index(root_index_data)

        items: list[CloudItem] = []

        for entry in root_entries:
            if not entry.is_index:
                continue

            try:
                doc_index_data = await self._fetch_hash_async(entry.hash)
                doc_entries = self._parse_index(doc_index_data)
            except ListItemsError:
                continue

            metadata: DocumentMetadata | None = None
            content: DocumentContent | None = None

            for doc_entry in doc_entries:
                if doc_entry.id.endswith(".metadata"):
                    try:
                        meta_data = await self._fetch_hash_async(doc_entry.hash)
                        metadata = DocumentMetadata.model_validate(
                            json.loads(meta_data)
                        )
                    except (ListItemsError, json.JSONDecodeError):
                        pass
                elif doc_entry.id.endswith(".content"):
                    try:
                        content_data = await self._fetch_hash_async(doc_entry.hash)
                        content = DocumentContent.model_validate(
                            json.loads(content_data)
                        )
                    except (ListItemsError, json.JSONDecodeError):
                        pass

            if metadata is None:
                continue

            if metadata.deleted:
                continue

            if metadata.type == "CollectionType":
                item_type = ItemType.COLLECTION
            else:
                item_type = ItemType.DOCUMENT

            cloud_item = CloudItem(
                id=entry.id,
                hash=entry.hash,
                item_type=item_type,
                visible_name=metadata.visible_name,
                parent=metadata.parent,
                last_modified=metadata.last_modified,
                file_type=content.file_type if content else "",
            )
            items.append(cloud_item)

        self._items_cache = {item.id: item for item in items}

        return items

    # =========================================================================
    # Get / find items
    # =========================================================================

    def get_item(self, item_id: str) -> CloudItem:
        """Get a specific item by ID from the items cache.

        Args:
            item_id: UUID of the item.

        Returns:
            CloudItem for the requested item.

        Raises:
            ItemNotFoundError: If item is not found.
        """
        if self._items_cache is None:
            self.list_items()

        if self._items_cache and item_id in self._items_cache:
            return self._items_cache[item_id]

        raise ItemNotFoundError(f"Item not found: {item_id}")

    def find_item_by_path(self, path: str) -> CloudItem | None:
        """Find an item by its path.

        Args:
            path: Path like "/folder/subfolder/document".

        Returns:
            CloudItem if found, None otherwise.
        """
        items = self.list_items()

        path = path.strip("/")
        if not path:
            return None

        parts = path.split("/")

        items_by_parent: dict[str, list[CloudItem]] = {}
        for item in items:
            parent = item.parent or ROOT_FOLDER
            if parent not in items_by_parent:
                items_by_parent[parent] = []
            items_by_parent[parent].append(item)

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

    # =========================================================================
    # Upload / create folder (v2 API)
    # =========================================================================

    def upload_document(
        self,
        file_path: str | Path,
        name: str | None = None,
        parent_id: str = ROOT_FOLDER,
    ) -> CloudItem:
        """Upload a document (PDF/EPUB) to the cloud via the v2 API.

        Args:
            file_path: Path to the PDF or EPUB file.
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

        suffix = file_path.suffix.lower()
        if suffix == ".epub":
            content_type = "application/epub+zip"
        else:
            content_type = "application/pdf"

        rm_meta = base64.b64encode(
            json.dumps({"file_name": name}).encode()
        ).decode()

        headers = {
            **self._get_auth_headers(),
            "Content-Type": content_type,
            "rm-meta": rm_meta,
            "rm-source": "RoR-Browser",
        }

        try:
            with httpx.Client(timeout=UPLOAD_TIMEOUT) as client:
                response = client.post(
                    f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
                    content=file_path.read_bytes(),
                    headers=headers,
                )

                if response.status_code not in (200, 201):
                    raise UploadError(
                        f"Upload failed: {response.status_code} - "
                        f"{response.text}"
                    )

                upload_resp = UploadResponse.model_validate(response.json())

                # Invalidate cache
                self._items_cache = None

                return CloudItem(
                    id=upload_resp.doc_id,
                    hash=upload_resp.hash,
                    item_type=ItemType.DOCUMENT,
                    visible_name=name,
                    parent=parent_id,
                    file_type="pdf" if content_type == "application/pdf" else "epub",
                )

        except httpx.HTTPError as e:
            raise UploadError(f"HTTP error during upload: {e}") from e

    async def upload_document_async(
        self,
        file_path: str | Path,
        name: str | None = None,
        parent_id: str = ROOT_FOLDER,
    ) -> CloudItem:
        """Upload a document (PDF/EPUB) to the cloud via the v2 API (async).

        Args:
            file_path: Path to the PDF or EPUB file.
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

        suffix = file_path.suffix.lower()
        if suffix == ".epub":
            content_type = "application/epub+zip"
        else:
            content_type = "application/pdf"

        rm_meta = base64.b64encode(
            json.dumps({"file_name": name}).encode()
        ).decode()

        headers = {
            **await self._get_auth_headers_async(),
            "Content-Type": content_type,
            "rm-meta": rm_meta,
            "rm-source": "RoR-Browser",
        }

        try:
            async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
                response = await client.post(
                    f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
                    content=file_path.read_bytes(),
                    headers=headers,
                )

                if response.status_code not in (200, 201):
                    raise UploadError(
                        f"Upload failed: {response.status_code} - "
                        f"{response.text}"
                    )

                upload_resp = UploadResponse.model_validate(response.json())

                self._items_cache = None

                return CloudItem(
                    id=upload_resp.doc_id,
                    hash=upload_resp.hash,
                    item_type=ItemType.DOCUMENT,
                    visible_name=name,
                    parent=parent_id,
                    file_type="pdf" if content_type == "application/pdf" else "epub",
                )

        except httpx.HTTPError as e:
            raise UploadError(f"HTTP error during upload: {e}") from e

    def create_folder(
        self, name: str, parent_id: str = ROOT_FOLDER
    ) -> CloudItem:
        """Create a new folder via the v2 API.

        Args:
            name: Name for the new folder.
            parent_id: UUID of parent folder (empty for root).

        Returns:
            CloudItem for the created folder.

        Raises:
            CreateFolderError: If folder creation fails.
        """
        rm_meta = base64.b64encode(
            json.dumps({"file_name": name}).encode()
        ).decode()

        headers = {
            **self._get_auth_headers(),
            "Content-Type": "folder",
            "rm-meta": rm_meta,
            "rm-source": "RoR-Browser",
        }

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.post(
                    f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
                    content=b"",
                    headers=headers,
                )

                if response.status_code not in (200, 201):
                    raise CreateFolderError(
                        f"Folder creation failed: {response.status_code} - "
                        f"{response.text}"
                    )

                upload_resp = UploadResponse.model_validate(response.json())

                self._items_cache = None

                return CloudItem(
                    id=upload_resp.doc_id,
                    hash=upload_resp.hash,
                    item_type=ItemType.COLLECTION,
                    visible_name=name,
                    parent=parent_id,
                )

        except httpx.HTTPError as e:
            raise CreateFolderError(
                f"HTTP error while creating folder: {e}"
            ) from e

    async def create_folder_async(
        self, name: str, parent_id: str = ROOT_FOLDER
    ) -> CloudItem:
        """Create a new folder via the v2 API (async).

        Args:
            name: Name for the new folder.
            parent_id: UUID of parent folder (empty for root).

        Returns:
            CloudItem for the created folder.

        Raises:
            CreateFolderError: If folder creation fails.
        """
        rm_meta = base64.b64encode(
            json.dumps({"file_name": name}).encode()
        ).decode()

        headers = {
            **await self._get_auth_headers_async(),
            "Content-Type": "folder",
            "rm-meta": rm_meta,
            "rm-source": "RoR-Browser",
        }

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
                    content=b"",
                    headers=headers,
                )

                if response.status_code not in (200, 201):
                    raise CreateFolderError(
                        f"Folder creation failed: {response.status_code} - "
                        f"{response.text}"
                    )

                upload_resp = UploadResponse.model_validate(response.json())

                self._items_cache = None

                return CloudItem(
                    id=upload_resp.doc_id,
                    hash=upload_resp.hash,
                    item_type=ItemType.COLLECTION,
                    visible_name=name,
                    parent=parent_id,
                )

        except httpx.HTTPError as e:
            raise CreateFolderError(
                f"HTTP error while creating folder: {e}"
            ) from e

    # =========================================================================
    # Download
    # =========================================================================

    def download_document(
        self, item_id: str, output_path: str | Path
    ) -> Path:
        """Download a document from the cloud by walking the sync tree.

        Finds the .pdf entry in the document's index and fetches it directly.

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

        # Ensure we have the item in cache
        item = self.get_item(item_id)

        if not item.hash:
            raise DownloadError(f"No hash for item: {item_id}")

        try:
            # Fetch document index
            doc_index_data = self._fetch_hash(item.hash)
            doc_entries = self._parse_index(doc_index_data)

            # Find the PDF/EPUB file entry
            pdf_entry: IndexEntry | None = None
            for entry in doc_entries:
                if entry.id.endswith(".pdf") or entry.id.endswith(".epub"):
                    pdf_entry = entry
                    break

            if pdf_entry is None:
                raise DownloadError(
                    f"No PDF/EPUB found in document index for: {item_id}"
                )

            # Fetch the actual file
            file_content = self._fetch_hash(pdf_entry.hash)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(file_content)

            return output_path

        except ListItemsError as e:
            raise DownloadError(f"Failed to download document: {e}") from e

    async def download_document_async(
        self, item_id: str, output_path: str | Path
    ) -> Path:
        """Download a document from the cloud (async).

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

        item = self.get_item(item_id)

        if not item.hash:
            raise DownloadError(f"No hash for item: {item_id}")

        try:
            doc_index_data = await self._fetch_hash_async(item.hash)
            doc_entries = self._parse_index(doc_index_data)

            pdf_entry: IndexEntry | None = None
            for entry in doc_entries:
                if entry.id.endswith(".pdf") or entry.id.endswith(".epub"):
                    pdf_entry = entry
                    break

            if pdf_entry is None:
                raise DownloadError(
                    f"No PDF/EPUB found in document index for: {item_id}"
                )

            file_content = await self._fetch_hash_async(pdf_entry.hash)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(file_content)

            return output_path

        except ListItemsError as e:
            raise DownloadError(f"Failed to download document: {e}") from e

    # =========================================================================
    # Stubbed operations (require tree mutation + root hash update)
    # =========================================================================

    def move_item(
        self,
        item_id: str,
        new_parent_id: str = ROOT_FOLDER,
        new_name: str | None = None,
    ) -> CloudItem:
        """Move an item to a new parent folder.

        Not yet implemented - requires sync tree mutation and root hash update.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "move_item requires sync tree mutation which is not yet implemented"
        )

    async def move_item_async(
        self,
        item_id: str,
        new_parent_id: str = ROOT_FOLDER,
        new_name: str | None = None,
    ) -> CloudItem:
        """Move an item (async). Not yet implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "move_item requires sync tree mutation which is not yet implemented"
        )

    def rename_item(self, item_id: str, new_name: str) -> CloudItem:
        """Rename an item. Not yet implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "rename_item requires sync tree mutation which is not yet implemented"
        )

    async def rename_item_async(self, item_id: str, new_name: str) -> CloudItem:
        """Rename an item (async). Not yet implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "rename_item requires sync tree mutation which is not yet implemented"
        )

    def delete_item(self, item_id: str) -> bool:
        """Delete an item. Not yet implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "delete_item requires sync tree mutation which is not yet implemented"
        )

    async def delete_item_async(self, item_id: str) -> bool:
        """Delete an item (async). Not yet implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "delete_item requires sync tree mutation which is not yet implemented"
        )

    # =========================================================================
    # Factory methods
    # =========================================================================

    @classmethod
    def from_auth_client(cls, auth_client: AuthClient) -> Self:
        """Create a CloudClient from an existing AuthClient."""
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
