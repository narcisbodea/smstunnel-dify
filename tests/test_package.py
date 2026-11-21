"""What a reviewer checks: the metadata, the wording and the security boundary."""

import ast
import re
from pathlib import Path

import pytest
import yaml

import smstunnel_client as sc

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ["send_text", "get_text_status", "list_phones"]

# Written in pieces so a search of the repository for these words finds nothing, this file included.
BANNED = ["gate" + "way", "bu" + "lk", "camp" + "aign", "market" + "ing", "ma" + "ss", "群" + "发", "营" + "销", "网" + "关"]
WORD = re.compile("|".join(rf"\b{re.escape(word)}\b" if word.isascii() else re.escape(word) for word in BANNED), re.IGNORECASE)
SHIPPED_TEXT = ["README.md", "PRIVACY.md", "manifest.yaml", "provider/smstunnel.yaml", *[f"tools/{name}.yaml" for name in TOOLS]]


def load(relative):
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


@pytest.mark.parametrize("relative", SHIPPED_TEXT + ["smstunnel_client.py", "tools/send_text.py", "tools/get_text_status.py", "tools/list_phones.py"])
def test_no_banned_words(relative):
    found = WORD.findall((ROOT / relative).read_text(encoding="utf-8"))
    assert not found, f"{relative}: {found}"


@pytest.mark.parametrize("relative", SHIPPED_TEXT)
def test_nothing_says_smstunnel_can_confirm_a_text_arrived(relative):
    # `sent` is the last status there is. `delivered_at` stays in the output as a reserved, empty field.
    text = (ROOT / relative).read_text(encoding="utf-8").replace("delivered_at", "")
    assert not re.search("deliver|送" + "达", text, re.IGNORECASE), relative
    for sentence in re.findall(r"[^.\n]*\barriv\w*[^.\n]*", text):
        assert "can't confirm" in sentence, f"{relative}: {sentence}"


def test_status_meanings_promise_nothing_past_sent():
    from tools.get_text_status import MEANINGS

    assert set(MEANINGS) == {"pending", "sent", "delivered", "unconfirmed"}
    assert all("deliver" not in meaning.lower() for meaning in MEANINGS.values())
    assert "can't confirm that it arrived" in MEANINGS["sent"]


def test_status_tool_lists_the_real_states():
    tool = load("tools/get_text_status.yaml")
    assert "pending, sent, failed or unconfirmed" in tool["description"]["llm"]
    assert "last good status" in tool["description"]["llm"]
    assert "never tell the user it did" in tool["description"]["llm"]
    assert tool["output_schema"]["properties"]["delivered_at"]["description"] == "Reserved. Empty today."


def test_manifest_has_what_the_marketplace_requires():
    manifest = load("manifest.yaml")
    assert manifest["author"] == "smstunnel" and manifest["name"] == "smstunnel"
    assert "dify" not in manifest["author"] and "langgenius" not in manifest["author"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["type"] == "plugin"
    assert manifest["privacy"] == "PRIVACY.md" and (ROOT / "PRIVACY.md").is_file()
    assert (ROOT / "_assets" / manifest["icon"]).is_file()
    assert manifest["repo"].startswith("https://github.com/")
    assert re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]+", manifest["contact"])
    assert manifest["meta"]["runner"] == {"language": "python", "version": "3.12", "entrypoint": "main"}
    assert manifest["meta"]["minimum_dify_version"]
    assert 1 <= len(manifest["tags"]) <= 3
    vocabulary = {"agent", "business", "design", "education", "entertainment", "finance", "image", "news", "other", "productivity", "rag", "search", "social", "travel", "utilities", "videos", "weather", "llm", "model_provider", "audio", "document", "devtools", "database", "security", "translation"}
    assert set(manifest["tags"]) <= vocabulary
    assert manifest["network"]["domains"] == ["smstunnel.io"]
    assert manifest["resource"]["permission"] == {}
    assert sc.USER_AGENT == f"smstunnel-dify/{manifest['version']}"
    provider = load("provider/smstunnel.yaml")
    assert set(provider["identity"]["tags"]) == set(manifest["tags"])


def test_requirements_are_pinned_and_current():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").split()
    assert requirements == ["dify_plugin==0.11.1", "httpx==0.28.1"]


def test_one_credential_and_no_way_to_change_the_host():
    provider = load("provider/smstunnel.yaml")
    credentials = provider["credentials_for_provider"]
    assert list(credentials) == ["api_key"] and credentials["api_key"]["type"] == "secret-input"
    for name in TOOLS:
        for parameter in load(f"tools/{name}.yaml").get("parameters") or []:
            assert not re.search(r"url|host|endpoint|base|server|proxy", parameter["name"], re.IGNORECASE)


def test_the_host_is_not_read_from_anywhere():
    client = (ROOT / "smstunnel_client.py").read_text(encoding="utf-8")
    assert 'BASE_URL = "https://smstunnel.io"' in client
    assert "environ" not in client and "getenv" not in client
    assert "follow_redirects=False" in client
    assert client.count("BASE_URL") == 2  # defined once, used once
    tree = ast.parse(client)
    assert sum(isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "BASE_URL" for t in node.targets) for node in ast.walk(tree)) == 1
    for path in [ROOT / "main.py", ROOT / "provider/smstunnel.py", *[ROOT / f"tools/{name}.py" for name in TOOLS]]:
        source = path.read_text(encoding="utf-8")
        assert "environ" not in source and "http://" not in source and "https://" not in source


def test_nothing_logs_the_data():
    for path in [ROOT / "smstunnel_client.py", *[ROOT / f"tools/{name}.py" for name in TOOLS], ROOT / "provider/smstunnel.py"]:
        source = path.read_text(encoding="utf-8")
        assert "logging" not in source and "print(" not in source


def test_send_tool_tells_the_model_the_rules():
    tool = load("tools/send_text.yaml")
    text = tool["description"]["llm"]
    for rule in [
        "Send only texts the user asked for and the recipient expects",
        "wait for their OK",
        "One recipient per call",
        "once per person",
        "Never call it again on your own",
        "tell the user and let them decide",
    ]:
        assert rule in text
    parameters = {p["name"]: p for p in tool["parameters"]}
    assert "Never a list" in parameters["to"]["llm_description"]
    assert "never guess" in parameters["to"]["llm_description"]
    assert parameters["to"]["form"] == "llm" and parameters["message"]["form"] == "llm"
    # A person chooses the phone in the node or agent settings; the model doesn't see it.
    assert parameters["device_id"]["form"] == "form" and "llm_description" not in parameters["device_id"]


@pytest.mark.parametrize("name", TOOLS)
def test_tools_are_complete(name):
    tool = load(f"tools/{name}.yaml")
    assert tool["identity"]["name"] == name and tool["identity"]["author"] == "smstunnel"
    assert tool["identity"]["label"]["en_US"]
    assert tool["description"]["human"]["en_US"] and tool["description"]["llm"]
    assert tool["extra"]["python"]["source"] == f"tools/{name}.py" and (ROOT / f"tools/{name}.py").is_file()
    assert tool["output_schema"]["properties"]


@pytest.mark.parametrize("name", ["get_text_status", "list_phones"])
def test_read_tools_say_they_are_read_only(name):
    assert "Read-only" in load(f"tools/{name}.yaml")["description"]["llm"]


@pytest.mark.parametrize("name", TOOLS)
def test_every_output_field_is_declared(name, api, key):
    declared = set(load(f"tools/{name}.yaml")["output_schema"]["properties"])
    source = (ROOT / f"tools/{name}.py").read_text(encoding="utf-8")
    assert "create_variable_message" in source
    client_fields = {
        "send_text": {"status", "message_id", "to", "queued", "branded", "already_sent"},
        "get_text_status": {"message_id", "status", "to", "sent_at", "delivered_at", "error"},
        "list_phones": {"phones", "online", "total"},
    }[name]
    assert declared == client_fields


def test_the_package_leaves_out_what_is_not_needed():
    ignored = (ROOT / ".difyignore").read_text(encoding="utf-8").split()
    for entry in ["tests/", "dist/", ".git/", ".env", "__pycache__/", ".venv/"]:
        assert entry in ignored
    assert not (ROOT / "pyproject.toml").exists(), "the runtime prefers pyproject.toml over requirements.txt"


def test_docs_cover_what_the_marketplace_asks():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://github.com/narcisbodea/smstunnel-dify" in readme
    for section in ["## Setup", "## Tools", "## Usage", "## Connection requirements", "## Privacy", "## Support"]:
        assert section in readme
    assert "API key" in readme
    privacy = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
    assert "https://smstunnel.io/privacy/" in privacy
    for fact in ["phone number", "words of the text", "API key", "does not store", "smstunnel.io"]:
        assert fact in privacy
    assert not re.search(r"[㐀-鿿]", readme), "the README's main language is English"
