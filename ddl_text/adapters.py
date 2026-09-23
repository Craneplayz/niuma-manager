"""Trusted, spawn-compatible adapter objects implementing one small protocol."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from .config import Settings


class AgentError(Exception):
    def __init__(self, code="agent_unavailable", retryable=False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class AgentAdapter(Protocol):
    def extract(self, record: dict, settings: Settings, attempt: int) -> list:
        """Return candidate task objects; attempt starts at zero.

        Adapters are trusted Python objects and must support multiprocessing spawn.
        They receive one normalized record and only its explicitly supplied context.
        No output, exception or model instructions may invoke unrelated tools here.
        """
        ...


@dataclass
class UnavailableAdapter:
    def extract(self, record, settings, attempt):
        # Configuration is not a verified implementation of a real provider.
        raise AgentError("agent_unavailable", retryable=False)


@dataclass
class ReplayAdapter:
    cases: list

    def extract(self, record, settings, attempt):
        # Exact record matching prevents edited/private input being labelled as a
        # successful replay merely because it reuses a fixture ID.
        for case in self.cases:
            for original in case["input"]["records"]:
                if original == record:
                    for error in case["expected"]["errors"]:
                        if error["input_id"] == record["input_id"]:
                            raise AgentError(error["code"], error["retryable"])
                    return deepcopy([task for task in case["expected"]["tasks"]
                                     if task["input_id"] == record["input_id"]])
        raise AgentError("agent_unavailable", retryable=False)
