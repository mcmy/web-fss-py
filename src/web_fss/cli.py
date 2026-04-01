import argparse
import os
from typing import Optional, Sequence

from . import __version__
from .server import DEFAULT_CHUNK_SIZE, serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web-fss",
        description=(
            "Simple file server compatible with Python 3.7+; "
            "supports directory listing, range download and resumable upload."
        ),
    )
    parser.add_argument(
        "port",
        nargs="?",
        type=int,
        default=8000,
        help="TCP port to bind (default: 8000)",
    )
    parser.add_argument(
        "-b",
        "--bind",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "-d",
        "--directory",
        default=os.getcwd(),
        help="Directory to serve (default: current working directory)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Upload chunk size in bytes for web UI (default: %(default)s)",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Disable upload endpoints and upload button",
    )
    parser.add_argument(
        "--show-hidden",
        action="store_true",
        help="Show hidden files (dotfiles) in directory listing",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s " + __version__,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.port < 1 or args.port > 65535:
        parser.error("port must be between 1 and 65535")

    if args.chunk_size <= 0:
        parser.error("--chunk-size must be > 0")

    serve(
        bind=args.bind,
        port=args.port,
        directory=args.directory,
        enable_upload=not args.no_upload,
        chunk_size=args.chunk_size,
        show_hidden=args.show_hidden,
    )
    return 0
