from pathlib import Path

import typer

from pyrmapi.rmapi import RMAPI

app = typer.Typer()


@app.callback()
def callback(ctx: typer.Context, config: str = "~/.rmapi") -> None:
    ctx.ensure_object(dict)
    ctx.obj["api"] = RMAPI(config_path=config)


@app.command()
def ls(ctx: typer.Context, path: Path = Path("/")) -> None:
    print(ctx.obj["api"].ls(path))


@app.command()
def upload(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Local file to upload"),
    remote_dir: Path = typer.Argument(..., help="Remote directory on reMarkable"),
    name: str | None = typer.Option(None, help="Rename the file on the device"),
) -> None:
    """Upload a file to the reMarkable tablet."""
    api: RMAPI = ctx.obj["api"]
    success = api.upload(file, remote_dir, remote_file_name=name)
    if not success:
        raise typer.Exit(code=1)


@app.command()
def put(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Local file to upload"),
    remote_dir: Path = typer.Argument(..., help="Remote directory on reMarkable"),
) -> None:
    """Upload a file without directory creation or renaming."""
    api: RMAPI = ctx.obj["api"]
    api.put(file, remote_dir)


if __name__ == "__main__":
    app()
