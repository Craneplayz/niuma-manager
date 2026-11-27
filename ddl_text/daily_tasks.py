"""Calendar expansion of explicitly bounded daily tasks in manual quick entry."""
from copy import deepcopy
from datetime import date, datetime, timezone
import re
from zoneinfo import ZoneInfo

from .contracts import require


# These phrases describe calendars, not task titles or subjects. A window is an
# option for the model to bind to an action, never an instruction to repeat all actions.
WEEK = r'(?P<week>[这本上下])(?:个)?(?:星期|礼拜|周)'
COUNT = r'(?P<count>(?:未来|接下来)[0-9一二两三四五六七八九十]+天)'
CADENCE = r'(?P<cadence>每个工作日|工作日|每一天|每天|每日|每晚|每夜|天天)'
# Keep the source phrase as the key so each template can cite its own calendar.
DAY_OFFSETS = {'前天': -2, '昨天': -1, '今天': 0, '今日': 0, '明天': 1, '明日': 1, '后天': 2, '大后天': 3}
BOUND = (r'(?:从|自)?(?P<day>' + '|'.join(DAY_OFFSETS) + r')'
         r'(?P<edge>之后|以后|之前|以前|开始|起)')
JOIN = r'\s*(?:的|[，,])?\s*'
WINDOW = re.compile(r'(?:' + WEEK + '|' + COUNT + r')\s*(?:里|内)?' + JOIN
                    + r'(?:' + BOUND + JOIN + r')?' + CADENCE)
RESTRICTED = re.compile(r'除了?|以外|除外|不含|跳过|仅限|只在|只参加|只去|不参加|不用|不要|取消|从.{0,8}(?:起|开始)|(?:之|以)[前后]|剩[下余]|节假日|节日|调休|隔天')
DAILY_REQUEST = re.compile(r'(?:每天|每日|每一天|逐日|每晚)[^。！？；\n]{0,12}(?:设置|设|加|建|记|列|生成|提醒)[^。！？；\n]{0,8}(?:待办|任务|提醒|一条|一个)')
# A following clause can narrow the requested reminders. Only consume a
# reminder instruction adjacent to this schedule, never a separate action.
FOLLOWUP = re.compile(r'[^，,。！？；;\n]*[，,。！？；;\n]\s*'
                      r'(?:请|麻烦)?(?:你)?(?:给我|帮我|替我)?(?:只)?(?:在)?'
                      + r'(?P<request>' + BOUND + JOIN + CADENCE + r')'
                      + r'(?:都|各)?(?:帮我|给我)?(?:设置|设|添加|加|创建|建|记|列|生成|提醒)'
                      r'(?:上|下)?(?:个|一[个条项次])?(?:待办|任务|提醒)(?=[，,。！？；;\s]|$)')


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


def requested_days(days, match, anchor):
    """Intersect ordinal days before converting them to supported dates."""
    if '工作日' in match['cadence']:
        days = [day for day in days if (day - 1) % 7 < 5]
    if not match['day']:
        return days
    pivot = anchor.toordinal() + DAY_OFFSETS[match['day']]
    if match['edge'] in ('之前', '以前'):
        return [day for day in days if day < pivot]
    if match['edge'] in ('之后', '以后'):
        pivot += 1
    return [day for day in days if day >= pivot]


def daily_windows(text, anchor, weeks):
    windows = {}
    schedules = list(WINDOW.finditer(text))
    for index, match in enumerate(schedules):
        if match['week']:
            name = {'这': '本', '本': '本', '上': '上', '下': '下'}[match['week']] + '周'
            first = anchor.toordinal() - anchor.weekday() + {'上周': -7, '本周': 0, '下周': 7}[name]
            days = list(range(first, first + 7))
        else:
            count = day_count(match['count'])
            if not 1 <= count <= 100:
                continue
            days = list(range(anchor.toordinal(), anchor.toordinal() + count))
        days = requested_days(days, match, anchor)
        phrase = match.group()
        end = schedules[index + 1].start() if index + 1 < len(schedules) else len(text)
        followup = FOLLOWUP.match(text, match.end(), end)
        if followup:
            days = requested_days(days, followup, anchor)
            phrase = followup['request']
        # Apply the requested intersection before checking representability. Keep
        # missing dates, as weeks already does, so a selected range cannot shrink.
        dates = [date.fromordinal(day).isoformat() if 1 <= day <= date.max.toordinal() else None
                 for day in days]
        # Identical reminder wording can refer to different schedules. In that
        # case let the model resolve the dates without offering an ambiguous key.
        if phrase in windows and windows[phrase] != dates:
            windows[phrase] = None
        else:
            windows[phrase] = dates
    return {phrase: days for phrase, days in windows.items() if days is not None}


def requires_daily_template(text, windows):
    if not windows or not DAILY_REQUEST.search(text):
        return False
    # Explicit boundaries already accounted for in a window must not disable
    # the daily request. Other restrictions may require individual dates.
    for phrase in windows:
        text = text.replace(phrase, ' ')
    return not RESTRICTED.search(text)


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
    require(any(e['input_id'] == record['input_id'] and phrase in e['quote'] for e in task['evidence']))
    require(all(days))
    if not days:
        return
    require(clock['status'] in ('explicit', 'inferred') and clock['value'] and
            (clock['kind'] == 'deadline' or clock.get('role') == 'event_start'))
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
