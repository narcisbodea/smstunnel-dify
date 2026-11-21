from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from smstunnel_client import SMSTunnelClient, SMSTunnelError


class SMSTunnelProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        # A read-only call: checking a key never sends anything.
        try:
            SMSTunnelClient(credentials.get("api_key")).list_phones()
        except SMSTunnelError as error:
            raise ToolProviderCredentialValidationError(str(error)) from None
