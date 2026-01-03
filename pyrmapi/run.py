from pathlib import Path

import typer
from rmapi import RMAPI  # type: ignore

app = typer.Typer()


@app.callback()
def main(ctx: typer.Context, config: str = "~/.rmapi") -> None:
    ctx.ensure_object(dict)
    ctx.obj["api"] = RMAPI(config_path=config)


@app.command()
def ls(ctx: typer.Context, path: Path = Path("/")) -> None:
    print(ctx.obj["api"].ls(path))


@app.command()
def mv(path: Path) -> None:
    pass


if __name__ == "__main__":
    app()
