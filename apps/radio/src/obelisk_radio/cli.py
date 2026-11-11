"""CLI configuration commands run before the Textual event loop exists."""

import argparse

from . import __version__
from .config import ConfigError, config_path, edit_config, init_config, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="obelisk-radio",
        description="Obelisk Radio — independent Internet Radio.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command")
    configuration = commands.add_parser("config", help="Manage TOML configuration")
    actions = configuration.add_subparsers(dest="config_action", required=True)
    for name, help_text in (
        ("init", "Create a documented configuration without overwriting"),
        ("edit", "Open configuration using VISUAL, EDITOR, or vi"),
        ("path", "Print the effective XDG configuration path"),
        ("validate", "Validate the file and environment overrides"),
    ):
        actions.add_parser(name, help=help_text)
    args = parser.parse_args()
    try:
        path = config_path()
        if args.command == "config":
            if args.config_action == "init":
                init_config(path)
                print(
                    f"Created {path}; review the settings, then run 'obelisk-radio config validate'"
                )
            elif args.config_action == "edit":
                edit_config(path)
                print(f"Edited {path}; run 'obelisk-radio config validate'")
            elif args.config_action == "path":
                print(path)
            else:
                load_settings(path, required=True)
                print(f"Configuration valid: {path}")
            return
        settings = load_settings(path)
    except ConfigError as exc:
        parser.exit(2, f"obelisk-radio: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "obelisk: Configuration operation cancelled\n")
    from .app import RadioApp

    RadioApp(settings=settings, config_file=path).run()
