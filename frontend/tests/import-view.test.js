import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {updateImportView} from '../src/import-view.js';

function screen(){
 const dom=new JSDOM('<div id="app"></div>'),win=dom.window,root=win.document.querySelector('#app');
 win.scrollTo=({top,left=0})=>{win.scrollY=top;win.scrollX=left;};
 return {dom,win,root};
}
const card=(id)=>`<article class="import-review-card" data-candidate-card="${id}"><details data-import-part="original"><summary>查看原文</summary><pre>第一行\n第二行\n最后一行</pre></details><button data-agent-confirm="${id}">加入</button></article>`;
const page=(cards,progress='1 / 3')=>`<div class="capture-pages"><p role="status">${progress}</p><section id="import-review"><section class="current-import-section">${cards}</section></section></div>`;

test('new results retain open evidence, its scroll, focus and selected text',()=>{
 const {dom,win,root}=screen();root.innerHTML=page(card('one'));
 const article=root.querySelector('article'),details=root.querySelector('details'),pre=root.querySelector('pre'),button=root.querySelector('button');
 details.open=true;pre.scrollTop=70;button.focus();
 const range=win.document.createRange();range.setStart(pre.firstChild,4);range.setEnd(pre.firstChild,7);win.getSelection().addRange(range);
 const selected=win.getSelection().toString();
 updateImportView(root,page(card('one')+card('two'),'2 / 3'));
 assert.equal(root.querySelector('article'),article);assert.equal(root.querySelector('details'),details);
 assert.equal(details.open,true);assert.equal(pre.scrollTop,70);assert.equal(win.document.activeElement,button);
 assert.equal(win.getSelection().toString(),selected);
 assert.deepEqual([...root.querySelectorAll('article')].map(node=>node.dataset.candidateCard),['one','two']);dom.window.close();
});

test('progress-only changes compensate layout above the card being reviewed',()=>{
 const {dom,win,root}=screen();root.innerHTML=page(card('one'));win.scrollY=600;
 const article=root.querySelector('article');let offset=720;
 article.getBoundingClientRect=()=>({top:offset-win.scrollY,bottom:offset-win.scrollY+200});
 updateImportView(root,page(card('one'),'无候选的新进度'),()=>{offset+=80;});
 assert.equal(win.scrollY,680);assert.equal(article.getBoundingClientRect().top,120);dom.window.close();
});

test('finishing import or removing an earlier card preserves the surviving reading anchor',()=>{
 const {dom,win,root}=screen();root.innerHTML=page(card('one')+card('two'));win.scrollY=800;
 const article=root.querySelector('[data-candidate-card="two"]');let offset=900;
 article.getBoundingClientRect=()=>({top:offset-win.scrollY,bottom:offset-win.scrollY+240});
 article.querySelector('details').open=true;
 updateImportView(root,page(card('two'),'已全部导入'),()=>{offset-=150;});
 assert.equal(win.scrollY,650);assert.equal(root.querySelector('article'),article);assert.equal(article.querySelector('details').open,true);dom.window.close();
});

test('history and nested disclosures stay open as candidates refresh',()=>{
 const {dom,root}=screen();const markup=`<section id="import-review"><details data-import-history="old"><summary>历史</summary>${card('old')}</details></section>`;
 root.innerHTML=markup;root.querySelectorAll('details').forEach(node=>node.open=true);
 updateImportView(root,markup);
 assert.ok([...root.querySelectorAll('details')].every(node=>node.open));dom.window.close();
});

test('background updates preserve typed input and file controls while applying changed disabled state',()=>{
 const {dom,win,root}=screen();root.innerHTML='<textarea id="agent-input">原文</textarea><input id="source-file" type="file"><button id="submit">识别</button>';
 const input=root.querySelector('textarea'),file=root.querySelector('input');input.focus();input.value='正在补充的原文';input.setSelectionRange(3,5);
 updateImportView(root,'<textarea id="agent-input">原文</textarea><input id="source-file" type="file"><button id="submit" disabled>识别中</button>');
 assert.equal(win.document.activeElement,input);assert.equal(input.value,'正在补充的原文');assert.equal(input.selectionStart,3);
 assert.equal(root.querySelector('input'),file);assert.equal(root.querySelector('button').disabled,true);dom.window.close();
});
