import { summarizeQuickNotice } from "./quick-notice.js";
import { shiftDay } from "./task-model.js";

const weekday = "一二三四五六日";
const numbers = "零〇一二两三四五六七八九十";
const datePattern = /(?<![\d/.-])(?:(?:(?<year>\d{4})(?:年|[-/.]))?(?<month>\d{1,2})(?:月|[-/]|\.(?=\d{1,2}日))(?<day>\d{1,2})(?:日|号)?(?![\d/.-])|(?<relative>大后天|后天|明天|今天|今日|明晚|今晚|明早|今早|昨天|昨日|前天)|(?<week>下下|上上|下|上|本|这)?(?:周|星期|礼拜)(?<weekday>[一二三四五六日天]))/gu;
const timePattern = new RegExp(
  `(?:(?<period>凌晨|清晨|早晨|早上|上午|中午|下午|傍晚|晚上|晚间|晚|早)\\s*|(?<![\\d:：]))(?:(?<hour>\\d{1,2})[:：](?<minute>\\d{2})(?![\\d:：])|(?<spokenHour>[\\d${numbers}]{1,3})[点时](?:(?<half>半)|(?<quarter>[一三])刻|(?<spokenMinute>[\\d${numbers}]{1,3})分|(?<bareMinute>[0-5]\\d|[二三四五]?十[一二三四五六七八九]?|零[一二三四五六七八九]))?)`, "gu",
);

// In short notes these words can change whether a date is a deadline at all.
const uncertainDate = /上个月|下个月|[上下本这]周末|周末|月底|月初|待定|取消|暂定|另行|推迟|延期|改期|每(?:天|晚|周|月|星期)|(?:几个|[\d一二两三四五六七八九十]+)\s*(?:个?星期|周|天|小时|分钟)(?:之|以)?[前后内]/u;
const relativeOffsets = new Map([
  ["前天", -2], ["昨天", -1], ["昨日", -1], ["今天", 0], ["今日", 0], ["今晚", 0], ["今早", 0],
  ["明天", 1], ["明晚", 1], ["明早", 1], ["后天", 2], ["大后天", 3],
]);

function resolveDate(groups, today) {
  if (groups.month) {
    const year = groups.year || today.slice(0, 4);
    const date = [year, groups.month.padStart(2, "0"), groups.day.padStart(2, "0")].join("-");
    return shiftDay(date, 0);
  }
  if (!today) return "";
  if (groups.relative) return shiftDay(today, relativeOffsets.get(groups.relative));
  const current = (new Date(today + "T00:00:00Z").getUTCDay() + 6) % 7;
  const delta = weekday.indexOf(groups.weekday.replace("天", "日")) - current;
  const weeks = ({ 下下: 2, 上上: -2, 下: 1, 上: -1, 本: 0, 这: 0 })[groups.week] ?? (delta < 0 ? 1 : 0);
  return shiftDay(today, delta + 7 * weeks);
}

function readNumber(value) {
  if (/^\d+$/u.test(value)) return Number(value);
  const numeral = value.replaceAll("两", "二").replaceAll("〇", "零");
  const digits = "零一二三四五六七八九";
  if (/^[零一二三四五六七八九]?[十][一二三四五六七八九]?$/u.test(numeral)) {
    const [tens, ones] = numeral.split("十");
    return (tens ? digits.indexOf(tens) : 1) * 10 + (ones ? digits.indexOf(ones) : 0);
  }
  if (/^[零一二三四五六七八九]{1,2}$/u.test(numeral))
    return Number([...numeral].map(digit => digits.indexOf(digit)).join(""));
  return NaN;
}

function resolveTime(groups, period) {
  let hour = readNumber(groups.hour || groups.spokenHour);
  const minute = groups.half ? 30 : groups.quarter === "一" ? 15 : groups.quarter === "三" ? 45
    : readNumber(groups.minute || groups.spokenMinute || groups.bareMinute || "0");
  if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour > 23 || minute > 59) return "";
  if (hour > 12 && ["清晨", "早晨", "早上", "早", "上午", "凌晨"].includes(period)) return "";
  if (hour === 12 && ["晚上", "晚间", "晚"].includes(period)) return "";
  if (period && hour <= 12) {
    if (["下午", "傍晚", "晚上", "晚间", "晚"].includes(period) && hour < 12) hour += 12;
    else if (period === "中午" && hour >= 1 && hour <= 3) hour += 12;
    else if (period === "凌晨" && hour === 12) hour = 0;
  }
  return [hour, minute].map(part => String(part).padStart(2, "0")).join(":");
}

function knownCourseIn(text, courses) {
  const names = [...new Set(courses.filter(name => typeof name === "string" && name.trim()))]
    .sort((left, right) => right.length - left.length);
  let match = "";
  for (const name of names) {
    const literal = name.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
    const left = /^\w/u.test(name) ? "(?<![a-z0-9_])" : "";
    const right = /\w$/u.test(name) ? "(?![a-z0-9_])" : "";
    const pattern = new RegExp(left + literal + right, "iu");
    if (!pattern.test(text)) continue;
    // A course name can include the entire name of a shorter course.
    if (match && !match.toLowerCase().includes(name.toLowerCase())) return "";
    match ||= name;
    text = text.replace(new RegExp(pattern, "giu"), "");
  }
  return match;
}

function takeCourse(text, courses, defaultCourse) {
  const tags = [...text.matchAll(/(?<![a-zA-Z0-9_#&])[#＃](?<tag>[^\s#＃，,。；;：:、]+)[#＃]?|(?:^|(?<=[\s，,；;]))(?:课程|科目|分类)\s*[:：]\s*(?<field>[^\s，,。；;、]+)/gu)];
  // Mask tags without shifting offsets. A punctuation marker also prevents one
  // time match from spanning a tag between its day part and its clock digits.
  let masked = text;
  for (const tag of [...tags].reverse()) {
    masked = masked.slice(0, tag.index) + "，".repeat(tag[0].length) + masked.slice(tag.index + tag[0].length);
  }
  if (tags.length === 1) {
    const [tag] = tags;
    const name = tag.groups.tag || tag.groups.field;
    const course = courses.find(known => typeof known === "string" && known.toLowerCase() === name.toLowerCase()) || name;
    return { masked, course, tag: { start: tag.index, end: tag.index + tag[0].length } };
  }
  return { masked, course: tags.length ? "" : knownCourseIn(text, courses) || defaultCourse };
}

function isUncertain(text, dates, times) {
  if (dates.length > 1 || times.length > 1 || uncertainDate.test(text)) return true;
  for (const token of [...dates, ...times]) {
    const after = text.slice(token.index + token[0].length);
    const before = text.slice(0, token.index);
    // Check abbreviated ranges at the edge of the date/time token. Hyphens
    // inside an ISO date or an unrelated exercise number remain unambiguous.
    if (/^\s*(?:[-~～—–至到]|或(?:者)?)\s*(?:[上下早晚午周]|[\d一二两三四五六七八九十])/u.test(after)
        || /[\d一二两三四五六七八九十]\s*[-~～—–至到]\s*$/u.test(before)) return true;
    if ((after.startsWith("的") && times[0]?.index !== token.index + token[0].length + 1)
        || /^(?:左右|前后|以后|之后|后再|起至|开始|无需|不(?:用|必|交|提交|要|上|是))/u.test(after)
        || /(?:不在|不是|不必|不用|不要|无需|别在|并非|从|除了)\s*$/u.test(before)) return true;
  }
  return false;
}

function stripTokens(source, tokens) {
  if (!tokens.length) return source.trim();
  let title = source;
  for (const token of [...tokens].sort((a, b) => b.start - a.start)) {
    let before = title.slice(0, token.start);
    let after = title.slice(token.end);
    if (token.deadline) {
      before = before.replace(/(?:截止(?:到|于)?|截至|不(?:晚|迟)于|最迟(?:到|于|在)?|请[于在]|在)\s*$/u, "");
      after = after.replace(/^(?:(?:之|以)?前|截止)(?=$|[\s，,。；;]|[交提还写发完上做填])/u, "");
    }
    title = before + after;
  }
  return title.replace(/^[\s，,；;：:]+|[\s，,；;：:]+$/gu, "").replace(/[ \t]{2,}/gu, " ");
}

function mergedTimeSpans(tokens, source) {
  const spans = [];
  for (const token of tokens.sort((a, b) => a.index - b.index)) {
    const end = token.index + token[0].length;
    const last = spans.at(-1);
    if (last && (last.end >= token.index || /^(?:的|T)$/u.test(source.slice(last.end, token.index)))) {
      last.end = Math.max(last.end, end);
    } else spans.push({ start: token.index, end });
  }
  return spans;
}

// Only finish a local summary when all of its words fit one simple action.
function summarizeAction(raw, source, spans) {
  const title = raw.trim()
    .replace(/[，,。\s]*(?:你)?(?:帮我|给我|替我)(?:记录|记)(?:一下|下来|上)?(?:吧)?[。！!\s]*$/u, "")
    .replace(/^(?:请(?:各位|全体|所有)?(?:同学们?|同事们?|成员|老师们?)|(?:各位|全体)同学们?)[，,、\s]*(?:请)?/u, "")
    .replace(/^(?:请务必|请大家|请于|请|务必|于(?=前往|到|在|参加|观看|提交|交|完成))\s*/u, "")
    .replace(/^[，,\s]+|[。！!\s]+$/gu, "");
  const exam = title.match(/^(?:有|要考|要参加|参加)?(?:一场)?([^，,。；;！？\s]{0,20}?)(考试|测验)$/u);
  const simple = title.length > 0 && title.length <= 48 &&
    !/[，,；;。！？!?]|(?:然后|并且|顺便|另行|分别|之后|之前|取消|无需|不用|不要|不必|如果|除非|改为|改到|待定|或者|还需|一下|结束后|结束前|会后|会前)|(?:并|及|再)(?:把|提交|观看|参加|领取|签到|将)/u.test(title);
  const hasLimit = /截止|截至|最迟|不晚于|不迟于/u.test(source) ||
    spans.some(({ end }) => /^\s*(?:之|以)?前(?!往|去|来|进)/u.test(source.slice(end)));
  const visit = simple ? title.match(/^(?:前往|前去|到|在|去)\s*(?<place>[^，,。；;！？]{1,40}?)\s*(?<action>(?:观看|参加|出席|参观|旁听|听取|集合|签到|开会|上课|提交|领取|归还).*)$/u) : null;
  const action = visit?.groups.action || (exam ? exam[1] + exam[2] : title);
  const event = !hasLimit && (Boolean(exam) || /^(?:观看|参加|出席|参观|旁听|听取|集合|签到|开会|上(?:.{0,12})课|值班)/u.test(action));
  const clear = simple && (event || /^(?:提交|交|领取|归还|还书|完成|写|整理|复习|阅读|购买|打印|准备|填写|发送|报名|买|取|背|订|发)/u.test(action));
  const subject = exam?.[1].replace(/(?:期中|期末|随堂|闭卷|开卷|线上|线下)+$/u, "");
  const course = subject && !/一场|期中|期末|补考|资格|等级|有|的|要/u.test(subject) ? subject : "";
  return { title: action || raw, ...(course && simple ? { course } : {}), ...(visit ? { notes: '地点：' + visit.groups.place.trim() } : {}),
    ...(event && simple ? { timeKind: 'event' } : {}), clear };
}

/** Spans index the original UTF-16 string, including whitespace and any tags. */
export function analyzeQuickTask(input, { today = "", defaultDate = "", defaultCourse = "", courses = [] } = {}) {
  const source = String(input ?? "");
  const notice = summarizeQuickNotice(source, today);
  if (notice) {
    const course = knownCourseIn(notice.title, courses) || defaultCourse || notice.course;
    return { task: { ...notice, course }, spans: [], explicitCourse: false, localOnly: true };
  }
  // A long or multi-sentence notice may contain several unrelated dates. Save
  // a short, editable preview and let recognition read the complete original.
  if (source.length > 180 || /[。！？][^。！？\s]/u.test(source.trim())) {
    const first = source.trim().split(/[。！？\n]/u).find(Boolean) || source.trim();
    return { task: { title: first.slice(0, 100), due: "", dueTime: "", course: defaultCourse },
      spans: [], explicitCourse: false, localOnly: false };
  }
  const { masked, course, tag } = takeCourse(source, courses, defaultCourse);
  const tags = tag ? [tag] : [];
  const explicitCourse = Boolean(tag);
  const task = { title: stripTokens(source, tags), due: shiftDay(defaultDate, 0), dueTime: "", course };
  // Normalize full-width digits one code unit at a time so highlight offsets stay valid.
  const searchable = masked
    .replace(/[０-９]/gu, digit => String.fromCharCode(digit.charCodeAt(0) - 0xfee0))
    .replace(/\b(\d{4})\.(\d{1,2})\.(\d{1,2})(?![\d.])/gu, "$1-$2-$3");
  const dates = [...searchable.matchAll(datePattern)].filter(match => {
    if (!match.groups.month || match.groups.year || /月/u.test(match[0])) return true;
    return !/第\s*$/u.test(searchable.slice(0, match.index))
      && !/^\s*[题章页节个份次]/u.test(searchable.slice(match.index + match[0].length));
  });
  const times = [...searchable.matchAll(timePattern)].filter(match => {
    const following = searchable.slice(match.index + match[0].length);
    if (!match.groups.period && /^(?:要求|建议|特征|原因|意见|式|水偏旁)/u.test(following)) return false;
    if (match[0] !== "一点") return true;
    return !/(?:有|找|剩|留|学|写|补|花|给|抽|再|带|买|借|多|少|快|慢)$/u.test(searchable.slice(0, match.index))
      && !/^(?:点|儿|时间|资料|东西|想法|建议)/u.test(following);
  });
  const [date] = dates, [time] = times;
  if (isUncertain(searchable, dates, times)) return { task: { ...task, due: "" }, spans: [], explicitCourse };
  if (!date && !time) {
    const { clear, ...summary } = summarizeAction(task.title, source, []);
    const unresolvedTime = /下周|本周|今晚|明早|稍后|一会|之前|以后|随后|课前|会前|结束后/u.test(source);
    return { task: { ...task, ...summary, course: course || summary.course || "" }, spans: [], explicitCourse, localOnly: clear && !unresolvedTime };
  }

  today = shiftDay(today, 0);
  const due = date ? resolveDate(date.groups, today) : task.due || today;
  const shortPeriod = /晚$/u.test(date?.groups.relative || "") ? "晚上" : /早$/u.test(date?.groups.relative || "") ? "早上" : "";
  const dueTime = time ? resolveTime(time.groups, time.groups.period || shortPeriod) : "";
  if (!due || (time && !dueTime)) return { task: { ...task, due: "" }, spans: [], explicitCourse };

  const spans = mergedTimeSpans([date, time].filter(Boolean), source);
  // "明晚" resolves a date without an exact clock time. Keep the imprecise
  // part in the title rather than silently losing what the student wrote.
  const dateWords = shortPeriod && !time ? [] : spans.map(span => ({ ...span, deadline: true }));
  const title = stripTokens(source, [...tags, ...dateWords]);
  const { clear, ...summary } = summarizeAction(title, source, spans);
  const possibleEvent = /前往|观看|参加|出席|参观|旁听|开场|开演|集合|签到/u.test(source);
  if (!clear && possibleEvent)
    return { task: { ...task, due: "" }, spans: [], explicitCourse, localOnly: false };
  const category = course || summary.course || (summary.timeKind === "event" && summary.notes ? "校园活动" : "");
  return { task: { ...summary, due, dueTime, course: category }, spans, explicitCourse, localOnly: clear };
}

export function parseQuickTask(input, options) {
  return analyzeQuickTask(input, options).task;
}
