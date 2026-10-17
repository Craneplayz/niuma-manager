"""Read captured teaching files using the same bounded readers as WeChat.

No networking except the configured multimodal model; never fetch document links.
Transcripts are labelled model-reported evidence, not verified original text.
"""
import base64
import hashlib
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .adapters import AgentError
from .image_reader import ImageLimits, read_image
from .pdf_reader import PDF_MIME, PdfLimits, read_pdf
from .docx_reader import DOCX_MIME, DocxLimits, read_docx
from .runner import run_attempt

SKILL = Path(__file__).parent / 'skills/teaching-file-reading/SKILL.md'
MAX_TEXT = 50000
MAX_PDF_PAGES = 128


@dataclass
class TranscriptionAdapter:
    client: object

    def extract(self, record, settings, attempt):
        return [self.client.complete_json(record['instructions'],record['parts'],settings)]


def transcribe(client, instructions, parts, settings):
    """Reap even a slow streaming HTTP response at the wall-clock deadline."""
    result = run_attempt(TranscriptionAdapter(client),dict(instructions=instructions,parts=parts),settings,0)
    if len(result)!=1 or not isinstance(result[0],str):
        raise AgentError('invalid_agent_output',True)
    return result[0]


def cache_version(settings):
    files = [Path(__file__), SKILL] + [Path(__file__).parent / name for name in
        ('image_reader.py', 'pdf_reader.py', 'docx_reader.py', 'media_worker.py')]
    data = b''.join(p.read_bytes() for p in files)
    return hashlib.sha256(data + (settings.model + settings.endpoint).encode()).hexdigest()


def read_attachment(data, asset, client, settings, *, expires, checkpoint=None, save_checkpoint=None):
    if asset['mime_type'] == PDF_MIME:
        return _read_pdf_batches(data, asset, client, settings, expires, checkpoint, save_checkpoint)
    def remaining(cap):
        value = min(cap, expires - time.monotonic())
        if value <= 0: raise AgentError('agent_timeout', True)
        return value
    mime = asset['mime_type']
    text, pictures, notes = [], [], []
    complete = True
    if mime == DOCX_MIME:
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
        raw = transcribe(client, SKILL.read_text(encoding='utf-8'), parts,
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


def _read_pdf_batches(data, asset, client, settings, expires, checkpoint, save_checkpoint):
    """Cache only validated page transcriptions; never cache a timeout as completion.

    Checkpoints are loaded by the server under account, file hash and reader/model
    version. Keep rendered images only for the current two-page batch.
    """
    from copy import deepcopy
    progress = deepcopy(checkpoint) if checkpoint else dict(pdf_checkpoint=1, total_pages=None, pages=[])
    if (not isinstance(progress,dict) or progress.get('pdf_checkpoint') != 1 or not isinstance(progress.get('pages'),list)
            or len(progress['pages'])>MAX_PDF_PAGES or progress.get('total_pages') is not None and
            (type(progress['total_pages']) is not int or progress['total_pages']<max(1,len(progress['pages'])))):
        raise AgentError('file_read_failed', False)
    pages = progress['pages']
    if any(not isinstance(p,dict) or not isinstance(p.get('text'),str) or type(p.get('uncertain')) is not bool
           or not isinstance(p.get('note'),str) or len(p['note'])>1000 for p in pages):
        raise AgentError('file_read_failed', False)
    if sum(len(p['text'])+len(p['note']) for p in pages)>MAX_TEXT: raise AgentError('limit_exceeded',False)
    total = progress['total_pages']
    def remaining(cap):
        seconds = min(cap, expires - time.monotonic())
        if seconds <= 0: raise AgentError('agent_timeout', True)
        return seconds
    notes = []
    def unread(reason):
        return f'第 {len(pages)+1} 页至第 {total} 页未读取：{reason}'
    while (total is None or len(pages) < total) and len(pages) < MAX_PDF_PAGES:
        batch_size = min(2, MAX_PDF_PAGES-len(pages))
        try:
            rendered = read_pdf(data, asset, PdfLimits(start_page=len(pages), max_pages=batch_size,
                                render_seconds=remaining(15)))
        except AgentError as error:
            if not pages or error.retryable or error.code not in ('file_read_failed','limit_exceeded','reader_unsupported'):
                raise
            notes.append(unread(error.code))
            break
        total = rendered['total_pages']
        progress['total_pages'] = total
        pictures = rendered['pages']
        # A short batch indicates a page failure, unlike the normal two-page cap.
        failed_page = len(pictures) < min(batch_size, total-len(pages))
        parts = []
        for index, png in enumerate(pictures, len(pages) + 1):
            parts.extend([dict(type='text', text=f'第 {index} 页（本批第 {(index-len(pages))} 页）'),
                dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(png).decode()))])
        raw = transcribe(client, SKILL.read_text(encoding='utf-8'), parts,
                         replace(settings, timeout_seconds=remaining(30)))
        try:
            output = json.loads(raw)
            batch = output['pages']
            if not isinstance(batch, list) or len(batch) != len(pictures): raise ValueError()
            checked = []
            for index, page in enumerate(batch, 1):
                if not isinstance(page, dict) or type(page.get('index')) is not int or page['index'] != index: raise ValueError()
                if not isinstance(page.get('text'), str) or len(page['text']) > MAX_TEXT or type(page.get('uncertain')) is not bool: raise ValueError()
                note = page.get('note', '')
                if not isinstance(note, str) or len(note) > 1000: raise ValueError()
                checked.append(dict(text=page['text'].strip() or '未见可读文字。', uncertain=page['uncertain'], note=note))
        except (ValueError, KeyError, TypeError):
            raise AgentError('invalid_agent_output', True) from None
        pages.extend(checked)
        if sum(len(p['text']) + len(p['note']) for p in pages) > MAX_TEXT:
            notes.append('转录文字超过长度限制，后续内容未提供。')
            if len(pages) < total: notes.append(unread('文字长度限制'))
            break
        if save_checkpoint: save_checkpoint(deepcopy(progress))
        if failed_page:
            notes.append(unread(rendered.get('error') or 'file_read_failed'))
            break
        # Release page images before rendering the next batch.
        del pictures, parts, rendered
    if total is not None and len(pages) == MAX_PDF_PAGES and len(pages) < total:
        notes.append(unread('页数限制'))
    notes.extend(f"第 {i} 页：{p['note'] or '图像文字不清晰'}" for i, p in enumerate(pages, 1) if p['uncertain'])
    text = '\n\n'.join(f"【模型转录：第 {i} 页】\n{p['text']}" for i, p in enumerate(pages, 1))
    suffix = '\n【读取缺口】\n' + '\n'.join(notes) if notes else ''
    if len(text) + len(suffix) > MAX_TEXT:
        # Leave room for both the truncation warning and unread-page locations.
        suffix = '\n【读取缺口】\n转录文字超过长度限制，后续内容未提供。\n' + '\n'.join(notes)
        suffix = suffix[:MAX_TEXT//2]
        text = text[:MAX_TEXT-len(suffix)]
    text += suffix
    return dict(read_state='partial' if suffix else 'complete', text=text)
