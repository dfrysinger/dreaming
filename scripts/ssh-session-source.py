#!/usr/bin/env python3
"""Run a Dreaming session-source adapter on another host over SSH."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--ssh-bin", default="/usr/bin/ssh")
    result.add_argument("--host", required=True)
    result.add_argument("--address-family", choices=("4", "6"))
    result.add_argument("--remote-python", required=True)
    result.add_argument("--remote-script", required=True)
    return result


def main() -> int:
    try:
        separator = sys.argv.index("--")
    except ValueError:
        print("remote adapter arguments must follow --", file=sys.stderr)
        return 2
    args = parser().parse_args(sys.argv[1:separator])
    if args.host.startswith("-"):
        print("SSH host must not begin with '-'", file=sys.stderr)
        return 2
    remote_argv = [
        args.remote_python,
        args.remote_script,
        *sys.argv[separator + 1 :],
    ]
    command = [
        args.ssh_bin,
        *([f"-{args.address_family}"] if args.address_family else []),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "--",
        args.host,
        shlex.join(remote_argv),
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
