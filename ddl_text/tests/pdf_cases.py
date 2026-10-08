"""Generated public test content; no stored documents or network fetches."""
import io

from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from .media_cases import request_for


def pdf_bytes(pages=('Synthetic homework: submit a report.',), size=(595, 842)):
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=size, invariant=1)
    for content in pages:
        if isinstance(content, bytes):
            document.drawImage(ImageReader(io.BytesIO(content)), 20, size[1] - 170,
                               width=size[0] - 40, height=140, preserveAspectRatio=True)
        else:
            for index, line in enumerate(content.splitlines()):
                document.drawString(30, size[1] - 50 - 24 * index, line)
        document.showPage()
    document.save()
    return output.getvalue()


def pdf_request(data):
    request, _ = request_for(data)
    request['records'][0]['attachments'][0].update(mime_type='application/pdf', file_name='synthetic.pdf')
    return request
