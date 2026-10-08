"""Small synthetic OOXML packages for deterministic, network-free reader tests."""
import io
import zipfile
from xml.sax.saxutils import escape

from ddl_text.docx_reader import DOCX_MIME, MAIN_TYPE, W, R
from .media_cases import request_for


def paragraph(text):
    return '<w:p><w:r><w:t xml:space="preserve">'+escape(text)+'</w:t></w:r></w:p>'


def package(body, extra=None, relations='', content_types=''):
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="'+MAIN_TYPE+'"/>'+content_types+'</Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="main" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': '<w:document xmlns:w="'+W[1:-1]+'" xmlns:r="'+R[1:-1]+'" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>'+body+'</w:body></w:document>',
        'word/_rels/document.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+relations+'</Relationships>',
    }
    parts.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


def relationship(rid, kind, target, external=False):
    return f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{target}"'+(' TargetMode="External"' if external else '')+'/>'


def drawing(rid='image', crop=''):
    return '<w:p><w:r><w:drawing><a:blip r:embed="'+rid+'"/>'+crop+'</w:drawing></w:r></w:p>'


def docx_request(data):
    request, _ = request_for(data)
    record = request['records'][0]
    record['context'] = []
    record['attachments'][0].update(mime_type=DOCX_MIME, file_name='synthetic.docx', origin='original',
        original_published_at=dict(value='2026-10-01', precision='date', basis='source'))
    return request
