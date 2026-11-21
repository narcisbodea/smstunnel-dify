#!/usr/bin/env python3
"""Builds a TEST-ONLY copy of the package that talks to a private API instead of smstunnel.io.

    DIFY_BIN=dify python tests/e2e/dev_package.py dist/smstunnel-1.0.0.difypkg http://host.docker.internal:3231 out.difypkg

The shipped package has no setting for its host, so a local test has to change the one line that holds it.
Never submit the output: its file name says so, and this script refuses any public address.
"""

import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

CLIENT = "smstunnel_client.py"
SHIPPED_HOST_LINE = 'BASE_URL = "https://smstunnel.io"'
PRIVATE_URL = re.compile(
    r"http://(127\.\d+\.\d+\.\d+|localhost|host\.docker\.internal|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+):\d+"
)


def build(package: Path, url: str, output: Path, dify: str) -> int:
    """Returns how many lines differ from the shipped client (always 1 replaced line, so 2 in the diff)."""
    if not PRIVATE_URL.fullmatch(url):
        raise SystemExit("The test package only talks to a private address.")
    if "DO-NOT-SUBMIT" not in output.name:
        raise SystemExit("The output file name must contain DO-NOT-SUBMIT.")
    workdir = Path(tempfile.mkdtemp(prefix="smstunnel-dev-package-"))
    try:
        source = workdir / "src"
        with zipfile.ZipFile(package) as archive:
            archive.extractall(source)
        client = source / CLIENT
        original = client.read_text(encoding="utf-8")
        if original.count(SHIPPED_HOST_LINE) != 1:
            raise SystemExit("The shipped host line was not found exactly once.")
        patched = original.replace(SHIPPED_HOST_LINE, f'BASE_URL = "{url}"')
        client.write_text(patched, encoding="utf-8")
        subprocess.run([dify, "plugin", "package", str(source), "-o", str(output)], check=True, capture_output=True, text=True)
        return sum(1 for line in difflib.ndiff(original.splitlines(), patched.splitlines()) if line[:2] in ("- ", "+ "))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    changed = build(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), os.environ.get("DIFY_BIN", "dify"))
    print(f"Built {sys.argv[3]} ({changed} changed lines in the diff: the host). Never submit this file.")
