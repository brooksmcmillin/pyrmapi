from pathlib import Path

import typer
from rmapi import RMAPI

app = typer.Typer()

@app.callback()
def main(ctx: typer.Context, config: str = "~/.rmapi"):
    ctx.ensure_object(dict)
    ctx.obj["api"] = RMAPI(config_path=config)

@app.command()
def ls(ctx: typer.Context, path: Path = Path("/")):
    print(ctx.obj["api"].ls(path))

@app.command()
def mv(path: Path):
    pass

if __name__ == "__main__":
    app()
