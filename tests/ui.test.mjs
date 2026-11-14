import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const html=readFileSync(new URL('../tz_audit/static/index.html',import.meta.url),'utf8');
const js=readFileSync(new URL('../tz_audit/static/app.js',import.meta.url),'utf8');
const model='deepseek/deepseek-v4.1-flash-1017';
async function setup(handler,url='http://localhost:8000'){
 const dom=new JSDOM(html,{url,runScripts:'outside-only'});
 dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
 dom.window.fetch=async(path,options={})=>({ok:true,json:async()=>handler(path,options)});
 dom.window.eval(js+'\nwindow.testUI={preview(value){job=value;renderPreview();},result(value){report=value;renderTab();},progress:renderProgress,run,monitor};');await new Promise(r=>setTimeout(r,10));return dom;
}
test('selected model is shown, key is not requested again, local assets only',async()=>{
 const dom=await setup(()=>({has_key:true,model,models:[model]}));const d=dom.window.document;
 assert.equal(d.getElementById('model').value,model);
 assert.equal(d.getElementById('key-button-label').textContent,'Ключ добавлен');
 assert.equal(d.querySelectorAll('script[src^="https:"]').length,0);
 assert.equal(d.querySelectorAll('link[href^="https:"]').length,0);
 d.getElementById('settings-top').click();assert.equal(d.getElementById('settings-dialog').open,true);
 dom.window.close();
});
test('unknown key state keeps paid analysis disabled',async()=>{
 const dom=await setup(()=>({has_key:false,model,models:[model]}));const d=dom.window.document;
 d.getElementById('privacy-approved').checked=true;d.getElementById('privacy-approved').dispatchEvent(new dom.window.Event('change'));
 assert.equal(d.getElementById('run-button').disabled,true);dom.window.close();
});
test('Kimi provider is visible and changing model updates the route hint',async()=>{
 const kimi='moonshotai/kimi-k3.1-pro';
 const dom=await setup(()=>({has_key:true,model:kimi,models:[kimi,model],model_routes:{[kimi]:{label:'Fireworks · фиксированный провайдер'},[model]:{label:'Маршрутизация OpenRouter'}}}));
 const d=dom.window.document;
 assert.equal(d.getElementById('model').value,kimi);
 assert.match(d.getElementById('model-route').textContent,/Fireworks/);
 d.getElementById('model').value=model;d.getElementById('model').dispatchEvent(new dom.window.Event('change'));
 assert.equal(d.getElementById('model-route').textContent,'Маршрутизация OpenRouter');
 dom.window.close();
});
test('preview and model findings are rendered as text, never HTML',async()=>{
 const dom=await setup(()=>({has_key:true,model,models:[model]}));
 dom.window.testUI.preview({blocks:1,redactions:{},public:{blocks:[{id:'b0001',kind:'paragraph',text:'<img src=x onerror=alert(1)> ⟦PRIVATE_0001⟧'}]}});
 const d=dom.window.document;
 assert.equal(d.querySelector('#document-preview img'),null);
 assert.ok(d.querySelector('#document-preview').textContent.includes('<img'));
 assert.ok(d.querySelector('#document-preview').textContent.includes('[скрыто]'));
 dom.window.testUI.result({review:{findings:[{title:'<script>unsafe</script>',status:'needs_review',category:'other',anchors:[],explanation:'test',recommendation:'test'}]},changes:[],sources:[]});
 assert.equal(d.querySelector('#result-content script'),null);
 dom.window.close();
});


test('saved result URL opens report and working download links',async()=>{
 const job='a'.repeat(32);
 const dom=await setup(path=>path==='/settings'?{has_key:true,model,models:[model]}:path.endsWith('/status')?{status:'complete',events:[]}:path.endsWith('report.json')?{mode:'openrouter',model:'moonshotai/kimi-k3.1-pro',stats:{applied_edits:2,providers_used:['DeepInfra']},review:{summary:'Проверка завершена',findings:[]},changes:[],sources:[]}:{job_id:job,blocks:0,redactions:{},public:{blocks:[]}},'http://localhost:8000/?job='+job);
 const d=dom.window.document;
 assert.equal(d.getElementById('result-view').hidden,false);
 assert.equal(d.getElementById('stat-edits').textContent,'2');
 assert.equal(d.getElementById('result-model').textContent,'Kimi K3.1 Pro · DeepInfra');
 assert.equal(d.getElementById('corrected-download').getAttribute('href'),'/jobs/'+job+'/corrected.docx');
 dom.window.close();
});


test('privacy selector offers installed profiles and explains choice',async()=>{
 const profiles=[{id:'ensemble',label:'Natasha + spaCy',available:true,description:'Два локальных NER'},{id:'rules',label:'Правила',available:true,description:'Возможны пропуски имён'}];
 const dom=await setup(()=>({has_key:true,model,models:[model],privacy_profiles:profiles,privacy_default:'ensemble'}));const d=dom.window.document;
 assert.equal(d.getElementById('privacy-backend').value,'ensemble');
 d.getElementById('privacy-backend').value='rules';d.getElementById('privacy-backend').dispatchEvent(new dom.window.Event('change'));
 assert.match(d.getElementById('privacy-hint').textContent,/пропуски/);dom.window.close();
});
test('progress deduplicates events, treats messages as text and disables cancellation',async()=>{
 const dom=await setup(()=>({has_key:true,model,models:[model]}));const d=dom.window.document;
 const state={status:'running',stage:'Ожидаем модель',events:[{seq:1,time:'2026-11-12T10:00:00Z',message:'<img src=x onerror=alert(1)>'}]};
 dom.window.testUI.progress(state);dom.window.testUI.progress(state);
 assert.equal(d.querySelectorAll('#progress-log li').length,1);assert.equal(d.querySelector('#progress-log img'),null);
 dom.window.testUI.progress({...state,status:'cancelling'});assert.equal(d.getElementById('cancel-button').disabled,true);dom.window.close();
});

test('cancel button stops polling and restores the prepared document',async()=>{
 let cancelled=false;const id='b'.repeat(32);
 const dom=await setup(path=>path==='/settings'?{has_key:true,model,models:[model]}:path.endsWith('/cancel')?(cancelled=true,{status:'cancelled',stage:'Отменено',events:[]}):path.endsWith('/status')?{status:cancelled?'cancelled':'running',stage:'Ожидаем модель',events:[]}:{status:'queued'});
 dom.window.testUI.preview({job_id:id,release_digest:'a'.repeat(64),blocks:0,redactions:{},public:{blocks:[]}});
 const running=dom.window.testUI.run('openrouter');await new Promise(r=>setTimeout(r,20));
 dom.window.document.getElementById('cancel-button').click();await running;
 assert.ok(cancelled);assert.equal(dom.window.document.getElementById('preview-view').hidden,false);
 assert.match(dom.window.document.getElementById('alert').textContent,/отменена/);dom.window.close();
});

test('selected proposals create a new version and coverage is visible',async()=>{
 const old='a'.repeat(32),next='b'.repeat(32);let approved;
 const base={mode:'openrouter',stats:{applied_edits:0},review:{summary:'Проверка завершена',findings:[],coverage:[{topic:'scope',result:'checked',note:'Проверен состав спецификации',block_ids:['b0001']}]},changes:[{id:'f1-r1',finding:1,block_id:'b0001',before:'(новая строка)',after:'Лифт',reason:'Нужно решение',status:'skipped',selectable:true}],sources:[]};
 const dom=await setup((path,options)=>{
  if(path==='/settings')return {has_key:true,model,models:[model]};
  if(path.endsWith('/status'))return {status:'complete',events:[]};
  if(path.endsWith('report.json'))return path.includes(next)?{...base,stats:{applied_edits:1},changes:[]} : base;
  if(path.endsWith('/proposals'))return {report_digest:'c'.repeat(64),changes:base.changes};
  if(path.endsWith('/apply-selected')){approved=JSON.parse(options.body);return {job_id:next};}
  return {job_id:path.includes(next)?next:old,blocks:0,redactions:{},public:{blocks:[]}};
 },'http://localhost:8000/?job='+old);
 const d=dom.window.document;
 d.querySelector('[data-tab="coverage"]').click();assert.match(d.getElementById('result-content').textContent,/состав спецификации/);
 d.querySelector('[data-tab="changes"]').click();assert.equal(d.getElementById('apply-selected').disabled,true);
 d.querySelector('.proposal-choice input').click();d.getElementById('apply-selected').click();await new Promise(r=>setTimeout(r,30));
 assert.deepEqual(approved.change_ids,['f1-r1']);assert.equal(approved.report_digest,'c'.repeat(64));
 assert.equal(d.getElementById('corrected-download').getAttribute('href'),'/jobs/'+next+'/corrected.docx');
 assert.equal(d.getElementById('stat-edits').textContent,'1');dom.window.close();
});
