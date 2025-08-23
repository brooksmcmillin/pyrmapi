import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, mock_open
import pytest

from pyrmapi.rmapi import RMAPI


class TestRMAPI:
    """Test suite for RMAPI class."""

    @patch("pyrmapi.rmapi.Path.exists")
    def test_init_with_default_config(self, mock_exists):
        """Test RMAPI initialization with default config path."""
        mock_exists.return_value = True
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            
        assert "RMAPI_CONFIG" in rmapi.env
        assert rmapi.env["RMAPI_CONFIG"] == "./.rmapi"

    @patch("pyrmapi.rmapi.Path.exists")
    def test_init_with_custom_config(self, mock_exists):
        """Test RMAPI initialization with custom config path."""
        mock_exists.return_value = True
        custom_path = "~/.rmapi"
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI(config_path=custom_path)
            
        # Should expand ~ to home directory
        expected_path = os.path.expanduser(custom_path)
        assert rmapi.env["RMAPI_CONFIG"] == expected_path

    @patch("pyrmapi.rmapi.Path.exists")
    @patch("pyrmapi.rmapi.urllib.request.urlretrieve")
    @patch("pyrmapi.rmapi.tarfile.open")
    @patch("pyrmapi.rmapi.os.chmod")
    @patch("pyrmapi.rmapi.os.remove")
    def test_setup_downloads_rmapi(self, mock_remove, mock_chmod, mock_tarfile, mock_urlretrieve, mock_exists):
        """Test setup method downloads and extracts rmapi when not present."""
        mock_exists.side_effect = [False, True]  # First check fails, second succeeds after download
        mock_tar = Mock()
        mock_tarfile.return_value.__enter__.return_value = mock_tar
        
        rmapi = RMAPI()
        
        mock_urlretrieve.assert_called_once()
        mock_tar.extractall.assert_called_once_with("./bin")
        mock_chmod.assert_called_once()
        mock_remove.assert_called_once()

    @patch("pyrmapi.rmapi.Path.exists")
    def test_setup_skips_download_when_exists(self, mock_exists):
        """Test setup method skips download when rmapi already exists."""
        mock_exists.return_value = True
        
        with patch("pyrmapi.rmapi.urllib.request.urlretrieve") as mock_urlretrieve:
            rmapi = RMAPI()
            mock_urlretrieve.assert_not_called()

    @patch("pyrmapi.rmapi.subprocess.run")
    @patch("pyrmapi.rmapi.Path.exists")
    def test_run_command_success(self, mock_exists, mock_run):
        """Test _run_command method with successful execution."""
        mock_exists.return_value = True
        mock_run.return_value = Mock(
            returncode=0,
            stdout="success output",
            stderr=""
        )
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi._run_command(["ls", "/"])
            
        mock_run.assert_called_once_with(
            ["./bin/rmapi", "ls", "/"],
            capture_output=True,
            text=True,
            check=False,
            env=rmapi.env,
            timeout=10
        )
        assert result.stdout == "success output"

    @patch("pyrmapi.rmapi.subprocess.run")
    @patch("pyrmapi.rmapi.Path.exists")
    @patch("pyrmapi.rmapi.logging.error")
    def test_run_command_with_stderr(self, mock_log_error, mock_exists, mock_run):
        """Test _run_command method logs stderr when present."""
        mock_exists.return_value = True
        mock_run.return_value = Mock(
            returncode=1,
            stdout="",
            stderr="error message"
        )
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            rmapi._run_command(["ls", "/"])
            
        mock_log_error.assert_called_once_with("error message")

    @patch("pyrmapi.rmapi.subprocess.run")
    @patch("pyrmapi.rmapi.Path.exists")
    def test_ls_method(self, mock_exists, mock_run):
        """Test ls method."""
        mock_exists.return_value = True
        mock_run.return_value = Mock(
            returncode=0,
            stdout="[d] folder1\n[f] file1.pdf",
            stderr=""
        )
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi.ls(Path("/"))
            
        assert result == "[d] folder1\n[f] file1.pdf"
        mock_run.assert_called_once_with(
            ["./bin/rmapi", "ls", "/"],
            capture_output=True,
            text=True,
            check=False,
            env=rmapi.env,
            timeout=10
        )

    @patch("pyrmapi.rmapi.subprocess.run")
    @patch("pyrmapi.rmapi.Path.exists")
    def test_mkdir_method(self, mock_exists, mock_run):
        """Test mkdir method."""
        mock_exists.return_value = True
        mock_run.return_value = Mock(
            returncode=0,
            stdout="Directory created",
            stderr=""
        )
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi.mkdir(Path("/test"))
            
        assert result == "Directory created"
        mock_run.assert_called_once_with(
            ["./bin/rmapi", "mkdir", "/test"],
            capture_output=True,
            text=True,
            check=False,
            env=rmapi.env,
            timeout=10
        )

    @patch("pyrmapi.rmapi.subprocess.run")
    @patch("pyrmapi.rmapi.Path.exists")
    def test_mv_method(self, mock_exists, mock_run):
        """Test mv method."""
        mock_exists.return_value = True
        mock_run.return_value = Mock(
            returncode=0,
            stdout="File moved",
            stderr=""
        )
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi.mv(Path("/old"), Path("/new"))
            
        assert result == "File moved"
        mock_run.assert_called_once_with(
            ["./bin/rmapi", "mv", "/old", "/new"],
            capture_output=True,
            text=True,
            check=False,
            env=rmapi.env,
            timeout=10
        )

    @patch("pyrmapi.rmapi.subprocess.run")
    @patch("pyrmapi.rmapi.Path.exists")
    @patch("pyrmapi.rmapi.logging.error")
    def test_put_method_success(self, mock_log_error, mock_exists, mock_run):
        """Test put method with successful upload."""
        mock_exists.return_value = True
        mock_run.return_value = Mock(
            returncode=0,
            stdout="Upload successful",
            stderr=""
        )
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi.put(Path("/local/file.pdf"), Path("/remote"))
            
        assert result == "Upload successful"
        mock_log_error.assert_not_called()

    @patch("pyrmapi.rmapi.subprocess.run")
    @patch("pyrmapi.rmapi.Path.exists")
    @patch("pyrmapi.rmapi.logging.error")
    def test_put_method_failure(self, mock_log_error, mock_exists, mock_run):
        """Test put method with upload failure."""
        mock_exists.return_value = True
        mock_run.return_value = Mock(
            returncode=1,
            stdout="",
            stderr="Upload failed"
        )
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            rmapi.put(Path("/local/file.pdf"), Path("/remote"))
            
        mock_log_error.assert_called_with("Upload failed: Upload failed")

    @patch.object(RMAPI, "ls")
    @patch.object(RMAPI, "mkdir")
    @patch("pyrmapi.rmapi.Path.exists")
    def test_ensure_directory_creates_missing_directory(self, mock_exists, mock_mkdir, mock_ls):
        """Test ensure_directory creates directory when it doesn't exist."""
        mock_exists.return_value = True
        mock_ls.return_value = "[d] other_folder"  # Directory not in listing
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi.ensure_directory(Path("/papers/test"))
            
        assert result is True
        mock_ls.assert_called_once_with(Path("/papers"))
        mock_mkdir.assert_called_once_with(Path("/papers/test"))

    @patch.object(RMAPI, "ls")
    @patch.object(RMAPI, "mkdir")
    @patch("pyrmapi.rmapi.Path.exists")
    def test_ensure_directory_skips_existing_directory(self, mock_exists, mock_mkdir, mock_ls):
        """Test ensure_directory skips creation when directory exists."""
        mock_exists.return_value = True
        mock_ls.return_value = "[d] test\n[d] other_folder"  # Directory exists
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi.ensure_directory(Path("/papers/test"))
            
        assert result is True
        mock_ls.assert_called_once_with(Path("/papers"))
        mock_mkdir.assert_not_called()

    @patch.object(RMAPI, "ensure_directory")
    @patch.object(RMAPI, "put")
    @patch.object(RMAPI, "mv")
    @patch("pyrmapi.rmapi.os.path.exists")
    @patch("pyrmapi.rmapi.Path.exists")
    def test_upload_success_with_rename(self, mock_rmapi_exists, mock_file_exists, mock_mv, mock_put, mock_ensure_dir):
        """Test upload method with successful upload and rename."""
        mock_rmapi_exists.return_value = True
        mock_file_exists.return_value = True
        mock_put.return_value = "Upload successful"
        mock_ensure_dir.return_value = True
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi.upload(
                Path("/local/test.pdf"), 
                Path("/papers/category"), 
                "renamed_file.pdf"
            )
            
        assert result is True
        mock_ensure_dir.assert_called()  # Should ensure directory structure
        mock_put.assert_called_once()
        mock_mv.assert_called_once()

    @patch("pyrmapi.rmapi.os.path.exists")
    @patch("pyrmapi.rmapi.Path.exists")
    def test_upload_fails_with_missing_file(self, mock_rmapi_exists, mock_file_exists):
        """Test upload method fails when local file doesn't exist."""
        mock_rmapi_exists.return_value = True
        mock_file_exists.return_value = False
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI()
            result = rmapi.upload(Path("/nonexistent.pdf"), Path("/remote"))
            
        assert result is False

    @patch("pyrmapi.rmapi.Path.exists")
    def test_environment_variables_set_correctly(self, mock_exists):
        """Test that environment variables are set correctly."""
        mock_exists.return_value = True
        original_env = os.environ.copy()
        
        with patch.object(RMAPI, "setup"):
            rmapi = RMAPI("~/.custom_rmapi")
            
        # Should have original env plus our addition
        assert len(rmapi.env) >= len(original_env)
        assert rmapi.env["RMAPI_CONFIG"] == os.path.expanduser("~/.custom_rmapi")
        
        # Should not modify the original environment
        for key, value in original_env.items():
            if key != "RMAPI_CONFIG":
                assert rmapi.env[key] == value