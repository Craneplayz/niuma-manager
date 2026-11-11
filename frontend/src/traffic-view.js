export function renderTraffic(traffic) {
  if (!traffic) return '';
  const number = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
  const cards = [['游客数', traffic.guests], ['新增注册', traffic.registrations], ['回访次数', traffic.returnVisits]];
  return `<section class="cards traffic-cards" aria-label="访问与注册">${cards.map(([label, value]) =>
    `<article><span>${label}</span><strong>${number(value)}</strong></article>`).join('')}</section>
    <article><h2>每日访问与注册</h2><p class="empty">游客按匿名浏览器标识去重；回访指跨北京时间自然日再次访问，每个标识每天最多一次，登录用户按账号去重。当前已注册账号 ${number(traffic.registeredAccounts)} 个。</p>
    <div class="table-wrap"><table><thead><tr><th>日期</th><th>游客</th><th>新增注册</th><th>回访次数</th></tr></thead><tbody>${(traffic.daily || []).filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row.day)).map(row =>
      `<tr><td>${row.day}</td><td>${number(row.guests)}</td><td>${number(row.registrations)}</td><td>${number(row.returningVisitors)}</td></tr>`).join('')}</tbody></table></div></article>`;
}
