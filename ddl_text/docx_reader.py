"""Bounded OOXML reading without Office, archive extraction, links or macros."""
import hashlib
import io
import math
import posixpath
import zipfile
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .adapters import AgentError
from .image_reader import MetadataMismatch, _decode
from .media_worker import bounded

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MAIN_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
READER_VERSION = 'niuma-docx-structure/1'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/package/2006/relationships}'
C = '{http://schemas.openxmlformats.org/package/2006/content-types}'


@dataclass(frozen=True)
class DocxLimits:
    max_bytes: int = 20 * 1024 * 1024
    max_entries: int = 512
    max_expanded_bytes: int = 64 * 1024 * 1024
    max_xml_bytes: int = 8 * 1024 * 1024
    max_paragraphs: int = 500
    max_text_chars: int = 50000
    max_images: int = 8
    max_image_pixels: int = 32_000_000
    max_encoded_bytes: int = 8 * 1024 * 1024
    read_seconds: float = 15

    def __post_init__(self):
        maxima = dict(max_bytes=20*1024*1024, max_entries=2048, max_expanded_bytes=128*1024*1024,
            max_xml_bytes=16*1024*1024, max_paragraphs=2000, max_text_chars=100000,
            max_images=16, max_image_pixels=64_000_000, max_encoded_bytes=16*1024*1024)
        for name, maximum in maxima.items():
            if type(getattr(self, name)) is not int or not 0 < getattr(self, name) <= maximum:
                raise ValueError('invalid_docx_limit')
        if isinstance(self.read_seconds, bool) or not isinstance(self.read_seconds, (int, float)) or not math.isfinite(self.read_seconds) or not 0 < self.read_seconds <= 60:
            raise ValueError('invalid_docx_limit')


class _StopReading(Exception):
    pass


def _member_name(name):
    return (bool(name) and not name.startswith('/') and '\\' not in name and ':' not in name
            and not any(ord(c) < 32 or ord(c) == 127 for c in name)
            and all(p not in ('', '.', '..') for p in name.rstrip('/').split('/')))


def _target(part, target):
    value = unquote(target)
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '\\' in value:
        raise AgentError('file_read_failed', False)
    name = posixpath.normpath(posixpath.join(posixpath.dirname(part), value.lstrip('/'))) if not value.startswith('/') else value[1:]
    if not _member_name(name):
        raise AgentError('file_read_failed', False)
    return name


class _Reader:
    def __init__(self, archive, limits, image_limits):
        self.archive, self.limits, self.image_limits = archive, limits, image_limits
        self.blocks, self.images, self.gaps = [], [], []
        self.number = self.chars = self.pixels = self.encoded = self.expanded = 0
        self.tables = 0
        self.error = None
        self.truncated = False
        self.parsed = {}

    def gap(self, message, code='reader_unsupported'):
        if message not in self.gaps and len(self.gaps) < 30:
            self.gaps.append(message)
        self.error = self.error or code
        if code == 'limit_exceeded':
            self.error = code
            self.truncated = True

    def read(self, name, maximum):
        try:
            info = self.archive.getinfo(name)
            if info.file_size > maximum or self.expanded + info.file_size > self.limits.max_expanded_bytes:
                raise AgentError('limit_exceeded', False)
            with self.archive.open(info) as stream:
                data = stream.read(maximum + 1)
            self.expanded += len(data)
            if len(data) > maximum or self.expanded > self.limits.max_expanded_bytes:
                raise AgentError('limit_exceeded', False)
            return data
        except (KeyError, zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError):
            raise AgentError('file_read_failed', False) from None

    def xml(self, name):
        from defusedxml.ElementTree import fromstring
        if name not in self.parsed:
            try:
                root = fromstring(self.read(name, self.limits.max_xml_bytes), forbid_dtd=True)
                stack, count = [(root, 0)], 0
                while stack:
                    node, depth = stack.pop(); count += 1
                    if depth > 100 or count > 100000:
                        raise AgentError('limit_exceeded', False)
                    stack.extend((child, depth+1) for child in node)
                self.parsed[name] = root
            except AgentError:
                raise
            except Exception:
                raise AgentError('file_read_failed', False) from None
        return self.parsed[name]

    def relations(self, part):
        name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part)+'.rels') if part else '_rels/.rels'
        if name not in self.archive.namelist():
            return {}
        root = self.xml(name)
        if root.tag != P+'Relationships':
            raise AgentError('file_read_failed', False)
        result = {}
        for node in root:
            rid = node.get('Id')
            if not rid or rid in result or node.tag != P+'Relationship':
                raise AgentError('file_read_failed', False)
            result[rid] = dict(type=node.get('Type', '').rsplit('/', 1)[-1], target=node.get('Target', ''),
                               external=node.get('TargetMode', 'Internal') != 'Internal')
        return result

    def image(self, drawing, part, rels, paragraph):
        blips = list(drawing.iter(A+'blip'))
        # Preserve what is visibly included: do not reveal cropped-away content,
        # execute SVG or treat a chart's thumbnail as the whole chart.
        unsupported = any(n.tag.rsplit('}', 1)[-1] in ('chart', 'relIds', 'txbxContent', 'svgBlip') for n in drawing.iter())
        altered = any(n.tag == A+'srcRect' and any(v != '0' for v in n.attrib.values()) or
                      n.tag == A+'xfrm' and any(n.get(k, '0') not in ('0', 'false') for k in ('rot', 'flipH', 'flipV'))
                      for n in drawing.iter())
        if len(blips) != 1 or unsupported or altered or list(blips[0]):
            self.gap('绘图、裁剪/变换图片或其特殊效果未读取。'); return
        rel = rels.get(blips[0].get(R+'embed'))
        if not rel or rel['external'] or rel['type'] != 'image' or blips[0].get(R+'link'):
            self.gap('外部或缺失的图片未读取。'); return
        if len(self.images) >= self.limits.max_images:
            self.gap('超过图片数量限制，剩余图片未读取。', 'limit_exceeded'); return
        try:
            name = _target(part, rel['target'])
            data = self.read(name, self.image_limits.max_bytes)
            from PIL import Image
            with Image.open(io.BytesIO(data), formats=['PNG', 'JPEG']) as image:
                pixels = image.width * image.height
            if self.pixels + pixels > self.limits.max_image_pixels:
                raise AgentError('limit_exceeded', False)
            png = _decode(data, self.image_limits)  # Already inside the bounded DOCX worker.
            if self.encoded + len(png) > self.limits.max_encoded_bytes:
                raise AgentError('limit_exceeded', False)
            self.images.append(dict(paragraph=paragraph, png=png))
            self.pixels += pixels; self.encoded += len(png)
        except AgentError as error:
            self.gap('部分内嵌图片未读取。', error.code)
        except Exception:
            self.gap('图片格式不支持或图片损坏。', 'file_read_failed')

    def paragraph(self, node, part, rels, location):
        self.number += 1
        if self.number > self.limits.max_paragraphs:
            self.gap('超过段落数量限制，后续内容未读取。', 'limit_exceeded'); raise _StopReading()
        number = self.number
        special = {'fldChar', 'instrText', 'fldSimple', 'ins', 'del', 'moveFrom', 'moveTo', 'vanish', 'webHidden', 'strike', 'dstrike'}
        if any(n.tag.startswith(W) and n.tag[len(W):] in special and
               (n.tag not in (W+'vanish', W+'webHidden', W+'strike', W+'dstrike') or n.get(W+'val', '1') not in ('0', 'false', 'off')) for n in node.iter()):
            self.gap(f'第 {number} 段含域、修订、删除线或隐藏内容，未据缓存内容判断。'); return

        def text(element):
            if element.tag == W+'t': return element.text or ''
            if element.tag in (W+'tab',): return '\t'
            if element.tag in (W+'br', W+'cr'): return '\n'
            if element.tag == W+'noBreakHyphen': return '\u2011'
            if element.tag == W+'softHyphen': return '\u00ad'
            if element.tag == W+'drawing':
                self.image(element, part, rels, number); return ''
            if element.tag in (W+'pPr', W+'rPr'): return ''
            if element.tag in (W+'r', W+'p', W+'hyperlink'):
                return ''.join(text(child) for child in element)
            if element.tag in {W+n for n in ('bookmarkStart', 'bookmarkEnd', 'proofErr', 'footnoteReference',
                                             'endnoteReference', 'commentReference', 'commentRangeStart', 'commentRangeEnd', 'lastRenderedPageBreak')}:
                return ''
            self.gap(f'第 {number} 段存在尚未支持的内容。')
            return ''
        content = text(node)
        if self.chars + len(content) > self.limits.max_text_chars:
            # Never cut a sentence/number into a misleading complete paragraph.
            self.gap('超过文字长度限制，后续段落未读取。', 'limit_exceeded'); raise _StopReading()
        self.blocks.append(dict(paragraph=number, location=location, text=content))
        self.chars += len(content)

    def walk(self, root, part, rels, location):
        for child in root:
            if child.tag == W+'p':
                self.paragraph(child, part, rels, location)
            elif child.tag == W+'tbl':
                self.tables += 1
                table = self.tables
                for row, tr in enumerate(child.findall(W+'tr'), 1):
                    if any(n.tag not in (W+'tc', W+'trPr') for n in tr):
                        self.gap('表格行中存在未支持的内容。')
                    for column, cell in enumerate(tr.findall(W+'tc'), 1):
                        self.walk(cell, part, rels, f'{location}/表格{table}行{row}列{column}')
                if any(n.tag not in (W+'tr', W+'tblPr', W+'tblGrid') for n in child):
                    self.gap('表格中存在未支持的内容。')
            elif child.tag not in (W+'sectPr', W+'tcPr'):
                self.gap('正文或表格含文本框、嵌入对象或其他未支持结构。')

    def document(self):
        roots = [r for r in self.relations('').values() if r['type'] == 'officeDocument']
        if len(roots) != 1 or roots[0]['external']:
            raise AgentError('reader_unsupported', False)
        part = _target('', roots[0]['target'])
        types = self.xml('[Content_Types].xml')
        if types.tag != C+'Types':
            raise AgentError('file_read_failed', False)
        if not any(n.tag == C+'Override' and n.get('PartName') == '/'+part and n.get('ContentType') == MAIN_TYPE for n in types):
            raise AgentError('asset_metadata_mismatch', False)
        if any('macro' in n.get('ContentType', '').lower() or 'vba' in n.get('ContentType', '').lower() for n in types):
            raise AgentError('reader_unsupported', False)
        root, rels = self.xml(part), self.relations(part)
        if root.tag != W+'document' or root.find(W+'body') is None:
            raise AgentError('reader_unsupported', False)
        for rel in rels.values():
            if rel['type'] == 'styles' and not rel['external']:
                styles = self.xml(_target(part, rel['target']))
                if any(n.tag in (W+'vanish', W+'webHidden', W+'strike', W+'dstrike') and n.get(W+'val', '1') not in ('0', 'false', 'off') for n in styles.iter()):
                    raise AgentError('reader_unsupported', False)
        try:
            self.walk(root.find(W+'body'), part, rels, '正文')
            visited = set()
            for kind, ref_tag in [('header', 'headerReference'), ('footer', 'footerReference'),
                                  ('footnotes', 'footnoteReference'), ('endnotes', 'endnoteReference'), ('comments', 'commentReference')]:
                references = list(root.iter(W+ref_tag))
                if not references: continue
                linked = ([rels.get(ref.get(R+'id')) for ref in references] if kind in ('header', 'footer') else
                          [r for r in rels.values() if r['type'] == kind])
                if not linked: self.gap('引用的附属文字缺失。', 'file_read_failed')
                for rel in linked:
                    if not rel or rel['external'] or rel['type'] != kind:
                        self.gap('引用的附属文字不可读取。', 'file_read_failed'); continue
                    try:
                        target = _target(part, rel['target'])
                        if target in visited: continue
                        visited.add(target)
                        extra, extra_rels = self.xml(target), self.relations(target)
                        expected = {'header':'hdr', 'footer':'ftr'}.get(kind, kind)
                        if extra.tag != W+expected:
                            raise AgentError('file_read_failed', False)
                        if kind in ('header', 'footer'):
                            self.walk(extra, target, extra_rels, '页眉' if kind == 'header' else '页脚')
                        else:
                            ids = {ref.get(W+'id') for ref in references}
                            found = set()
                            for item in extra:
                                if item.get(W+'id') in ids:
                                    found.add(item.get(W+'id'))
                                    self.walk(item, target, extra_rels, {'footnotes':'脚注','endnotes':'尾注','comments':'批注'}[kind])
                            if found != ids: self.gap('部分脚注、尾注或批注缺失。', 'file_read_failed')
                    except AgentError as error:
                        self.gap('附属文字读取未完成。', error.code)
        except _StopReading:
            pass
        return dict(blocks=self.blocks, images=self.images, error=self.error,
                    coverage=dict(complete=not self.gaps, unread_locations=self.gaps, truncated=self.truncated))


def _read(data, limits, image_limits):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_entries or sum(e.file_size for e in entries) > limits.max_expanded_bytes:
                raise AgentError('limit_exceeded', False)
            names = [e.filename for e in entries]
            if len(names) != len(set(names)) or any(not _member_name(n) for n in names):
                raise AgentError('file_read_failed', False)
            if any(e.flag_bits & 1 or e.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for e in entries):
                raise AgentError('reader_unsupported', False)
            if any(e.file_size > max(1024*1024, e.compress_size*1000) for e in entries):
                raise AgentError('limit_exceeded', False)
            return _Reader(archive, limits, image_limits).document()
    except AgentError:
        raise
    except Exception:
        raise AgentError('file_read_failed', False) from None


def read_docx(data, asset, limits, image_limits):
    if not isinstance(data, bytes): raise AgentError('file_read_failed', False)
    if len(data) > limits.max_bytes: raise AgentError('limit_exceeded', False)
    if len(data) != asset['size_bytes'] or hashlib.sha256(data).hexdigest() != asset['sha256'] or asset['mime_type'] != DOCX_MIME:
        raise MetadataMismatch()
    if data.startswith(b'\xd0\xcf\x11\xe0'): raise AgentError('reader_unsupported', False)
    if not data.startswith(b'PK\x03\x04'): raise MetadataMismatch()
    try:
        return bounded(_read, (data, limits, image_limits), limits.read_seconds, 'file_read_failed')
    except AgentError as error:
        if error.code == 'asset_metadata_mismatch':
            raise MetadataMismatch() from None
        raise
