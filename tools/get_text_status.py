from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from smstunnel_client import SMSTunnelClient, check_message_id

DO_NOT_RESEND = "Do not send it again on your own: tell the user and let them decide."

MEANINGS = {
    "pending": "It is waiting for your phone, or being sent.",
    "sent": "Your phone handed it to its SIM and Android confirmed it went out. This is the last status: SMSTunnel can't confirm that it arrived.",
    # The API doesn't report this state today. If it ever does, the tool keeps working and promises nothing more.
    "delivered": "Your phone sent it.",
    "unconfirmed": f"Your phone handed it to the SIM but got no confirmation, so it may have gone out. {DO_NOT_RESEND}",
}


class GetTextStatusTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        message_id = check_message_id(tool_parameters.get("message_id"))
        result = SMSTunnelClient(self.runtime.credentials.get("api_key")).get_text_status(message_id)

        status = result["status"]
        if status == "failed":
            reason = f" Reason: {result['error']}" if result["error"] else ""
            meaning = f"It did not go out.{reason} {DO_NOT_RESEND}"
        else:
            meaning = MEANINGS.get(status, "")
        yield self.create_text_message(f"Status: {status}. {meaning}".strip())
        yield self.create_json_message(result)
        for name, value in result.items():
            yield self.create_variable_message(name, value)
