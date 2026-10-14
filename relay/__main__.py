"""入口：python -m relay --config routes.yaml [--bind 127.0.0.1] [--port 8787]"""
from __future__ import annotations

import argparse
import os
import sys

from .config import ConfigError, load_config
from .logging_setup import setup_logging
from .server import run
from .state import State


def main() -> None:
    parser = argparse.ArgumentParser(prog="claude-relay")
    parser.add_argument("--config", default=os.environ.get("RELAY_CONFIG", "routes.yaml"))
    parser.add_argument("--bind", default=os.environ.get("RELAY_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RELAY_PORT", "8787")))
    parser.add_argument("--log-level", default=os.environ.get("RELAY_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    setup_logging(args.log_level)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        raise SystemExit(1) from e

    state = State(config.dedup_db_path)
    run(config, state, bind=args.bind, port=args.port)


if __name__ == "__main__":
    main()
