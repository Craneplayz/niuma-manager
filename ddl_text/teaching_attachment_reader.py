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
from .extraction import _object

SKILL = Path(__file__).parent / 'skills/teaching-file-reading/SKILL.md'
MAX_TEXT = 50000
MAX_PDF_PAGES = 128
PDF_BATCH_PAGES = 6


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


def _checked_pages(raw, count):
    """Accept the whole ordered batch or none; never guess a missing page."""
    try:
        output = json.loads(raw, object_pairs_hook=_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(output, dict) or set(output) != {'pages'}:
            raise ValueError()
        batch = output['pages']
        if not isinstance(batch, list) or len(batch) != count:
            raise ValueError()
        checked = []
        for index, page in enumerate(batch, 1):
            if (not isinstance(page, dict) or set(page) - {'index', 'text', 'uncertain', 'note'}
                    or type(page.get('index')) is not int or page['index'] != index
                    or not isinstance(page.get('text'), str) or len(page['text']) > MAX_TEXT
                    or type(page.get('uncertain')) is not bool):
                raise ValueError()
            note = page.get('note', '')
            if not isinstance(note, str) or len(note) > 1000:
                raise ValueError()
            checked.append(dict(text=page['text'].strip() or '未见可读文字。', uncertain=page['uncertain'], note=note))
        return checked
    except (ValueError, KeyError, TypeError):
        raise AgentError('invalid_agent_output', True, 'attachment_schema') from None


def _transcribe_pages(client, parts, settings, expires, guard=None, diagnostic_event=None):
    """One format correction inside the same 60-second batch budget.

    Provider failures/timeouts propagate to the bounded batch-size recovery.
    A partial or ambiguous but well-shaped transcript is kept as partial; it
    must not be turned into confidence by repeated requests.
    """
    end = min(expires, time.monotonic() + min(60, settings.timeout_seconds))
    instructions = SKILL.read_text(encoding='utf-8')
    emit = diagnostic_event or (lambda *args, **kwargs: None)
    for attempt in range(2):
        if guard: guard()
        remaining = end - time.monotonic()
        if remaining <= 0: raise AgentError('agent_timeout', True)
        raw = transcribe(client, instructions, parts, replace(settings, timeout_seconds=remaining))
        try:
            return _checked_pages(raw, len(parts)//2)
        except AgentError:
            if attempt or time.monotonic() >= end: raise
            emit('attachment_format_retry', code='invalid_agent_output', diagnostic='attachment_schema')
            instructions += ('\n上次响应未通过格式校验。请重新读取本批图片，只返回完整的 pages JSON；'
                             '每张图片恰好一项，index 从 1 连续递增，text/note 为字符串，uncertain 为布尔值。'
                             '不要省略页面、编造文字或把读取缺口改成确定内容。')


def read_attachment(data, asset, client, settings, *, expires, checkpoint=None, save_checkpoint=None,
                    single_pdf_batch=False, pdf_batch_pages=PDF_BATCH_PAGES, guard=None, diagnostic_event=None):
    if guard: guard()
    if asset['mime_type'] == PDF_MIME:
        if type(pdf_batch_pages) is not int or not 1 <= pdf_batch_pages <= PDF_BATCH_PAGES:
            raise ValueError('invalid PDF batch size')
        return _read_pdf_batches(data, asset, client, settings, expires, checkpoint, save_checkpoint,
                                 single_pdf_batch, pdf_batch_pages, guard, diagnostic_event)
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
        if guard: guard()
        checked = _transcribe_pages(client, parts, settings, expires, guard, diagnostic_event)
        for i, page in enumerate(checked):
            text.append(f'【模型转录：{pictures[i][0]}】\n' + page['text'])
            if page['uncertain']:
                complete = False
                notes.append(f'{pictures[i][0]}：' + (page['note'].strip() or '图像文字不清晰'))
    if notes: text.append('【读取缺口】\n' + '\n'.join(notes))
    joined = '\n\n'.join(text).strip()
    if len(joined) > MAX_TEXT:
        joined = joined[:MAX_TEXT - 40] + '\n【读取缺口】附件文本超过长度限制，后续内容未提供。'
        complete = False
    if not joined:
        joined = '【读取结果】文件未提取到可读内容，请查看原件。'
        complete = False
    return dict(read_state='complete' if complete else 'partial', text=joined)


def _read_pdf_batches(data, asset, client, settings, expires, checkpoint, save_checkpoint, single_batch, batch_pages, guard, diagnostic_event):
    """Cache only validated page transcriptions; never cache a timeout as completion.

    Checkpoints are loaded by the server under account, file hash and reader/model
    version. Keep rendered images only for the current bounded batch. In step
    mode, return a checkpoint (not a partial/complete result) when more remains;
    the caller schedules the next step with its own call budget.
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
    emit = diagnostic_event or (lambda *args, **kwargs: None)
    while (total is None or len(pages) < total) and len(pages) < MAX_PDF_PAGES:
        if guard: guard()
        batch_size = min(batch_pages, MAX_PDF_PAGES-len(pages))
        emit('pdf_render',page_start=len(pages)+1,batch_pages=batch_size,timeout_seconds=remaining(15))
        try:
            rendered = read_pdf(data, asset, PdfLimits(start_page=len(pages), max_pages=batch_size,
                                render_seconds=remaining(15), native_text=True))
        except AgentError as error:
            if not pages or error.retryable or error.code not in ('file_read_failed','limit_exceeded','reader_unsupported'):
                raise
            notes.append(unread(error.code))
            break
        total = rendered['total_pages']
        progress['total_pages'] = total
        pictures = rendered['pages']
        native = rendered.get('native_texts', [None] * len(pictures))
        # A full pixel/byte budget is a continuation, not an unreadable page.
        failed_page = len(pictures) < min(batch_size, total-len(pages)) and not rendered.get('batch_full')
        parts = []
        visual_indices = [i for i, text in enumerate(native) if text is None]
        emit('pdf_batch',page_start=len(pages)+1,page_count=len(pictures),total_pages=total,
                          completed_pages=len(pages),native_pages=len(native)-len(visual_indices),visual_pages=len(visual_indices))
        for local, i in enumerate(visual_indices, 1):
            parts.extend([dict(type='text', text=f'第 {len(pages)+i+1} 页（本批第 {local} 页）'),
                dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(pictures[i]).decode()))])
        if guard: guard()
        started = time.monotonic()
        timeout = remaining(60)
        emit('pdf_transcription',timeout_seconds=timeout,visual_pages=len(visual_indices))
        try:
            checked = _transcribe_pages(client, parts, settings, expires, guard, diagnostic_event) if parts else []
        except AgentError as error:
            emit('pdf_transcription_failed',code=error.code,diagnostic=error.diagnostic,seconds=time.monotonic()-started)
            raise
        emit('pdf_transcription_done',seconds=time.monotonic()-started)
        visual = iter(checked)
        pages.extend(next(visual) if text is None else dict(text=text,uncertain=False,note='',method='native') for text in native)
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
        if single_batch and len(pages) < min(total, MAX_PDF_PAGES):
            return progress
    if total is not None and len(pages) == MAX_PDF_PAGES and len(pages) < total:
        notes.append(unread('页数限制'))
    notes.extend(f"第 {i} 页：{p['note'] or '图像文字不清晰'}" for i, p in enumerate(pages, 1) if p['uncertain'])
    text = '\n\n'.join(f"【{'原件文字' if p.get('method')=='native' else '模型转录'}：第 {i} 页】\n{p['text']}" for i, p in enumerate(pages, 1))
    suffix = '\n【读取缺口】\n' + '\n'.join(notes) if notes else ''
    if len(text) + len(suffix) > MAX_TEXT:
        # Leave room for both the truncation warning and unread-page locations.
        suffix = '\n【读取缺口】\n转录文字超过长度限制，后续内容未提供。\n' + '\n'.join(notes)
        suffix = suffix[:MAX_TEXT//2]
        text = text[:MAX_TEXT-len(suffix)]
    text += suffix
    return dict(read_state='partial' if suffix else 'complete', text=text)
