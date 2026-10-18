"""Render bounded, static PDF pages in an isolated process; never open links."""
import hashlib
import io
import math
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

    def __post_init__(self):
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
        pages, pixels, encoded = [], 0, 0
        end = min(total, limits.start_page + limits.max_pages)
        failure = 'limit_exceeded' if total > end else None
        for index in range(limits.start_page, end):
            try:
                width, height = document.get_page_size(index)
                if not all(math.isfinite(n) and n > 0 for n in (width, height)):
                    raise AgentError('file_read_failed', False)
                count = math.ceil(width * limits.scale) * math.ceil(height * limits.scale)
                if count > limits.max_page_pixels or pixels + count > limits.max_total_pixels:
                    raise AgentError('limit_exceeded', False)
                page = document[index]
                try:
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
                    raise AgentError('limit_exceeded', False)
                pages.append(png)
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
        return dict(pages=pages, total_pages=total, error=failure)


def read_pdf(data, asset, limits):
    if not isinstance(data, bytes):
        raise AgentError('file_read_failed', False)
    if len(data) > limits.max_bytes:
        raise AgentError('limit_exceeded', False)
    if (len(data) != asset['size_bytes'] or hashlib.sha256(data).hexdigest() != asset['sha256']
            or asset['mime_type'] != PDF_MIME or not data.startswith(b'%PDF-')):
        raise MetadataMismatch()
    return bounded(_render, (data, limits), limits.render_seconds, 'file_read_failed')
