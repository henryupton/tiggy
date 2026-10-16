import click

from tiggy import __version__


@click.group(no_args_is_help=True)
@click.version_option(__version__, prog_name="tiggy")
def cli() -> None:
    """Declarative Google Tag Manager."""
