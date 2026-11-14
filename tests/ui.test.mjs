import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const html=readFileSync(new URL('../tz_audit/static/index.html',import.meta.url),'utf8');
const js=readFileSync(new URL('../tz_audit/static/app.js',import.meta.url),'utf8');
const model='deepseek/deepseek-v4.1-flash-1017';
const settings={has_key:true,model,models:[model]};
const tick=(ms=10)=>new Promise(r=>setTimeout(r,ms));
async function setup(handler,url='http://localhost:8000',before=()=>{}){
 const dom=new JSDOM(html,{url,runScripts:'outside-only'});
 const w=dom.window;
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 // jsdom не рисует: анимация счетчиков сразу доходит до конца, прокрутка ничего не делает.
 w.requestAnimationFrame=cb=>cb(w.performance.now()+1e6);
 w.scrollTo=()=>{};w.Element.prototype.scrollIntoView=function(){};
 w.fetch=async(path,options={})=>{const r=handler(path,options);const bad=r&&r.__status;return {ok:!bad,status:bad||200,json:async()=>bad?{detail:r.detail}:r};};
 before(w);
 w.eval(js+'\nwindow.testUI={preview(value){job=value;renderPreview();},result(value){report=value;renderTab();},progress:renderProgress,run,monitor};window.app={upload,updateReady,beginAnalysisView,tickProgress,stopLive,setJob(v){job=v;},get progress(){return progress;}};');await tick();return dom;
}
test('page loads only local assets and opens help',async()=>{
 const dom=await setup(()=>settings);const d=dom.window.document;
 assert.equal(d.querySelectorAll('script[src^="http"]').length,0);
 assert.equal(d.querySelectorAll('link[href^="http"]').length,0);
 assert.equal(d.getElementById('upload-view').hidden,false);
 d.getElementById('help-button').click();assert.equal(d.getElementById('help-dialog').open,true);
 dom.window.close();
});
test('unknown key state keeps paid analysis disabled',async()=>{
 const dom=await setup(()=>({has_key:false,model,models:[model]}));const d=dom.window.document;
 d.getElementById('privacy-approved').checked=true;d.getElementById('privacy-approved').dispatchEvent(new dom.window.Event('change'));
 assert.equal(d.getElementById('run-button').disabled,true);
 assert.match(d.getElementById('run-hint').textContent,/недоступна/);dom.window.close();
});
test('preview and model findings are rendered as text, never HTML',async()=>{
 const dom=await setup(()=>settings);
 dom.window.testUI.preview({blocks:1,redactions:{x:2},public:{blocks:[{id:'b0001',kind:'paragraph',text:'<img src=x onerror=alert(1)> ⟦PRIVATE_0001⟧'}]}});
 const d=dom.window.document;
 assert.equal(d.querySelector('#document-preview img'),null);
 assert.ok(d.querySelector('#document-preview').textContent.includes('<img'));
 assert.equal(d.querySelector('#document-preview .private-token').textContent,'скрыто');
 assert.equal(d.getElementById('redactions-count').textContent,'2');
 assert.ok(!d.querySelector('#document-preview').textContent.includes('b0001'),'служебные ID не показываются');
 dom.window.testUI.result({review:{findings:[{title:'<script>unsafe</script>',status:'needs_review',category:'other',severity:'low',anchors:[],explanation:'test',recommendation:'test'}]},changes:[],sources:[]});
 assert.equal(d.querySelector('#result-content script'),null);
 assert.ok(d.getElementById('result-content').textContent.includes('<script>unsafe</script>'));
 dom.window.close();
});
test('saved result URL opens report with readable download names',async()=>{
 const job='a'.repeat(32);
 const handler=path=>path==='/settings'?settings:path.endsWith('/status')?{status:'complete',events:[]}:path.endsWith('report.json')?{mode:'openrouter',stats:{applied_edits:2},review:{summary:'Проверка завершена',findings:[]},changes:[],sources:[]}:{job_id:job,blocks:0,redactions:{},public:{blocks:[]}};
 const dom=await setup(handler,'http://localhost:8000/?job='+job,w=>w.localStorage.setItem('tz-name-'+job,'Лифт.docx'));
 const d=dom.window.document;
 assert.equal(d.getElementById('result-view').hidden,false);
 assert.equal(d.getElementById('stat-edits').textContent,'2');
 assert.match(d.getElementById('download-description').textContent,/желтым/);
 const name=a=>decodeURIComponent(d.getElementById(a).getAttribute('href').split('?name=')[1]);
 assert.ok(d.getElementById('corrected-download').getAttribute('href').startsWith('/jobs/'+job+'/corrected.docx?name='));
 assert.equal(name('corrected-download'),'Лифт (исправленное)');
 assert.equal(name('report-download'),'Отчет проверки ТЗ Лифт');
 dom.window.close();
});
test('report name does not repeat "ТЗ" when it is already in the file name',async()=>{
 const job='e'.repeat(32);
 const handler=path=>path==='/settings'?settings:path.endsWith('/status')?{status:'complete',events:[]}:path.endsWith('report.json')?{mode:'offline',stats:{applied_edits:0},review:{summary:'ok',findings:[]},changes:[],sources:[]}:{job_id:job,blocks:0,redactions:{},public:{blocks:[]}};
 const dom=await setup(handler,'http://localhost:8000/?job='+job,w=>w.localStorage.setItem('tz-name-'+job,'ТЗ Лифт.docx'));
 const d=dom.window.document;
 assert.equal(decodeURIComponent(d.getElementById('report-download').getAttribute('href').split('?name=')[1]),'Отчет проверки ТЗ Лифт');
 assert.equal(d.getElementById('demo-notice').hidden,false);
 dom.window.close();
});
test('privacy selector offers installed profiles and explains choice',async()=>{
 const profiles=[{id:'ensemble',label:'Natasha + spaCy',available:true},{id:'rules',label:'Правила',available:true},{id:'spacy',label:'spaCy',available:false}];
 const dom=await setup(()=>({...settings,privacy_profiles:profiles,privacy_default:'ensemble'}));const d=dom.window.document;
 assert.equal(d.getElementById('privacy-backend').value,'ensemble');
 assert.equal(d.getElementById('privacy-backend').options.length,2);
 d.getElementById('privacy-backend').value='rules';d.getElementById('privacy-backend').dispatchEvent(new dom.window.Event('change'));
 assert.match(d.getElementById('privacy-hint').textContent,/Без распознавания имен/);dom.window.close();
});
test('progress deduplicates events, treats messages as text and disables cancellation',async()=>{
 const dom=await setup(()=>settings);const d=dom.window.document;
 const state={status:'running',stage:'Ожидаем модель',events:[{seq:1,time:'2026-11-12T10:00:00Z',message:'<img src=x onerror=alert(1)>'}]};
 dom.window.testUI.progress(state);dom.window.testUI.progress(state);
 assert.equal(d.querySelectorAll('#progress-log li').length,1);assert.equal(d.querySelector('#progress-log img'),null);
 dom.window.testUI.progress({...state,status:'cancelling'});assert.equal(d.getElementById('cancel-button').disabled,true);dom.window.close();
});
test('cancel button stops polling and restores the prepared document',async()=>{
 let cancelled=false;const id='b'.repeat(32);
 const dom=await setup(path=>path==='/settings'?settings:path.endsWith('/cancel')?(cancelled=true,{status:'cancelled',stage:'Отменено',events:[]}):path.endsWith('/status')?{status:cancelled?'cancelled':'running',stage:'Ожидаем модель',events:[]}:{status:'queued'});
 dom.window.testUI.preview({job_id:id,release_digest:'a'.repeat(64),blocks:0,redactions:{},public:{blocks:[]}});
 const running=dom.window.testUI.run('openrouter');await tick(20);
 dom.window.document.getElementById('cancel-button').click();await running;
 assert.ok(cancelled);assert.equal(dom.window.document.getElementById('preview-view').hidden,false);
 assert.match(dom.window.document.getElementById('alert').textContent,/остановлена/);dom.window.close();
});
test('selected proposals create a new version and coverage is visible',async()=>{
 const old='a'.repeat(32),next='b'.repeat(32);let approved;
 const base={mode:'openrouter',stats:{applied_edits:0},review:{summary:'Проверка завершена',findings:[],coverage:[{topic:'scope',result:'checked',note:'Проверен состав спецификации',block_ids:['b0001']}]},changes:[{id:'f1-r1',finding:1,block_id:'b0001',before:'(новая строка)',after:'Лифт',reason:'Нужно решение',status:'skipped',selectable:true}],sources:[]};
 const dom=await setup((path,options)=>{
  if(path==='/settings')return settings;
  if(path.endsWith('/status'))return {status:'complete',events:[]};
  if(path.endsWith('report.json'))return path.includes(next)?{...base,stats:{applied_edits:1},changes:[]} : base;
  if(path.endsWith('/proposals'))return {report_digest:'c'.repeat(64),changes:base.changes};
  if(path.endsWith('/apply-selected')){approved=JSON.parse(options.body);return {job_id:next};}
  return {job_id:path.includes(next)?next:old,blocks:0,redactions:{},public:{blocks:[]}};
 },'http://localhost:8000/?job='+old);
 const d=dom.window.document;
 d.querySelector('[data-tab="coverage"]').click();assert.match(d.getElementById('result-content').textContent,/состав спецификации/);
 d.querySelector('[data-tab="changes"]').click();assert.equal(d.getElementById('apply-selected').disabled,true);
 d.querySelector('.choice input').click();d.getElementById('apply-selected').click();await tick(30);
 assert.deepEqual(approved.change_ids,['f1-r1']);assert.equal(approved.report_digest,'c'.repeat(64));
 assert.ok(d.getElementById('corrected-download').getAttribute('href').startsWith('/jobs/'+next+'/corrected.docx'));
 assert.equal(d.getElementById('stat-edits').textContent,'1');dom.window.close();
});
test('demo login shows the code, signs in and saves the current job',async()=>{
 const calls=[];const job='d'.repeat(32);
 const dom=await setup((path,options)=>{calls.push([path,options.body&&JSON.parse(options.body)]);
  if(path==='/settings')return settings;
  if(path==='/me')return {email:null,mail_enabled:true,demo:true};
  if(path==='/auth/request')return {sent:true,demo_code:'123456'};
  if(path==='/auth/verify')return {email:'user@example.com'};
  if(path==='/my/jobs')return {ok:true};
  return {};
 });
 const d=dom.window.document;
 assert.equal(d.getElementById('account-name').textContent,'Гость');
 dom.window.testUI.preview({job_id:job,blocks:0,redactions:{},public:{blocks:[]}});
 d.getElementById('account-action').click();assert.equal(d.getElementById('login-dialog').open,true);
 d.getElementById('login-email').value='user@example.com';
 d.getElementById('login-email-form').dispatchEvent(new dom.window.Event('submit',{cancelable:true}));await tick();
 assert.match(d.getElementById('login-email-error').textContent,/согласие/);assert.ok(!calls.some(c=>c[0]==='/auth/request'),'без согласия код не запрашивается');
 d.getElementById('login-consent').checked=true;
 d.getElementById('login-email-form').dispatchEvent(new dom.window.Event('submit',{cancelable:true}));await tick();
 assert.equal(d.getElementById('login-code-form').hidden,false);
 assert.match(d.querySelector('.demo-code').textContent,/123456/);
 d.getElementById('login-code').value='123456';
 d.getElementById('login-code-form').dispatchEvent(new dom.window.Event('submit',{cancelable:true}));await tick();
 assert.equal(d.getElementById('login-dialog').open,false);
 assert.equal(d.getElementById('account-name').textContent,'user@example.com');
 assert.equal(d.getElementById('account-action').textContent,'Выйти');
 assert.deepEqual(calls.find(c=>c[0]==='/auth/verify')[1],{email:'user@example.com',code:'123456'});
 assert.equal(calls.find(c=>c[0]==='/my/jobs')[1].job_id,job);
 dom.window.close();
});
test('login errors are shown in the dialog',async()=>{
 const dom=await setup(path=>path==='/settings'?settings:path==='/me'?{email:null,mail_enabled:true}:path==='/auth/request'?{__status:400,detail:'Проверьте адрес почты.'}:{});
 const d=dom.window.document;
 d.getElementById('account-action').click();d.getElementById('login-email').value='bad';d.getElementById('login-consent').checked=true;
 d.getElementById('login-email-form').dispatchEvent(new dom.window.Event('submit',{cancelable:true}));await tick();
 assert.equal(d.getElementById('login-email-error').textContent,'Проверьте адрес почты.');
 assert.equal(d.getElementById('login-code-form').hidden,true);dom.window.close();
});
test('history lists saved checks as text and opens one',async()=>{
 const job='f'.repeat(32);
 const dom=await setup(path=>{
  if(path==='/settings')return settings;
  if(path==='/me')return {email:'user@example.com',mail_enabled:true};
  if(path==='/my/jobs')return {email:'user@example.com',jobs:[{job_id:job,filename:'<img src=x>Лифт.docx',created:1763100000,status:'complete',findings:5,high:2,edits:3}]};
  if(path.endsWith('/status'))return {status:'complete',events:[]};
  if(path.endsWith('report.json'))return {mode:'openrouter',stats:{applied_edits:3},review:{summary:'ok',findings:[]},changes:[],sources:[]};
  return {job_id:job,blocks:0,redactions:{},public:{blocks:[]}};
 });
 const d=dom.window.document;
 d.getElementById('nav-history').click();await tick();
 assert.equal(d.getElementById('history-view').hidden,false);
 const row=d.querySelector('.history-row');assert.ok(row);
 assert.equal(d.querySelector('#history-content img'),null);
 assert.match(row.textContent,/<img src=x>Лифт/);assert.match(row.textContent,/Готово/);
 row.click();await tick(20);
 assert.equal(d.getElementById('result-view').hidden,false);
 assert.equal(dom.window.location.search,'?job='+job);
 dom.window.close();
});
test('history asks guests to sign in',async()=>{
 const dom=await setup(path=>path==='/me'?{email:null,mail_enabled:true}:settings);const d=dom.window.document;
 d.getElementById('nav-history').click();await tick();
 assert.match(d.getElementById('history-content').textContent,/Войдите по почте/);dom.window.close();
});

test('upload is locked until the user accepts the terms; choice is remembered',async()=>{
 const calls=[];
 const dom=await setup(path=>{calls.push(path);return path==='/settings'?{...settings,retention_hours:24}:{};});
 const d=dom.window.document,w=dom.window;
 assert.ok(d.getElementById('dropzone').classList.contains('locked'));
 assert.equal(d.getElementById('file-input').disabled,true);
 assert.match(d.getElementById('retention-text').textContent,/24 часа/);
 w.app.upload(new w.File(['x'],'a.docx'));await tick();
 assert.match(d.getElementById('alert').textContent,/согласие/);assert.ok(!calls.includes('/jobs'));
 d.getElementById('terms-consent').checked=true;d.getElementById('terms-consent').dispatchEvent(new w.Event('change'));
 assert.ok(!d.getElementById('dropzone').classList.contains('locked'));
 assert.equal(w.localStorage.getItem('tz-terms'),'1');
 assert.ok(d.querySelector('a[href="/static/terms.html"]')&&d.querySelector('a[href="/static/privacy.html"]'));
 dom.window.close();
});
test('result shows the disclaimer; files can be deleted',async()=>{
 const job='c'.repeat(32);let deleted=false;
 const dom=await setup((path,options)=>{
  if(path==='/settings')return settings;
  if(path.endsWith('/status'))return {status:'complete',events:[]};
  if(path.endsWith('report.json'))return {mode:'openrouter',stats:{applied_edits:0},review:{summary:'ok',findings:[]},changes:[],sources:[]};
  if(path==='/jobs/'+job&&options.method==='DELETE'){deleted=true;return {deleted:true};}
  return {job_id:job,blocks:0,redactions:{},public:{blocks:[]}};
 },'http://localhost:8000/?job='+job);
 const d=dom.window.document;dom.window.confirm=()=>true;
 assert.match(d.querySelector('#result-view .disclaimer').textContent,/не юридическое заключение/);
 d.getElementById('delete-files').click();await tick(20);
 assert.ok(deleted);assert.equal(d.getElementById('upload-view').hidden,false);
 assert.match(d.getElementById('save-note').textContent,/удалены/);
 dom.window.close();
});
test('preview shows the per-file time estimate',async()=>{
 const dom=await setup(()=>settings);const d=dom.window.document;
 dom.window.testUI.preview({job_id:'a'.repeat(32),blocks:1,redactions:{},estimate_seconds:{openrouter:330,offline:4},public:{blocks:[{id:'b0001',kind:'paragraph',text:'ТЗ'}]}});
 dom.window.app.updateReady();
 assert.equal(d.getElementById('estimate-hint').hidden,false);
 assert.match(d.getElementById('estimate-hint').textContent,/~6 мин/);
 assert.match(d.getElementById('estimate-hint').textContent,/меньше минуты/);
 dom.window.close();
});
test('progress grows evenly by time and the live feed describes real events',async()=>{
 const dom=await setup(()=>settings);const d=dom.window.document,w=dom.window;
 w.app.setJob({job_id:'a'.repeat(32),estimate_seconds:{openrouter:100,offline:4},public:{blocks:[]}});w.app.beginAnalysisView('t','openrouter');const pr=()=>w.app.progress;
 // 25 секунд из прогнозных 100: около четверти полосы.
 pr().started=Date.now()-25000;pr().last=pr().started;w.app.tickProgress();
 const p1=+d.getElementById('progress-percent').textContent;assert.ok(p1>=20&&p1<=28,'p1='+p1);
 assert.match(d.getElementById('remaining').textContent,/Осталось ~1 мин/);
 pr().started-=25000;pr().last-=25000;w.app.tickProgress();
 const p2=+d.getElementById('progress-percent').textContent;assert.ok(p2>p1&&p2<=55,'p2='+p2);
 // Время вышло: полоса не доходит до 100 и не стоит, пишем «Почти готово».
 pr().started-=200000;pr().last=Date.now()-1000;w.app.tickProgress();
 const p3=+d.getElementById('progress-percent').textContent;assert.ok(p3>p2&&p3<100,'p3='+p3);
 assert.equal(d.getElementById('remaining').textContent,'Почти готово');
 const ev=(seq,message)=>({seq,time:'2026-11-14T10:00:00Z',message});
 w.testUI.progress({status:'running',events:[ev(1,'Загружаем обезличенный документ'),ev(2,'Шаг 3/12: проверяем исходящие данные'),ev(3,'Шаг 3: ожидаем ответ модели'),ev(4,'Ищем нормативные источники'),ev(5,'Ищем нормативные источники'),ev(6,'Первый проход завершён. Перепроверяем полноту по семи темам')]});
 assert.equal(d.getElementById('now-title').textContent,'Первый проход завершен');
 assert.equal(d.getElementById('count-steps').textContent,'3');assert.equal(d.getElementById('count-sources').textContent,'2');
 const feed=[...d.querySelectorAll('#feed li b')].map(b=>b.textContent);
 assert.deepEqual(feed,['Первый проход завершен','Ищем нормативные основания','Модель читает документ','Документ передан агенту']);
 assert.equal(d.querySelectorAll('#topics .chip.on').length,7);
 w.app.stopLive();dom.window.close();
});

test('top call-to-action scrolls to consent first, then to the upload block',async()=>{
 const dom=await setup(()=>settings);const d=dom.window.document,w=dom.window;let scrolled=[];
 w.Element.prototype.scrollIntoView=function(){scrolled.push(this.id||this.className);};
 const btn=d.getElementById('start-check');assert.equal(btn.textContent.trim(),'Запустить проверку ТЗ');
 assert.ok(btn.compareDocumentPosition(d.getElementById('dropzone'))&w.Node.DOCUMENT_POSITION_FOLLOWING);
 btn.click();assert.match(scrolled.pop(),/legal-card/);
 d.getElementById('terms-consent').checked=true;btn.click();assert.equal(scrolled.pop(),'dropzone');
 assert.ok(d.getElementById('dropzone').classList.contains('flash'));dom.window.close();
});

test('with real mail: password login, and code login offers to set a password',async()=>{
 const calls=[];
 const dom=await setup((path,options)=>{const body=options.body&&JSON.parse(options.body);calls.push([path,body]);
  if(path==='/settings')return settings;
  if(path==='/me')return {email:null,mail_enabled:true,demo:false,password_enabled:true,has_password:false};
  if(path==='/auth/password')return body.password==='correct horse'?{email:'u@example.com',has_password:true}:{__status:400,detail:'Неверная почта или пароль.'};
  if(path==='/auth/request')return {sent:true};
  if(path==='/auth/verify')return {email:'u@example.com',has_password:false};
  if(path==='/auth/set-password')return body.password.length>=8?{email:'u@example.com',has_password:true}:{__status:400,detail:'Пароль должен быть длиной от 8 до 128 символов.'};
  return {};
 });
 const d=dom.window.document,w=dom.window,submit=id=>d.getElementById(id).dispatchEvent(new w.Event('submit',{cancelable:true}));
 d.getElementById('account-action').click();
 assert.equal(d.getElementById('login-password-row').hidden,false);assert.equal(d.getElementById('login-by-code').hidden,false);
 assert.equal(d.getElementById('login-submit-label').textContent,'Войти');
 d.getElementById('login-consent').checked=true;d.getElementById('login-email').value='u@example.com';
 d.getElementById('login-password').value='wrong pass';submit('login-email-form');await tick();
 assert.equal(d.getElementById('login-email-error').textContent,'Неверная почта или пароль.');
 d.getElementById('login-password').value='correct horse';submit('login-email-form');await tick();
 assert.equal(d.getElementById('login-dialog').open,false);assert.equal(d.getElementById('account-name').textContent,'u@example.com');
 assert.ok(!calls.some(c=>c[0]==='/auth/request'),'при входе по паролю письмо не запрашивается');
 // Выход и вход по коду: предлагается задать пароль, без демо-кода на экране.
 d.getElementById('account-action').click();await tick();d.getElementById('account-action').click();
 d.getElementById('login-email').value='u@example.com';d.getElementById('login-by-code').click();await tick();
 assert.equal(d.getElementById('login-code-form').hidden,false);assert.equal(d.querySelector('.demo-code'),null);
 assert.match(d.getElementById('login-code-hint').textContent,/Спам/);
 d.getElementById('login-code').value='123456';submit('login-code-form');await tick();
 assert.equal(d.getElementById('login-password-form').hidden,false);assert.equal(d.getElementById('login-dialog').open,true);
 d.getElementById('new-password').value='short';submit('login-password-form');await tick();
 assert.match(d.getElementById('set-password-error').textContent,/от 8/);
 d.getElementById('new-password').value='new secure pass';submit('login-password-form');await tick();
 assert.equal(d.getElementById('login-dialog').open,false);
 assert.deepEqual(calls.filter(c=>c[0]==='/auth/set-password').pop()[1],{password:'new secure pass'});
 assert.match(d.getElementById('save-note').textContent,/Пароль сохранен/);
 dom.window.close();
});
test('demo mode hides the password field and skips the set-password step',async()=>{
 const dom=await setup(path=>path==='/settings'?settings:path==='/me'?{email:null,mail_enabled:true,demo:true,password_enabled:false}:path==='/auth/request'?{sent:true,demo_code:'111222'}:path==='/auth/verify'?{email:'u@example.com',has_password:false}:{});
 const d=dom.window.document,w=dom.window;
 d.getElementById('account-action').click();
 assert.equal(d.getElementById('login-password-row').hidden,true);assert.equal(d.getElementById('login-submit-label').textContent,'Получить код');
 d.getElementById('login-consent').checked=true;d.getElementById('login-email').value='u@example.com';
 d.getElementById('login-email-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 d.getElementById('login-code').value='111222';d.getElementById('login-code-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 assert.equal(d.getElementById('login-dialog').open,false);assert.equal(d.getElementById('login-password-form').hidden,true);
 dom.window.close();
});
