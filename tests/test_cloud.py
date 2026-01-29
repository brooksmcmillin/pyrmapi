"""Tests for the native cloud storage module."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pytest_httpx import HTTPXMock

from pyrmapi.native import (
    AuthClient,
    CloudClient,
    CloudItem,
    CreateFolderError,
    DeleteError,
    DownloadError,
    ItemNotFoundError,
    ItemType,
    ListItemsError,
    ServiceDiscoveryResponse,
    UploadError,
)
from pyrmapi.native.auth import USER_TOKEN_URL
from pyrmapi.native.cloud import (
    DEFAULT_STORAGE_HOST,
    DELETE_ENDPOINT,
    LIST_DOCS_ENDPOINT,
    SERVICE_DISCOVERY_URL,
    UPDATE_STATUS_ENDPOINT,
    UPLOAD_REQUEST_ENDPOINT,
)


@pytest.fixture
def auth_client(tmp_path: Path) -> AuthClient:
    """Create an authenticated AuthClient for testing."""
    config_file = tmp_path / ".rmapi"
    tokens = {"devicetoken": "test_device_token", "usertoken": "test_user_token"}
    config_file.write_text(yaml.safe_dump(tokens))
    client = AuthClient(config_path=config_file)
    client.load_tokens()
    return client


@pytest.fixture
def cloud_client(auth_client: AuthClient) -> CloudClient:
    """Create a CloudClient for testing."""
    client = CloudClient(auth_client)
    client._storage_host = DEFAULT_STORAGE_HOST
    return client


def mock_token_refresh(httpx_mock: HTTPXMock, times: int = 5) -> None:
    """Add multiple token refresh mocks to handle repeated auth calls."""
    for _ in range(times):
        httpx_mock.add_response(
            url=USER_TOKEN_URL,
            method="POST",
            text="refreshed_token",
        )


@pytest.fixture
def sample_item_data() -> dict:
    """Sample item data from API response."""
    return {
        "ID": "550e8400-e29b-41d4-a716-446655440000",
        "Version": 1,
        "Message": "",
        "Success": True,
        "BlobURLGet": "https://storage.example.com/download/abc123",
        "BlobURLGetExpires": "2024-01-01T12:00:00Z",
        "ModifiedClient": "2024-01-01T10:00:00.000Z",
        "Type": "DocumentType",
        "VissibleName": "Test Document",
        "CurrentPage": 0,
        "Bookmarked": False,
        "Parent": "",
    }


@pytest.fixture
def sample_folder_data() -> dict:
    """Sample folder data from API response."""
    return {
        "ID": "660e8400-e29b-41d4-a716-446655440001",
        "Version": 1,
        "Message": "",
        "Success": True,
        "BlobURLGet": "",
        "BlobURLGetExpires": "",
        "ModifiedClient": "2024-01-01T10:00:00.000Z",
        "Type": "CollectionType",
        "VissibleName": "Test Folder",
        "CurrentPage": 0,
        "Bookmarked": False,
        "Parent": "",
    }


class TestCloudItemModel:
    """Tests for CloudItem model."""

    def test_create_document_item(self, sample_item_data: dict) -> None:
        """Test creating a document CloudItem."""
        item = CloudItem.model_validate(sample_item_data)
        assert item.id == "550e8400-e29b-41d4-a716-446655440000"
        assert item.version == 1
        assert item.item_type == ItemType.DOCUMENT
        assert item.visible_name == "Test Document"
        assert item.is_document is True
        assert item.is_folder is False

    def test_create_folder_item(self, sample_folder_data: dict) -> None:
        """Test creating a folder CloudItem."""
        item = CloudItem.model_validate(sample_folder_data)
        assert item.id == "660e8400-e29b-41d4-a716-446655440001"
        assert item.item_type == ItemType.COLLECTION
        assert item.visible_name == "Test Folder"
        assert item.is_document is False
        assert item.is_folder is True

    def test_item_with_parent(self) -> None:
        """Test item with parent folder."""
        data = {
            "ID": "test-id",
            "Version": 1,
            "Type": "DocumentType",
            "VissibleName": "Nested Doc",
            "Parent": "parent-folder-id",
        }
        item = CloudItem.model_validate(data)
        assert item.parent == "parent-folder-id"


class TestServiceDiscoveryResponse:
    """Tests for ServiceDiscoveryResponse model."""

    def test_parse_success_response(self) -> None:
        """Test parsing successful discovery response."""
        data = {"Status": "OK", "Host": "storage.example.com"}
        response = ServiceDiscoveryResponse.model_validate(data)
        assert response.status == "OK"
        assert response.host == "storage.example.com"


class TestCloudClientInit:
    """Tests for CloudClient initialization."""

    def test_init_with_auth_client(self, auth_client: AuthClient) -> None:
        """Test initializing CloudClient with AuthClient."""
        cloud = CloudClient(auth_client)
        assert cloud.auth_client is auth_client
        assert cloud._storage_host is None
        assert cloud._items_cache is None

    def test_from_config(self, tmp_path: Path) -> None:
        """Test creating CloudClient from config."""
        config_file = tmp_path / ".rmapi"
        config_file.write_text(
            yaml.safe_dump({"devicetoken": "dev123", "usertoken": "user456"})
        )
        patch_target = "pyrmapi.native.auth._get_default_config_path"
        with patch(patch_target, return_value=config_file):
            cloud = CloudClient.from_config(config_file)
            assert cloud.auth_client.tokens is not None
            assert cloud.auth_client.tokens.device_token == "dev123"


class TestServiceDiscovery:
    """Tests for service discovery."""

    def test_discover_storage_host_success(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test successful service discovery."""
        cloud_client._storage_host = None  # Reset to trigger discovery

        discovery_url = SERVICE_DISCOVERY_URL.format(
            service="document-storage", api_ver="2"
        )
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=discovery_url,
            method="GET",
            json={"Status": "OK", "Host": "discovered-host.example.com"},
        )

        host = cloud_client.discover_storage_host()
        assert host == "discovered-host.example.com"

    def test_discover_storage_host_fallback_on_error(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test fallback to default host on discovery error."""
        cloud_client._storage_host = None

        discovery_url = SERVICE_DISCOVERY_URL.format(
            service="document-storage", api_ver="2"
        )
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=discovery_url,
            method="GET",
            status_code=500,
            text="Internal Server Error",
        )

        host = cloud_client.discover_storage_host()
        assert host == DEFAULT_STORAGE_HOST

    def test_discover_storage_host_fallback_on_bad_status(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test fallback when discovery returns non-OK status."""
        cloud_client._storage_host = None

        discovery_url = SERVICE_DISCOVERY_URL.format(
            service="document-storage", api_ver="2"
        )
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=discovery_url,
            method="GET",
            json={"Status": "Error", "Host": ""},
        )

        host = cloud_client.discover_storage_host()
        assert host == DEFAULT_STORAGE_HOST

    def test_storage_host_property_triggers_discovery(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test that storage_host property triggers discovery."""
        cloud_client._storage_host = None

        discovery_url = SERVICE_DISCOVERY_URL.format(
            service="document-storage", api_ver="2"
        )
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=discovery_url,
            method="GET",
            json={"Status": "OK", "Host": "auto-discovered.example.com"},
        )

        # Access property triggers discovery
        host = cloud_client.storage_host
        assert host == "auto-discovered.example.com"

        # Second access uses cached value
        host2 = cloud_client.storage_host
        assert host2 == "auto-discovered.example.com"


class TestListItems:
    """Tests for listing items."""

    def test_list_items_success(
        self,
        cloud_client: CloudClient,
        httpx_mock: pytest.fixture,  # type: ignore[valid-type]
        sample_item_data: dict,
        sample_folder_data: dict,
    ) -> None:
        """Test successful item listing."""
        # Mock token refresh
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[sample_item_data, sample_folder_data],
        )

        items = cloud_client.list_items()

        assert len(items) == 2
        assert items[0].visible_name == "Test Document"
        assert items[1].visible_name == "Test Folder"
        assert cloud_client._items_cache is not None

    def test_list_items_empty(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test listing when no items exist."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[],
        )

        items = cloud_client.list_items()
        assert items == []

    def test_list_items_with_blob(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test listing with blob URLs."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?withBlob=true"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[],
        )

        cloud_client.list_items(with_blob=True)
        # Verify the URL was called with withBlob parameter
        requests = httpx_mock.get_requests()  # type: ignore[attr-defined]
        assert any("withBlob=true" in str(r.url) for r in requests)

    def test_list_items_http_error(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test listing with HTTP error."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            status_code=500,
            text="Internal Server Error",
        )

        with pytest.raises(ListItemsError, match="500"):
            cloud_client.list_items()


class TestGetItem:
    """Tests for getting a single item."""

    def test_get_item_success(
        self,
        cloud_client: CloudClient,
        httpx_mock: pytest.fixture,  # type: ignore[valid-type]
        sample_item_data: dict,
    ) -> None:
        """Test getting a single item."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        item_id = "550e8400-e29b-41d4-a716-446655440000"
        get_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?doc={item_id}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=get_url,
            method="GET",
            json=[sample_item_data],
        )

        item = cloud_client.get_item(item_id)
        assert item.id == item_id
        assert item.visible_name == "Test Document"

    def test_get_item_not_found(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test getting non-existent item."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        item_id = "nonexistent-id"
        get_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?doc={item_id}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=get_url,
            method="GET",
            json=[],
        )

        with pytest.raises(ItemNotFoundError):
            cloud_client.get_item(item_id)


class TestFindItemByPath:
    """Tests for finding items by path."""

    def test_find_item_by_path_root_level(
        self,
        cloud_client: CloudClient,
        httpx_mock: pytest.fixture,  # type: ignore[valid-type]
        sample_item_data: dict,
    ) -> None:
        """Test finding item at root level."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[sample_item_data],
        )

        item = cloud_client.find_item_by_path("/Test Document")
        assert item is not None
        assert item.visible_name == "Test Document"

    def test_find_item_by_path_nested(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test finding nested item."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        folder_data = {
            "ID": "folder-id",
            "Version": 1,
            "Type": "CollectionType",
            "VissibleName": "My Folder",
            "Parent": "",
        }
        doc_data = {
            "ID": "doc-id",
            "Version": 1,
            "Type": "DocumentType",
            "VissibleName": "My Doc",
            "Parent": "folder-id",
        }

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[folder_data, doc_data],
        )

        item = cloud_client.find_item_by_path("/My Folder/My Doc")
        assert item is not None
        assert item.visible_name == "My Doc"

    def test_find_item_by_path_not_found(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test finding non-existent path."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[],
        )

        item = cloud_client.find_item_by_path("/Nonexistent")
        assert item is None


class TestCreateFolder:
    """Tests for folder creation."""

    def test_create_folder_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test successful folder creation."""
        # Mock multiple token refreshes (upload request + update status)
        mock_token_refresh(httpx_mock, times=2)

        upload_url = f"https://{DEFAULT_STORAGE_HOST}{UPLOAD_REQUEST_ENDPOINT}"
        httpx_mock.add_response(
            url=upload_url,
            method="PUT",
            json=[{
                "ID": "new-folder-id", "Version": 1, "Success": True, "Message": ""
            }],
        )

        update_url = f"https://{DEFAULT_STORAGE_HOST}{UPDATE_STATUS_ENDPOINT}"
        httpx_mock.add_response(
            url=update_url,
            method="PUT",
            json=[{
                "ID": "new-folder-id",
                "Version": 1,
                "Success": True,
                "Message": "",
                "Type": "CollectionType",
                "VissibleName": "New Folder",
                "Parent": "",
            }],
        )

        folder = cloud_client.create_folder("New Folder")
        assert folder.visible_name == "New Folder"
        assert folder.is_folder is True

    def test_create_folder_with_parent(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test creating folder in parent folder."""
        mock_token_refresh(httpx_mock, times=2)

        upload_url = f"https://{DEFAULT_STORAGE_HOST}{UPLOAD_REQUEST_ENDPOINT}"
        httpx_mock.add_response(
            url=upload_url,
            method="PUT",
            json=[{
                "ID": "new-folder-id", "Version": 1, "Success": True, "Message": ""
            }],
        )

        update_url = f"https://{DEFAULT_STORAGE_HOST}{UPDATE_STATUS_ENDPOINT}"
        httpx_mock.add_response(
            url=update_url,
            method="PUT",
            json=[{
                "ID": "new-folder-id",
                "Version": 1,
                "Success": True,
                "Message": "",
                "Type": "CollectionType",
                "VissibleName": "Subfolder",
                "Parent": "parent-id",
            }],
        )

        folder = cloud_client.create_folder("Subfolder", parent_id="parent-id")
        assert folder.parent == "parent-id"

    def test_create_folder_upload_request_fails(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test folder creation when upload request fails."""
        mock_token_refresh(httpx_mock, times=1)

        upload_url = f"https://{DEFAULT_STORAGE_HOST}{UPLOAD_REQUEST_ENDPOINT}"
        httpx_mock.add_response(
            url=upload_url,
            method="PUT",
            status_code=500,
            text="Server Error",
        )

        with pytest.raises(CreateFolderError, match="500"):
            cloud_client.create_folder("New Folder")


class TestUploadDocument:
    """Tests for document upload."""

    def test_upload_document_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test successful document upload."""
        # Create a test PDF file
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        # Mock multiple token refreshes (upload request + update status)
        mock_token_refresh(httpx_mock, times=2)

        upload_url = f"https://{DEFAULT_STORAGE_HOST}{UPLOAD_REQUEST_ENDPOINT}"
        httpx_mock.add_response(
            url=upload_url,
            method="PUT",
            json=[{
                "ID": "new-doc-id",
                "Version": 1,
                "Success": True,
                "Message": "",
                "BlobURLPut": "https://storage.example.com/upload/abc123",
            }],
        )

        # Mock blob upload
        httpx_mock.add_response(
            url="https://storage.example.com/upload/abc123",
            method="PUT",
            status_code=200,
        )

        update_url = f"https://{DEFAULT_STORAGE_HOST}{UPDATE_STATUS_ENDPOINT}"
        httpx_mock.add_response(
            url=update_url,
            method="PUT",
            json=[{
                "ID": "new-doc-id",
                "Version": 1,
                "Success": True,
                "Message": "",
                "Type": "DocumentType",
                "VissibleName": "test",
                "Parent": "",
            }],
        )

        doc = cloud_client.upload_document(pdf_file)
        assert doc.visible_name == "test"
        assert doc.is_document is True

    def test_upload_document_custom_name(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test upload with custom name."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        mock_token_refresh(httpx_mock, times=2)

        upload_url = f"https://{DEFAULT_STORAGE_HOST}{UPLOAD_REQUEST_ENDPOINT}"
        httpx_mock.add_response(
            url=upload_url,
            method="PUT",
            json=[{
                "ID": "new-doc-id",
                "Version": 1,
                "Success": True,
                "BlobURLPut": "https://storage.example.com/upload/abc123",
            }],
        )

        httpx_mock.add_response(
            url="https://storage.example.com/upload/abc123",
            method="PUT",
            status_code=200,
        )

        update_url = f"https://{DEFAULT_STORAGE_HOST}{UPDATE_STATUS_ENDPOINT}"
        httpx_mock.add_response(
            url=update_url,
            method="PUT",
            json=[{
                "ID": "new-doc-id",
                "Version": 1,
                "Success": True,
                "Type": "DocumentType",
                "VissibleName": "Custom Name",
                "Parent": "",
            }],
        )

        doc = cloud_client.upload_document(pdf_file, name="Custom Name")
        assert doc.visible_name == "Custom Name"

    def test_upload_document_file_not_found(
        self, cloud_client: CloudClient, tmp_path: Path
    ) -> None:
        """Test upload with non-existent file."""
        with pytest.raises(FileNotFoundError):
            cloud_client.upload_document(tmp_path / "nonexistent.pdf")

    def test_upload_document_no_blob_url(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test upload when no blob URL is returned."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        mock_token_refresh(httpx_mock, times=1)

        upload_url = f"https://{DEFAULT_STORAGE_HOST}{UPLOAD_REQUEST_ENDPOINT}"
        httpx_mock.add_response(
            url=upload_url,
            method="PUT",
            json=[{
                "ID": "new-doc-id",
                "Version": 1,
                "Success": True,
                "BlobURLPut": "",  # Empty URL
            }],
        )

        with pytest.raises(UploadError, match="No upload URL"):
            cloud_client.upload_document(pdf_file)


class TestDownloadDocument:
    """Tests for document download."""

    def test_download_document_success(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture, tmp_path: Path  # type: ignore[valid-type]
    ) -> None:
        """Test successful document download."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        item_id = "doc-id"
        get_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?doc={item_id}&withBlob=true"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=get_url,
            method="GET",
            json=[{
                "ID": item_id,
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "Test Doc",
                "BlobURLGet": "https://storage.example.com/download/abc123",
            }],
        )

        # Create a ZIP file with a PDF
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("doc.pdf", b"%PDF-1.4 test pdf content")
        zip_content = zip_buffer.getvalue()

        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://storage.example.com/download/abc123",
            method="GET",
            content=zip_content,
        )

        output_path = tmp_path / "downloaded.pdf"
        result = cloud_client.download_document(item_id, output_path)

        assert result == output_path
        assert output_path.exists()
        assert b"%PDF-1.4 test pdf content" in output_path.read_bytes()

    def test_download_document_no_blob_url(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture, tmp_path: Path  # type: ignore[valid-type]
    ) -> None:
        """Test download when no blob URL is available."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        item_id = "doc-id"
        get_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?doc={item_id}&withBlob=true"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=get_url,
            method="GET",
            json=[{
                "ID": item_id,
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "Test Doc",
                "BlobURLGet": "",  # No URL
            }],
        )

        with pytest.raises(DownloadError, match="No download URL"):
            cloud_client.download_document(item_id, tmp_path / "output.pdf")


class TestMoveItem:
    """Tests for moving/renaming items."""

    def test_move_item_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test successful item move."""
        # Mock token refresh for get_item + move_item
        mock_token_refresh(httpx_mock, times=2)

        item_id = "doc-id"
        get_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?doc={item_id}"
        httpx_mock.add_response(
            url=get_url,
            method="GET",
            json=[{
                "ID": item_id,
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "Test Doc",
                "Parent": "",
                "Bookmarked": False,
            }],
        )

        update_url = f"https://{DEFAULT_STORAGE_HOST}{UPDATE_STATUS_ENDPOINT}"
        httpx_mock.add_response(
            url=update_url,
            method="PUT",
            json=[{
                "ID": item_id,
                "Version": 2,
                "Success": True,
                "Message": "",
                "Type": "DocumentType",
                "VissibleName": "Test Doc",
                "Parent": "new-parent-id",
            }],
        )

        result = cloud_client.move_item(item_id, new_parent_id="new-parent-id")
        assert result.parent == "new-parent-id"
        assert result.version == 2

    def test_rename_item_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test successful item rename."""
        # Mock token refresh for get_item x2 (rename_item + move_item) + update
        mock_token_refresh(httpx_mock, times=3)

        item_id = "doc-id"
        get_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?doc={item_id}"
        # Need to mock get_item twice (once for rename_item, once for move_item)
        httpx_mock.add_response(
            url=get_url,
            method="GET",
            json=[{
                "ID": item_id,
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "Old Name",
                "Parent": "",
                "Bookmarked": False,
            }],
        )
        httpx_mock.add_response(
            url=get_url,
            method="GET",
            json=[{
                "ID": item_id,
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "Old Name",
                "Parent": "",
                "Bookmarked": False,
            }],
        )

        update_url = f"https://{DEFAULT_STORAGE_HOST}{UPDATE_STATUS_ENDPOINT}"
        httpx_mock.add_response(
            url=update_url,
            method="PUT",
            json=[{
                "ID": item_id,
                "Version": 2,
                "Success": True,
                "Type": "DocumentType",
                "VissibleName": "New Name",
                "Parent": "",
            }],
        )

        result = cloud_client.rename_item(item_id, "New Name")
        assert result.visible_name == "New Name"


class TestDeleteItem:
    """Tests for item deletion."""

    def test_delete_item_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test successful item deletion."""
        # Mock token refresh for get_item + delete_item
        mock_token_refresh(httpx_mock, times=2)

        item_id = "doc-id"
        get_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?doc={item_id}"
        httpx_mock.add_response(
            url=get_url,
            method="GET",
            json=[{
                "ID": item_id,
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "Test Doc",
            }],
        )

        delete_url = f"https://{DEFAULT_STORAGE_HOST}{DELETE_ENDPOINT}"
        httpx_mock.add_response(
            url=delete_url,
            method="PUT",
            json=[{"ID": item_id, "Success": True, "Message": ""}],
        )

        result = cloud_client.delete_item(item_id)
        assert result is True

    def test_delete_item_failure(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test delete when operation fails."""
        # Mock token refresh for get_item + delete_item
        mock_token_refresh(httpx_mock, times=2)

        item_id = "doc-id"
        get_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}?doc={item_id}"
        httpx_mock.add_response(
            url=get_url,
            method="GET",
            json=[{
                "ID": item_id,
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "Test Doc",
            }],
        )

        delete_url = f"https://{DEFAULT_STORAGE_HOST}{DELETE_ENDPOINT}"
        httpx_mock.add_response(
            url=delete_url,
            method="PUT",
            json=[{"ID": item_id, "Success": False, "Message": "Delete failed"}],
        )

        with pytest.raises(DeleteError, match="Delete failed"):
            cloud_client.delete_item(item_id)


class TestGetItemPath:
    """Tests for getting item paths."""

    def test_get_item_path_root_level(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test getting path for root-level item."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[{
                "ID": "doc-id",
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "My Document",
                "Parent": "",
            }],
        )

        items = cloud_client.list_items()
        path = cloud_client.get_item_path(items[0])
        assert path == "/My Document"

    def test_get_item_path_nested(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test getting path for nested item."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[
                {
                    "ID": "folder-id",
                    "Version": 1,
                    "Type": "CollectionType",
                    "VissibleName": "My Folder",
                    "Parent": "",
                },
                {
                    "ID": "doc-id",
                    "Version": 1,
                    "Type": "DocumentType",
                    "VissibleName": "My Document",
                    "Parent": "folder-id",
                },
            ],
        )

        items = cloud_client.list_items()
        doc = next(i for i in items if i.id == "doc-id")
        path = cloud_client.get_item_path(doc)
        assert path == "/My Folder/My Document"


@pytest.mark.asyncio
class TestAsyncMethods:
    """Tests for async cloud operations."""

    async def test_list_items_async(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test async item listing."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        list_url = f"https://{DEFAULT_STORAGE_HOST}{LIST_DOCS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=list_url,
            method="GET",
            json=[{
                "ID": "doc-id",
                "Version": 1,
                "Type": "DocumentType",
                "VissibleName": "Async Doc",
                "Parent": "",
            }],
        )

        items = await cloud_client.list_items_async()
        assert len(items) == 1
        assert items[0].visible_name == "Async Doc"

    async def test_create_folder_async(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test async folder creation."""
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://my.remarkable.com/token/json/2/user/new",
            method="POST",
            text="refreshed_token",
        )

        upload_url = f"https://{DEFAULT_STORAGE_HOST}{UPLOAD_REQUEST_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=upload_url,
            method="PUT",
            json=[{"ID": "new-folder-id", "Version": 1, "Success": True}],
        )

        update_url = f"https://{DEFAULT_STORAGE_HOST}{UPDATE_STATUS_ENDPOINT}"
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=update_url,
            method="PUT",
            json=[{
                "ID": "new-folder-id",
                "Version": 1,
                "Success": True,
                "Type": "CollectionType",
                "VissibleName": "Async Folder",
                "Parent": "",
            }],
        )

        folder = await cloud_client.create_folder_async("Async Folder")
        assert folder.visible_name == "Async Folder"

    async def test_discover_storage_host_async(
        self, cloud_client: CloudClient, httpx_mock: pytest.fixture  # type: ignore[valid-type]
    ) -> None:
        """Test async service discovery."""
        cloud_client._storage_host = None

        discovery_url = SERVICE_DISCOVERY_URL.format(
            service="document-storage", api_ver="2"
        )
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url=discovery_url,
            method="GET",
            json={"Status": "OK", "Host": "async-discovered.example.com"},
        )

        host = await cloud_client.discover_storage_host_async()
        assert host == "async-discovered.example.com"
