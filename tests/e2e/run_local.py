#!/usr/bin/env python3
"""LOCAL ONLY. Runs every tool, through Dify's own Tool classes, against a local SMSTunnel API and a fake phone.

    SMSTUNNEL_E2E_URL=http://127.0.0.1:3231 SMSTUNNEL_E2E_SEED=seed.json SMSTUNNEL_E2E_PHONE_LOG=phone.log \
        python tests/e2e/run_local.py

It refuses any address other than this machine, so it can never reach the real service.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

URL = os.environ.get("SMSTUNNEL_E2E_URL", "")
if not re.fullmatch(r"http://(127\.0\.0\.1|localhost):\d+", URL):
    sys.exit("SMSTUNNEL_E2E_URL must be a local address, for example http://127.0.0.1:3231")

seed = json.loads(Path(os.environ["SMSTUNNEL_E2E_SEED"]).read_text(encoding="utf-8"))
KEYS = seed["keys"]
DEVICE_ID = seed["deviceId"]
PHONE_LOG = Path(os.environ["SMSTUNNEL_E2E_PHONE_LOG"])

import smstunnel_client  # noqa: E402

smstunnel_client.BASE_URL = URL  # only in this test process: the shipped host is a constant

from dify_plugin.entities.tool import ToolInvokeMessage  # noqa: E402
from dify_plugin.errors.tool import ToolProviderCredentialValidationError  # noqa: E402

from provider.smstunnel import SMSTunnelProvider  # noqa: E402
from smstunnel_client import SMSTunnelError  # noqa: E402
from tools.get_text_status import GetTextStatusTool  # noqa: E402
from tools.list_phones import ListPhonesTool  # noqa: E402
from tools.send_text import SendTextTool  # noqa: E402

TO = "+40712345601"
results = []


def check(name, passed):
    results.append((name, bool(passed)))
    print(("PASS " if passed else "FAIL ") + name)


def collect(messages):
    texts, variables = [], {}
    for item in messages:
        if item.type == ToolInvokeMessage.MessageType.TEXT:
            texts.append(item.message.text)
        elif item.type == ToolInvokeMessage.MessageType.VARIABLE:
            variables[item.message.variable_name] = item.message.variable_value
    return " ".join(texts), variables


def send(key=KEYS["full"], turn=None, **params):
    tool = SendTextTool.from_credentials({"api_key": key})
    tool.session.message_id = turn
    return collect(tool.invoke({"to": TO, "message": "E2E text", **params}))


def status(message_id, key=KEYS["full"]):
    return collect(GetTextStatusTool.from_credentials({"api_key": key}).invoke({"message_id": message_id}))


def phones(key=KEYS["full"]):
    return collect(ListPhonesTool.from_credentials({"api_key": key}).invoke({}))


def refused(call):
    try:
        call()
    except (SMSTunnelError, ToolProviderCredentialValidationError) as error:
        return str(error)
    return ""


def texts_used(key=KEYS["full"]):
    response = httpx.get(URL + "/api/v1/account/usage", headers={"X-API-Key": key}, timeout=10)
    usage = response.json()["data"]["usage"]
    return usage["smsClean"] + usage["smsBranded"]


def phone_received():
    return [line for line in PHONE_LOG.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("Sending to ")]


def wait_status(message_id, wanted, seconds=8):
    deadline = time.time() + seconds
    while time.time() < deadline:
        _, variables = status(message_id)
        if variables["status"] == wanted:
            return True
        time.sleep(0.25)
    return False


def wait_phone(count, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if len(phone_received()) >= count:
            return True
        time.sleep(0.1)
    return False


# --- The key
check("a valid key passes validation", not refused(lambda: SMSTunnelProvider().validate_credentials({"api_key": KEYS["full"]})))
check("a key with only send permission passes validation", not refused(lambda: SMSTunnelProvider().validate_credentials({"api_key": KEYS["sendOnly"]})))
check("a wrong key is refused", "did not accept" in refused(lambda: SMSTunnelProvider().validate_credentials({"api_key": "sk_live_" + "0" * 32})))
check("a deactivated key is refused", "did not accept" in refused(lambda: SMSTunnelProvider().validate_credentials({"api_key": KEYS["inactive"]})))
used = texts_used()
check("validating a key sends nothing", used == 0)

# --- List phones
text, variables = phones()
check("list_phones: one phone, online", variables["total"] == 1 and variables["online"] == 1 and variables["phones"][0]["id"] == DEVICE_ID)
check("list_phones: summary", "1 online" in text)
text, variables = phones(KEYS["offline"])
check("list_phones: linked phone offline", variables["online"] == 0 and "none online" in text)
text, variables = phones(KEYS["other"])
check("list_phones: no phone", variables["total"] == 0 and "No phone is linked" in text)

# --- Send, then status
before = len(phone_received())
text, sent = send(message="Your order #1042 is ready for pickup.")
check("send_text: accepted", sent["status"] == "accepted" and bool(sent["message_id"]) and sent["to"] == TO)
check("send_text: says handed to the phone", "handed to your phone" in text)
check("the fake phone got the text once", wait_phone(before + 1) and len(phone_received()) == before + 1)
check("the phone got the exact number and words", phone_received()[-1] == f"Sending to {TO}: Your order #1042 is ready for pickup.")
check("get_text_status: reaches sent", wait_status(sent["message_id"], "sent"))
text, current = status(sent["message_id"])
check("get_text_status: fields", current["to"] == TO and bool(current["sent_at"]) and current["message_id"] == sent["message_id"])
check("get_text_status: says sent is the last status", "last status" in text and "can't confirm that it arrived" in text)

# --- Words and number written the human way
before = len(phone_received())
words = "Bună ziua! Șoseaua e închisă → 🙂\nСпасибо"
_, sent2 = send(to="+40 (712) 345-601", message=words)
check("number with spaces and dashes is sent in international format", sent2["to"] == TO and wait_phone(before + 1))
received = PHONE_LOG.read_text(encoding="utf-8", errors="replace")
check("diacritics, emoji and Cyrillic arrive unchanged", f"Sending to {TO}: {words}" in received)

# --- Choosing the phone
_, sent3 = send(device_id=DEVICE_ID)
check("send_text with Phone ID", sent3["status"] == "accepted")
message = refused(lambda: send(device_id="6b015894990acf4fb173baff"))
check("a phone ID that isn't on this account is refused", "Device not found" in message)

# --- Refused before any request
used_before = texts_used()
phone_before = len(phone_received())
for name, params in [
    ("comma list", {"to": "+40712345601,+40712345602"}),
    ("real list", {"to": ["+40712345601", "+40712345602"]}),
    ("stringified list", {"to": "['+40712345601', '+40712345602']"}),
    ("two numbers", {"to": "+40712345601 +40712345602"}),
]:
    check(f"refused, one recipient: {name}", "one number per call" in refused(lambda: send(**params)))
check("refused: local number without country code", "international format" in refused(lambda: send(to="0712345601")))
check("refused: empty text", "empty" in refused(lambda: send(message="   ")))
check("refused: 1601 characters", "1601" in refused(lambda: send(message="x" * 1601)))
time.sleep(0.5)
check("refused calls sent nothing (usage)", texts_used() == used_before)
check("refused calls sent nothing (phone)", len(phone_received()) == phone_before)

# --- Permissions and other accounts
check("a key without sms:send cannot send", "sms:send permission" in refused(lambda: send(KEYS["readOnly"])))
check("a key without sms:read cannot read status", "sms:read permission" in refused(lambda: status(sent["message_id"], KEYS["sendOnly"])))
check("another account cannot read this text", "not found" in refused(lambda: status(sent["message_id"], KEYS["other"])).lower())
check("an unknown message ID is reported", "not found" in refused(lambda: status("6b015894990acf4fb173baff")).lower())
check("a malformed message ID makes no request", "not a message ID" in refused(lambda: status("../../account/usage")))

# --- The same text twice in one turn goes out once
used_before = texts_used()
phone_before = len(phone_received())
_, first = send(turn="e2e-turn-1", message="Only once, please.")
wait_phone(phone_before + 1)
text, second = send(turn="e2e-turn-1", message="Only once, please.")
time.sleep(1.2)
check("same turn: second call reports already sent", second["already_sent"] is True and "not sent again" in text)
check("same turn: same message ID", first["message_id"] == second["message_id"])
check("same turn: counted once", texts_used() == used_before + 1)
check("same turn: phone got it once", len(phone_received()) == phone_before + 1)
_, third = send(turn="e2e-turn-2", message="Only once, please.")
check("a new turn is a new text", third["message_id"] != first["message_id"] and third["already_sent"] is False)
_, another = send(turn="e2e-turn-1", message="A different text.")
check("a different text in the same turn goes out", another["message_id"] != first["message_id"] and another["already_sent"] is False)
wait_phone(phone_before + 3)

# --- A phone that is offline
text, waiting = send(KEYS["offline"])
check("offline phone: queued", waiting["status"] == "queued" and waiting["queued"] is True)
check("offline phone: says it waits", "goes out when your phone reconnects" in text)
_, current = status(waiting["message_id"], KEYS["offline"])
check("offline phone: status pending", current["status"] == "pending")

# --- Plan limits
text, branded = send(KEYS["free"])
check("free plan past 25: carries the short line, and says so", branded["branded"] is True and "added SMSTunnel line" in text)
check("plan allowance used up: refused", "allowance is used up" in refused(lambda: send(KEYS["spent"])))

# --- No retries when the server is unreachable: one attempt, reported as not sent
smstunnel_client.BASE_URL = "http://127.0.0.1:9"
check("unreachable server: reported as not sent", "not sent" in refused(lambda: send()))
smstunnel_client.BASE_URL = URL

failed = [name for name, passed in results if not passed]
print(f"\n{len(results) - len(failed)} of {len(results)} checks passed")
sys.exit(1 if failed else 0)
