from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from smstunnel_client import SMSTunnelClient


class ListPhonesTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        result = SMSTunnelClient(self.runtime.credentials.get("api_key")).list_phones()

        if not result["total"]:
            summary = "No phone is linked yet. Install the SMSTunnel app on an Android phone and link it to this account."
        elif not result["online"]:
            summary = f"{result['total']} phone(s) linked, none online. Texts wait until a phone reconnects (up to 3 days)."
        else:
            summary = f"{result['total']} phone(s) linked, {result['online']} online."
        yield self.create_text_message(summary)
        yield self.create_json_message(result)
        for name, value in result.items():
            yield self.create_variable_message(name, value)
