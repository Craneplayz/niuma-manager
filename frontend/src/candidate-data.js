export const zonedParts = (value) =>
  Object.fromEntries(
    new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(value)
      .map((part) => [part.type, part.value]),
  );


export function candidateDue(deadline = {}) {
  if (
    deadline.kind !== "deadline" ||
    !["explicit", "inferred"].includes(deadline.status) ||
    !deadline.value
  )
    return { date: "", time: "" };
  if (deadline.precision === "date") return { date: deadline.value, time: "" };
  if (
    deadline.precision !== "datetime" ||
    !Number.isFinite(Date.parse(deadline.value))
  )
    return { date: "", time: "" };
  const value = new Date(deadline.value),
    p = zonedParts(value);
  return {
    date: `${p.year}-${p.month}-${p.day}`,
    time:
      value.getUTCSeconds() || value.getUTCMilliseconds()
        ? ""
        : `${p.hour}:${p.minute}`,
  };
}
