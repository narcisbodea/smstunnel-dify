import json

import httpx
import pytest

import smstunnel_client as sc
from smstunnel_client import SendOutcomeUnknown, SMSTunnelClient, SMSTunnelError

TO = "+40712345678"
WORDS = "Your order is ready for pickup."
MESSAGE_ID = "6b0158ee7e87ba0f1130c283"
OK = {"success": True, "messageId": MESSAGE_ID, "data": {"messageId": MESSAGE_ID, "branded": False, "remaining": {"clean": 9}}}


@pytest.mark.parametrize(
    "written, expected",
    [
        ("+40712345678", "+40712345678"),
        ("  +40712345678  ", "+40712345678"),
        ("+1 (415) 555-0123", "+14155550123"),
        ("+40.712.345.678", "+40712345678"),
        ("+44 7700 900123", "+447700900123"),
        ("+40 712 345 678", "+40712345678"),
        ("+1234567", "+1234567"),
        ("+123456789012345", "+123456789012345"),
    ],
)
def test_one_number_in_international_format(written, expected):
    assert sc.normalize_recipient(written) == expected


@pytest.mark.parametrize(
    "value",
    [
        "+40712345678,+40712345679",
        "+40712345678, +40712345679",
        "+40712345678;+40712345679",
        "+40712345678 +40712345679",
        "+40712345678\n+40712345679",
        "+40712345678/+40712345679",
        "+40712345678|+40712345679",
        "+40712345678 & +40712345679",
        "+40712345678 and +40712345679",
        "['+40712345678', '+40712345679']",
        '["+40712345678"]',
        "+40712345678 0712345679",
        "0712345678 0723456789",
        ["+40712345678", "+40712345679"],
        ("+40712345678",),
        {"+40712345678"},
        {"to": "+40712345678"},
    ],
)
def test_a_list_is_refused(value):
    with pytest.raises(SMSTunnelError) as error:
        sc.normalize_recipient(value)
    assert "one number per call" in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        None,
        40712345678,
        "0712345678",
        "712345678",
        "0040712345678",
        "+0712345678",
        "+123456",
        "+40abc345678",
        "tel:+40712345678",
        "Ana",
        "+40٧١٢345678",
    ],
)
def test_a_number_without_its_country_code_is_refused(value):
    with pytest.raises(SMSTunnelError) as error:
        sc.normalize_recipient(value)
    assert "international format" in str(error.value)


@pytest.mark.parametrize("value", ["", "   ", "\n", None, 12, ["a"]])
def test_empty_text_is_refused(value):
    with pytest.raises(SMSTunnelError):
        sc.check_message(value)


def test_text_length_limit():
    assert sc.check_message("a" * 1600) == "a" * 1600
    with pytest.raises(SMSTunnelError) as error:
        sc.check_message("a" * 1601)
    assert "1601" in str(error.value) and "a" * 20 not in str(error.value)


def test_text_is_kept_exactly():
    words = "  Ciao, șt→\n🙂 "
    assert sc.check_message(words) == words


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_phone_id_means_default_phone(value):
    assert sc.check_device_id(value) is None


@pytest.mark.parametrize("value", ["abc", "Test phone", MESSAGE_ID + "0", "../devices", 5])
def test_bad_phone_id_is_refused(value):
    with pytest.raises(SMSTunnelError):
        sc.check_device_id(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "abc",
        "../../account/usage",
        MESSAGE_ID + "/../..",
        MESSAGE_ID[:-1] + "%",
        MESSAGE_ID + "?x=1",
        MESSAGE_ID + "\n",
        "../" + MESSAGE_ID,
        123,
    ],
)
def test_message_id_cannot_steer_the_url(value):
    if value == MESSAGE_ID + "\n":
        # Surrounding whitespace is trimmed; what remains is the plain ID.
        assert sc.check_message_id(value) == MESSAGE_ID
        return
    with pytest.raises(SMSTunnelError):
        sc.check_message_id(value)


def test_turn_key_only_within_a_turn():
    assert sc.turn_idempotency_key(None, TO, WORDS, None) is None
    assert sc.turn_idempotency_key("", TO, WORDS, None) is None
    first = sc.turn_idempotency_key("turn-1", TO, WORDS, None)
    assert first == sc.turn_idempotency_key("turn-1", TO, WORDS, None)
    assert first.startswith("dify-turn-") and len(first) <= 255 and first.isascii()
    assert TO not in first and WORDS not in first
    assert first != sc.turn_idempotency_key("turn-2", TO, WORDS, None)
    assert first != sc.turn_idempotency_key("turn-1", "+40712345679", WORDS, None)
    assert first != sc.turn_idempotency_key("turn-1", TO, WORDS + "!", None)
    assert first != sc.turn_idempotency_key("turn-1", TO, WORDS, MESSAGE_ID)


def test_the_key_must_look_like_a_key():
    for bad in ("", "   ", None, 5, "short", "with space inside", "bad\nkey12345", "ключ-ключ-ключ"):
        with pytest.raises(SMSTunnelError):
            SMSTunnelClient(bad)


def test_host_is_a_fixed_https_constant():
    assert sc.BASE_URL == "https://smstunnel.io"


def test_send_makes_one_request_to_the_fixed_route(api, key):
    api.respond = lambda request: httpx.Response(200, json=OK)
    result = SMSTunnelClient(key).send_text(TO, WORDS)

    assert len(api.requests) == 1
    request = api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://smstunnel.io/api/v1/sms/send"
    assert request.headers["x-api-key"] == key
    assert "idempotency-key" not in request.headers
    assert json.loads(request.content) == {"to": TO, "message": WORDS}
    assert result == {"message_id": MESSAGE_ID, "queued": False, "branded": False, "already_sent": False}


def test_send_with_phone_and_turn_key(api, key):
    api.respond = lambda request: httpx.Response(200, json=OK)
    SMSTunnelClient(key).send_text(TO, WORDS, MESSAGE_ID, "dify-turn-abc")
    request = api.requests[0]
    assert json.loads(request.content) == {"to": TO, "message": WORDS, "deviceId": MESSAGE_ID}
    assert request.headers["idempotency-key"] == "dify-turn-abc"


def test_queued_branded_and_replayed_are_read(api, key):
    body = {"success": True, "messageId": MESSAGE_ID, "queued": True, "data": {"messageId": MESSAGE_ID, "branded": True, "queued": True}}
    api.respond = lambda request: httpx.Response(200, json=body, headers={"Idempotent-Replayed": "true"})
    assert SMSTunnelClient(key).send_text(TO, WORDS) == {
        "message_id": MESSAGE_ID,
        "queued": True,
        "branded": True,
        "already_sent": True,
    }


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("slow"),
        httpx.WriteTimeout("slow"),
        httpx.PoolTimeout("slow"),
        httpx.ReadError("cut"),
        httpx.WriteError("cut"),
        httpx.RemoteProtocolError("cut"),
        httpx.ProxyError("proxy"),
        httpx.DecodingError("bad gzip"),
    ],
)
def test_a_lost_answer_is_unknown_and_never_retried(api, key, error):
    def respond(request):
        raise error

    api.respond = respond
    with pytest.raises(SendOutcomeUnknown) as caught:
        SMSTunnelClient(key).send_text(TO, WORDS)
    assert len(api.requests) == 1
    assert "may or may not" in str(caught.value) and "Do not send it again" in str(caught.value)


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")])
def test_never_reached_the_server_is_not_sent_and_never_retried(api, key, error):
    def respond(request):
        raise error

    api.respond = respond
    with pytest.raises(SMSTunnelError) as caught:
        SMSTunnelClient(key).send_text(TO, WORDS)
    assert not isinstance(caught.value, SendOutcomeUnknown)
    assert "not sent" in str(caught.value)
    assert len(api.requests) == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_server_error_is_unknown_and_never_retried(api, key, status):
    api.respond = lambda request: httpx.Response(status, json={"message": "boom"})
    with pytest.raises(SendOutcomeUnknown):
        SMSTunnelClient(key).send_text(TO, WORDS)
    assert len(api.requests) == 1


def test_a_send_still_running_is_unknown(api, key):
    api.respond = lambda request: httpx.Response(409, json={"code": "IDEMPOTENCY_KEY_IN_PROGRESS"}, headers={"Retry-After": "1"})
    with pytest.raises(SendOutcomeUnknown):
        SMSTunnelClient(key).send_text(TO, WORDS, idempotency_key="dify-turn-x")
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["unexpected"]),
        httpx.Response(200, json={"success": True}),
        httpx.Response(200, json={"success": False, "messageId": MESSAGE_ID}),
        httpx.Response(200, json={"success": True, "messageId": 5}),
    ],
)
def test_an_unreadable_success_is_unknown(api, key, response):
    api.respond = lambda request: response
    with pytest.raises(SendOutcomeUnknown):
        SMSTunnelClient(key).send_text(TO, WORDS)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_not_followed(api, key, status):
    api.respond = lambda request: httpx.Response(status, headers={"Location": "https://attacker.example/steal"})
    with pytest.raises(SMSTunnelError) as caught:
        SMSTunnelClient(key).send_text(TO, WORDS)
    assert not isinstance(caught.value, SendOutcomeUnknown)
    assert len(api.requests) == 1
    assert api.requests[0].url.host == "smstunnel.io"


@pytest.mark.parametrize(
    "status, body, headers, expected",
    [
        (400, {"message": ["Phone number must contain 7-25 characters"]}, {}, "Phone number must contain"),
        (401, {"message": "Invalid or expired API key"}, {}, "did not accept this API key"),
        (403, {"code": "PERMISSION_REQUIRED", "permission": "sms:send", "message": "x"}, {}, "sms:send permission"),
        (403, {"message": "Not on this plan"}, {}, "Not on this plan"),
        (404, {"message": "Device not found"}, {}, "Device not found"),
        (429, {"message": "Too many"}, {"Retry-After": "42"}, "Wait 42 seconds"),
        (429, {"message": "Daily limit reached"}, {}, "allowance is used up"),
        (422, {"code": "IDEMPOTENCY_KEY_REUSED", "message": "Reused"}, {}, "Reused"),
    ],
)
def test_a_definite_refusal_is_a_plain_error(api, key, status, body, headers, expected):
    api.respond = lambda request: httpx.Response(status, json=body, headers=headers)
    with pytest.raises(SMSTunnelError) as caught:
        SMSTunnelClient(key).send_text(TO, WORDS)
    assert not isinstance(caught.value, SendOutcomeUnknown)
    assert expected in str(caught.value)
    assert len(api.requests) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_errors_never_carry_the_key_number_or_words(api, key, status):
    api.respond = lambda request: httpx.Response(status, json={"message": "refused"})
    with pytest.raises(SMSTunnelError) as caught:
        SMSTunnelClient(key).send_text(TO, WORDS)
    shown = str(caught.value)
    assert key not in shown and TO not in shown and WORDS not in shown


def test_status_reads_the_real_state(api, key):
    body = {
        "success": True,
        "data": {
            "id": MESSAGE_ID,
            "phoneNumber": TO,
            "status": "failed",
            "unconfirmed": True,
            "deliveryStatus": "unconfirmed",
            "sentAt": None,
            "deliveredAt": None,
            "errorMessage": "Not confirmed by the phone",
        },
    }
    api.respond = lambda request: httpx.Response(200, json=body)
    result = SMSTunnelClient(key).get_text_status(MESSAGE_ID)
    assert str(api.requests[0].url) == f"https://smstunnel.io/api/v1/sms/status/{MESSAGE_ID}"
    assert api.requests[0].method == "GET"
    assert result["status"] == "unconfirmed" and result["to"] == TO and result["sent_at"] == ""


def test_phones_are_read(api, key):
    body = {
        "success": True,
        "data": {
            "devices": [
                {"id": "a" * 24, "name": "Store phone", "isOnline": True, "lastSeenAt": "2026-11-21T08:00:00Z"},
                {"id": "b" * 24, "name": "Old phone", "isOnline": False, "lastSeenAt": None},
            ],
            "online": 1,
            "total": 2,
        },
    }
    api.respond = lambda request: httpx.Response(200, json=body)
    result = SMSTunnelClient(key).list_phones()
    assert api.requests[0].method == "GET" and str(api.requests[0].url) == "https://smstunnel.io/api/v1/devices"
    assert result["online"] == 1 and result["total"] == 2
    assert result["phones"][1] == {"id": "b" * 24, "name": "Old phone", "online": False, "last_seen_at": ""}


@pytest.mark.parametrize("call", ["get_text_status", "list_phones"])
def test_read_routes_report_a_network_failure_without_detail(api, key, call):
    def respond(request):
        raise httpx.ConnectError(f"cannot connect with {key}")

    api.respond = respond
    client = SMSTunnelClient(key)
    with pytest.raises(SMSTunnelError) as caught:
        client.get_text_status(MESSAGE_ID) if call == "get_text_status" else client.list_phones()
    assert key not in str(caught.value)


@pytest.mark.parametrize("response", [httpx.Response(200, text="nope"), httpx.Response(200, json={"success": True})])
def test_read_routes_refuse_an_unreadable_answer(api, key, response):
    api.respond = lambda request: response
    with pytest.raises(SMSTunnelError):
        SMSTunnelClient(key).list_phones()
