"""Read captured teaching files using the same bounded readers as WeChat.

No networking except the configured multimodal model; never fetch document links.
Transcripts are labelled model-reported evidence, not verified original text.
"""
import base64
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from .adapters import AgentError
from .image_reader import ImageLimits, read_image
from .pdf_reader import PDF_MIME, PdfLimits, read_pdf
from .docx_reader import DOCX_MIME, DocxLimits, read_docx

SKILL = Path(__file__).parent / 'skills/teaching-file-reading/SKILL.md'
MAX_TEXT = 50000


def cache_version(settings):
    files = [Path(__file__), SKILL] + [Path(__file__).parent / name for name in
        ('image_reader.py', 'pdf_reader.py', 'docx_reader.py', 'media_worker.py')]
    data = b''.join(p.read_bytes() for p in files)
    return hashlib.sha256(data + (settings.model + settings.endpoint).encode()).hexdigest()


def read_attachment(data, asset, client, settings, *, expires):
    def remaining(cap):
        value = min(cap, expires - time.monotonic())
        if value <= 0: raise AgentError('agent_timeout', True)
        return value
    mime = asset['mime_type']
    text, pictures, notes = [], [], []
    complete = True
    if mime == PDF_MIME:
        result = read_pdf(data, asset, PdfLimits(render_seconds=remaining(15)))
        pictures = [(f'第 {i} 页', png) for i, png in enumerate(result['pages'], 1)]
        complete = len(pictures) == result['total_pages'] and not result['error']
        if not complete: notes.append(f"PDF 共 {result['total_pages']} 页，已渲染 {len(pictures)} 页；其余内容未读取")
    elif mime == DOCX_MIME:
        result = read_docx(data, asset, DocxLimits(read_seconds=remaining(15)), ImageLimits())
        text = [f"【DOCX 段落 {block['paragraph']}：{block['location']}】\n{block['text']}" for block in result['blocks']]
        pictures = [(f"DOCX 段落 {item['paragraph']} 中的图片", item['png']) for item in result['images']]
        complete = result['coverage']['complete']
        notes.extend(result['coverage']['unread_locations'])
    else:
        pictures = [('图片', read_image(data, asset, ImageLimits(decode_seconds=remaining(10))))]
    encoded_size = sum(len(png) * 4 // 3 for _, png in pictures)
    if encoded_size > 12 * 1024 * 1024: raise AgentError('limit_exceeded', False)
    # Each image is labelled; a single bounded call reads all supplied pages.
    if pictures:
        parts = []
        for location, png in pictures:
            parts.extend([dict(type='text', text=location), dict(type='image_url',
                image_url=dict(url='data:image/png;base64,' + base64.b64encode(png).decode()))])
        raw = client.complete_json(SKILL.read_text(encoding='utf-8'), parts,
            replace(settings, timeout_seconds=remaining(60)))
        try:
            output = json.loads(raw)
            if not isinstance(output,dict) or not isinstance(output.get('pages'),list) or len(output['pages']) != len(pictures): raise ValueError()
            for i, page in enumerate(output['pages']):
                if not isinstance(page,dict) or type(page.get('index')) is not int or page['index'] != i + 1: raise ValueError()
                page.setdefault('note','')
                if not isinstance(page['text'], str) or len(page['text']) > MAX_TEXT or type(page['uncertain']) is not bool: raise ValueError()
                if not isinstance(page['note'], str) or len(page['note']) > 1000: raise ValueError()
                text.append(f'【模型转录：{pictures[i][0]}】\n' + (page['text'].strip() or '未见可读文字。'))
                if page['uncertain']:
                    complete = False
                    notes.append(f'{pictures[i][0]}：' + (page['note'].strip() or '图像文字不清晰'))
        except (ValueError, KeyError, TypeError):
            raise AgentError('invalid_agent_output', True) from None
    if notes: text.append('【读取缺口】\n' + '\n'.join(notes))
    joined = '\n\n'.join(text).strip()
    if len(joined) > MAX_TEXT:
        joined = joined[:MAX_TEXT - 40] + '\n【读取缺口】附件文本超过长度限制，后续内容未提供。'
        complete = False
    if not joined:
        joined = '【读取结果】文件未提取到可读内容，请查看原件。'
        complete = False
    return dict(read_state='complete' if complete else 'partial', text=joined)
