'use strict';
const $ = id => document.getElementById(id);
let settings = {}, job = null, report = null, filename = '', redactBlock = null, activeTab = 'findings', busy = false;
let timer = null, lastEvent = 0;
const selectedChanges = new Set();
const coverageLabels = {object:'Предмет и заголовки',numbers:'Числа и размеры',scope:'Комплектность и спецификация',warranty_acceptance:'Гарантия и приёмка',classification:'Коды товаров и работ',standards:'Нормативные ссылки',language:'Опечатки и грамматика'};
const categories = {contradiction:'Противоречия',competition:'Конкуренция',characteristics:'Характеристики',classification:'ОКПД 2',warranty:'Гарантия',standards:'Нормативные документы',completeness:'Комплектность',acceptance:'Приёмка',other:'Прочее'};
const modelLabels = {'deepseek/deepseek-v4.1-flash-1017':'DeepSeek V4.1 Flash 1017','moonshotai/kimi-k2.5':'Kimi K2.5','moonshotai/kimi-k3.1-pro':'Kimi K3.1 Pro','deepseek/deepseek-v3.2':'DeepSeek V3.2'};
function updateModelRoute(){ $('model-route').textContent=settings.model_routes?.[$('model').value]?.label || 'Выбранная модель работает через OpenRouter в ограниченной оболочке.'; }
$('model').onchange=updateModelRoute;
function el(tag, className, text) { const node=document.createElement(tag); if(className)node.className=className;if(text!==undefined)node.textContent=text;return node; }
async function api(path, options={}) {
  const response = await fetch(path, {credentials:'same-origin',...options});
  let body; try { body=await response.json(); } catch { throw new Error('Сервер вернул неожиданный ответ. Обновите страницу.'); }
  if(!response.ok) throw new Error(body.detail || 'Запрос не выполнен. Попробуйте ещё раз.');
  return body;
}
const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function alertMessage(message='') { $('alert').textContent=message;$('alert').hidden=!message; }
function view(name,step) {
  for(const id of ['upload','loading','preview','result'])$(id+'-view').hidden=id!==name;
  for(const item of document.querySelectorAll('[data-step]')){const n=Number(item.dataset.step);item.classList.toggle('current',n===step);item.classList.toggle('done',n<step);}
}
function updateReady(){
 for(const id of ['new-document','replace-file','nav-documents'])$(id).disabled=busy;
 $('key-button-label').textContent=settings.has_key?'Ключ добавлен':'Добавить API-ключ';
 $('sidebar-key-status').textContent=settings.has_key?'Ключ настроен':'Ожидает API-ключ';
 document.querySelector('.engine-status').classList.toggle('ready',!!settings.has_key);
 $('run-button').disabled=busy||!settings.has_key||!$('privacy-approved').checked;
 $('run-hint').textContent=!settings.has_key?'Для анализа добавьте ключ OpenRouter.':!$('privacy-approved').checked?'Подтвердите, что в тексте нет закрытых данных.':'Будет использована выбранная модель. Исходный файл останется локально.';
}
function showSettings(){ $('api-key').value='';$('settings-error').textContent='';$('settings-dialog').showModal(); }
$('settings-top').onclick=showSettings;$('settings-nav').onclick=showSettings;
$('help-button').onclick=()=>$('help-dialog').showModal();
for(const button of document.querySelectorAll('[data-close]')) button.onclick=()=>$(button.dataset.close).close();
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
$('settings-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button[type=submit]');button.disabled=true;try{await post('/settings',{api_key:$('api-key').value.trim()});$('api-key').value='';settings.has_key=true;updateReady();$('settings-dialog').close();}catch(error){$('settings-error').textContent=error.message;}finally{button.disabled=false;}};
function reset(){if(busy)return;history.replaceState(null,'','/');job=null;report=null;filename='';$('file-input').value='';$('progress-panel').hidden=true;$('progress-log').replaceChildren();lastEvent=0;$('privacy-approved').checked=false;$('preview-search').value='';alertMessage();view('upload',0);}
$('new-document').onclick=reset;$('replace-file').onclick=reset;$('nav-documents').onclick=reset;
$('privacy-approved').onchange=updateReady;
$('file-input').addEventListener('change',e=>{if(e.target.files[0])upload(e.target.files[0]);});
for(const event of ['dragenter','dragover'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.add('drag-over');});
for(const event of ['dragleave','drop'])$('dropzone').addEventListener(event,e=>{e.preventDefault();$('dropzone').classList.remove('drag-over');});
$('dropzone').addEventListener('drop',e=>{const file=e.dataTransfer.files[0];if(file)upload(file);});
async function upload(file){
 if(busy)return;if(!file.name.toLowerCase().endsWith('.docx'))return alertMessage('Выберите документ Word в формате .docx.');
 if(file.size>12*1024*1024)return alertMessage('Файл превышает 12 МБ. Разделите документ на части.');
 busy=true;filename=file.name;alertMessage();view('loading',0);$('cancel-button').hidden=true;$('cancel-hint').hidden=true;$('live-stage').textContent='Локальная обработка';$('progress-panel').hidden=true;$('loading-title').textContent='Подготавливаем документ';$('loading-description').textContent='Извлекаем текст и таблицы, скрываем персональные данные. На этом этапе внешних запросов нет.';$('elapsed').textContent='';
 try{const data=new FormData();data.append('file',file);data.append('privacy_backend',$('privacy-backend').value);const manifest=await api('/jobs',{method:'POST',body:data});job=await api('/jobs/'+manifest.job_id);renderPreview();view('preview',1);}catch(e){view('upload',0);alertMessage(e.message);}finally{busy=false;updateReady();}
}
function markedText(text){const node=document.createDocumentFragment();let pos=0;const pattern=/⟦PRIVATE_\d{4,6}⟧/g;for(const m of text.matchAll(pattern)){node.append(document.createTextNode(text.slice(pos,m.index)));node.append(el('span','private-token','[скрыто]'));pos=m.index+m[0].length;}node.append(document.createTextNode(text.slice(pos)));return node;}
function renderPreview(){
 $('privacy-profile-label').textContent=(settings.privacy_profiles||[]).find(p=>p.id===job.privacy_backend)?.label||'Обработано локально';
 $('preview-name').textContent=filename||'Загруженный документ';$('blocks-count').textContent=job.blocks;$('redactions-count').textContent=Object.values(job.redactions||{}).reduce((a,b)=>a+b,0)+(job.manual_redactions||0);
 const container=$('document-preview');container.replaceChildren();const query=$('preview-search').value.trim().toLowerCase();
 for(const block of job.public.blocks){if(query&&!block.text.toLowerCase().includes(query))continue;const section=el('section','preview-block');const top=el('div','block-top');top.append(el('span','',block.id+' · '+(block.kind==='table_row'?'Строка таблицы':'Фрагмент документа')));const hide=el('button','','Скрыть фрагмент');hide.type='button';hide.onclick=()=>{redactBlock=block.id;const selected=window.getSelection()?.toString()||'';$('redact-quote').value=selected&&block.text.includes(selected)?selected:'';$('redact-error').textContent='';$('redact-dialog').showModal();};top.append(hide);const text=el('p','block-text');text.append(markedText(block.text));section.append(top,text);container.append(section);}
 if(!container.children.length)container.append(el('p','empty','Совпадений нет.'));
}
$('preview-search').oninput=()=>{if(job)renderPreview();};
$('redact-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button[type=submit]');button.disabled=true;try{await post('/jobs/'+job.job_id+'/redactions',{block_id:redactBlock,quote:$('redact-quote').value});job=await api('/jobs/'+job.job_id);$('privacy-approved').checked=false;renderPreview();updateReady();$('redact-dialog').close();}catch(error){$('redact-error').textContent=error.message;}finally{button.disabled=false;}};
$('demo-button').onclick=()=>run('offline');$('run-button').onclick=()=>run('openrouter');
function renderProgress(state){
 $('live-stage').textContent=state.stage||'';
 $('progress-panel').hidden=false;
 for(const event of state.events||[]){
  if(event.seq<=lastEvent)continue;
  const row=el('li',''),time=el('time','',new Date(event.time).toLocaleTimeString('ru-RU'));
  time.dateTime=event.time;row.append(time,el('span','',event.message));$('progress-log').append(row);lastEvent=event.seq;
 }
 $('cancel-button').disabled=['cancelling','cancelled','complete','failed'].includes(state.status);
 if(state.status==='cancelling')$('cancel-label').textContent='Останавливаем…';
}
$('cancel-button').onclick=async()=>{
 if(!job)return;$('cancel-button').disabled=true;
 try{renderProgress(await post('/jobs/'+job.job_id+'/cancel',{}));}
 catch(e){alertMessage(e.message);$('cancel-button').disabled=false;}
};
async function monitor(){
 while(true){
  const state=await api('/jobs/'+job.job_id+'/status');renderProgress(state);
  if(state.status==='failed'){view('preview',1);alertMessage(state.error);return;}
  if(state.status==='cancelled'){view('preview',1);alertMessage('Работа отменена. Исходный файл сохранён; можно запустить проверку снова.');return;}
  if(state.status==='complete')break;
  await new Promise(r=>setTimeout(r,1000));
 }
 report=await api('/jobs/'+job.job_id+'/report.json');renderResult();view('result',3);
}
function lostConnection(error){
 busy=true;view('loading',2);$('loading-title').textContent='Связь с заданием прервана';
 $('loading-description').textContent='Сервер может продолжать обработку. Восстановите журнал или отправьте отмену.';
 $('reconnect-button').hidden=false;$('cancel-button').hidden=false;$('cancel-button').disabled=false;
 alertMessage(error.message);updateReady();
}
$('reconnect-button').onclick=async()=>{
 $('reconnect-button').disabled=true;
 try{await monitor();busy=false;$('reconnect-button').hidden=true;alertMessage();updateReady();}
 catch(e){lostConnection(e);}
 finally{$('reconnect-button').disabled=false;}
};
async function run(mode){
 if(busy||!job)return;busy=true;alertMessage();updateReady();view('loading',2);
 $('loading-title').textContent=mode==='offline'?'Выполняем локальную проверку':'Агент работает с документом';
 $('loading-description').textContent='Ниже появляются реальные этапы обработки. Любую незавершённую проверку можно остановить.';
 $('cancel-button').hidden=false;$('cancel-button').disabled=true;$('cancel-label').textContent='Остановить работу';$('cancel-hint').hidden=mode==='offline';
 $('progress-log').replaceChildren();lastEvent=0;$('progress-panel').hidden=false;$('live-stage').textContent='Запускаем проверку';
 let submitted=false, disconnected=false;$('reconnect-button').hidden=true;
 const started=Date.now();$('elapsed').textContent='';timer=setInterval(()=>{$('elapsed').textContent='Прошло '+Math.floor((Date.now()-started)/1000)+' сек.';},1000);
 try{
  await post('/jobs/'+job.job_id+'/run',{mode,approved_digest:job.release_digest,model:$('model').value,web_enabled:$('web-enabled').checked});
  submitted=true;history.replaceState(null,'','?job='+job.job_id);$('cancel-button').disabled=false;
  await monitor();
 }catch(e){if(submitted){disconnected=true;lostConnection(e);}else{view('preview',1);alertMessage(e.message);}}
 finally{clearInterval(timer);timer=null;if(!disconnected)busy=false;updateReady();}
}
$('privacy-backend').onchange=()=>{
 const profile=(settings.privacy_profiles||[]).find(p=>p.id===$('privacy-backend').value);
 $('privacy-hint').textContent=profile?.description||'';
};
function renderResult(){
 $('result-name').textContent=filename||'Результат проверки';$('demo-notice').hidden=report.mode!=='offline';
 $('result-model').textContent=report.mode==='offline'?'Локальная проверка':[(modelLabels[report.model]||report.model||'Модель не указана'),...(report.stats.providers_used||[])].join(' · ');
 $('stat-edits').textContent=report.stats.applied_edits||0;$('stat-findings').textContent=report.review.findings.length;$('stat-review').textContent=new Set([...report.review.findings.flatMap((f,i)=>f.status==='needs_review'?[i+1]:[]),...(report.changes||[]).filter(c=>c.status==='skipped').map(c=>c.finding)]).size;
 $('download-description').textContent=report.stats.applied_edits?'Внесено правок: '+report.stats.applied_edits+(report.stats.tracked_changes?'. Изменённые слова отмечены исправлениями Word: Рецензирование → Все исправления. Можно принять или отклонить.':'. Исправления внесены в копию исходника; список доступен в отчёте.'):'Автоматических правок нет. Скачивается копия исходника; замечания доступны в отчёте.';
 $('report-download').href='/jobs/'+job.job_id+'/report.docx';$('corrected-download').href='/jobs/'+job.job_id+'/corrected.docx';$('result-summary').textContent=report.review.summary;
 selectedChanges.clear();activeTab='findings';renderTab();
}
for(const button of document.querySelectorAll('[data-tab]'))button.onclick=()=>{activeTab=button.dataset.tab;renderTab();};
function renderTab(){
 for(const tab of document.querySelectorAll('[data-tab]')){const active=tab.dataset.tab===activeTab;tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));}
 const container=$('result-content');container.replaceChildren();
 if(activeTab==='findings')for(const [i,f] of report.review.findings.entries()){
  const item=el('article','finding'),head=el('div','finding-header');head.append(el('h3','',(i+1)+'. '+f.title),el('span','badge'+(f.status==='document_issue'?' applied':''),f.status==='needs_review'?'Требует решения':'Несоответствие'));item.append(head,el('small','',categories[f.category]||f.category));
  for(const a of f.anchors)item.append(el('blockquote','',a.quote));item.append(el('p','',f.explanation));const rec=el('p','');rec.append(el('span','label','Рекомендация: '),document.createTextNode(f.recommendation));item.append(rec);container.append(item);
 }
 if(activeTab==='changes')for(const c of report.changes||[]){const item=el('article','finding'),head=el('div','finding-header');head.append(el('h3','','Замечание '+c.finding+' · '+c.block_id),el('span','badge'+(c.status==='applied'?' applied':''),c.status==='applied'?'Применено':'Предложение'));item.append(head,el('p','change-before','Было: '+c.before),el('p','change-after','Стало: '+c.after),el('p','',c.reason));
  if(c.selectable&&c.status==='skipped'){
   const label=el('label','proposal-choice'),input=el('input');input.type='checkbox';input.checked=selectedChanges.has(c.id);input.disabled=busy;
   input.onchange=()=>{if(input.checked)selectedChanges.add(c.id);else selectedChanges.delete(c.id);const button=$('apply-selected');if(button){button.disabled=!selectedChanges.size||busy;button.textContent='Применить выбранные ('+selectedChanges.size+')';}};
   label.append(input,document.createTextNode(' Включить эту правку в новый документ'));item.append(label);
  }
  container.append(item);
 }
 if(activeTab==='changes'&&(report.changes||[]).some(c=>c.selectable&&c.status==='skipped')){
  const actions=el('div','proposal-actions'),button=el('button','primary','Применить выбранные ('+selectedChanges.size+')');button.id='apply-selected';button.type='button';button.disabled=!selectedChanges.size||busy;
  button.onclick=async()=>{
   if(busy||!selectedChanges.size)return;busy=true;button.disabled=true;button.textContent='Собираем документ…';updateReady();alertMessage();
   try{
    const current=await api('/jobs/'+job.job_id+'/proposals');
    const result=await post('/jobs/'+job.job_id+'/apply-selected',{report_digest:current.report_digest,change_ids:[...selectedChanges]});
    job=await api('/jobs/'+result.job_id);report=await api('/jobs/'+result.job_id+'/report.json');history.replaceState(null,'','?job='+job.job_id);renderResult();activeTab='changes';
   }catch(e){alertMessage(e.message);}finally{busy=false;updateReady();renderTab();}
  };
  actions.append(el('p','','Проверьте формулировки выше. Выбранные правки будут отмечены в новой версии Word. Предыдущая версия сохранится.'),button);container.append(actions);
 }
 if(activeTab==='coverage'){
  for(const check of report.review.coverage||[]){const item=el('article','finding');item.append(el('h3','',coverageLabels[check.topic]||check.topic),el('span','badge'+(check.result==='checked'?' applied':''),{checked:'Проверено по указанным данным',unverified:'Не хватает подтверждения',not_applicable:'Не относится к документу'}[check.result]),el('p','',check.note),el('small','',check.block_ids.join(', ')));container.append(item);}
  if(!container.children.length)container.append(el('p','empty','Для этого результата второй проход не выполнялся. Запустите новый анализ.'));
 }

 if(activeTab==='sources')for(const s of report.sources){const item=el('article','finding'),a=el('a','source-link',s.url);a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';item.append(el('h3','',s.title),a,el('p','',(s.retrieval_method==='search_excerpt'?'Выдержка поискового индекса (полная страница не прочитана). Получена ':s.truncated?'Прочитан сокращённый текст источника. Получен ':'Текст источника получен ')+new Date(s.retrieved_at).toLocaleString('ru-RU')+'. Применимость нормы требует отдельной проверки.'));container.append(item);}
 if(activeTab==='sources')for(const failure of report.stats.research_failures||[]){const item=el('article','finding');item.append(el('h3','','Источник не прочитан: '+failure.host),el('p','',failure.reason));container.append(item);}
 if(!container.children.length)container.append(el('p','empty',activeTab==='sources'?'Подтверждённых внешних источников нет.':activeTab==='changes'?'Точечные правки не предложены. Решения по замечаниям доступны в отчёте.':'Замечаний не найдено. Это не удостоверяет юридическое соответствие документа.'));
}
async function init(){
 try{
  settings=await api('/settings');$('model').replaceChildren();
  for(const model of settings.models){const option=el('option','',modelLabels[model]||model);option.value=model;option.selected=model===settings.model;$('model').append(option);}
  updateModelRoute();
  if(settings.privacy_profiles){$('privacy-backend').replaceChildren();for(const p of settings.privacy_profiles){const option=el('option','',p.label+(p.available?'':' · не установлен'));option.value=p.id;option.disabled=!p.available;option.selected=p.id===settings.privacy_default;$('privacy-backend').append(option);}$('privacy-backend').onchange();}
  updateReady();
  const saved=new URLSearchParams(location.search).get('job');
  if(saved&&/^[a-f0-9]{32}$/.test(saved)){
   job=await api('/jobs/'+saved);renderPreview();
   const state=await api('/jobs/'+saved+'/status');
   if(['queued','running','cancelling'].includes(state.status)){
    busy=true;view('loading',2);$('loading-title').textContent='Проверка продолжается';$('loading-description').textContent='Подключились к выполняющемуся заданию.';$('cancel-button').hidden=false;$('cancel-hint').hidden=false;
    try{await monitor();busy=false;updateReady();}catch(e){lostConnection(e);}
   }else if(state.status==='complete'||job.status==='complete'){
    renderProgress(state);report=await api('/jobs/'+saved+'/report.json');renderResult();view('result',3);
   }else{view('preview',1);if(state.status==='cancelled')alertMessage('Предыдущая проверка отменена. Можно запустить её снова.');}
  }
 }catch(e){alertMessage('Не удалось загрузить состояние. '+e.message);}
}
init();
