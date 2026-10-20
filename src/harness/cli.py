import typer
from rich import print

app = typer.Typer(help="Harness de IA para generación de código")


@app.command()
def run(ticket: str):
    """Procesa un ticket de Jira y genera el código."""
    print(f"[bold green]Procesando ticket {ticket}...[/bold green]")


@app.command()
def version():
    """Muestra la versión del harness."""
    print("harness 0.1.0")


def main():
    app()