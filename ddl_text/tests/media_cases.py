"""Synthetic shared-contract inputs and spawn-compatible model doubles."""
import copy
import hashlib
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
from ddl_text.adapters import AgentError
from ddl_text.media_schema import GENERATED

ROOT = Path(__file__).resolve().parents[2]


def image_bytes(format='PNG', size=(40, 20), text=None):
    image = Image.new('RGB', size, 'white')
    if text:
        ImageDraw.Draw(image).multiline_text((15, 15), text, fill='black', font_size=26, spacing=10)
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


def request_for(data=None):
    request = json.loads((ROOT / 'contracts/source-to-text/text-attachments-v031-request.json').read_text(encoding='utf-8'))
    data = image_bytes() if data is None else data
    asset = request['records'][0]['attachments'][0]
    asset.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    return request, data


def draft_task(task):
    draft = {key: copy.deepcopy(value) for key, value in task.items() if key not in GENERATED}
    draft['review_reasons'] = [dict(code='other', message=reason) for reason in draft['review_reasons']]
    return draft


def good_draft():
    result = json.loads((ROOT / 'contracts/text-to-app/text-attachments-v031-output.json').read_text(encoding='utf-8'))
    task = draft_task(result['tasks'][0])
    task['review_reasons'] = []
    return dict(tasks=[task], image_quality=[dict(asset_id='asset-001', quality='normal', quality_notes=[])])


@dataclass
class FakeMediaClient:
    draft: object = None
    behavior: str = ''

    def complete_json(self, instructions, parts, settings):
        if self.behavior == 'timeout':
            time.sleep(30)
        if self.behavior == 'failure':
            raise AgentError('agent_unavailable', True)
        if self.behavior == 'crash':
            print('PRIVATE-SENTINEL')
            raise RuntimeError('PRIVATE-SENTINEL')
        if isinstance(self.draft, str):
            return self.draft
        return json.dumps(copy.deepcopy(self.draft or good_draft()), ensure_ascii=False)


def memory_loader(data):
    def load(file_ref, *, max_bytes, timeout_seconds):
        if file_ref != 'asset:synthetic_asset_001_001':
            raise FileNotFoundError()
        assert max_bytes > 0 and timeout_seconds > 0
        return data
    return load
