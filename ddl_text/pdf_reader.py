"""Render bounded, static PDF pages in an isolated process; never open links."""
import hashlib
import io
import math
from ctypes import byref, c_int, c_uint, c_ulong, create_string_buffer, cast, POINTER
from dataclasses import dataclass

from .adapters import AgentError
from .image_reader import MetadataMismatch
from .media_worker import bounded

PDF_MIME = 'application/pdf'
READER_VERSION = 'niuma-pdf-pages/1'


@dataclass(frozen=True)
class PdfLimits:
    max_bytes: int = 20 * 1024 * 1024
    max_pages: int = 6
    max_page_pixels: int = 16_000_000
    max_total_pixels: int = 32_000_000
    max_encoded_bytes: int = 8 * 1024 * 1024
    render_seconds: float = 15
    scale: float = 2  # 144 dpi, full page including visible annotations.
    start_page: int = 0  # Zero-based offset for bounded, resumable page batches.
    native_text: bool = False  # Teaching reader may reuse verified text-only pages.

    def __post_init__(self):
        if type(self.native_text) is not bool: raise ValueError('invalid_pdf_limit')
        if type(self.start_page) is not int or not 0 <= self.start_page < 10000:
            raise ValueError('invalid_pdf_limit')
        bounds = dict(max_bytes=20 * 1024 * 1024, max_pages=16,
                      max_page_pixels=20_000_000, max_total_pixels=64_000_000,
                      max_encoded_bytes=16 * 1024 * 1024)
        for key, maximum in bounds.items():
            if type(getattr(self, key)) is not int or not 0 < getattr(self, key) <= maximum:
                raise ValueError('invalid_pdf_limit')
        for key, maximum in [('render_seconds', 60), ('scale', 3)]:
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError('invalid_pdf_limit')


def _native_text(page, pdfium):
    """Reuse only visible, opaque text; the worker never saves its local edits."""
    if pdfium.raw.FPDFPage_GetAnnotCount(page) != 0:
        return None
    objects = list(page.get_objects(max_depth=1))
    if not objects or any(obj.type != pdfium.raw.FPDF_PAGEOBJ_TEXT for obj in objects):
        return None
    box = page.get_bbox()
    if not all(math.isfinite(value) for value in box):
        return None
    left, bottom, right, top = box
    if left >= right or bottom >= top:
        return None
    hidden = []
    for obj in objects:
        # IsActive is an editing flag, not OCG/OCMD visibility. Let PDFium's
        # renderer evaluate optional-content membership (including nested marks)
        # instead of promoting invisible layer text to original evidence.
        marks = pdfium.raw.FPDFPageObj_CountMarks(obj)
        if marks < 0:
            return None
        for index in range(marks):
            mark = pdfium.raw.FPDFPageObj_GetMark(obj, index)
            size = c_ulong()
            if (not mark or not pdfium.raw.FPDFPageObjMark_GetName(mark, None, 0, byref(size))
                    or not 0 < size.value <= 256):
                return None
            buffer = create_string_buffer(size.value)
            if not pdfium.raw.FPDFPageObjMark_GetName(mark,
                    cast(buffer, POINTER(pdfium.raw.FPDF_WCHAR)), len(buffer), byref(size)):
                return None
            if buffer.raw.decode('utf-16-le').rstrip('\0') == 'OC':
                return None
        bounds = obj.get_bounds()
        if not all(math.isfinite(value) for value in bounds):
            return None
        l, b, r, t = bounds
        mode = pdfium.raw.FPDFTextObj_GetTextRenderMode(obj)
        active = c_int()
        if not pdfium.raw.FPDFPageObj_GetIsActive(obj, byref(active)):
            return None
        if not active.value or mode == 3 or r <= left or l >= right or t <= bottom or b >= top:
            hidden.append(obj)
            continue
        if mode not in (0, 1, 2):
            return None  # Text clipping modes can affect later objects too.
        colors = []
        for get_color in ([pdfium.raw.FPDFPageObj_GetFillColor] if mode == 0 else
                          [pdfium.raw.FPDFPageObj_GetStrokeColor] if mode == 1 else
                          [pdfium.raw.FPDFPageObj_GetFillColor, pdfium.raw.FPDFPageObj_GetStrokeColor]):
            rgba = [c_uint() for _ in range(4)]
            if not get_color(obj, *(byref(value) for value in rgba)):
                return None
            colors.append(tuple(value.value for value in rgba))
        if all(color[3] == 0 for color in colors):
            hidden.append(obj)
            continue
        # Cropped glyphs, nontrivial clipping and compositing need the visible
        # page. Ordinary text and wholly hidden objects need no remote call.
        if (l < left or b < bottom or r > right or t > top
                or pdfium.raw.FPDFClipPath_CountPaths(pdfium.raw.FPDFPageObj_GetClipPath(obj)) >= 0
                or pdfium.raw.FPDFPageObj_HasTransparency(obj)
                or any(color[:3] == (255, 255, 255) for color in colors if color[3])):
            return None
    # GetTextRange includes off-page and transparent text. Remove only objects
    # proven invisible before opening the textpage; rendered fallback is unchanged.
    for obj in hidden:
        page.remove_obj(obj)
        obj.close()
    if len(hidden) == len(objects):
        return '未见可读文字。'
    textpage = page.get_textpage()
    try:
        if textpage.count_chars() > 50000:
            return None
        candidate = textpage.get_text_range()
    finally:
        textpage.close()
    if (candidate.strip() and len(candidate) <= 50000 and '\ufffd' not in candidate
            and all(c.isprintable() or c in '\n\r\t' for c in candidate)):
        return candidate.strip()
    return None


def _render(data, limits):
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except Exception:
        raise AgentError('file_read_failed', False) from None
    with document:
        # Static pages only. Do not claim to read forms, portfolios, scripts or
        # encrypted documents; a successful default-password open is not consent.
        if (document.get_formtype() != pdfium.raw.FORMTYPE_NONE
                or document.count_attachments()
                or pdfium.raw.FPDF_GetSecurityHandlerRevision(document) != -1
                or pdfium.raw.FPDFDoc_GetJavaScriptActionCount(document) > 0):
            raise AgentError('reader_unsupported', False)
        total = len(document)
        if total == 0 or limits.start_page >= total:
            raise AgentError('file_read_failed', False)
        pages, native_texts, pixels, encoded = [], [], 0, 0
        batch_full = False
        end = min(total, limits.start_page + limits.max_pages)
        failure = 'limit_exceeded' if total > end else None
        for index in range(limits.start_page, end):
            try:
                width, height = document.get_page_size(index)
                if not all(math.isfinite(n) and n > 0 for n in (width, height)):
                    raise AgentError('file_read_failed', False)
                page = document[index]
                try:
                    # Be conservative: graphics, annotations, forms and unknown
                    # object types still require the visible page. Plain text
                    # pages in a mixed PDF need no model transcription.
                    text = None
                    if limits.native_text:
                        try:
                            text = _native_text(page, pdfium)
                        except Exception:
                            # An optional text shortcut must not make an otherwise
                            # renderable page unreadable.
                            text = None
                    if text is not None:
                        pages.append(b'')
                        native_texts.append(text)
                        continue
                    # Pixel budgets bound bitmap allocation, not safe text extraction.
                    count = math.ceil(width * limits.scale) * math.ceil(height * limits.scale)
                    if count > limits.max_page_pixels:
                        raise AgentError('limit_exceeded', False)
                    if pixels + count > limits.max_total_pixels:
                        batch_full = bool(pages)
                        raise AgentError('limit_exceeded', False)
                    bitmap = page.render(scale=limits.scale, draw_annots=True, may_draw_forms=False)
                    try:
                        with bitmap.to_pil() as image:
                            output = io.BytesIO()
                            image.save(output, format='PNG')
                            png = output.getvalue()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                if encoded + len(png) > limits.max_encoded_bytes:
                    batch_full = bool(pages)
                    raise AgentError('limit_exceeded', False)
                pages.append(png)
                native_texts.append(None)
                pixels += count
                encoded += len(png)
            except AgentError as error:
                failure = error.code
                break
            except Exception:
                failure = 'file_read_failed'
                break
        if not pages:
            raise AgentError(failure or 'file_read_failed', False)
        result = dict(pages=pages, total_pages=total, error=failure, batch_full=batch_full)
        if limits.native_text: result['native_texts'] = native_texts
        return result


def read_pdf(data, asset, limits):
    if not isinstance(data, bytes):
        raise AgentError('file_read_failed', False)
    if len(data) > limits.max_bytes:
        raise AgentError('limit_exceeded', False)
    if (len(data) != asset['size_bytes'] or hashlib.sha256(data).hexdigest() != asset['sha256']
            or asset['mime_type'] != PDF_MIME or not data.startswith(b'%PDF-')):
        raise MetadataMismatch()
    return bounded(_render, (data, limits), limits.render_seconds, 'file_read_failed')
