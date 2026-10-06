let theme='auto';
const system=()=>matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
function apply(){const resolved=theme==='auto'?system():theme;document.documentElement.dataset.theme=resolved;document.documentElement.style.colorScheme=resolved;}
export function initTheme(){theme='auto';if(!['auto','light','dark'].includes(theme))theme='auto';apply();matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change',()=>{if(theme==='auto')apply();});}
export function themeButton(){const dark=document.documentElement.dataset.theme==='dark';return `<button class="icon-btn theme-toggle" data-theme-toggle aria-label="${dark?'切换到浅色模式':'切换到夜间模式'}" title="${dark?'浅色模式':'夜间模式'}"><span aria-hidden="true">${dark?'☀':'☾'}</span></button>`;}
export function handleThemeClick(button){if(!button.hasAttribute('data-theme-toggle'))return false;theme=document.documentElement.dataset.theme==='dark'?'light':'dark';apply();button.outerHTML=themeButton();return true;}
export function themeSetting(){return `<div class="theme-setting"><h2>外观</h2><label><span class="sr-only">外观模式</span><select id="theme-setting"><option value="auto" ${theme==='auto'?'selected':''}>跟随系统</option><option value="light" ${theme==='light'?'selected':''}>浅色</option><option value="dark" ${theme==='dark'?'selected':''}>夜间</option></select></label></div>`;}
export function handleThemeChange(event){if(event.target.id!=='theme-setting')return false;theme=event.target.value;apply();return true;}
