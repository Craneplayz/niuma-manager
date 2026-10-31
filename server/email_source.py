"""Read selected messages from supported PKU mailboxes without changing them."""
import email
import email.utils
import imaplib
import base64
import quopri
import re
import socket
import ssl
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, date
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.policy import default
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

try:
    from . import email_mailbox_pool
except ImportError:
    import email_mailbox_pool


MAILBOXES = {
    'stu.pku.edu.cn': 'imaphz.qiye.163.com',
    'pku.edu.cn': 'imap.pku.edu.cn',
}
MAX_PREVIEW = 50
MAX_SELECTED = 20
MAX_BODY_BYTES = 131072
MAX_BODY_CHARS = 30000
MAX_IMAP_METADATA_BYTES = 200000
MAX_HEADER_BYTES = 262144
TZ = ZoneInfo('Asia/Shanghai')
_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+$")


class EmailSourceError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class SkipEmail(Exception):
    """Skip one message whose per-item policy changed during a batch fetch."""


def _set_command_timeout(client):
    deadline = getattr(client, '_niuma_deadline', None)
    if deadline is None:
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EmailSourceError(503, '邮箱读取超时，请减少邮件数量后重试')
    if client.sock:
        client.sock.settimeout(min(15, remaining))


def normalize_address(address):
    if not isinstance(address, str) or len(address) > 254:
        raise EmailSourceError(400, '请填写有效的北大邮箱地址')
    value = address.strip().lower()
    if not _ADDRESS.fullmatch(value) or value.count('@') != 1:
        raise EmailSourceError(400, '请填写有效的北大邮箱地址')
    local = value.split('@', 1)[0]
    if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
        raise EmailSourceError(400, '请填写有效的北大邮箱地址')
    domain = value.rsplit('@', 1)[1]
    if domain not in MAILBOXES:
        raise EmailSourceError(400, '首发仅支持 @stu.pku.edu.cn 和 @pku.edu.cn 邮箱')
    return value


@contextmanager
def _mailbox(address, username, password, cancel_check=None):
    host = MAILBOXES[address.rsplit('@', 1)[1]]
    client = None
    try:
        with email_mailbox_pool.mailbox(address, cancel_check):
            if cancel_check:
                cancel_check()
            connect_started = time.perf_counter()
            client = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=15)
            try:
                try:
                    result, _ = client.login(username, password)
                except imaplib.IMAP4.error:
                    raise EmailSourceError(401, '邮箱登录失败，请核对邮箱登录账号和客户端专用密码') from None
                if result != 'OK':
                    raise EmailSourceError(401, '邮箱登录失败，请核对邮箱登录账号和客户端专用密码')
                client._niuma_connect_ms = (time.perf_counter() - connect_started) * 1000
                yield client
            finally:
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    pass
    except EmailSourceError:
        raise
    except (socket.timeout, TimeoutError):
        raise EmailSourceError(503, '连接北大邮箱超时，请稍后重试') from None
    except (OSError, ssl.SSLError, imaplib.IMAP4.abort, imaplib.IMAP4.error):
        raise EmailSourceError(503, '暂时无法连接北大邮箱，请检查网络后重试') from None


def _uidvalidity(client):
    _, values = client.response('UIDVALIDITY')
    for value in values or []:
        match = re.search(rb'\d+', value if isinstance(value, bytes) else str(value).encode())
        if match:
            return match.group().decode('ascii')
    raise EmailSourceError(502, '邮箱未返回有效的邮件箱版本，请稍后重试')


def _select_inbox(client):
    _set_command_timeout(client)
    result, _ = client.select('INBOX', readonly=True)
    if result != 'OK':
        raise EmailSourceError(502, '无法只读打开邮箱收件箱')
    return _uidvalidity(client)


def _start_day(value):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise EmailSourceError(400, '开始日期格式应为 YYYY-MM-DD') from None


def _search_uids(client, start_date=None):
    """Use a broad IMAP date search, then enforce the Shanghai date boundary."""
    day = _start_day(start_date)
    _set_command_timeout(client)
    if day:
        # IMAP compares INTERNALDATE calendar dates without converting zones.
        # Include the preceding day, then filter metadata in Asia/Shanghai.
        broad_day = day - timedelta(days=1) if day > date.min else day
        result, data = client.uid('SEARCH', None, 'SINCE', broad_day.strftime('%d-%b-%Y'))
    else:
        result, data = client.uid('SEARCH', None, 'ALL')
    if result != 'OK':
        raise EmailSourceError(502, '无法读取收件箱邮件列表')
    raw = next((item for item in (data or []) if isinstance(item, bytes)), b'')
    try:
        uids = sorted({int(uid) for uid in raw.split()}, reverse=True)
    except ValueError:
        raise EmailSourceError(502, '邮箱返回的邮件编号无效') from None
    if not day:
        return uids
    eligible = set()
    for offset in range(0, len(uids), 100):
        _set_command_timeout(client)
        chunk = uids[offset:offset+100]
        result, metadata = client.uid('FETCH', ','.join(map(str, chunk)), '(UID INTERNALDATE)')
        if result != 'OK':
            raise EmailSourceError(502, '无法读取邮件日期')
        for item in metadata or []:
            value = item[0] if isinstance(item, tuple) else item
            if not isinstance(value, bytes):
                continue
            if len(value) > MAX_IMAP_METADATA_BYTES:
                raise EmailSourceError(422, '邮件元数据超过安全读取限制')
            match = re.search(rb'\bUID\s+(\d+)', value, re.I)
            received = _received_at(value)
            if match and received and received.date() >= day:
                eligible.add(int(match.group(1)))
    return [uid for uid in uids if uid in eligible]


def _fetch_tuple(client, uid, query, *, metadata_only=False):
    _set_command_timeout(client)
    result, data = client.uid('FETCH', str(uid), query)
    if result != 'OK':
        return None, None
    for item in data or []:
        # FETCH values without literals (such as BODYSTRUCTURE) arrive as bytes.
        # Only accept them for metadata requests; a header/body literal is still
        # required for content fetches, whose responses may contain byte trailers.
        if metadata_only and isinstance(item, bytes):
            if len(item) > MAX_IMAP_METADATA_BYTES:
                raise EmailSourceError(422, '邮件元数据超过安全读取限制')
            if re.search(rb'\bBODYSTRUCTURE\s+', item, re.I):
                return item, None
        if isinstance(item, tuple) and len(item) >= 2:
            if isinstance(item[0], bytes) and len(item[0]) > MAX_IMAP_METADATA_BYTES:
                raise EmailSourceError(422, '邮件元数据超过安全读取限制')
            if isinstance(item[1], bytes) and len(item[1]) > MAX_HEADER_BYTES:
                raise EmailSourceError(422, '邮件标题超过安全读取限制')
            return item[0], item[1]
    return None, None


def _fetch_headers(client, uids):
    """Fetch only requested header literals, in bounded UID batches."""
    output={}
    for offset in range(0,len(uids),50):
        batch=uids[offset:offset+50]; allowed=set(batch)
        _set_command_timeout(client)
        status,data=client.uid('FETCH',','.join(map(str,batch)),
            '(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])')
        if status!='OK': raise EmailSourceError(502,'无法获取邮件标题，请稍后重试')
        for item in data or []:
            if not isinstance(item,tuple) or len(item)<2: continue
            meta,headers=item[:2]
            if not isinstance(meta,bytes) or not isinstance(headers,bytes): continue
            if len(meta)>MAX_IMAP_METADATA_BYTES or len(headers)>MAX_HEADER_BYTES:
                raise EmailSourceError(422,'邮件标题超过安全读取限制')
            match=re.search(rb'\bUID\s+(\d+)\b',meta,re.I)
            if not match: raise EmailSourceError(502,'邮件标题缺少有效标识')
            uid=int(match.group(1))
            if uid not in allowed or uid in output: raise EmailSourceError(502,'邮件标题标识不匹配')
            output[uid]=(meta,headers)
    return output


def _decode_header(value):
    if not value:
        return ''
    try:
        return str(make_header(decode_header(str(value)))).replace('\r', ' ').replace('\n', ' ').strip()
    except (LookupError, UnicodeError, ValueError):
        return str(value).replace('\r', ' ').replace('\n', ' ').strip()


def _received_at(metadata):
    match = re.search(rb'INTERNALDATE\s+"([^"]+)"', metadata or b'', re.I)
    if match:
        try:
            instant = datetime.strptime(match.group(1).decode('ascii'), '%d-%b-%Y %H:%M:%S %z')
            return instant.astimezone(TZ)
        except (UnicodeDecodeError, ValueError):
            pass
    return None


def _identity(address, uidvalidity, uid, namespace=None):
    import hashlib
    identity = address if namespace is None else address + '\0' + namespace
    mailbox_hash = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]
    return f'email:{mailbox_hash}:{uidvalidity}:{uid}'


def preview_cached(cache_path, user_id, address, limit=50, start_date=None, offset=0, page_size=50, **options):
    """Read cached headers or lazily synchronize the requested range."""
    try:
        from . import email_header_cache
    except ImportError:
        import email_header_cache
    return email_header_cache.preview(cache_path, user_id, address, limit, start_date, offset,
                                      page_size, **options)


def preview(address, username, password, *, identity_namespace=None):
    return preview_range(address, username, password, limit=MAX_PREVIEW, offset=0, page_size=MAX_PREVIEW, identity_namespace=identity_namespace)


def list_input_ids(address, username, password, *, limit=50, start_date=None, identity_namespace=None):
    address=normalize_address(address)
    if start_date is None and (type(limit) is not int or limit < 1):
        raise EmailSourceError(400,'邮件数量范围无效')
    with _mailbox(address,username,password) as client:
        client._niuma_deadline=time.monotonic()+80
        uidvalidity=_select_inbox(client)
        uids=_search_uids(client,start_date)
        if start_date is None: uids=uids[:limit]
        return [_identity(address,uidvalidity,uid,identity_namespace) for uid in uids]


def preview_range(address, username, password, *, limit=50, start_date=None, offset=0, page_size=50,
                  select_uids=None, identity_namespace=None):
    address = normalize_address(address)
    _start_day(start_date)
    if start_date is None and (type(limit) is not int or limit < 1):
        raise EmailSourceError(400, '邮件数量范围无效')
    if type(offset) is not int or offset < 0 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise EmailSourceError(400, '邮件分页条件无效')
    messages = []
    with _mailbox(address, username, password) as client:
        client._niuma_deadline = time.monotonic() + 80
        uidvalidity = _select_inbox(client)
        uids = _search_uids(client, start_date)
        if start_date is None:
            uids = uids[:limit]
        total = len(uids)
        if select_uids is not None:
            selected = select_uids(uidvalidity, tuple(uids))
            if not isinstance(selected, (list, tuple)) or any(type(uid) is not int for uid in selected):
                raise EmailSourceError(400, '邮件筛选条件无效')
            uids = list(selected)
        matched_total = len(uids)
        for uid in uids[offset:offset + page_size]:
            metadata, raw_headers = _fetch_tuple(
                client, uid,
                '(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])',
            )
            if raw_headers is None:
                continue
            try:
                headers = BytesParser(policy=default).parsebytes(raw_headers)
            except Exception:
                continue
            received = _received_at(metadata)
            sender = _decode_header(headers.get('From', ''))[:300]
            subject = _decode_header(headers.get('Subject', ''))[:300] or '(无主题)'
            messages.append({
                'input_id': _identity(address, uidvalidity, uid, identity_namespace),
                'subject': subject,
                'sender': sender,
                'received_at': received.isoformat() if received else None,
            })
    summary = {'total': total}
    if select_uids is not None:
        summary['matched_total'] = matched_total
    return {'messages': messages, 'summary': summary,
            'next_offset': offset + page_size if offset + page_size < (matched_total if select_uids is not None else total) else None}


def _parse_sexp(source, index=0):
    length = len(source)
    while index < length and source[index].isspace():
        index += 1
    if index >= length:
        raise ValueError('missing IMAP value')
    if source[index] == '(':
        values, index = [], index + 1
        while True:
            while index < length and source[index].isspace():
                index += 1
            if index >= length:
                raise ValueError('unterminated IMAP list')
            if source[index] == ')':
                return values, index + 1
            value, index = _parse_sexp(source, index)
            values.append(value)
    if source[index] == '"':
        chars, index = [], index + 1
        while index < length:
            char = source[index]
            index += 1
            if char == '"':
                return ''.join(chars), index
            if char == '\\' and index < length:
                char = source[index]
                index += 1
            chars.append(char)
        raise ValueError('unterminated IMAP string')
    end = index
    while end < length and not source[end].isspace() and source[end] not in '()':
        end += 1
    atom = source[index:end]
    if not atom:
        raise ValueError('invalid IMAP atom')
    if atom.upper() == 'NIL':
        return None, end
    if atom.isdigit():
        return int(atom), end
    return atom, end


def _body_structure(metadata):
    decoded = (metadata or b'').decode('ascii', 'replace')
    match = re.search(r'\bBODYSTRUCTURE\s+', decoded, re.I)
    if not match:
        raise EmailSourceError(422, '邮件没有可读取的正文结构，未发送邮件内容')
    try:
        value, _ = _parse_sexp(decoded, match.end())
        return value
    except ValueError:
        raise EmailSourceError(422, '邮件正文结构无法安全解析，未发送邮件内容') from None


def _parameters(value):
    if not isinstance(value, list):
        return {}
    return {str(value[index]).lower(): str(value[index + 1])
            for index in range(0, len(value) - 1, 2)
            if isinstance(value[index], str) and value[index + 1] is not None}


def _has_filename(parameters):
    return any(key == 'name' or key.startswith('name*') or key == 'filename' or key.startswith('filename*')
               for key in parameters)


def _attachment(node, major):
    params = _parameters(node[2] if len(node) > 2 else None)
    if _has_filename(params):
        return True
    disposition_index = 9 if major == 'text' else 8
    disposition = node[disposition_index] if len(node) > disposition_index else None
    if isinstance(disposition, list) and disposition:
        if str(disposition[0]).lower() == 'attachment':
            return True
        disp_params = _parameters(disposition[1] if len(disposition) > 1 else None)
        if _has_filename(disp_params):
            return True
    return False


def _walk_body(node, prefix=''):
    """Return safe inline text leaves and whether omitted parts may contain content."""
    if not isinstance(node, list) or not node:
        return [], False
    if isinstance(node[0], list):
        children = []
        for child in node:
            if isinstance(child, list):
                children.append(child)
            else:
                break
        leaves, omitted = [], False
        for index, child in enumerate(children, 1):
            part = f'{prefix}.{index}' if prefix else str(index)
            child_leaves, child_omitted = _walk_body(child, part)
            leaves.extend(child_leaves)
            omitted = omitted or child_omitted
        return leaves, omitted
    if len(node) < 2 or not isinstance(node[0], str) or not isinstance(node[1], str):
        return [], True
    major, subtype = node[0].lower(), node[1].lower()
    attachment = _attachment(node, major)
    if major == 'text' and subtype in ('plain', 'html') and not attachment:
        params = _parameters(node[2] if len(node) > 2 else None)
        section = prefix or 'TEXT'
        octets = node[6] if len(node) > 6 and isinstance(node[6], int) else None
        encoding = node[5].lower() if len(node) > 5 and isinstance(node[5], str) else '7bit'
        return [dict(section=section, subtype=subtype, charset=params.get('charset', 'utf-8'), encoding=encoding, octets=octets)], False
    # Non-text, attached, and encapsulated-message parts are never fetched.
    return [], attachment or major in ('application', 'image', 'audio', 'video', 'message')


class _VisibleHTML(HTMLParser):
    _breaks = {'br', 'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'blockquote'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ('script', 'style', 'head'):
            self.hidden += 1
        elif tag.lower() in self._breaks and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag.lower() in ('script', 'style', 'head') and self.hidden:
            self.hidden -= 1
        elif tag.lower() in self._breaks and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _decode_part(raw, part):
    charset = part.get('charset') or 'utf-8'
    encoding = part.get('encoding', '7bit').lower()
    # IMAP returns the transfer-encoded leaf bytes; decode only this text part.
    if encoding == 'base64':
        compact = re.sub(rb'\s+', b'', raw)
        decoded = base64.b64decode(compact + b'=' * (-len(compact) % 4), validate=False)
    elif encoding == 'quoted-printable':
        decoded = quopri.decodestring(raw)
    else:
        decoded = raw
    try:
        text = decoded.decode(charset, 'replace')
    except LookupError:
        text = decoded.decode('utf-8', 'replace')
    if part['subtype'] == 'html':
        parser = _VisibleHTML()
        try:
            parser.feed(text)
            text = ''.join(parser.parts)
        except Exception:
            text = re.sub(r'<[^>]*>', ' ', text)
    return text


def _fetch_text(client, uid, timings=None, guard=None, body_capacity_reserved=False,
                message_guard=None):
    started=time.perf_counter()
    metadata, _ = _fetch_tuple(client, uid, '(UID BODYSTRUCTURE)', metadata_only=True)
    if timings is not None: timings['fetch_ms']=timings.get('fetch_ms',0)+(time.perf_counter()-started)*1000
    if metadata is None:
        return None, False, False
    started=time.perf_counter()
    structure = _body_structure(metadata)
    leaves, omitted = _walk_body(structure)
    if timings is not None: timings['parse_ms']=timings.get('parse_ms',0)+(time.perf_counter()-started)*1000
    if not leaves:
        return None, False, omitted
    part = next((item for item in leaves if item['subtype'] == 'plain'), leaves[0])
    started=time.perf_counter()
    if body_capacity_reserved:
        if guard: guard()
        if message_guard: message_guard()
        metadata, raw = _fetch_tuple(client, uid, f"(UID BODY.PEEK[{part['section']}]<0.{MAX_BODY_BYTES}>)")
    else:
        with email_mailbox_pool.body_slot(MAX_BODY_BYTES, guard):
            if guard: guard()
            if message_guard: message_guard()
            metadata, raw = _fetch_tuple(client, uid, f"(UID BODY.PEEK[{part['section']}]<0.{MAX_BODY_BYTES}>)")
    if timings is not None: timings['fetch_ms']=timings.get('fetch_ms',0)+(time.perf_counter()-started)*1000
    if raw is None:
        return None, False, omitted
    started=time.perf_counter()
    text = _decode_part(raw, part)
    if timings is not None: timings['parse_ms']=timings.get('parse_ms',0)+(time.perf_counter()-started)*1000
    truncated = len(raw) >= MAX_BODY_BYTES or (part['octets'] is not None and part['octets'] > MAX_BODY_BYTES)
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS]
        truncated = True
    return text.strip(), truncated, omitted


def fetch_selected(address, username, password, input_ids, *, timings=None, guard=None,
                   body_capacity_reserved=False, message_guard=None, identity_namespace=None):
    address = normalize_address(address)
    if not isinstance(input_ids, list) or not 1 <= len(input_ids) <= MAX_SELECTED:
        raise EmailSourceError(400, f'每次请选择 1–{MAX_SELECTED} 封邮件')
    account_prefix = _identity(address, 'x', 1, identity_namespace).rsplit(':', 2)[0] + ':'
    selected = []
    seen = set()
    for value in input_ids:
        if not isinstance(value, str) or len(value) > 120 or not value.startswith(account_prefix):
            raise EmailSourceError(400, '邮件选择无效，请重新预览收件箱')
        match = re.fullmatch(re.escape(account_prefix) + r'(\d+):(\d+)', value)
        if not match or value in seen:
            raise EmailSourceError(400, '邮件选择无效，请重新预览收件箱')
        seen.add(value)
        selected.append((value, match.group(1), int(match.group(2))))

    # Keep each authenticated read-only session short. A bounded group of five
    # messages can reuse its connection without sharing an IMAP client across
    # workers or holding a session while recognition/model work runs.
    if len(selected) > 5:
        records, unavailable, truncated, omitted = [], 0, 0, 0
        for offset in range(0, len(selected), 5):
            batch_records, summary = fetch_selected(address, username, password,
                [item[0] for item in selected[offset:offset + 5]], timings=timings, guard=guard,
                body_capacity_reserved=body_capacity_reserved, message_guard=message_guard, identity_namespace=identity_namespace)
            records.extend(batch_records)
            unavailable += summary['unavailable']
            truncated += summary['truncated']
            omitted += summary['attachments_or_other_parts_omitted']
        return records, {'selected': len(input_ids), 'unavailable': unavailable,
                         'truncated': truncated, 'attachments_or_other_parts_omitted': omitted}

    records, unavailable, truncated_count, omitted_count = [], 0, 0, 0
    with _mailbox(address, username, password, cancel_check=guard) as client:
        if timings is not None:
            timings['connect_ms'] = timings.get('connect_ms', 0) + float(
                getattr(client, '_niuma_connect_ms', 0) or 0)
        client._niuma_deadline = time.monotonic() + 270
        current_uidvalidity = _select_inbox(client)
        if any(uidvalidity != current_uidvalidity for _, uidvalidity, _ in selected):
            raise EmailSourceError(409, '收件箱已变化，请重新预览并选择邮件')
        # UID SEARCH ALL makes old selected messages valid too; selection is
        # still constrained by the stable mailbox UIDVALIDITY identity.
        recent = set(_search_uids(client))
        for input_id, _, uid in selected:
            if guard: guard()
            if uid not in recent:
                unavailable += 1
                continue
            if message_guard:
                try:
                    message_guard(input_id)
                except SkipEmail:
                    unavailable += 1
                    continue
            started=time.perf_counter()
            metadata, raw_headers = _fetch_tuple(
                client, uid,
                '(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])',
            )
            if timings is not None: timings['fetch_ms']=timings.get('fetch_ms',0)+(time.perf_counter()-started)*1000
            if raw_headers is None:
                unavailable += 1
                continue
            try:
                started=time.perf_counter()
                headers = BytesParser(policy=default).parsebytes(raw_headers)
                if timings is not None: timings['parse_ms']=timings.get('parse_ms',0)+(time.perf_counter()-started)*1000
            except Exception:
                unavailable += 1
                continue
            try:
                body, truncated, omitted = _fetch_text(client, uid, timings, guard,
                    body_capacity_reserved=body_capacity_reserved,
                    message_guard=(lambda: message_guard(input_id)) if message_guard else None)
            except SkipEmail:
                unavailable += 1
                continue
            except EmailSourceError as error:
                if error.status == 422:
                    unavailable += 1
                    continue
                raise
            if not body:
                unavailable += 1
                continue
            truncated_count += int(truncated)
            omitted_count += int(omitted)
            received = _received_at(metadata)
            if received:
                published = {'value': received.isoformat(), 'precision': 'datetime', 'basis': 'source'}
            else:
                date_header = headers.get('Date')
                try:
                    date_value = email.utils.parsedate_to_datetime(str(date_header)) if date_header else None
                    if date_value and date_value.tzinfo:
                        published = {'value': date_value.astimezone(TZ).isoformat(), 'precision': 'datetime', 'basis': 'upstream_unverified'}
                    else:
                        published = {'value': None, 'precision': 'unknown', 'basis': 'unknown'}
                except (TypeError, ValueError, OverflowError):
                    published = {'value': None, 'precision': 'unknown', 'basis': 'unknown'}
            subject = _decode_header(headers.get('Subject', ''))[:300] or '(无主题)'
            sender = _decode_header(headers.get('From', ''))[:300]
            quality = 'partial' if truncated or omitted else 'complete'
            body_text = f'主题：{subject}\n发件人：{sender}\n收到时间：{received.isoformat() if received else "未知"}\n\n{body}'
            records.append({
                'text_quality': quality,
                'platform': 'email',
                'published_at': published,
                'collected_at': datetime.now(TZ).isoformat(),
                'timezone': 'Asia/Shanghai',
                'timezone_basis': 'source' if received else 'caller_default',
                'source_url': None,
                'source_label': subject,
                'source_item_id': f'{current_uidvalidity}:{uid}',
                'course_or_project': None,
                'context': [],
                'upstream_hints': None,
                'input_id': input_id,
                'text': body_text,
            })
    return records, {'selected': len(input_ids), 'unavailable': unavailable,
                     'truncated': truncated_count, 'attachments_or_other_parts_omitted': omitted_count}
