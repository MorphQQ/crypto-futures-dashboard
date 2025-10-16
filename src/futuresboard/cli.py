from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import os  # For str() compat if needed
import traceback  # For error print

import futuresboard.app
import futuresboard.scraper
from futuresboard import __version__  # type: ignore[attr-defined]
from futuresboard.config import Config
from dotenv import load_dotenv  # Explicit .env load

log = logging.getLogger(__name__)


def main():
    print("CLI Loaded: Starting main()")  # Debug: Confirms entry
    parser = argparse.ArgumentParser(prog="futuresboard")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c",
        "--config-dir",
        type=pathlib.Path,
        default=None,
        help="Path to configuration directory. Defaults to the `config/` sub-directory on the current directory",
    )
    parser.add_argument(
        "--scrape-only", default=False, action="store_true", help="Run only the scraper code"
    )
    parser.add_argument(
        "--disable-auto-scraper",
        default=False,
        action="store_true",
        help="Disable the routines which scrape while the webservice is running",
    )
    server_settings = parser.add_argument_group("Server Settings")
    server_settings.add_argument(
        "--host",
        default='0.0.0.0',
        help="Server host. Default: 0.0.0.0",
        type=str,  # Str for argparse (v1 compat)
    )
    server_settings.add_argument(
        "--port", type=int, default=None, help="Server port. Default: 5000"
    )
    args = parser.parse_args()

    print(f"Args parsed: config_dir={args.config_dir}, port={args.port}, scrape_only={args.scrape_only}")  # Debug: Argparse OK?

    # Default config_dir to root/config (backend/ -> ../config for json/db)
    if args.config_dir is None:
        args.config_dir = pathlib.Path.cwd().parent / "config"
    else:
        args.config_dir = args.config_dir.resolve()

    print(f"Resolved config_dir: {args.config_dir} (exists? {args.config_dir.exists()})")  # Debug: Path check

    # .env load from backend/ (cwd=backend, self)
    backend_dir = pathlib.Path.cwd()
    dotenv_path = backend_dir / ".env"
    load_dotenv(dotenv_path=str(dotenv_path))
    # Debug print: Confirm load before Config
    print(f"Debug: Loaded API_KEY from .env: {os.getenv('API_KEY')[:10] + '...' if os.getenv('API_KEY') else 'MISSING'}")
    print(f"Debug: .env path resolved: {dotenv_path.resolve()} (exists? {dotenv_path.exists()})")

    print("Pre-Config: About to call from_config_dir...")  # Debug: Before call
    try:
        config = Config.from_config_dir(args.config_dir)
        print("Post-Config: Config loaded OK!")  # If reaches, validation passed
    except Exception as e:
        print(f"Config Error: {e}")
        traceback.print_exc()
        sys.exit(1)
    if not args.host:
        args.host = config.HOST
    if not args.port:
        args.port = config.PORT

    # Run the application
    try:
        app = futuresboard.app.init_app(config)
        print("Init App OK!")  # Confirms init_app (DB/blueprint/scraper setup)
    except Exception as e:
        print(f"Init App Error: {e}")
        traceback.print_exc()
        sys.exit(1)

    if args.scrape_only:
        with app.app_context():
            futuresboard.scraper.scrape()
        sys.exit(0)

    app.run(host=args.host, port=args.port)  # Direct str host


if __name__ == '__main__':
    main()