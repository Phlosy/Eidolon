"""``python -m eidolon_runner --workspace DIR``"""

import argparse
import sys

from eidolon_runner.server import Server
from eidolon_runner.tools import Limits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eidolon_runner")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--max-output-bytes", type=int, default=Limits.max_output_bytes)
    parser.add_argument("--default-timeout", type=float, default=Limits.default_timeout_s)
    parser.add_argument("--max-timeout", type=float, default=Limits.max_timeout_s)
    args = parser.parse_args(argv)
    limits = Limits(args.max_output_bytes, args.default_timeout, args.max_timeout)
    return Server(args.workspace, limits).serve()


if __name__ == "__main__":
    sys.exit(main())
