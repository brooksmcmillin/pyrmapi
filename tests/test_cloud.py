"""Tests for the native cloud storage module."""

from __future__ import annotations

import base64
import json
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
    DownloadError,
    ItemNotFoundError,
    ItemType,
    ListItemsError,
    UploadError,
)
from pyrmapi.native.auth import USER_TOKEN_URL
from pyrmapi.native.cloud import (
    SYNC_FILES_ENDPOINT,
    SYNC_HOST,
    SYNC_ROOT_ENDPOINT,
    UPLOAD_ENDPOINT,
    UPLOAD_HOST,
)

# =============================================================================
# Test data helpers
# =============================================================================

DOC_UUID = "550e8400-e29b-41d4-a716-446655440000"
FOLDER_UUID = "660e8400-e29b-41d4-a716-446655440001"
ROOT_HASH = "aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb"
DOC_INDEX_HASH = "1111111111111111111111111111111111111111111111111111111111111111"
FOLDER_INDEX_HASH = "2222222222222222222222222222222222222222222222222222222222222222"
DOC_META_HASH = "3333333333333333333333333333333333333333333333333333333333333333"
DOC_CONTENT_HASH = "4444444444444444444444444444444444444444444444444444444444444444"
DOC_PDF_HASH = "5555555555555555555555555555555555555555555555555555555555555555"
FOLDER_META_HASH = "6666666666666666666666666666666666666666666666666666666666666666"


def make_index_blob(entries: list[tuple[str, str, str, int, int]]) -> bytes:
    """Build an index blob from (hash, type, id, subfiles, size) tuples."""
    lines = ["3"]  # schema version
    for h, t, i, sf, sz in entries:
        lines.append(f"{h}:{t}:{i}:{sf}:{sz}")
    return "\n".join(lines).encode()


def make_metadata_json(
    visible_name: str,
    parent: str = "",
    item_type: str = "DocumentType",
    deleted: bool = False,
    last_modified: str = "1772514968895",
) -> bytes:
    return json.dumps({
        "visibleName": visible_name,
        "parent": parent,
        "type": item_type,
        "lastModified": last_modified,
        "deleted": deleted,
        "metadatamodified": False,
        "modified": False,
        "pinned": False,
        "synced": True,
        "version": 1,
    }).encode()


def make_content_json(file_type: str = "pdf", page_count: int = 5) -> bytes:
    return json.dumps({
        "fileType": file_type,
        "pageCount": page_count,
    }).encode()


# Root index containing one document and one folder entry
ROOT_INDEX_BLOB = make_index_blob([
    (DOC_INDEX_HASH, "80000000", DOC_UUID, 3, 0),
    (FOLDER_INDEX_HASH, "80000000", FOLDER_UUID, 1, 0),
])

# Document index containing .metadata, .content, and .pdf
DOC_INDEX_BLOB = make_index_blob([
    (DOC_META_HASH, "0", f"{DOC_UUID}.metadata", 0, 100),
    (DOC_CONTENT_HASH, "0", f"{DOC_UUID}.content", 0, 50),
    (DOC_PDF_HASH, "0", f"{DOC_UUID}.pdf", 0, 1024),
])

# Folder index containing .metadata only
FOLDER_INDEX_BLOB = make_index_blob([
    (FOLDER_META_HASH, "0", f"{FOLDER_UUID}.metadata", 0, 80),
])

DOC_METADATA_BLOB = make_metadata_json("Test Document", item_type="DocumentType")
DOC_CONTENT_BLOB = make_content_json("pdf", 10)
FOLDER_METADATA_BLOB = make_metadata_json(
    "Test Folder", item_type="CollectionType"
)

PDF_CONTENT = b"%PDF-1.4 test pdf content"


# =============================================================================
# Fixtures
# =============================================================================


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
    return CloudClient(auth_client)


def mock_token_refresh(httpx_mock: HTTPXMock) -> None:
    """Add a token refresh mock (reusable via can_send_already_matched_responses)."""
    httpx_mock.add_response(
        url=USER_TOKEN_URL,
        method="POST",
        text="refreshed_token",
    )


def mock_sync_tree(httpx_mock: HTTPXMock) -> None:
    """Mock the full sync tree: root hash, root index, doc/folder indexes, metadata."""
    # Root hash
    httpx_mock.add_response(
        url=f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
        method="GET",
        json={"hash": ROOT_HASH, "generation": 42, "schemaVersion": 3},
    )

    # Root index blob
    httpx_mock.add_response(
        url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{ROOT_HASH}",
        method="GET",
        content=ROOT_INDEX_BLOB,
    )

    # Document index
    httpx_mock.add_response(
        url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{DOC_INDEX_HASH}",
        method="GET",
        content=DOC_INDEX_BLOB,
    )

    # Document metadata
    httpx_mock.add_response(
        url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{DOC_META_HASH}",
        method="GET",
        content=DOC_METADATA_BLOB,
    )

    # Document content
    httpx_mock.add_response(
        url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{DOC_CONTENT_HASH}",
        method="GET",
        content=DOC_CONTENT_BLOB,
    )

    # Folder index
    httpx_mock.add_response(
        url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{FOLDER_INDEX_HASH}",
        method="GET",
        content=FOLDER_INDEX_BLOB,
    )

    # Folder metadata
    httpx_mock.add_response(
        url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{FOLDER_META_HASH}",
        method="GET",
        content=FOLDER_METADATA_BLOB,
    )


# =============================================================================
# Tests
# =============================================================================


class TestParseIndex:
    """Tests for CloudClient._parse_index."""

    def test_parse_basic_index(self) -> None:
        """Test parsing a basic v3 index blob."""
        blob = make_index_blob([
            ("abc123", "80000000", "some-uuid", 3, 0),
            ("def456", "0", "file.pdf", 0, 1024),
        ])
        entries = CloudClient._parse_index(blob)
        assert len(entries) == 2
        assert entries[0].hash == "abc123"
        assert entries[0].entry_type == "80000000"
        assert entries[0].id == "some-uuid"
        assert entries[0].subfiles == 3
        assert entries[0].is_index is True
        assert entries[0].is_file is False
        assert entries[1].hash == "def456"
        assert entries[1].entry_type == "0"
        assert entries[1].id == "file.pdf"
        assert entries[1].size == 1024
        assert entries[1].is_index is False
        assert entries[1].is_file is True

    def test_parse_empty_index(self) -> None:
        """Test parsing empty index."""
        entries = CloudClient._parse_index(b"3\n")
        assert entries == []

    def test_parse_index_skips_short_lines(self) -> None:
        """Test that short/malformed lines are skipped."""
        blob = b"3\nabc:80000000:id:3:0\nshort:line\n"
        entries = CloudClient._parse_index(blob)
        assert len(entries) == 1


class TestCloudItemModel:
    """Tests for CloudItem model."""

    def test_create_document_item(self) -> None:
        """Test creating a document CloudItem."""
        item = CloudItem(
            id=DOC_UUID,
            hash="abc123",
            item_type=ItemType.DOCUMENT,
            visible_name="Test Document",
            file_type="pdf",
        )
        assert item.id == DOC_UUID
        assert item.item_type == ItemType.DOCUMENT
        assert item.visible_name == "Test Document"
        assert item.is_document is True
        assert item.is_folder is False

    def test_create_folder_item(self) -> None:
        """Test creating a folder CloudItem."""
        item = CloudItem(
            id=FOLDER_UUID,
            item_type=ItemType.COLLECTION,
            visible_name="Test Folder",
        )
        assert item.is_document is False
        assert item.is_folder is True


class TestCloudClientInit:
    """Tests for CloudClient initialization."""

    def test_init_with_auth_client(self, auth_client: AuthClient) -> None:
        """Test initializing CloudClient with AuthClient."""
        cloud = CloudClient(auth_client)
        assert cloud.auth_client is auth_client
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


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
class TestListItems:
    """Tests for listing items via sync tree walk."""

    def test_list_items_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test successful item listing."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        items = cloud_client.list_items()

        assert len(items) == 2

        doc = next(i for i in items if i.id == DOC_UUID)
        assert doc.visible_name == "Test Document"
        assert doc.item_type == ItemType.DOCUMENT
        assert doc.is_document is True
        assert doc.file_type == "pdf"

        folder = next(i for i in items if i.id == FOLDER_UUID)
        assert folder.visible_name == "Test Folder"
        assert folder.item_type == ItemType.COLLECTION
        assert folder.is_folder is True

        assert cloud_client._items_cache is not None

    def test_list_items_empty(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test listing when no items exist."""
        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
            method="GET",
            json={"hash": ROOT_HASH, "generation": 1, "schemaVersion": 3},
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{ROOT_HASH}",
            method="GET",
            content=b"3\n",
        )

        items = cloud_client.list_items()
        assert items == []

    def test_list_items_skips_deleted(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test that deleted items are skipped."""
        mock_token_refresh(httpx_mock)

        deleted_meta = make_metadata_json("Deleted Doc", deleted=True)
        deleted_meta_hash = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        del_doc_hash = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        del_uuid = "770e8400-e29b-41d4-a716-446655440002"

        root_blob = make_index_blob([
            (del_doc_hash, "80000000", del_uuid, 1, 0),
        ])
        doc_blob = make_index_blob([
            (deleted_meta_hash, "0", f"{del_uuid}.metadata", 0, 80),
        ])

        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
            method="GET",
            json={"hash": ROOT_HASH, "generation": 1, "schemaVersion": 3},
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{ROOT_HASH}",
            method="GET",
            content=root_blob,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{del_doc_hash}",
            method="GET",
            content=doc_blob,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{deleted_meta_hash}",
            method="GET",
            content=deleted_meta,
        )

        items = cloud_client.list_items()
        assert items == []

    def test_list_items_root_hash_error(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test listing with HTTP error on root hash."""
        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
            method="GET",
            status_code=500,
            text="Internal Server Error",
        )

        with pytest.raises(ListItemsError, match="500"):
            cloud_client.list_items()


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
class TestGetItem:
    """Tests for getting a single item from cache."""

    def test_get_item_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test getting a single item populates cache then returns."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        item = cloud_client.get_item(DOC_UUID)
        assert item.id == DOC_UUID
        assert item.visible_name == "Test Document"

    def test_get_item_not_found(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test getting non-existent item."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        with pytest.raises(ItemNotFoundError):
            cloud_client.get_item("nonexistent-id")


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
class TestFindItemByPath:
    """Tests for finding items by path."""

    def test_find_item_by_path_root_level(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test finding item at root level."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        item = cloud_client.find_item_by_path("/Test Document")
        assert item is not None
        assert item.visible_name == "Test Document"

    def test_find_item_by_path_nested(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test finding nested item."""
        mock_token_refresh(httpx_mock)

        # Build tree with a doc nested in a folder
        nested_doc_uuid = "880e8400-e29b-41d4-a716-446655440003"
        nested_doc_hash = "7777777777777777777777777777777777777777777777777777777777777777"
        nested_meta_hash = "8888888888888888888888888888888888888888888888888888888888888888"

        root_blob = make_index_blob([
            (FOLDER_INDEX_HASH, "80000000", FOLDER_UUID, 1, 0),
            (nested_doc_hash, "80000000", nested_doc_uuid, 1, 0),
        ])
        nested_doc_blob = make_index_blob([
            (nested_meta_hash, "0", f"{nested_doc_uuid}.metadata", 0, 80),
        ])
        nested_meta = make_metadata_json(
            "My Doc", parent=FOLDER_UUID, item_type="DocumentType"
        )

        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
            method="GET",
            json={"hash": ROOT_HASH, "generation": 1, "schemaVersion": 3},
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{ROOT_HASH}",
            method="GET",
            content=root_blob,
        )
        # Folder
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{FOLDER_INDEX_HASH}",
            method="GET",
            content=FOLDER_INDEX_BLOB,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{FOLDER_META_HASH}",
            method="GET",
            content=FOLDER_METADATA_BLOB,
        )
        # Nested doc
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{nested_doc_hash}",
            method="GET",
            content=nested_doc_blob,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{nested_meta_hash}",
            method="GET",
            content=nested_meta,
        )

        item = cloud_client.find_item_by_path("/Test Folder/My Doc")
        assert item is not None
        assert item.visible_name == "My Doc"

    def test_find_item_by_path_not_found(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test finding non-existent path."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        item = cloud_client.find_item_by_path("/Nonexistent")
        assert item is None


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
class TestGetItemPath:
    """Tests for getting item paths."""

    def test_get_item_path_root_level(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test getting path for root-level item."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        items = cloud_client.list_items()
        doc = next(i for i in items if i.id == DOC_UUID)
        path = cloud_client.get_item_path(doc)
        assert path == "/Test Document"

    def test_get_item_path_nested(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test getting path for nested item."""
        mock_token_refresh(httpx_mock)

        nested_doc_uuid = "880e8400-e29b-41d4-a716-446655440003"
        nested_doc_hash = "7777777777777777777777777777777777777777777777777777777777777777"
        nested_meta_hash = "8888888888888888888888888888888888888888888888888888888888888888"

        root_blob = make_index_blob([
            (FOLDER_INDEX_HASH, "80000000", FOLDER_UUID, 1, 0),
            (nested_doc_hash, "80000000", nested_doc_uuid, 1, 0),
        ])
        nested_doc_blob = make_index_blob([
            (nested_meta_hash, "0", f"{nested_doc_uuid}.metadata", 0, 80),
        ])
        nested_meta = make_metadata_json(
            "My Document", parent=FOLDER_UUID, item_type="DocumentType"
        )

        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
            method="GET",
            json={"hash": ROOT_HASH, "generation": 1, "schemaVersion": 3},
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{ROOT_HASH}",
            method="GET",
            content=root_blob,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{FOLDER_INDEX_HASH}",
            method="GET",
            content=FOLDER_INDEX_BLOB,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{FOLDER_META_HASH}",
            method="GET",
            content=FOLDER_METADATA_BLOB,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{nested_doc_hash}",
            method="GET",
            content=nested_doc_blob,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{nested_meta_hash}",
            method="GET",
            content=nested_meta,
        )

        items = cloud_client.list_items()
        doc = next(i for i in items if i.id == nested_doc_uuid)
        path = cloud_client.get_item_path(doc)
        assert path == "/Test Folder/My Document"


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
class TestCreateFolder:
    """Tests for folder creation via v2 API."""

    def test_create_folder_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test successful folder creation."""
        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "new-folder-id", "hash": "newhash123"},
        )

        folder = cloud_client.create_folder("New Folder")
        assert folder.id == "new-folder-id"
        assert folder.visible_name == "New Folder"
        assert folder.is_folder is True

    def test_create_folder_with_parent(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test creating folder in parent folder."""
        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "new-folder-id", "hash": "newhash123"},
        )

        folder = cloud_client.create_folder("Subfolder", parent_id="parent-id")
        assert folder.parent == "parent-id"

    def test_create_folder_http_error(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test folder creation when API returns error."""
        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            status_code=500,
            text="Server Error",
        )

        with pytest.raises(CreateFolderError, match="500"):
            cloud_client.create_folder("New Folder")

    def test_create_folder_sends_correct_headers(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test that folder creation sends correct Content-Type and rm-meta."""
        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "id", "hash": "h"},
        )

        cloud_client.create_folder("My Folder")

        requests = httpx_mock.get_requests()
        upload_req = next(
            r for r in requests if str(r.url).startswith(UPLOAD_HOST)
        )
        assert upload_req.headers["Content-Type"] == "folder"
        assert upload_req.headers["rm-source"] == "RoR-Browser"

        rm_meta = json.loads(base64.b64decode(upload_req.headers["rm-meta"]))
        assert rm_meta["file_name"] == "My Folder"


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
class TestUploadDocument:
    """Tests for document upload via v2 API."""

    def test_upload_document_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test successful document upload."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "new-doc-id", "hash": "dochash123"},
        )

        doc = cloud_client.upload_document(pdf_file)
        assert doc.id == "new-doc-id"
        assert doc.visible_name == "test"
        assert doc.is_document is True
        assert doc.file_type == "pdf"

    def test_upload_document_custom_name(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test upload with custom name."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "new-doc-id", "hash": "dochash123"},
        )

        doc = cloud_client.upload_document(pdf_file, name="Custom Name")
        assert doc.visible_name == "Custom Name"

    def test_upload_document_epub(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test uploading an EPUB file."""
        epub_file = tmp_path / "test.epub"
        epub_file.write_bytes(b"PK\x03\x04 epub content")

        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "new-epub-id", "hash": "epubhash"},
        )

        doc = cloud_client.upload_document(epub_file)
        assert doc.file_type == "epub"

        requests = httpx_mock.get_requests()
        upload_req = next(
            r for r in requests if str(r.url).startswith(UPLOAD_HOST)
        )
        assert upload_req.headers["Content-Type"] == "application/epub+zip"

    def test_upload_document_file_not_found(
        self, cloud_client: CloudClient, tmp_path: Path
    ) -> None:
        """Test upload with non-existent file."""
        with pytest.raises(FileNotFoundError):
            cloud_client.upload_document(tmp_path / "nonexistent.pdf")

    def test_upload_document_http_error(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test upload when API returns error."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            status_code=500,
            text="Server Error",
        )

        with pytest.raises(UploadError, match="500"):
            cloud_client.upload_document(pdf_file)

    def test_upload_sends_correct_headers(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test that upload sends correct headers and body."""
        pdf_file = tmp_path / "test.pdf"
        pdf_content = b"%PDF-1.4 test content"
        pdf_file.write_bytes(pdf_content)

        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "id", "hash": "h"},
        )

        cloud_client.upload_document(pdf_file, name="My PDF")

        requests = httpx_mock.get_requests()
        upload_req = next(
            r for r in requests if str(r.url).startswith(UPLOAD_HOST)
        )
        assert upload_req.headers["Content-Type"] == "application/pdf"
        assert upload_req.headers["rm-source"] == "RoR-Browser"
        assert upload_req.content == pdf_content

        rm_meta = json.loads(base64.b64decode(upload_req.headers["rm-meta"]))
        assert rm_meta["file_name"] == "My PDF"


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
class TestDownloadDocument:
    """Tests for document download via sync tree."""

    def test_download_document_success(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test successful document download."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        # Mock the PDF blob fetch
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{DOC_PDF_HASH}",
            method="GET",
            content=PDF_CONTENT,
        )

        output_path = tmp_path / "downloaded.pdf"
        result = cloud_client.download_document(DOC_UUID, output_path)

        assert result == output_path
        assert output_path.exists()
        assert output_path.read_bytes() == PDF_CONTENT

    def test_download_document_not_found(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test download when document is not in tree."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        with pytest.raises(ItemNotFoundError):
            cloud_client.download_document("nonexistent-id", tmp_path / "out.pdf")

    def test_download_document_no_pdf_in_index(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test download when document index has no PDF entry."""
        mock_token_refresh(httpx_mock)

        # Manually set up a doc with no pdf entry
        no_pdf_uuid = "990e8400-e29b-41d4-a716-446655440099"
        no_pdf_hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        no_pdf_meta_hash = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        root_blob = make_index_blob([
            (no_pdf_hash, "80000000", no_pdf_uuid, 1, 0),
        ])
        doc_blob = make_index_blob([
            (no_pdf_meta_hash, "0", f"{no_pdf_uuid}.metadata", 0, 80),
        ])
        meta = make_metadata_json("No PDF Doc")

        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_ROOT_ENDPOINT}",
            method="GET",
            json={"hash": ROOT_HASH, "generation": 1, "schemaVersion": 3},
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{ROOT_HASH}",
            method="GET",
            content=root_blob,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{no_pdf_hash}",
            method="GET",
            content=doc_blob,
        )
        httpx_mock.add_response(
            url=f"{SYNC_HOST}{SYNC_FILES_ENDPOINT}/{no_pdf_meta_hash}",
            method="GET",
            content=meta,
        )

        # list_items first to populate cache
        cloud_client.list_items()

        with pytest.raises(DownloadError, match="No PDF/EPUB"):
            cloud_client.download_document(no_pdf_uuid, tmp_path / "out.pdf")


class TestStubbedOperations:
    """Tests for move/rename/delete (not yet implemented)."""

    def test_move_item_raises(self, cloud_client: CloudClient) -> None:
        with pytest.raises(NotImplementedError):
            cloud_client.move_item("id")

    def test_rename_item_raises(self, cloud_client: CloudClient) -> None:
        with pytest.raises(NotImplementedError):
            cloud_client.rename_item("id", "name")

    def test_delete_item_raises(self, cloud_client: CloudClient) -> None:
        with pytest.raises(NotImplementedError):
            cloud_client.delete_item("id")


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
@pytest.mark.asyncio
class TestAsyncMethods:
    """Tests for async cloud operations."""

    async def test_list_items_async(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test async item listing."""
        mock_token_refresh(httpx_mock)
        mock_sync_tree(httpx_mock)

        items = await cloud_client.list_items_async()
        assert len(items) == 2
        doc = next(i for i in items if i.id == DOC_UUID)
        assert doc.visible_name == "Test Document"

    async def test_create_folder_async(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock
    ) -> None:
        """Test async folder creation."""
        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "new-folder-id", "hash": "newhash"},
        )

        folder = await cloud_client.create_folder_async("Async Folder")
        assert folder.visible_name == "Async Folder"
        assert folder.is_folder is True

    async def test_upload_document_async(
        self, cloud_client: CloudClient, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Test async document upload."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 async test")

        mock_token_refresh(httpx_mock)

        httpx_mock.add_response(
            url=f"{UPLOAD_HOST}{UPLOAD_ENDPOINT}",
            method="POST",
            json={"docID": "async-doc-id", "hash": "asynchash"},
        )

        doc = await cloud_client.upload_document_async(pdf_file)
        assert doc.id == "async-doc-id"
        assert doc.is_document is True

    async def test_move_item_async_raises(
        self, cloud_client: CloudClient
    ) -> None:
        with pytest.raises(NotImplementedError):
            await cloud_client.move_item_async("id")

    async def test_rename_item_async_raises(
        self, cloud_client: CloudClient
    ) -> None:
        with pytest.raises(NotImplementedError):
            await cloud_client.rename_item_async("id", "name")

    async def test_delete_item_async_raises(
        self, cloud_client: CloudClient
    ) -> None:
        with pytest.raises(NotImplementedError):
            await cloud_client.delete_item_async("id")
