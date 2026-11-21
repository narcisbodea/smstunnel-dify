#!/usr/bin/env python3
"""LOCAL ONLY. Installs the built package in the official Dify plugin runtime (`dify plugin run`) and drives it.

The CLI unpacks the .difypkg, builds its Python environment with uv and speaks the same protocol as the Dify
plugin daemon. The only change to the package under test is the API host, rewritten to a private address in a
temporary copy; the script checks that this is the only line that differs.

    DIFY_BIN=dify SMSTUNNEL_E2E_URL=http://<private-address>:3231 SMSTUNNEL_E2E_SEED=seed.json \
    SMSTUNNEL_E2E_PHONE_LOG=phone.log UV_PATH=... PYTHON_INTERPRETER_PATH=... python tests/e2e/run_cli.py pkg.difypkg
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_package  # noqa: E402

PACKAGE = Path(sys.argv[1])
DIFY = os.environ["DIFY_BIN"]
URL = os.environ["SMSTUNNEL_E2E_URL"]
if not re.fullmatch(r"http://(127\.\d+\.\d+\.\d+|localhost|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+):\d+", URL):
    sys.exit("SMSTUNNEL_E2E_URL must be a private address")
seed = json.loads(Path(os.environ["SMSTUNNEL_E2E_SEED"]).read_text(encoding="utf-8"))
KEY = seed["keys"]["full"]
PHONE_LOG = Path(os.environ["SMSTUNNEL_E2E_PHONE_LOG"])
TO = "+40712345611"

results = []


def check(name, passed):
    results.append((name, bool(passed)))
    print(("PASS " if passed else "FAIL ") + name, flush=True)


def phone_received():
    return [line for line in PHONE_LOG.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("Sending to ")]


def build_test_package(workdir):
    """The shipped package with one line changed: the host."""
    output = workdir / "under-test-DO-NOT-SUBMIT.difypkg"
    changed = dev_package.build(PACKAGE, URL, output, DIFY)
    check("the package under test differs from the shipped one by the host line only", changed == 2)
    with zipfile.ZipFile(PACKAGE) as shipped, zipfile.ZipFile(output) as tested:
        same = sorted(shipped.namelist()) == sorted(tested.namelist()) and all(
            shipped.read(name) == tested.read(name) for name in shipped.namelist() if name != dev_package.CLIENT
        )
    check("every other file in the package is byte-identical", same)
    return output


class Runtime:
    def __init__(self, package):
        self.process = subprocess.Popen(
            [DIFY, "plugin", "run", str(package), "-r", "json"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        )
        self.lines = []
        self.ready = threading.Event()
        self.condition = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.process.stdout:
            raw = raw.strip()
            if not raw.startswith("{"):
                continue
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            with self.condition:
                self.lines.append(item)
                if item.get("type") == "plugin_ready":
                    self.ready.set()
                self.condition.notify_all()

    def invoke(self, invoke_id, action, request, timeout=60):
        payload = {"invoke_id": invoke_id, "type": "tool", "action": action, "request": {"user_id": "e2e", **request}}
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        deadline = time.time() + timeout
        with self.condition:
            while True:
                mine = [item for item in self.lines if item.get("invoke_id") == invoke_id]
                if any(item.get("type") in ("plugin_invoke_end", "error") for item in mine):
                    return mine
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(invoke_id)
                self.condition.wait(remaining)

    def tool(self, invoke_id, name, parameters):
        items = self.invoke(invoke_id, "invoke_tool", {
            "provider": "smstunnel", "tool": name, "credentials": {"api_key": KEY}, "credential_type": "api-key", "tool_parameters": parameters,
        })
        text, variables, error = [], {}, ""
        for item in items:
            if item.get("type") == "error":
                error = json.dumps(item.get("response"), ensure_ascii=False)
            elif item.get("type") == "plugin_response":
                response = item["response"]
                if response.get("type") == "text":
                    text.append(response["message"]["text"])
                elif response.get("type") == "variable":
                    variables[response["message"]["variable_name"]] = response["message"]["variable_value"]
        return " ".join(text), variables, error

    def stop(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.process.terminate()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()


def main():
    workdir = Path(tempfile.mkdtemp(prefix="smstunnel-cli-e2e-"))
    runtime = None
    try:
        package = build_test_package(workdir)
        runtime = Runtime(package)
        check("the plugin installs and starts in the official runtime", runtime.ready.wait(240))

        items = runtime.invoke("v1", "validate_tool_credentials", {"provider": "smstunnel", "credentials": {"api_key": KEY}})
        check("validate: a good key passes", any(item.get("type") == "plugin_response" and item["response"].get("result") is True for item in items))

        items = runtime.invoke("v2", "validate_tool_credentials", {"provider": "smstunnel", "credentials": {"api_key": "sk_live_" + "0" * 32}})
        errors = " ".join(json.dumps(item.get("response"), ensure_ascii=False) for item in items if item.get("type") == "error")
        check("validate: a wrong key is refused", "did not accept this API key" in errors and "0" * 32 not in errors)

        text, variables, error = runtime.tool("t1", "list_phones", {})
        check("list_phones through the runtime", not error and variables.get("online") == 1 and "1 online" in text)

        before = len(phone_received())
        words = "Runtime check: ă î ș ț 🙂"
        text, sent, error = runtime.tool("t2", "send_text", {"to": TO, "message": words})
        check("send_text through the runtime", not error and sent.get("status") == "accepted" and bool(sent.get("message_id")))
        deadline = time.time() + 6
        while time.time() < deadline and len(phone_received()) <= before:
            time.sleep(0.1)
        received = phone_received()
        check("the phone got it once, word for word", len(received) == before + 1 and received[-1] == f"Sending to {TO}: {words}")

        status = ""
        deadline = time.time() + 8
        while time.time() < deadline and status != "sent":
            _, current, error = runtime.tool(f"t3-{time.time()}", "get_text_status", {"message_id": sent["message_id"]})
            status = current.get("status", "")
            time.sleep(0.3)
        check("get_text_status through the runtime reaches sent", status == "sent" and not error)

        before = len(phone_received())
        _, _, error = runtime.tool("t4", "send_text", {"to": "+40712345611,+40712345612", "message": "A list"})
        check("a list of numbers is refused with its reason", "one number per call" in error)
        check("the refusal names no secret", KEY not in error)
        _, _, error = runtime.tool("t5", "send_text", {"to": "0712345611", "message": "No country code"})
        check("a number without country code is refused", "international format" in error)
        time.sleep(1)
        check("refused sends reached no phone", len(phone_received()) == before)
    finally:
        if runtime:
            runtime.stop()
        shutil.rmtree(workdir, ignore_errors=True)

    failed = [name for name, passed in results if not passed]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
