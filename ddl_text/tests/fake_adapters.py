"""Spawn-importable, offline test doubles. No production model calls."""

import socket
import time
from copy import deepcopy
from dataclasses import dataclass
from unittest.mock import patch

from ddl_text.adapters import AgentError, ReplayAdapter


@dataclass
class NetworkForbiddenReplay(ReplayAdapter):
    def extract(self, record, settings, attempt):
        with patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")):
            return super().extract(record, settings, attempt)


@dataclass
class ControlledAdapter:
    behavior: str
    tasks: list = None

    def extract(self, record, settings, attempt):
        if self.behavior == "timeout":
            time.sleep(30)
        if self.behavior == "retry" and attempt == 0:
            raise AgentError("agent_unavailable", True)
        if self.behavior == "nonretryable" and attempt == 0:
            raise AgentError("agent_unavailable", False)
        if self.behavior == "malformed":
            return {"not": "a task list"}
        if self.behavior == "secret":
            print("PRIVATE-TEXT-SENTINEL")
            raise RuntimeError("PRIVATE-TEXT-SENTINEL SECRET-KEY-SENTINEL")
        if self.behavior == "context":
            if record["context"] or "other_user" in record:
                raise RuntimeError("Unexpected state")
            record["context"].append({"other_user": "MUTATED"})
        return deepcopy(self.tasks or [])


@dataclass
class TimeoutFirstAdapter:
    def extract(self, record, settings, attempt):
        if record["input_id"] == "slow":
            time.sleep(30)
        return []
