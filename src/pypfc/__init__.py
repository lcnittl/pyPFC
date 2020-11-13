#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

from processes import FanCtrl, PwrCtrl

try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata

try:
    __version__ = metadata.version("pyPFC")
except metadata.PackageNotFoundError:
    __version__ = None

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--version",
        action="version",
        version=str(__version__),
    )
    parser.add_argument(
        "-v",
        "--verbosity",
        choices=list(logging._nameToLevel.keys()),
        default="WARNING",
        type=str.upper,
        help="Main log - Console log level",
    )
    parser.add_argument(
        "-l",
        "--log",
        choices=list(logging._nameToLevel.keys()),
        default="WARNING",
        type=str.upper,
        help="Main log - File log level",
    )
    return parser.parse_args()


def setup_root_logger() -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(logging.NOTSET)

    module_loglevel_map = {}
    for module, loglevel in module_loglevel_map.items():
        logging.getLogger(module).setLevel(loglevel)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        mode="a",
        maxBytes=2 * 1024 ** 2,
        backupCount=9,
        encoding="utf-8",
    )
    file_handler.setLevel(args.log)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="[%(asctime)s.%(msecs)03d][%(name)s:%(levelname).4s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(args.verbosity)
    console_handler.setFormatter(
        logging.Formatter(
            fmt="[%(name)s:%(levelname).4s] %(message)s",
        )
    )
    logger.addHandler(console_handler)

    if False:
        # List all log levels with their respective coloring
        for log_lvl_name, log_lvl in logging._nameToLevel.items():
            logger.log(log_lvl, "This is test message for %s", log_lvl_name)

    return logger


args = parse_args()

log_file = f"{Path(__file__).stem}.log"
root_logger = setup_root_logger()


def main(args=None) -> int:
    """The main routine."""
    if args is None:
        args = sys.argv[1:]

    processes = {
        "fan_ctrl": FanCtrl(),
        "pwr_ctrl": PwrCtrl(),
    }
    for process in processes:
        processes[process].start()

    try:
        for process in processes:
            processes[process].join()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt")
        return 128 + signal.SIGINT
    finally:
        pass


if __name__ == "__main__":
    sys.exit(main())
