#!/usr/bin/env python3
"""LOCAL ONLY. Installs the package in a real Dify Community Edition and runs it from a workflow.

It needs a disposable Dify with FORCE_VERIFYING_SIGNATURE=false, the local API with a fake phone, and
tests/e2e/lossy_proxy.py in front of the API. The package under test is a copy whose only change is the host
(tests/e2e/dev_package.py), so no real text can go out.

    DIFY_URL=http://127.0.0.1:18231 SMSTUNNEL_E2E_SEED=seed.json SMSTUNNEL_E2E_PHONE_LOG=phone.log \
    PROXY_LOG=requests.log python tests/e2e/run_dify_ce.py shipped.difypkg dev-DO-NOT-SUBMIT.difypkg
"""

import base64
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

import httpx

SHIPPED, DEV = Path(sys.argv[1]), Path(sys.argv[2])
URL = os.environ["DIFY_URL"]
if not re.fullmatch(r"http://(127\.0\.0\.1|localhost):\d+", URL):
    sys.exit("DIFY_URL must be a local address.")
seed = json.loads(Path(os.environ["SMSTUNNEL_E2E_SEED"]).read_text(encoding="utf-8"))
KEY = seed["keys"]["full"]
PHONE_LOG = Path(os.environ["SMSTUNNEL_E2E_PHONE_LOG"])
PROXY_LOG = Path(os.environ["PROXY_LOG"])
PROVIDER = "smstunnel/smstunnel/smstunnel"
EMAIL = "catalogs@example.com"
PASSWORD = os.environ.get("DIFY_TEST_PASSWORD") or "Local-Test-" + secrets.token_hex(8) + "a1"
TO = "+40712345641"
LOST_TO = "+40712345699"

results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    print(("PASS " if passed else "FAIL ") + name + (f"  [{detail}]" if detail and not passed else ""), flush=True)


def phone_received():
    return [l for l in PHONE_LOG.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("Sending to ")]


class Console:
    def __init__(self):
        self.http = httpx.Client(base_url=URL, timeout=120, follow_redirects=False)

    def _headers(self):
        token = self.http.cookies.get("csrf_token")
        return {"X-CSRF-Token": token} if token else {}

    def get(self, path):
        return self.http.get("/console/api" + path, headers=self._headers())

    def post(self, path, **kwargs):
        return self.http.post("/console/api" + path, headers=self._headers(), **kwargs)

    def login(self):
        state = self.http.get("/console/api/setup").json()
        if state.get("step") != "finished":
            answer = self.http.post("/console/api/setup", json={"email": EMAIL, "name": "Catalogs test", "password": PASSWORD, "language": "en-US"})
            assert answer.status_code in (200, 201), answer.text[:200]
        encoded = base64.b64encode(PASSWORD.encode()).decode()
        answer = self.http.post("/console/api/login", json={"email": EMAIL, "password": encoded, "remember_me": True})
        assert answer.status_code == 200, answer.text[:200]

    def install(self, package):
        with open(package, "rb") as handle:
            upload = self.post("/workspaces/current/plugin/upload/pkg", files={"pkg": (package.name, handle, "application/octet-stream")})
        assert upload.status_code == 200, upload.text[:300]
        identifier = upload.json()["unique_identifier"]
        answer = self.post("/workspaces/current/plugin/install/pkg", json={"plugin_unique_identifiers": [identifier]})
        assert answer.status_code == 200, answer.text[:300]
        body = answer.json()
        if not body.get("all_installed"):
            task = body["task_id"]
            for _ in range(180):
                status = self.get(f"/workspaces/current/plugin/tasks/{task}").json()["task"]
                if status["status"] in ("success", "failed"):
                    assert status["status"] == "success", json.dumps(status)[:500]
                    break
                time.sleep(2)
            else:
                raise TimeoutError("plugin install")
        return identifier

    def uninstall_all(self):
        plugins = self.get("/workspaces/current/plugin/list?page=1&page_size=100").json().get("plugins", [])
        for plugin in plugins:
            self.post("/workspaces/current/plugin/uninstall", json={"plugin_installation_id": plugin["installation_id"] if "installation_id" in plugin else plugin["id"]})

    def tools(self):
        for provider in self.get("/workspaces/current/tools/builtin").json():
            if provider.get("name") == PROVIDER:
                return provider
        return None

    def run(self, app_id, inputs):
        finished, nodes = None, {}
        with self.http.stream(
            "POST", f"/console/api/apps/{app_id}/workflows/draft/run",
            headers=self._headers(), json={"inputs": inputs, "files": []}, timeout=120,
        ) as stream:
            assert stream.status_code == 200, stream.read()[:300]
            for line in stream.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event.get("event") == "node_finished":
                    nodes[event["data"]["node_id"]] = event["data"]
                elif event.get("event") == "node_retry":
                    nodes.setdefault("_retries", []).append(event["data"]["node_id"])
                elif event.get("event") == "workflow_finished":
                    finished = event["data"]
        return finished, nodes


def tool_node(node_id, title, tool, parameters, x, plugin, configurations=None, retry=False):
    data = {
        "type": "tool", "title": title, "desc": "", "selected": False,
        "provider_id": PROVIDER, "provider_type": "builtin", "provider_name": PROVIDER,
        "tool_name": tool, "tool_label": title, "tool_node_version": "2",
        "tool_configurations": configurations or {},
        "tool_parameters": parameters, "plugin_unique_identifier": plugin,
    }
    if retry:
        data["retry_config"] = {"retry_enabled": True, "max_retries": 2, "retry_interval": 200}
    return {"id": node_id, "type": "custom", "position": {"x": x, "y": 100}, "data": data}


def graph(plugin, retry=False):
    start = {"id": "start", "type": "custom", "position": {"x": 0, "y": 100}, "data": {
        "type": "start", "title": "Start", "desc": "", "selected": False,
        "variables": [
            {"variable": "to", "label": "to", "type": "text-input", "required": True, "max_length": 200, "options": []},
            {"variable": "message", "label": "message", "type": "paragraph", "required": True, "max_length": 2000, "options": []},
        ],
    }}
    send = tool_node("send", "Send a text", "send_text", {
        "to": {"type": "mixed", "value": "{{#start.to#}}"},
        "message": {"type": "mixed", "value": "{{#start.message#}}"},
    }, 300, plugin, {"device_id": {"type": "constant", "value": ""}}, retry)
    end = {"id": "end", "type": "custom", "position": {"x": 600, "y": 100}, "data": {
        "type": "end", "title": "End", "desc": "", "selected": False,
        "outputs": [
            {"variable": "status", "value_selector": ["send", "status"], "value_type": "string"},
            {"variable": "message_id", "value_selector": ["send", "message_id"], "value_type": "string"},
            {"variable": "text", "value_selector": ["send", "text"], "value_type": "string"},
        ],
    }}
    edges = [
        {"id": "e1", "source": "start", "target": "send", "sourceHandle": "source", "targetHandle": "target", "type": "custom", "data": {"sourceType": "start", "targetType": "tool"}},
        {"id": "e2", "source": "send", "target": "end", "sourceHandle": "source", "targetHandle": "target", "type": "custom", "data": {"sourceType": "tool", "targetType": "end"}},
    ]
    return {"nodes": [start, send, end], "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


def status_graph(plugin):
    start = {"id": "start", "type": "custom", "position": {"x": 0, "y": 100}, "data": {
        "type": "start", "title": "Start", "desc": "", "selected": False,
        "variables": [{"variable": "message_id", "label": "message_id", "type": "text-input", "required": True, "max_length": 64, "options": []}],
    }}
    status = tool_node("status", "Get text status", "get_text_status", {"message_id": {"type": "mixed", "value": "{{#start.message_id#}}"}}, 300, plugin)
    phones = tool_node("phones", "List phones", "list_phones", {}, 600, plugin)
    end = {"id": "end", "type": "custom", "position": {"x": 900, "y": 100}, "data": {
        "type": "end", "title": "End", "desc": "", "selected": False,
        "outputs": [
            {"variable": "status", "value_selector": ["status", "status"], "value_type": "string"},
            {"variable": "online", "value_selector": ["phones", "online"], "value_type": "number"},
        ],
    }}
    edges = [
        {"id": "e1", "source": "start", "target": "status", "sourceHandle": "source", "targetHandle": "target", "type": "custom", "data": {"sourceType": "start", "targetType": "tool"}},
        {"id": "e2", "source": "status", "target": "phones", "sourceHandle": "source", "targetHandle": "target", "type": "custom", "data": {"sourceType": "tool", "targetType": "tool"}},
        {"id": "e3", "source": "phones", "target": "end", "sourceHandle": "source", "targetHandle": "target", "type": "custom", "data": {"sourceType": "tool", "targetType": "end"}},
    ]
    return {"nodes": [start, status, phones, end], "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


def new_app(console, name, workflow):
    app = console.post("/apps", json={"name": name, "mode": "workflow", "icon_type": "emoji", "icon": "A", "icon_background": "#FFEAD5"})
    assert app.status_code == 201, app.text[:300]
    app_id = app.json()["id"]
    draft = console.post(f"/apps/{app_id}/workflows/draft", json={"graph": workflow, "features": {}, "environment_variable_patch": None, "conversation_variables": []})
    assert draft.status_code == 200, draft.text[:400]
    return app_id


def main():
    console = Console()
    console.login()
    check("Dify CE is set up and signed in", True)
    version = console.http.get("/console/api/version?current_version=0").status_code
    print(f"(version route answered {version})")

    console.uninstall_all()  # a rerun on the same instance starts clean
    for _ in range(30):
        if console.tools() is None:
            break
        time.sleep(1)

    # The package exactly as it will be submitted: it must install.
    identifier = console.install(SHIPPED)
    provider = console.tools()
    names = sorted(tool["name"] for tool in provider["tools"]) if provider else []
    check("the shipped .difypkg installs in Dify CE", provider is not None)
    check("it shows the three tools", names == ["get_text_status", "list_phones", "send_text"], str(names))
    check("the marketplace label and description are read", provider and provider["label"]["en_US"] == "SMSTunnel" and "own Android phone" in provider["description"]["en_US"])
    schema = console.get(f"/workspaces/current/tool-provider/builtin/{PROVIDER}/credential/schema/api-key").json()
    check("one credential field, an API key", [item["name"] for item in schema] == ["api_key"] and schema[0]["type"] == "secret-input", str(schema)[:200])
    console.uninstall_all()
    for _ in range(30):
        if console.tools() is None:
            break
        time.sleep(1)

    # The same package with the host pointing at the local API, so nothing real can go out.
    plugin = console.install(DEV)
    check("the test copy installs", console.tools() is not None)

    wrong = console.post(f"/workspaces/current/tool-provider/builtin/{PROVIDER}/add", json={"credentials": {"api_key": "sk_live_" + "0" * 32}, "name": "wrong", "type": "api-key"})
    check("Dify refuses a wrong key", wrong.status_code >= 400 and "did not accept this API key" in wrong.text, wrong.text[:200])
    check("the refusal doesn't repeat the key", "0" * 32 not in wrong.text)
    good = console.post(f"/workspaces/current/tool-provider/builtin/{PROVIDER}/add", json={"credentials": {"api_key": KEY}, "name": "local-" + secrets.token_hex(3), "type": "api-key"})
    check("Dify accepts a good key", good.status_code in (200, 201), good.text[:200])

    # A workflow: Start, Send a text, End.
    app_id = new_app(console, "SMSTunnel plugin test", graph(plugin))
    before = len(phone_received())
    words = 'Workflow check: "quotes" $HOME ă î ș ț 🙂'
    finished, nodes = console.run(app_id, {"to": TO, "message": words})
    outputs = (finished or {}).get("outputs") or {}
    check("the workflow runs to the end", finished and finished["status"] == "succeeded", str(finished)[:300])
    check("Send a text: accepted, with a message ID", outputs.get("status") == "accepted" and len(outputs.get("message_id", "")) == 24, str(outputs)[:200])
    check("its text output tells the user what happened", "handed to your phone" in outputs.get("text", ""))
    deadline = time.time() + 6
    while time.time() < deadline and len(phone_received()) <= before:
        time.sleep(0.1)
    received = phone_received()
    check("the phone got it once, word for word", len(received) == before + 1 and received[-1] == f"Sending to {TO}: {words}", str(received[-1:])[:120])

    # The other two tools in a workflow.
    time.sleep(1.5)
    status_app = new_app(console, "SMSTunnel status test", status_graph(plugin))
    finished2, _ = console.run(status_app, {"message_id": outputs.get("message_id", "")})
    out2 = (finished2 or {}).get("outputs") or {}
    check("Get text status and List phones run in a workflow", finished2 and finished2["status"] == "succeeded", str(finished2)[:300])
    check("status is sent, one phone online", out2.get("status") == "sent" and out2.get("online") == 1, str(out2))

    # A list of numbers.
    before = len(phone_received())
    finished3, nodes3 = console.run(app_id, {"to": "+40712345641,+40712345642", "message": "A list"})
    error = (nodes3.get("send") or {}).get("error") or (finished3 or {}).get("error") or ""
    check("a list of numbers stops the node", finished3 and finished3["status"] == "failed", str(finished3)[:200])
    check("with the reason", "one number per call" in error, error[:200])
    time.sleep(1)
    check("and nothing was sent", len(phone_received()) == before)

    # The answer is lost after the text went out, on a node with Retry on Failure turned on.
    retry_app = new_app(console, "SMSTunnel lost answer test", graph(plugin, retry=True))
    # First prove that retry really is on for this node: a refused call does get retried, and sends nothing.
    before = len(phone_received())
    _, control = console.run(retry_app, {"to": "+40712345641,+40712345642", "message": "A list"})
    check("control: Retry on Failure is active on this node", len(control.get("_retries", [])) == 2, str(control.get("_retries")))
    check("control: a retried refusal sends nothing", len(phone_received()) == before)
    PROXY_LOG.write_text("", encoding="utf-8")
    before = len([l for l in phone_received() if l.startswith(f"Sending to {LOST_TO}")])
    finished4, nodes4 = console.run(retry_app, {"to": LOST_TO, "message": "The answer to this send is lost."})
    out4 = (finished4 or {}).get("outputs") or {}
    time.sleep(2)
    posts = [l for l in PROXY_LOG.read_text(encoding="utf-8").splitlines() if l == f"POST {LOST_TO}"]
    got = len([l for l in phone_received() if l.startswith(f"Sending to {LOST_TO}")]) - before
    check("lost answer: the plugin made one request", len(posts) == 1, f"{len(posts)} requests")
    check("lost answer: Dify's retry did not fire", not nodes4.get("_retries"), str(nodes4.get("_retries")))
    check("lost answer: the phone got it exactly once", got == 1, f"{got} texts")
    check("lost answer: reported as unknown, not as sent", out4.get("status") == "unknown", str(out4)[:200])
    check("lost answer: says not to send it again", "Do not send it again" in out4.get("text", ""))

    failed = [name for name, passed in results if not passed]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
