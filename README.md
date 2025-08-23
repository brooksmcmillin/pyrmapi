# pyrmapi

A Python wrapper and future reimplementation of the [ddvk/rmapi](https://github.com/ddvk/rmapi) tool for accessing reMarkable tablet files through the Cloud API.

## Overview

pyrmapi provides a Pythonic interface to manage files on your reMarkable tablet. Currently, it wraps the excellent [rmapi](https://github.com/ddvk/rmapi) Go application by ddvk, but the long-term goal is to gradually reimplement the functionality in pure Python.

## Features

- **Automatic rmapi Setup**: Downloads and configures the latest rmapi binary automatically
- **File Upload**: Upload PDF files and other documents to your reMarkable tablet
- **Directory Management**: Create and manage directory structures on your tablet
- **Python API**: Clean, Pythonic interface for reMarkable operations
- **Configuration Management**: Handles authentication and configuration seamlessly

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd pyrmapi
```

## Quick Start

```python
from pyrmapi import RMAPI

# Initialize the API (downloads rmapi if needed)
rm = RMAPI()

# Create directory structure
rm.ensure_directory("research")

# Upload a file
success = rm.upload(
    file_path="./paper.pdf",
    remote_directory="/papers/research",
    remote_file_name="Important Research Paper"
)

if success:
    print("File uploaded successfully!")
```

## Configuration

pyrmapi uses the same configuration as the underlying rmapi tool:

- **Config Path**: By default, authentication tokens are stored in `./.rmapi`. You can specify a custom path:
  ```python
  rm = RMAPI(config_path="/path/to/custom/config")
  ```

- **Environment Variables**:
  - `RMAPI_CONFIG`: Custom filepath for authentication tokens
  - `RMAPI_TRACE=1`: Enable trace logging
  - `RMAPI_USE_HIDDEN_FILES=1`: Include hidden files/directories

## API Reference

### RMAPI Class

#### `__init__(config_path: str = "./.rmapi")`
Initialize the RMAPI client with optional custom config path.

#### `setup() -> None`
Download and set up the rmapi binary if not already present.

#### `ensure_directory(classification: str) -> bool`
Create directory structure `/papers/<classification>` if it doesn't exist.

**Parameters:**
- `classification`: Name of the subdirectory under `/papers/`

**Returns:** `True` if successful, `False` otherwise

#### `upload(file_path: str, remote_directory: str, remote_file_name: str | None = None) -> bool`
Upload a file to the reMarkable tablet.

**Parameters:**
- `file_path`: Local path to the file to upload
- `remote_directory`: Target directory on the tablet
- `remote_file_name`: Optional custom name for the uploaded file

**Returns:** `True` if successful, `False` otherwise

## Development Roadmap

This project is in active development with the following planned phases:

### Phase 1: Python Wrapper (Current)
-  Wrap rmapi binary with Python interface
-  Automatic binary management and setup
-  Basic file operations (upload, directory creation)
- = Extended file operations (download, delete, move)
- = Complete directory management
- = File listing and search functionality

### Phase 2: Hybrid Implementation
- = Reimplement authentication in Python
- = Python-based file metadata operations
- = Keep binary for complex operations temporarily

### Phase 3: Pure Python Implementation
- = Full reMarkable Cloud API implementation
- = Native PDF handling and annotation support
- = Remove dependency on rmapi binary
- = Enhanced features and performance optimizations

## Contributing

Contributions are welcome! Whether you're interested in:
- Extending the current wrapper functionality
- Helping with the pure Python reimplementation
- Improving documentation and examples
- Adding tests and CI/CD

Please feel free to open issues and pull requests.

## Dependencies

Currently depends on:
- The [rmapi](https://github.com/ddvk/rmapi) binary (automatically downloaded)
- Python 3.11+
- Standard library modules (urllib, subprocess, tarfile, pathlib)

## License

MIT

## Acknowledgments

- **ddvk** and contributors to [rmapi](https://github.com/ddvk/rmapi) for the excellent Go implementation
- **juruen** for the original rmapi project
- The reMarkable community for reverse engineering the Cloud API

## Related Projects

- [rmapi](https://github.com/ddvk/rmapi) - The original Go implementation
