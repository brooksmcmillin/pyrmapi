import logging
import os
import subprocess
import tarfile
import urllib.request
from pathlib import Path

RMAPI_URL = (
    "https://github.com/ddvk/rmapi/releases/latest/download/rmapi-linux-amd64.tar.gz"
)


class RMAPI:
    def __init__(self, config_path: str = "./.rmapi"):
        # Make sure the rmapi executable exists
        logging.basicConfig(level=logging.INFO)
        self.setup()

        # Set the path of the config
        self.env = os.environ.copy()
        self.env["RMAPI_CONFIG"] = os.path.expanduser(config_path)

    def _run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["./bin/rmapi"] + command,
            capture_output=True,
            text=True,
            check=False,
            env=self.env,
            timeout=10,
        )
        if result.stderr:
            logging.error(result.stderr)
        return result

    def setup(self) -> None:
        """
        Download and unpack the lastest version of rmapi if needed
        """

        # Check if rmapi is already in the bin directory
        rmapi_path = Path("./bin/rmapi")
        if rmapi_path.exists():
            logging.debug("rmapi already exists in current directory")
            return

        # If not, download and unpack it
        logging.debug("Downloading rmapi...")
        tarball_path = "rmapi.tar.gz"

        try:
            # Download the tarball
            urllib.request.urlretrieve(RMAPI_URL, tarball_path)
            logging.debug(f"Downloaded rmapi to {tarball_path}")

            # Extract the tarball
            logging.info("Extracting rmapi...")
            with tarfile.open(tarball_path, "r:gz") as tar:
                tar.extractall("./bin")
            logging.info("Extracted rmapi successfully")

            # Make rmapi executable
            if rmapi_path.exists():
                os.chmod(rmapi_path, 0o700)
                logging.debug("Made rmapi executable with owner-only permissions")

            # Clean up the tarball
            os.remove(tarball_path)
            logging.info("Cleaned up tarball")

        except Exception as e:
            logging.error(f"Error setting up rmapi: {e}")
            # Clean up partial download if it exists
            if os.path.exists(tarball_path):
                os.remove(tarball_path)
            raise

    def ls(self, path: Path) -> str:
        return self._run_command(["ls", str(path)]).stdout

    def mkdir(self, path: Path) -> str:
        return self._run_command(["mkdir", str(path)]).stdout

    def mv(self, original_path: Path, new_path: Path) -> str:
        result = self._run_command(["mv", str(original_path), str(new_path)])
        return result.stdout

    def put(self, local_path: Path, remote_path: Path) -> str:
        result = self._run_command(["put", str(local_path), str(remote_path)])
        if result.returncode != 0:
            logging.error(f"Upload failed: {result.stderr}")

        return result.stdout

    def ensure_directory(self, path: Path) -> bool:
        """
        Ensure the directory exists.
        Returns True if successful, False otherwise.
        """

        try:
            # First, check if the directory exists in parent
            result = self.ls(path.parent)

            # Create the directory if it doesn't exist
            if path.name not in result:
                print(f"Creating {path} directory...")
                self.mkdir(path)

            return True

        except subprocess.CalledProcessError as e:
            print(f"Error creating directory structure: {e}")
            return False

    def upload(
        self,
        file_path: Path,
        remote_directory: Path,
        remote_file_name: str | None = None,
    ) -> bool:
        """
        Uploads a file from file_path to the remote_directory.
        By default, the file name will be the name of the file at file_path,
        if remote_file_name is set, this will overwrite it.
        Returns True if successful, False otherwise.
        """

        if not os.path.exists(file_path):
            print(f"Error: No file found at '{file_path}'")
            return False

        # Ensure all remote directories exist
        path_parts = remote_directory.parts
        current_dir = ""
        for i in range(1, len(path_parts)):
            current_dir += f"/{path_parts[i]}"
            self.ensure_directory(Path(current_dir))

        # Upload the file to reMarkable
        print(f"Uploading to: {remote_directory}")

        try:
            # Upload the file
            self.put(file_path, remote_directory)

            # Rename the file to the formatted name if needed
            if remote_file_name is not None:
                original_name = Path(file_path).name
                print(f"Renaming to {remote_file_name}")
                self.mv(
                    Path(f"{remote_directory}/{original_name.replace('.pdf', '')}"),
                    Path(f"{remote_directory}/{remote_file_name.replace('.pdf', '')}"),
                )

            print(f"Successfully uploaded file to {remote_directory}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"Error uploading paper: {e}")
            return False
