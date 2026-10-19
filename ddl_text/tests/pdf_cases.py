"""Generated public test content; no stored documents or network fetches."""
import io

from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from .media_cases import request_for


def pdf_bytes(pages=('Synthetic homework: submit a report.',), size=(595, 842), *, graphics=False):
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=size, invariant=1)
    for content in pages:
        if graphics: document.rect(10, 10, 20, 20)
        if isinstance(content, bytes):
            document.drawImage(ImageReader(io.BytesIO(content)), 20, size[1] - 170,
                               width=size[0] - 40, height=140, preserveAspectRatio=True)
        else:
            for index, line in enumerate(content.splitlines()):
                document.drawString(30, size[1] - 50 - 24 * index, line)
        document.showPage()
    document.save()
    return output.getvalue()


def visual_pdf_bytes(pages=('Synthetic homework: submit a report.',), size=(595, 842)):
    """Force visual-reader fixtures to contain graphics, not only a text layer."""
    return pdf_bytes(pages,size,graphics=True)


def visibility_pdf_bytes(kind, *, mixed=False, visible=True):
    """Text-only page with hidden text or an ambiguous visibility operation."""
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(300, 300), invariant=1)
    if visible:
        document.drawString(20, 250, 'Visible information')
    document.saveState()
    if kind == 'offpage':
        document.drawString(20, 500, 'Hidden assignment due Friday')
    elif kind == 'outside_crop':
        document.drawString(20, 150, 'Hidden assignment due Friday')
    elif kind == 'transparent':
        document.setFillAlpha(0)
        document.drawString(20, 220, 'Hidden assignment due Friday')
    elif kind in ('transparent_stroke', 'transparent_fill_stroke'):
        document.setFillAlpha(0)
        document.setStrokeAlpha(0)
        text = document.beginText(20, 220)
        text.setTextRenderMode(1 if kind == 'transparent_stroke' else 2)
        text.textLine('Hidden assignment due Friday')
        document.drawText(text)
    elif kind == 'invisible':
        text = document.beginText(20, 220)
        text.setTextRenderMode(3)
        text.textLine('Hidden assignment due Friday')
        document.drawText(text)
    elif kind == 'translucent':
        document.setFillAlpha(.25)
        document.drawString(20, 220, 'Translucent text')
    elif kind == 'white':
        document.setFillColorRGB(1, 1, 1)
        document.drawString(20, 220, 'White text')
    elif kind == 'clip':
        clipping = document.beginPath()
        clipping.rect(20, 200, 40, 30)
        document.clipPath(clipping, stroke=0, fill=0)
        document.drawString(20, 220, 'Clipped text')
    elif kind == 'partial':
        document.drawString(280, 220, 'Partially cropped text')
    else:
        raise ValueError('unknown visibility case')
    document.restoreState()
    document.showPage()
    if mixed:
        document.rect(20, 20, 20, 20)
        document.drawString(20, 250, 'Graphical second page')
        document.showPage()
        document.drawString(20, 250, 'Native third page')
        document.showPage()
    document.save()
    return output.getvalue()


def pdf_request(data):
    request, _ = request_for(data)
    request['records'][0]['attachments'][0].update(mime_type='application/pdf', file_name='synthetic.pdf')
    return request
