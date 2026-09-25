"""Choose a credential-isolated provider without requiring Codex for Qiniu."""

from .chat_client import ChatClient
from .codex_client import CodexClient, preflight


def make_client(options):
    if options["provider"] == "qiniu":
        return ChatClient(options["key"])
    return CodexClient(preflight(options), options["key"])
