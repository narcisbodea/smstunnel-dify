"""A small client for the SMSTunnel API.

The rules that keep a person from getting a wrong or a repeated text live here:

- The host is a constant. No credential, tool parameter or setting can change it, and redirects are
  not followed, so the API key only ever goes to smstunnel.io.
- One recipient per call, in international format. A list is refused before any request is made,
  and so is a number without its country code (the API would complete it with a default country).
- A send is made once. Nothing here retries. When the answer is lost, the outcome is reported as
  unknown (SendOutcomeUnknown), because the text may have gone out.
- Errors never carry the API key, the recipient's number or the words of the text.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx

BASE_URL = "https://smstunnel.io"
USER_AGENT = "smstunnel-dify/1.0.0"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
MAX_MESSAGE_LENGTH = 1600

_E164 = re.compile(r"\+[1-9][0-9]{6,14}")
_DIGITS_ONLY = re.compile(r"\+?[0-9]+")
_MAX_E164_DIGITS = 15
# What people write inside a single number: "+1 (415) 555-0123", "+40.712.345.678".
_NUMBER_PUNCTUATION = re.compile(r"[\s().\-]")
# What only appears when several numbers were given (a stringified list included).
_LIST_MARK = re.compile(r"[,;/|&\[\]{}'\"\n\r]|\band\b", re.IGNORECASE)
_OBJECT_ID = re.compile(r"[0-9a-fA-F]{24}")
_API_KEY = re.compile(r"[\x21-\x7e]{8,256}")

ONE_RECIPIENT = (
    "This tool sends to one number per call. Call it once per person, "
    "and only for people the user approved."
)
BAD_NUMBER = (
    "Write the number in international format: + and the country code, for example +14155550123. "
    "If you don't know the country code, ask the user; never guess or complete a number."
)
NOT_SENT_UNREACHABLE = (
    "Could not reach SMSTunnel, so the text was not sent. Check the network connection to "
    "smstunnel.io and try again when the user asks."
)
OUTCOME_UNKNOWN = (
    "SMSTunnel did not confirm this send, so the text may or may not have gone out. Do not send it "
    "again automatically: check Messages in the SMSTunnel dashboard, tell the user, and let them decide."
)
UNREACHABLE = "Could not reach SMSTunnel. Check the network connection to smstunnel.io."


class SMSTunnelError(Exception):
    """A failure whose message is safe to show to the user and the model."""


class SendOutcomeUnknown(SMSTunnelError):
    """The send may have gone through, so it must not be repeated automatically."""


def normalize_recipient(value: Any) -> str:
    """One number in E.164, or a clear refusal. Lists never get through."""
    if not isinstance(value, str):
        raise SMSTunnelError(ONE_RECIPIENT if isinstance(value, (list, tuple, set, dict)) else BAD_NUMBER)
    raw = value.strip()
    if not raw:
        raise SMSTunnelError(BAD_NUMBER)
    if _LIST_MARK.search(raw) or raw.count("+") > 1:
        raise SMSTunnelError(ONE_RECIPIENT)
    number = _NUMBER_PUNCTUATION.sub("", raw)
    if _E164.fullmatch(number):
        return number
    if _DIGITS_ONLY.fullmatch(number) and len(number.lstrip("+")) > _MAX_E164_DIGITS:
        # Too many digits for one number: two numbers run together.
        raise SMSTunnelError(ONE_RECIPIENT)
    raise SMSTunnelError(BAD_NUMBER)


def check_message(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SMSTunnelError("The text is empty. Give the exact words to send.")
    if len(value) > MAX_MESSAGE_LENGTH:
        raise SMSTunnelError(
            f"The text has {len(value)} characters; the limit is {MAX_MESSAGE_LENGTH}. Shorten it and ask the user to approve the new words."
        )
    return value


def check_device_id(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not _OBJECT_ID.fullmatch(value.strip()):
        raise SMSTunnelError("Unknown phone ID. Use an ID from the List phones tool, or leave it empty for your default phone.")
    return value.strip()


def check_message_id(value: Any) -> str:
    # The ID goes into the URL path, so only the exact shape of an ID is accepted.
    if not isinstance(value, str) or not _OBJECT_ID.fullmatch(value.strip()):
        raise SMSTunnelError("That is not a message ID. Use the message_id returned by the Send a text tool.")
    return value.strip()


def turn_idempotency_key(turn_id: str | None, to: str, message: str, device_id: str | None) -> str | None:
    """The same text to the same number within one conversation turn goes out once.

    Without a turn (a plain workflow run has none) there is no key.
    """
    if not turn_id:
        return None
    material = "\x1f".join((turn_id, to, message, device_id or ""))
    return "dify-turn-" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _body(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _server_message(body: dict[str, Any]) -> str:
    message = body.get("message")
    if isinstance(message, list):
        message = "; ".join(str(part) for part in message)
    return str(message)[:300] if message else ""


def _refusal(response: httpx.Response) -> SMSTunnelError:
    """A definite answer from the API: nothing was sent."""
    status = response.status_code
    body = _body(response)
    detail = _server_message(body)
    if status == 401:
        return SMSTunnelError(
            "SMSTunnel did not accept this API key. Check it in the plugin's settings, or create a new one in the SMSTunnel dashboard (Apps)."
        )
    if status == 403 and body.get("code") == "PERMISSION_REQUIRED":
        permission = str(body.get("permission", ""))[:40]
        return SMSTunnelError(
            f"This API key does not have the {permission} permission. Allow it in the SMSTunnel dashboard (Apps), or use another key."
        )
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.strip().isdigit():
            return SMSTunnelError(
                f"Too many requests. Nothing was sent. Wait {retry_after.strip()} seconds before the user tries again."
            )
        return SMSTunnelError(f"Your SMSTunnel plan's allowance is used up for now, so nothing was sent. {detail}".strip())
    if 300 <= status < 400:
        return SMSTunnelError(f"SMSTunnel answered with a redirect (HTTP {status}), so nothing was sent.")
    return SMSTunnelError(detail or f"SMSTunnel answered HTTP {status}.")


class SMSTunnelClient:
    def __init__(self, api_key: Any, *, transport: httpx.BaseTransport | None = None) -> None:
        key = api_key.strip() if isinstance(api_key, str) else ""
        if not _API_KEY.fullmatch(key):
            raise SMSTunnelError("Add your SMSTunnel API key in the plugin's settings (SMSTunnel dashboard: Apps, Add an app).")
        self._headers = {"X-API-Key": key, "Accept": "application/json", "User-Agent": USER_AGENT}
        self._transport = transport

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        # One request, no retries, no redirects. Proxy settings of the host running Dify are honored.
        with httpx.Client(
            headers=self._headers,
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            return client.request(method, BASE_URL + path, **kwargs)

    def _get(self, path: str) -> dict[str, Any]:
        try:
            response = self._request("GET", path)
        except httpx.HTTPError:
            raise SMSTunnelError(UNREACHABLE) from None
        if not response.is_success:
            raise _refusal(response)
        body = _body(response)
        data = body.get("data")
        if body.get("success") is not True or not isinstance(data, dict):
            raise SMSTunnelError("SMSTunnel sent an answer this plugin could not read.")
        return data

    def send_text(
        self,
        to: str,
        message: str,
        device_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/sms/send, exactly once."""
        payload: dict[str, Any] = {"to": to, "message": message}
        if device_id:
            payload["deviceId"] = device_id
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            response = self._request("POST", "/api/v1/sms/send", json=payload, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            # The request never reached the server: nothing can have gone out.
            raise SMSTunnelError(NOT_SENT_UNREACHABLE) from None
        except httpx.HTTPError:
            # Anything else may have happened after the server took the request.
            raise SendOutcomeUnknown(OUTCOME_UNKNOWN) from None

        if response.status_code >= 500 or response.status_code == 409:
            # 409: the same send is still running on the server.
            raise SendOutcomeUnknown(OUTCOME_UNKNOWN)
        if not response.is_success:
            raise _refusal(response)

        body = _body(response)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        message_id = body.get("messageId") or data.get("messageId")
        if body.get("success") is not True or not isinstance(message_id, str) or not message_id:
            raise SendOutcomeUnknown(OUTCOME_UNKNOWN)
        return {
            "message_id": message_id,
            "queued": bool(body.get("queued") or data.get("queued")),
            "branded": bool(data.get("branded")),
            "already_sent": response.headers.get("Idempotent-Replayed", "").lower() == "true",
        }

    def get_text_status(self, message_id: str) -> dict[str, Any]:
        """GET /api/v1/sms/status/{id}. `deliveryStatus` holds the real state, unconfirmed included."""
        data = self._get(f"/api/v1/sms/status/{message_id}")
        return {
            "message_id": str(data.get("id") or message_id),
            "status": str(data.get("deliveryStatus") or data.get("status") or "unknown"),
            "to": data.get("phoneNumber") or "",
            "sent_at": data.get("sentAt") or "",
            "delivered_at": data.get("deliveredAt") or "",
            "error": data.get("errorMessage") or "",
        }

    def list_phones(self) -> dict[str, Any]:
        """GET /api/v1/devices. Read-only; also how the API key is validated."""
        data = self._get("/api/v1/devices")
        phones = [
            {
                "id": str(device.get("id", "")),
                "name": str(device.get("name", "")),
                "online": bool(device.get("isOnline")),
                "last_seen_at": device.get("lastSeenAt") or "",
            }
            for device in data.get("devices", [])
            if isinstance(device, dict)
        ]
        return {
            "phones": phones,
            "online": sum(1 for phone in phones if phone["online"]),
            "total": len(phones),
        }
