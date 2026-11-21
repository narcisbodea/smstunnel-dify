from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from smstunnel_client import (
    SendOutcomeUnknown,
    SMSTunnelClient,
    check_device_id,
    check_message,
    normalize_recipient,
    turn_idempotency_key,
)


class SendTextTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        # Everything is checked before the first request, so a refused call never reaches the phone.
        to = normalize_recipient(tool_parameters.get("to"))
        message = check_message(tool_parameters.get("message"))
        device_id = check_device_id(tool_parameters.get("device_id"))
        client = SMSTunnelClient(self.runtime.credentials.get("api_key"))
        key = turn_idempotency_key(getattr(self.session, "message_id", None), to, message, device_id)

        try:
            sent = client.send_text(to, message, device_id, key)
        except SendOutcomeUnknown as error:
            # Reported as a result, not an error: a node set to retry on failure must not send it twice.
            result = {"status": "unknown", "message_id": "", "to": to, "queued": False, "branded": False, "already_sent": False}
            yield from self._emit(str(error), result)
            return

        status = "queued" if sent["queued"] else "accepted"
        result = {"status": status, "to": to, **sent}
        yield from self._emit(self._summary(result), result)

    def _emit(self, summary: str, result: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        yield self.create_text_message(summary)
        yield self.create_json_message(result)
        for name, value in result.items():
            yield self.create_variable_message(name, value)

    @staticmethod
    def _summary(result: dict[str, Any]) -> str:
        message_id = result["message_id"]
        if result["already_sent"]:
            return f"This exact text was already sent in this conversation turn, so it was not sent again. Message ID: {message_id}."
        if result["status"] == "queued":
            text = (
                "No phone is online right now. The text is waiting and goes out when your phone reconnects "
                f"(within 3 days). Do not send it again. Message ID: {message_id}."
            )
        else:
            text = f"The text was handed to your phone. Get text status shows whether it went out. Message ID: {message_id}."
        if result["branded"]:
            text += " On the free plan, this text carries a short added SMSTunnel line."
        return text
