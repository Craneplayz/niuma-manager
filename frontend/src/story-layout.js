import {icon} from './icons.js';

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const chapters=[
 {number:'01',title:'接住信息',hint:'通知导入',route:'agent',pages:['agent']},
 {number:'02',title:'看清事情',hint:'任务 · AI 拆解',route:'tasks',pages:['tasks','decompose','trash','book']},
 {number:'03',title:'腾出时间',hint:'规划 · 日历',route:'plan',pages:['plan','calendar']},
];
const sections={
 tasks:[['tasks','任务中心'],['decompose','AI 拆解'],['book','课程与资料'],['trash','回收站']],
 plan:[['plan','智能规划'],['calendar','学习日程']],
};
const stories={
 overview:{kicker:'今天 / 你的学习空间',title:['今晚，从容地','做一点。'],lead:'先接住通知，再看清任务，最后为它留一段时间。'},
 agent:{kicker:'01 / 通知导入',title:['把散落的消息，','先接在一起。'],lead:'教学网的作业、群里的提醒、随手记下的事。给一段原文，留下真正需要做的事。'},
 tasks:{kicker:'02 / 任务中心',title:['每一件事，','都有下一步。'],lead:'看清轻重、截止与进度。把想做的事，变成一步步的完成。'},
 decompose:{kicker:'02 / AI 任务拆解',title:['一件大事，','从一小步开始。'],lead:'先看清要交什么，再决定从哪里开始。AI 给出步骤与完成标准，由你核对确认。'},
 plan:{kicker:'03 / 智能规划',title:['让计划，落在','真正有空的时候。'],lead:'给出你的可用时间，看看任务怎么安排。确认喜欢的方案，再写入学习日历。'},
 calendar:{kicker:'03 / 学习日程',title:['给每一步，','留一段时间。'],lead:'截止日期与学习安排，在同一条时间线上。需要调整时，随时改变节奏。'},
 book:{kicker:'02 / 课程与资料',title:['学过的东西，','在这里接起来。'],lead:'课程、任务、参考资料与笔记，围绕同一门课放在一起。'},
 trash:{kicker:'02 / 回收站',title:['暂时放下，','也留一条回来的路。'],lead:'恢复任务时一并找回进度与学习安排；确定不再需要，再彻底删除。'},
 settings:{kicker:'你的空间 / 账号与数据',title:['按你的习惯，','照顾好这片空间。'],lead:'账号、通知来源、外观与完成反馈，都在这里管理。'},
};

export function pageIntro(page,actions=''){
 const story=stories[page]||stories.overview;
 return `<header class="story-intro"><div class="story-intro-copy"><span class="story-kicker">${story.kicker}</span><h1>${story.title.map(esc).join('<br>')}</h1><p>${esc(story.lead)}</p></div>${actions?`<div class="story-intro-actions">${actions}</div>`:''}</header>`;
}

export function storyShell({page,user,saveLabel,saveAlert,content,focus=''}){
 const chapter=chapters.find(item=>item.pages.includes(page));
 const local=chapter?.route==='tasks'?sections.tasks:chapter?.route==='plan'?sections.plan:[];
 const day=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'long',day:'numeric',weekday:'long'}).format(new Date());
 return `<div class="story-shell" data-page="${esc(page)}"><header class="topbar story-masthead"><a href="#overview" class="story-brand" aria-label="牛马管理器，今日概览">牛马<span>管理器</span><i aria-hidden="true"></i></a><a href="#overview" class="story-today ${page==='overview'?'selected':''}">今日</a><div class="story-tools"><button class="icon-btn" data-experience="search" aria-label="搜索任务与页面">${icon('search')}</button><button class="icon-btn" data-experience="focus" aria-label="打开专注计时">${icon('clock')}</button><button class="icon-btn notification" data-action="notifications" aria-label="查看到期提醒">${icon('bell')}</button>${user?`<a class="story-account" href="#settings" aria-label="${esc(user.display_name)}，账号与数据"><span class="avatar">${esc(user.display_name?.slice(0,1)||'我')}</span><span>${esc(user.display_name)}</span></a>`:'<button class="btn" data-action="login">登录 / 注册</button>'}<a class="icon-btn" href="#settings" aria-label="账号、通知来源与数据设置">${icon('settings')}</a></div></header>
 <nav class="story-chapters" aria-label="学习旅程">${chapters.map(item=>`<a href="#${item.route}" class="story-chapter ${chapter===item?'selected':''}" ${chapter===item?'aria-current="true"':''}><span class="story-chapter-number">${item.number}</span><span>${item.title}<small>${item.hint}</small></span></a>`).join('<span class="story-chapter-line" aria-hidden="true"></span>')}<div class="story-day">${esc(day)}<span id="save-status">${esc(saveLabel)}</span></div></nav>
 ${local.length?`<nav class="story-section-nav" aria-label="本章节功能">${local.map(([id,label])=>`<a href="#${id}" ${page===id?'class="selected" aria-current="page"':''}>${label}</a>`).join('')}</nav>`:''}
 <main class="story-main"><div class="content"><div id="save-alert">${saveAlert}</div>${content}</div></main>
 <footer class="story-footer"><span>${chapter?['散落的信息 → 一件清楚的事','一件清楚的事 → 几个能开始的步骤','几个小步骤 → 日历里的真实时间'][chapters.indexOf(chapter)]:'照顾好今天，也留一点时间给自己。'}</span><div><a href="#book">课程与资料</a><a href="#trash">回收站</a><a href="#settings">账号与来源</a><button data-action="notifications">到期提醒</button><button data-action="calendar-export">导出日历</button><button data-action="export">数据备份</button></div></footer>${focus}</div>`;
}

export function journeyHome(state,user){
 const tasks=state.tasks.filter(task=>task.status!=='done');
 const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const schedule=state.schedule.filter(block=>block.date===today).sort((a,b)=>a.start-b.start);
 const time=value=>`${String(Math.floor(value)).padStart(2,'0')}:${String(Math.round(value%1*60)).padStart(2,'0')}`;
 const next=[...tasks].sort((a,b)=>(a.due||'9999').localeCompare(b.due||'9999'))[0];
 return `${pageIntro('overview',user?'<a class="btn primary" href="#agent">接住一条新通知 '+icon('arrow')+'</a>':'<button class="btn primary" data-action="login">打开我的学习空间 '+icon('arrow')+'</button>')}
 <div class="journey-home"><section class="journey-next"><span class="story-kicker">先做这一件 / 任务</span>${next?`<button class="journey-task-title" data-edit="${esc(next.id)}">${esc(next.title)}</button><p>${esc(next.course||'未分类')} · ${next.due?`截止 ${esc(next.due)}${next.dueTime?' '+esc(next.dueTime):' · 时刻待补'}`:'尚未设置截止时间'}</p><div class="journey-task-actions"><button class="btn" data-experience="focus">${icon('play')}开始专注</button><a href="#tasks">查看 ${tasks.length} 项待办 ${icon('arrow')}</a></div>`:'<h2>给今天，留一个小目标。</h2><p>从一条通知开始，或亲手新建第一项任务。</p><button class="btn" data-action="new">'+icon('plus')+'新建任务</button>'}</section>
 <section class="journey-schedule"><span class="story-kicker">留给学习的时间 / 今日安排</span>${schedule.length?schedule.map(block=>`<button class="journey-time" data-schedule-id="${esc(block.id)}" data-schedule-task="${esc(block.taskId)}"><time>${time(block.start)}<span>${time(block.start+block.hours)}</span></time><span><strong>${esc(block.label||state.tasks.find(task=>task.id===block.taskId)?.title||'学习安排')}</strong><small>${Math.round(block.hours*60)} 分钟</small></span></button>`).join(''):'<h2>今晚，想从哪里开始？</h2><p>在真正有空的时刻，给任务留出位置。</p><a class="story-link" href="#plan">安排一段学习时间 '+icon('arrow')+'</a>'}</section></div>
 <div class="journey-links"><a href="#agent"><em>01</em><div><strong>接住信息</strong><span>从原文中找到行动与截止。</span></div>${icon('arrow')}</a><a href="#decompose"><em>02</em><div><strong>看清事情</strong><span>把复杂作业拆成能开始的小步骤。</span></div>${icon('arrow')}</a><a href="#calendar"><em>03</em><div><strong>腾出时间</strong><span>把每一步安放进学习日历。</span></div>${icon('arrow')}</a></div>`;
}
