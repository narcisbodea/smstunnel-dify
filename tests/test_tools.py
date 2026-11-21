import json

import httpx
import pytest
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from provider.smstunnel import SMSTunnelProvider
from smstunnel_client import SMSTunnelError
from tools.get_text_status import GetTextStatusTool
from tools.list_phones import ListPhonesTool
from tools.send_text import SendTextTool

TO = "+40712345678"
WORDS = "Your order is ready for pickup."
MESSAGE_ID = "6b0158ee7e87ba0f1130c283"
OK = {"success": True, "messageId": MESSAGE_ID, "data": {"messageId": MESSAGE_ID, "branded": False}}


def collect(messages):
    """The text, JSON and variables a tool produced."""
    texts, payloads, variables = [], [], {}
    for item in messages:
        if item.type == ToolInvokeMessage.MessageType.TEXT:
            texts.append(item.message.text)
        elif item.type == ToolInvokeMessage.MessageType.JSON:
            payloads.append(item.message.json_object)
        elif item.type == ToolInvokeMessage.MessageType.VARIABLE:
            variables[item.message.variable_name] = item.message.variable_value
    return " ".join(texts), payloads, variables


def send(key, turn=None, **params):
    tool = SendTextTool.from_credentials({"api_key": key})
    tool.session.message_id = turn
    return collect(tool.invoke({"to": TO, "message": WORDS, **params}))


def test_send_reports_an_accepted_text(api, key):
    api.respond = lambda request: httpx.Response(200, json=OK)
    text, payloads, variables = send(key)
    assert len(api.requests) == 1
    assert variables["status"] == "accepted" and variables["message_id"] == MESSAGE_ID and variables["to"] == TO
    assert payloads == [variables]
    assert "handed to your phone" in text and "Get text status shows whether it went out" in text
    assert "deliver" not in text.lower() and "arrived" not in text.lower()
    assert WORDS not in json.dumps(variables)


def test_send_reports_a_waiting_text(api, key):
    body = {"success": True, "messageId": MESSAGE_ID, "queued": True, "data": {"messageId": MESSAGE_ID, "queued": True}}
    api.respond = lambda request: httpx.Response(200, json=body)
    text, _, variables = send(key)
    assert variables["status"] == "queued" and variables["queued"] is True
    assert "No phone is online" in text and "Do not send it again" in text


def test_send_mentions_the_free_plan_line(api, key):
    body = {"success": True, "messageId": MESSAGE_ID, "data": {"messageId": MESSAGE_ID, "branded": True}}
    api.respond = lambda request: httpx.Response(200, json=body)
    text, _, variables = send(key)
    assert variables["branded"] is True and "added SMSTunnel line" in text


def test_a_repeat_in_the_same_turn_is_not_sent_again(api, key):
    api.respond = lambda request: httpx.Response(200, json=OK, headers={"Idempotent-Replayed": "true"})
    text, _, variables = send(key, turn="turn-1")
    assert variables["already_sent"] is True and "was not sent again" in text
    assert api.requests[0].headers["idempotency-key"].startswith("dify-turn-")


def test_two_turns_use_different_keys(api, key):
    api.respond = lambda request: httpx.Response(200, json=OK)
    send(key, turn="turn-1")
    send(key, turn="turn-2")
    assert api.requests[0].headers["idempotency-key"] != api.requests[1].headers["idempotency-key"]


def test_a_workflow_run_without_a_turn_sends_no_key(api, key):
    api.respond = lambda request: httpx.Response(200, json=OK)
    send(key, turn=None)
    assert "idempotency-key" not in api.requests[0].headers


@pytest.mark.parametrize(
    "cause",
    [httpx.ReadTimeout("slow"), httpx.Response(503, json={"message": "down"}), httpx.Response(200, text="garbage")],
)
def test_an_unknown_outcome_is_a_result_so_retry_on_failure_cannot_resend(api, key, cause):
    def respond(request):
        if isinstance(cause, Exception):
            raise cause
        return cause

    api.respond = respond
    text, _, variables = send(key)  # no exception
    assert len(api.requests) == 1
    assert variables["status"] == "unknown" and variables["message_id"] == ""
    assert "Do not send it again" in text


@pytest.mark.parametrize(
    "params",
    [
        {"to": "+40712345678,+40712345679"},
        {"to": ["+40712345678", "+40712345679"]},
        {"to": "0712345678"},
        {"to": ""},
        {"message": ""},
        {"message": "x" * 1601},
        {"device_id": "my phone"},
    ],
)
def test_a_refused_call_makes_no_request(api, key, params):
    with pytest.raises(SMSTunnelError):
        send(key, **params)
    assert api.requests == []


def test_definite_refusal_raises(api, key):
    api.respond = lambda request: httpx.Response(401, json={"message": "Invalid or expired API key"})
    with pytest.raises(SMSTunnelError) as caught:
        send(key)
    assert "did not accept this API key" in str(caught.value)


def test_missing_credential_makes_no_request(api):
    with pytest.raises(SMSTunnelError):
        send("")
    assert api.requests == []


def test_the_key_is_never_in_tool_output(api, key):
    api.respond = lambda request: httpx.Response(200, json=OK)
    text, payloads, variables = send(key)
    assert key not in text and key not in json.dumps(payloads) and key not in json.dumps(variables)


@pytest.mark.parametrize(
    "state, expected, warns",
    [
        ("pending", "waiting for your phone", False),
        ("sent", "SMSTunnel can't confirm that it arrived", False),
        # Not reported today. A future server state must not break the tool, and the text promises nothing more.
        ("delivered", "Your phone sent it", False),
        ("failed", "did not go out", True),
        ("unconfirmed", "may have gone out", True),
    ],
)
def test_status_explains_each_state(api, key, state, expected, warns):
    body = {
        "success": True,
        "data": {"id": MESSAGE_ID, "phoneNumber": TO, "status": state, "deliveryStatus": state, "errorMessage": "No signal" if state == "failed" else None},
    }
    api.respond = lambda request: httpx.Response(200, json=body)
    tool = GetTextStatusTool.from_credentials({"api_key": key})
    text, _, variables = collect(tool.invoke({"message_id": MESSAGE_ID}))
    assert variables["status"] == state and expected in text
    assert ("Do not send it again" in text) is warns
    assert "deliver" not in text.replace("Status: delivered", "").lower()
    if state == "failed":
        assert "No signal" in text and variables["error"] == "No signal"
    assert [request.method for request in api.requests] == ["GET"]


def test_status_refuses_a_bad_id_without_a_request(api, key):
    tool = GetTextStatusTool.from_credentials({"api_key": key})
    with pytest.raises(SMSTunnelError):
        list(tool.invoke({"message_id": "../../account/usage"}))
    assert api.requests == []


@pytest.mark.parametrize(
    "devices, expected",
    [
        ([], "No phone is linked"),
        ([{"id": "a" * 24, "name": "Store", "isOnline": False}], "none online"),
        ([{"id": "a" * 24, "name": "Store", "isOnline": True}, {"id": "b" * 24, "name": "Old", "isOnline": False}], "2 phone(s) linked, 1 online"),
    ],
)
def test_list_phones(api, key, devices, expected):
    api.respond = lambda request: httpx.Response(200, json={"success": True, "data": {"devices": devices}})
    tool = ListPhonesTool.from_credentials({"api_key": key})
    text, _, variables = collect(tool.invoke({}))
    assert expected in text and variables["total"] == len(devices)
    assert [request.method for request in api.requests] == ["GET"]


def test_a_valid_key_passes_with_a_read_only_call(api, key):
    api.respond = lambda request: httpx.Response(200, json={"success": True, "data": {"devices": []}})
    SMSTunnelProvider().validate_credentials({"api_key": key})
    assert len(api.requests) == 1
    assert api.requests[0].method == "GET" and api.requests[0].url.path == "/api/v1/devices"


@pytest.mark.parametrize("status", [401, 500])
def test_a_refused_key_fails_validation(api, key, status):
    api.respond = lambda request: httpx.Response(status, json={"message": "no"})
    with pytest.raises(ToolProviderCredentialValidationError) as caught:
        SMSTunnelProvider().validate_credentials({"api_key": key})
    assert key not in str(caught.value)


def test_an_empty_key_fails_validation_without_a_request(api):
    with pytest.raises(ToolProviderCredentialValidationError):
        SMSTunnelProvider().validate_credentials({"api_key": ""})
    assert api.requests == []


def test_the_network_failing_fails_validation(api, key):
    def respond(request):
        raise httpx.ConnectError("refused")

    api.respond = respond
    with pytest.raises(ToolProviderCredentialValidationError):
        SMSTunnelProvider().validate_credentials({"api_key": key})
