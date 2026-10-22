"""Trusted, spawn-compatible adapter objects implementing one small protocol."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from .config import Settings


# Only fixed labels may cross the worker boundary, never provider/validator text.
OUTPUT_DIAGNOSTICS = {
    'attachment_schema': '附件转录的页数、页码或字段格式不符合约定，结果未采纳，请重试。',
    'output_truncated': '模型输出达到长度上限，尚未生成完整结果，请重试。',
    'provider_response': '模型服务返回的响应不完整或格式异常，请重试。',
    'draft_json': '模型没有返回有效的任务 JSON，请重试。',
    'draft_schema': '模型返回的任务字段或日期格式不符合约定，请重试。',
    'draft_evidence': '模型引用未能在已读取原文中找到，结果未采纳，请重试。',
    'draft_semantics': '模型返回的日期、引用关系或字段相互矛盾，请重试。',
}
# Share public classification labels without permitting arbitrary provider text.
from contracts.recognition_errors import CATALOG
OUTPUT_DIAGNOSTICS.update({key: value['message'] + value['action'] for key, value in CATALOG.items()})


from contracts.recognition_errors import safe_schema_detail


class AgentError(Exception):
    def __init__(self, code="agent_unavailable", retryable=False, diagnostic=None):
        self.code = code
        self.retryable = retryable
        self.diagnostic = diagnostic if isinstance(diagnostic, str) and diagnostic in OUTPUT_DIAGNOSTICS else None
        self.detail = None
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
