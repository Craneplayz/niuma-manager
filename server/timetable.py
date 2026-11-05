"""Deterministic import of the teaching portal's weekday/period XLS timetable."""
import base64
import binascii
import hashlib
import io
import json
import re
import struct
from datetime import date, datetime, timedelta, timezone

import xlrd

MAX_FILE = 512 * 1024
TERM = dict(firstMonday='2026-09-07', lastDate='2026-12-25', examStart='2026-12-28', examEnd='2027-01-10')
PERIODS = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二']
# PKU main-campus timetable; users confirm/edit these clocks before saving.
# https://dean.pku.edu.cn/web/notice_details.php?id=771
CLOCKS = [('08:00', '08:50'), ('09:00', '09:50'), ('10:10', '11:00'),
          ('11:10', '12:00'), ('13:00', '13:50'), ('14:00', '14:50'),
          ('15:10', '16:00'), ('16:10', '17:00'), ('17:10', '18:00'),
          ('18:40', '19:30'), ('19:40', '20:30'), ('20:40', '21:30')]


def parse(encoded, Error):
    if not isinstance(encoded, str) or not encoded:
        raise Error(400, '请选择课表文件')
    if len(encoded) > (MAX_FILE + 2) // 3 * 4:
        raise Error(413, '课表文件不能超过 512 KB')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise Error(400, '课表文件编码无效') from None
    if len(raw) > MAX_FILE:
        raise Error(413, '课表文件不能超过 512 KB')
    if not raw.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        raise Error(400, '请上传教务系统导出的 XLS 课表，不支持此文件格式')
    try:
        book = xlrd.open_workbook(file_contents=raw, logfile=io.StringIO(), on_demand=True, ragged_rows=True)
        try:
            if book.nsheets != 1:
                raise ValueError('sheets')
            sheet = book.sheet_by_index(0)
            if sheet.nrows != 13 or sheet.ncols != 8:
                raise ValueError('shape')
            rows = []
            for i in range(13):
                values = sheet.row_values(i)
                rows.append([str(value).strip() for value in values] + [''] * (8 - len(values)))
        finally:
            book.release_resources()
    except (xlrd.XLRDError, xlrd.compdoc.CompDocError, ValueError, IndexError, TypeError, struct.error, EOFError):
        raise Error(400, '无法读取课表，请使用未加密的教务 XLS 原文件') from None
    return parse_rows(rows, Error)


def parse_rows(rows, Error):
    expected = ['节数'] + ['星期' + n for n in '一二三四五六日']
    if rows[0] != expected or any(rows[i + 1][0] != f'第{n}节' for i, n in enumerate(PERIODS)):
        raise Error(400, '课表须为星期一至星期日、第一至第十二节的教务导出格式')
    meetings = []
    for weekday in range(1, 8):
        period = 1
        while period <= 12:
            text = rows[period][weekday]
            if not text:
                period += 1
                continue
            if len(text) > 3000:
                raise Error(400, '课表单元格内容过长')
            end = period
            while end < 12 and rows[end + 1][weekday] == text:
                end += 1
            # The last parenthesized field before remarks is the classroom;
            # course titles can themselves contain parentheses (e.g. 原理(Ⅰ)).
            match = re.fullmatch(r'(.+)\(([^()]*)\)\(备注[：:](.*?)\)\s*(每周|单周|双周)(.*)', text, re.S)
            if not match or len(re.findall(r'\(备注[：:]', text)) != 1:
                raise Error(400, f'星期{"一二三四五六日"[weekday-1]}第{period}节格式无法确定，请检查原课表')
            title, location, notes, parity, exam_note = match.groups()
            if not title.strip():
                raise Error(400, '课程名称为空')
            identity = hashlib.sha256(f'{weekday}:{period}:{end}:{text}'.encode()).hexdigest()[:20]
            exam = None
            exam_matches = re.findall(r'考试时间[：:]\s*(\d{8})\s*(上午|下午|晚上)?', notes + ' ' + exam_note)
            if len(exam_matches) > 1:
                raise Error(400, '课程包含多个考试日期，请先核实原课表')
            if exam_matches:
                stamp, part = exam_matches[0]
                try:
                    exam_day = datetime.strptime(stamp, '%Y%m%d').date().isoformat()
                except ValueError:
                    raise Error(400, '课表中的考试日期无效，请检查原文件') from None
                exam = dict(date=exam_day, period=part)
            meetings.append(dict(id=identity, title=title.strip(), location='' if location == '暂无上课教室数据' else location,
                                 notes=notes.strip(), weekday=weekday, startPeriod=period, endPeriod=end,
                                 parity={'每周': 'all', '单周': 'odd', '双周': 'even'}[parity],
                                 startTime=CLOCKS[period-1][0], endTime=CLOCKS[end-1][1],
                                 examNote=exam_note.strip(), exam=exam))
            period = end + 1
    if not meetings:
        raise Error(400, '课表中没有课程')
    return meetings


def read(db, uid):
    row = db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="timetable"', (uid,)).fetchone()
    return dict(value=json.loads(row['value']) if row else [], version=row['version'] if row else 0)


def occurrences(tables, start=None, end=None):
    events = []
    for table in tables:
        first = date.fromisoformat(table['firstMonday'])
        last = date.fromisoformat(table['lastDate'])
        for meeting in table['meetings']:
            day = first + timedelta(days=meeting['weekday'] - 1)
            while day <= last:
                week = (day - first).days // 7 + 1
                if (meeting['parity'] == 'all' or (week % 2 == 1) == (meeting['parity'] == 'odd')) and (start is None or day >= start) and (end is None or day <= end):
                    events.append(dict(meeting, id=f"{table['id']}:{meeting['id']}:{day}", kind='class', date=day.isoformat(), week=week))
                day += timedelta(days=7)
    exams = set()
    for table in tables:
        for meeting in table['meetings']:
            exam = meeting.get('exam')
            if not exam:
                continue
            exam_day = date.fromisoformat(exam['date'])
            key = (meeting['title'], exam['date'], exam['period'])
            if key in exams or start is not None and exam_day < start or end is not None and exam_day > end:
                continue
            exams.add(key)
            identity = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:20]
            events.append(dict(id='exam:' + identity, title=meeting['title'], kind='exam', date=exam['date'],
                               period=exam['period'], startTime='', endTime='', location='', notes=' '.join(filter(None, [meeting['notes'], meeting['examNote']]))))
    return events


def occupied(db, uid):
    result = []
    for event in occurrences(read(db, uid)['value']):
        start, end = event_window(event)
        result.append(dict(id=event['id'], taskId='timetable:' + event['id'], title=event['title'],
                           startAt=start.isoformat(), endAt=end.isoformat(), origin='manual', isLocked=True))
    return result


def event_window(event):
    if event['kind'] == 'exam':
        # An unknown exact exam time excludes that day from automatic planning;
        # this is a scheduling constraint, not an invented exam duration.
        start = datetime.fromisoformat(event['date'] + 'T00:00:00+08:00')
        return start, start + timedelta(days=1)
    return (datetime.fromisoformat(event['date'] + 'T' + event['startTime'] + ':00+08:00'),
            datetime.fromisoformat(event['date'] + 'T' + event['endTime'] + ':00+08:00'))


def assert_no_conflicts(blocks, db, uid, Error):
    busy = occupied(db, uid)
    for block in blocks:
        start = datetime.fromisoformat(block['startAt'])
        end = datetime.fromisoformat(block['endAt']) if block.get('endAt') else start + timedelta(hours=1)
        if any(start < datetime.fromisoformat(event['endAt']) and datetime.fromisoformat(event['startAt']) < end for event in busy):
            raise Error(409, '课表与当前规划冲突，请重新生成规划')


def route(handler, db, uid, path, body, Error):
    if path not in ('/api/timetable', '/api/timetable/preview'):
        return False
    method = handler.command
    if path.endswith('/preview') and method == 'POST':
        handler.reply(200, dict(meetings=parse(body.get('file'), Error), term=TERM))
        return True
    if path == '/api/timetable' and method == 'GET':
        handler.reply(200, read(db, uid))
        return True
    if path != '/api/timetable' or method not in ('POST', 'DELETE'):
        raise Error(405, '不支持此课表操作')
    db.execute('BEGIN IMMEDIATE')
    current = read(db, uid)
    if method == 'POST':
        meetings = parse(body.get('file'), Error)
        try:
            first, last = date.fromisoformat(body['firstMonday']), date.fromisoformat(body['lastDate'])
            if first.weekday() != 0 or not 0 <= (last - first).days <= 279:
                raise ValueError('term')
            clocks = body['clocks']
            if not isinstance(clocks, dict) or set(clocks) != {m['id'] for m in meetings}:
                raise ValueError('clocks')
            for meeting in meetings:
                start, end = clocks[meeting['id']]
                if not all(isinstance(t, str) and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in (start, end)) or start >= end:
                    raise ValueError('clock')
                meeting.update(startTime=start, endTime=end)
        except (KeyError, ValueError, TypeError):
            raise Error(400, '请确认第一周星期一、结束日期（最多40周）及每门课的起止时间') from None
        table = dict(firstMonday=first.isoformat(), lastDate=last.isoformat(), meetings=meetings)
        table['id'] = hashlib.sha256(json.dumps(table, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        # Network retries of the same confirmed import are safe even after a receipt was lost.
        if any(t['id'] == table['id'] for t in current['value']):
            handler.reply(200, dict(current, duplicate=True))
            return True
        if len(current['value']) >= 8:
            raise Error(400, '最多保存8份课表，请先删除不再使用的课表')
        if not any(event['kind'] == 'class' for event in occurrences([table])):
            raise Error(400, '所选日期范围内没有上课安排，请检查学期日期和单双周')
        # Protect future active task plans; historical/completed work is not a constraint.
        tasks = db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"', (uid,)).fetchone()
        classes = occurrences([table])
        now = datetime.now(timezone.utc)
        for task in json.loads(tasks['value']) if tasks else []:
            if task.get('status') in ('completed', 'cancelled'):
                continue
            for plan in task.get('plans', []):
                start = datetime.fromisoformat(plan['startAt'])
                end = datetime.fromisoformat(plan['endAt']) if plan.get('endAt') else start + timedelta(hours=1)
                if end <= now:
                    continue
                for event in classes:
                    event_start, event_end = event_window(event)
                    if start < event_end and event_start < end:
                        raise Error(409, '课表与已保存的任务安排冲突，请先调整任务安排后再导入')
        value = current['value'] + [table]
    else:
        if not isinstance(body.get('id'), str):
            raise Error(400, '请选择要删除的课表')
        value = [table for table in current['value'] if table['id'] != body['id']]
        if len(value) == len(current['value']):
            raise Error(404, '课表不存在，请重新读取')
    if type(body.get('version')) is not int or body['version'] != current['version']:
        raise Error(409, '课表已在其他窗口修改，请重新打开后操作')
    version = current['version'] + 1
    db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',
               (uid, 'timetable', json.dumps(value, ensure_ascii=False), version))
    handler.reply(200, dict(value=value, version=version))
    return True
