"""Calendar expansion of explicitly bounded daily tasks in manual quick entry."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from .contracts import require


# These phrases describe calendars, not task titles or subjects. A window is an
# option for the model to bind to an action, never an instruction to repeat all actions.
WEEK = r'(?P<week>[这本上下])(?:个)?(?:星期|礼拜|周)'
COUNT = r'(?P<count>(?:未来|接下来)[0-9一二两三四五六七八九十]+天)'
CADENCE = r'(?P<cadence>每个工作日|工作日|每一天|每天|每日|每晚|每夜|天天)'
WINDOW = re.compile(r'(?:' + WEEK + '|' + COUNT + r')\s*(?:里|内)?\s*(?:的|[，,])?\s*' + CADENCE)
RESTRICTED = re.compile(r'除了?|以外|除外|不含|跳过|仅限|只在|只参加|只去|不参加|不用|不要|取消|从.{0,8}(?:起|开始)|剩[下余]|节假日|节日|调休|隔天')
DAILY_REQUEST = re.compile(r'(?:每天|每日|每一天|逐日|每晚)[^。！？；\n]{0,12}(?:设置|设|加|建|记|列|生成|提醒)[^。！？；\n]{0,8}(?:待办|任务|提醒|一条|一个)')


def day_count(text):
    number = re.search(r'[0-9一二两三四五六七八九十]+', text).group().replace('两', '二')
    if number.isascii():
        return int(number) if len(number) <= 3 else 0
    digits = '零一二三四五六七八九'
    if len(number) == 1:
        return 10 if number == '十' else digits.index(number)
    tens, separator, ones = number.partition('十')
    if not separator or len(tens) > 1 or len(ones) > 1 or '十' in ones:
        return 0
    return (digits.index(tens) if tens else 1) * 10 + (digits.index(ones) if ones else 0)


def daily_windows(text, anchor, weeks):
    windows = {}
    for match in WINDOW.finditer(text):
        if match['week']:
            name = {'这': '本', '本': '本', '上': '上', '下': '下'}[match['week']] + '周'
            days = [date.fromisoformat(day) for day in weeks[name] if day]
        else:
            count = day_count(match['count'])
            if not 1 <= count <= 100 or anchor.toordinal() + count - 1 > date.max.toordinal():
                continue
            days = [anchor + timedelta(days=n) for n in range(count)]
        if '工作日' in match['cadence']:
            days = [day for day in days if day.weekday() < 5]
        if days:
            windows[match.group()] = [day.isoformat() for day in days]
    return windows


def shift_local(value, delta, zone):
    if len(value) == 10:
        return (date.fromisoformat(value) + delta).isoformat()
    original = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(zone)
    shifted = original + delta
    wall = shifted.replace(tzinfo=None)
    # A repeated hour or a gap has no unique local instant on that date.
    offsets = {candidate.utcoffset() for fold in (0, 1)
               for candidate in [wall.replace(tzinfo=zone, fold=fold)]
               if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall}
    require(len(offsets) == 1)
    return shifted.isoformat()


def expand_daily(task, phrase, days, record):
    clock = task['deadline']
    require(task['notice_type'] == 'create' and phrase in record['text'])
    require(clock['status'] in ('explicit', 'inferred') and clock['value'] and
            (clock['kind'] == 'deadline' or clock.get('role') == 'event_start'))
    require(any(e['input_id'] == record['input_id'] and phrase in e['quote'] for e in task['evidence']))
    start = date.fromisoformat(clock['value'][:10])
    if clock.get('range_end'):
        require(0 <= (date.fromisoformat(clock['range_end'][:10]) - start).days <= 1)
    zone = ZoneInfo(record['timezone'])
    for day in days:
        occurrence = deepcopy(task)
        occurrence['candidate_id'] += ':' + day
        delta = date.fromisoformat(day) - start
        for key in ('value', 'range_end'):
            if clock.get(key):
                occurrence['deadline'][key] = shift_local(clock[key], delta, zone)
        yield occurrence
