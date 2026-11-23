import { shiftDay } from "./task-model.js";

const dateText = /(?<![\d:：])(?:(\d{4})\s*年\s*)?(\d{1,2})\s*(?:月\s*|[./-])\s*(\d{1,2})(?:\s*[日号])?(?!\d)/u;
const clock = /(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)/gu;
const weekday = /(?:周|星期|礼拜)([一二三四五六日天])/u;

function eventDate(text, today) {
  const found = text.match(dateText);
  if (!found || !shiftDay(today, 0)) return "";
  const [, year, month, day] = found;
  const base = Number(today.slice(0, 4));
  const candidates = (year ? [Number(year)] : [base - 1, base, base + 1])
    .map(y => [y, month.padStart(2, "0"), day.padStart(2, "0")].join("-"))
    .filter(date => shiftDay(date, 0))
    .sort((a, b) => Math.abs(Date.parse(a) - Date.parse(today)) - Math.abs(Date.parse(b) - Date.parse(today)));
  const date = candidates[0];
  const week = text.match(weekday)?.[1];
  if (!date || (week && "日一二三四五六"[new Date(date).getUTCDay()] !== week.replace("天", "日"))) return "";
  return date;
}

function timedEvent(text, today) {
  if ([...text.matchAll(new RegExp(dateText.source, "gu"))].length !== 1) return null;
  const due = eventDate(text, today);
  const moments = [...text.matchAll(clock)];
  if (!due || !moments.length || moments.length > 2) return null;
  const times = moments.map(m => m[1].padStart(2, "0") + ":" + m[2]);
  if (times.length === 2 && (!/^\s*[-–—~～至到]\s*$/u.test(
    text.slice(moments[0].index + moments[0][0].length, moments[1].index)) || times[1] <= times[0])) return null;
  return { due, dueTime: times[0], range: times.length === 2 ? times.join("–") : "" };
}

function field(text, label) {
  return text.match(new RegExp(label + "[：:]\\s*([^\\n。；;📅📍]+)", "u"))?.[1].trim() || "";
}

function examNotice(text, today) {
  const labels = "(?:考核|考试)";
  const when = field(text, labels + "时间");
  if (!when || [...text.matchAll(/(?:考试|考核)时间[：:]/gu)].length !== 1 ||
      /取消|延期|待定|另行通知/u.test(when)) return null;
  const time = timedEvent(when, today);
  const course = field(text, "课程名称") || text.match(/《([^》\n]+)》\s*(?:课程)?(?:期末|期中)?(?:考试|考核)/u)?.[1];
  if (!time || !course || course.length > 80) return null;
  const kind = text.match(/(?:期末|期中|结课|补考)(?:考核|考试)?/u)?.[0] || (text.includes("考核时间") ? "考核" : "考试");
  const place = field(text, labels + "地点"), format = field(text, labels + "(?:形式|方式)");
  const instructions = text.match(/(?:注意事项|考场要求)[：:]\s*([\s\S]+)/u)?.[1]
    ?.split(/\n\s*(?:任课教师|通知人|[0-9]{4}\s*年)/u)[0].trim();
  const notes = [place && "地点：" + place, time.range && "时段：" + time.range,
    format && "形式：" + format, instructions].filter(Boolean).join("\n");
  return { title: course + kind, course, explicitCourse: true, due: time.due, dueTime: time.dueTime, timeKind: "event", notes };
}

function poster(text, today) {
  let when = field(text, "(?:活动|会议|讲座|比赛)时间");
  let place = field(text, "(?:活动|会议|讲座|比赛)地点");
  let title = text.split(/[，,。！!\n]/u, 1)[0].trim();
  if (!when || !place) {
    // A prose announcement often leads with “名称，日期时间将于地点举办”。
    const lead = text.match(/^([^，,。\n]{3,80})[，,]\s*([^。；;，,\n]{4,65}?)(?:将于|将在|在)([^，,。；;\n]{2,60}?)(?:举办|举行|召开|开幕)[。！!\n]/u);
    if (!lead) return null;
    [, title, when, place] = lead;
  }
  if (!title || title.length > 90 || place.length > 90 || /取消|延期|改期|待定|改为|另行通知/u.test(text)) return null;
  const time = timedEvent(when, today);
  if (!time) return null;
  if (/我.{0,6}(?:没报名|未报名|取消报名|不参加)|请假不参加|无需参加/u.test(text)) return null;
  const registered = /我(?:已经|已)?报(?:过|了)名|我(?:已经|已)报名|我报名了/u.test(text);
  const notes = ["地点：" + place];
  if (time.range) notes.push("活动时段：" + time.range);
  if (registered) notes.push("已报名");
  const group = text.match(/[^。！？!\n]{0,30}(?:扫码入群|扫码加群)[^。！？!\n]{0,90}/u);
  if (group) notes.push(group[0]);
  const topic = text.match(/(?:聚焦|主题为|分享(?![：:]))\s*([^。！？!\n]{2,75}?)(?:，|。|；|$)/u);
  if (topic && !topic[1].includes("：")) notes.push("主题：" + topic[1]);
  return { title: registered ? "参加" + title : title, notes: notes.join("\n"),
    due: time.due, dueTime: time.dueTime, timeKind: "event", course: "校园活动" };
}

function changeOfDate(text, today) {
  const clauses = text.split(/[。！\n]/u).map(x => x.trim()).filter(Boolean);
  if (clauses.length !== 2 || !/照常|如常|正常进行/u.test(clauses[0])) return null;
  const move = clauses[1].match(/(?:安排|举行|举办)(?:集中的|一次|一场)?([^，,。]{2,36})[，,][^。]*?(?:调整到|改到|改至|推迟到|提前至)了?\s*(.+)$/u);
  if (!move || /可能|待定|拟调整|或许|取消/u.test(clauses[1])) return null;
  const original = eventDate(clauses[0], today), due = eventDate(move[2], today);
  if (!original || !due || [...move[2].matchAll(clock)].length) return null;
  const dateMatch = clauses[0].match(dateText);
  const existing = (clauses[0].slice(0, dateMatch.index).replace(/(?:这|本)?(?:周|星期)[一二三四五六日天][（(]?\s*$/u, "") + clauses[0].slice(dateMatch.index + dateMatch[0].length))
    .replace(/^[）)\s，,]+/u, "");
  if (!existing) return null;
  return { title: move[1].trim(), notes: original + " " + existing + "。\n" + move[1].trim() + "改至 " + due + "。",
    due, dueTime: "", timeKind: "event", course: "课程安排" };
}

// Availability windows in a service notice are not personal appointments.
// Keep eligibility and cost clauses verbatim, including optional/required wording.
function serviceNotice(text) {
  const sender = text.match(/^(?:转发|转)?([^：:，,\n]{2,28}?)通知[：:]/u)?.[1];
  const schedule = text.match(/([^\s。：:]{2,24})时间[：:]\s*([^。\n]+)/u);
  const place = field(text, "[^\s。：:]{0,16}地点");
  if (!sender || !schedule || !place || !/每周|均可|开放时段|工作日/u.test(schedule[2])) return null;
  const sentences = text.replace(/^[^：:]+[：:]/u, "").split(/[。\n]/u).map(s => s.trim()).filter(Boolean);
  const conditions = sentences.filter(s => /自愿|自费|免费|仅限|符合条件|须持|可预约/u.test(s) && !s.includes("时间："));
  if (!conditions.length) return null;
  const service = schedule[1].replace(sender, "");
  const object = conditions.join("。").match(/(?:免费|自费)(?:予以)?(?:接种|领取|办理|申领)([^，,。；;（）()]{2,18})/u)?.[1];
  const title = object ? object + service + "通知" : sender + service + "通知";
  const actions = sentences.filter(s => /携带|须带|请带|请扫|预约方式/u.test(s) && !conditions.includes(s));
  const audience = sentences.find(s => /请.{1,16}提醒.{1,25}/u.test(s));
  if (audience) actions.push(audience);
  return { title, content_type: "notice", course: "校园服务", due: "", dueTime: "",
    notes: [...conditions, "时间：" + schedule[2], "地点：" + place, ...actions].join("\n") };
}

export function summarizeQuickNotice(source, today) {
  const text = source.replace(/&#(?:x20|32);|&nbsp;/giu, " ").replace(/\\(?=[~\n\r])/gu, "")
    .replace(/^\s*@(?:所有人|全体成员)\s*/u, "").trim();
  // A single-event preview cannot own a separate assignment or several deadlines.
  if (/(?:任务|作业)\s*[一二三四五六七八九十\d]+\s*[：:、]/u.test(text) ||
      [...text.matchAll(/(?:截止|截至)\s*(?:时间|日期)?\s*[：:]/gu)].length > 1) return null;
  return examNotice(text, today) || changeOfDate(text, today) || poster(text, today) || serviceNotice(text);
}
