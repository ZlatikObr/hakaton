'use strict';
const $ = id => document.getElementById(id);
let settings = {}, me = {email:null}, job = null, report = null, filename = '', activeTab = 'findings', activeFilter = 'all', busy = false;
let lastEvent = 0, ticker = null, factTimer = null, clock = null, progress = {stage: 0, pct: 0, step: 0, max: 12};
const selectedChanges = new Set();
const blockInfo = new Map();

const coverageLabels = {object:'Предмет и заголовки',numbers:'Числа и размеры',scope:'Комплектность',warranty_acceptance:'Гарантия и приемка',classification:'Коды ОКПД 2',standards:'Стандарты и ГОСТ',language:'Опечатки и грамматика'};
const categories = {contradiction:'Противоречие',competition:'Ограничение конкуренции',characteristics:'Характеристики',classification:'Код ОКПД 2',warranty:'Гарантия',standards:'Стандарты',completeness:'Комплектность',acceptance:'Приемка',other:'Прочее'};
const severity = {high:'Высокий',medium:'Средний',low:'Низкий'};
const STAGES = [
 ['Подготовка пакета','обезличенный текст и правила отправки'],
 ['Анализ документа','модель читает требования и таблицы'],
 ['Нормативные источники','поиск оснований на официальных сайтах'],
 ['Перепроверка','второй проход по семи темам'],
 ['Проверка цитат','каждая цитата сверяется с текстом ТЗ'],
 ['Сборка документа','правки в Word и отчет'],
];
const STAGE_BASE = [3, 10, 42, 62, 82, 92, 100];
const FACTS = [
 'Если в ТЗ указан товарный знак, по 44-ФЗ к нему нужно добавить слова «или эквивалент» (п. 1 ч. 1 ст. 33).',
 'Код ОКПД 2 влияет на то, какие запреты и ограничения национального режима применяются к закупке.',
 'Гарантию на работы обычно отсчитывают от приемки результата работ, а не от поставки товара.',
 'Расхождение между таблицей характеристик и текстом ТЗ может стать поводом для запросов разъяснений и жалоб.',
 'Точные значения без диапазонов там, где допустимы аналоги, сужают круг участников закупки.',
 'Ссылка на отмененный ГОСТ делает требование непроверяемым: стоит указывать действующую редакцию.',
 'Если комплект состоит из самостоятельных товаров разного назначения, им могут требоваться разные коды ОКПД 2.',
];

/* helpers */
function el(tag, cls, text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function icon(name){const s=document.createElementNS('http://www.w3.org/2000/svg','svg');const u=document.createElementNS('http://www.w3.org/2000/svg','use');u.setAttribute('href','#i-'+name);s.append(u);return s;}
async function api(path, options={}){
 const response=await fetch(path,{credentials:'same-origin',...options});
 let body;try{body=await response.json();}catch{throw new Error('Сервер вернул неожиданный ответ. Обновите страницу.');}
 if(!response.ok)throw new Error(body.detail||'Запрос не выполнен. Попробуйте еще раз.');
 return body;
}
const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function alertMessage(m=''){$('alert').textContent=m;$('alert').hidden=!m;}
const store={get(k){try{return localStorage.getItem(k);}catch{return null;}},set(k,v){try{localStorage.setItem(k,v);}catch{}}};
const baseName=()=> (filename||'документ').replace(/\.docx$/i,'').trim();
const TITLES={upload:'Проверка ТЗ',loading:'Проверка ТЗ',preview:'Проверка данных',analysis:'Анализ документа',result:'Результат проверки',history:'Мои проверки'};
const PRIVACY_LABELS={natasha:['Стандартное','Имена, телефоны, почты, адреса и реквизиты.'],spacy:['Альтернативное','Другая модель распознавания имен. Полезно, если стандартное что-то пропустило.'],ensemble:['Усиленное','Скрывает больше, иногда и лишнее. Для документов с большим количеством ФИО.'],rules:['Только контакты и реквизиты','Без распознавания имен.']};

function view(name, step){
 for(const id of ['upload','loading','preview','analysis','result','history']){const n=$(id+'-view');const show=id===name;if(show&&n.hidden){n.hidden=false;n.style.animation='none';void n.offsetWidth;n.style.removeProperty('animation');}else n.hidden=!show;}
 for(const li of document.querySelectorAll('[data-step]')){const n=Number(li.dataset.step);li.classList.toggle('current',n===step);li.classList.toggle('done',n<step);}
 $('page-title').textContent=TITLES[name];
 document.querySelector('.steps').classList.toggle('hidden',name==='history');
 $('nav-documents').classList.toggle('active',name!=='history');$('nav-history').classList.toggle('active',name==='history');
 window.scrollTo({top:0,behavior:'smooth'});
}

/* settings */
function updateReady(){
 for(const id of ['new-document','replace-file','nav-documents','nav-history'])$(id).disabled=busy;
 $('run-button').disabled=busy||!settings.has_key||!$('privacy-approved').checked;
 $('run-hint').textContent=!settings.has_key?'Проверка моделью временно недоступна. Можно посмотреть демо без модели.':!$('privacy-approved').checked?'Отметьте, что просмотрели текст.':'';
 $('run-hint').hidden=!$('run-hint').textContent;
}
$('privacy-backend').onchange=()=>{$('privacy-hint').textContent=(PRIVACY_LABELS[$('privacy-backend').value]||[])[1]||'';};
$('help-button').onclick=()=>$('help-dialog').showModal();
for(const b of document.querySelectorAll('[data-close]'))b.onclick=()=>$(b.dataset.close).close();
for(const d of document.querySelectorAll('dialog'))d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}});

/* личный кабинет */
function noteHTML(node,iconName,text,button,saved){node.replaceChildren();if(!text)return;node.className='save-note'+(saved?' saved':'');node.append(icon(iconName),el('span','',text));if(button){const b=el('button','button ghost small',button);b.type='button';b.onclick=openLogin;node.append(b);}}
function renderAccount(){
 const email=me.email;
 $('account-name').textContent=email||'Гость';
 $('account-action').textContent=email?'Выйти':'Войти по почте';
 const av=$('account-avatar');av.replaceChildren();if(email)av.append((email[0]||'?').toUpperCase());else av.append(icon('user'));
 if(email)noteHTML($('save-note'),'check','Вы вошли как '+email+'. Проверки сохранятся в «Мои проверки».',null,true);
 else if(me.mail_enabled!==false)noteHTML($('save-note'),'mail','Войдите по почте, чтобы результаты сохранялись в личном кабинете. Это необязательно: без входа проверка работает так же, но результат не сохранится.','Войти');
 else noteHTML($('save-note'),'info','Результаты проверки не сохраняются: скачайте отчет и исправленное ТЗ на последнем шаге.');
 if(report&&job){if(email)noteHTML($('result-save-note'),'check','Проверка сохранена в «Мои проверки».',null,true);else if(me.mail_enabled!==false)noteHTML($('result-save-note'),'mail','Этот результат не сохранится после закрытия страницы. Войдите по почте, и он появится в «Мои проверки».','Войти и сохранить');else noteHTML($('result-save-note'),'info','Результат не сохраняется: скачайте файлы, пока страница открыта.');}
}
async function attachJob(id){if(!me.email||!id)return;try{await post('/my/jobs',{job_id:id,filename:filename||'Документ.docx'});}catch{}}
let loginEmail='';
function openLogin(){$('login-email-form').hidden=false;$('login-code-form').hidden=true;$('login-email-error').textContent='';$('login-code-error').textContent='';$('login-dialog').showModal();setTimeout(()=>$('login-email').focus(),50);}
$('account-action').onclick=async()=>{if(!me.email)return openLogin();try{await post('/auth/logout',{});}catch{}me.email=null;renderAccount();if(!$('history-view').hidden)renderHistory();};
$('login-email-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button[type=submit]');b.disabled=true;$('login-email-error').textContent='';
 try{loginEmail=$('login-email').value.trim();const r=await post('/auth/request',{email:loginEmail});$('login-email-form').hidden=true;$('login-code-form').hidden=false;
  $('login-code-hint').textContent='Мы отправили 6 цифр на '+loginEmail+'. Код действует 10 минут.';$('login-code').value='';
  const old=document.querySelector('.demo-code');old?.remove();if(r.demo_code){const d=el('div','demo-code','Демо-режим, письмо не отправлялось. Ваш код: '+r.demo_code);$('login-code-hint').after(d);}
  setTimeout(()=>$('login-code').focus(),50);}
 catch(err){$('login-email-error').textContent=err.message;}finally{b.disabled=false;}};
$('login-back').onclick=()=>{$('login-email-form').hidden=false;$('login-code-form').hidden=true;};
$('login-code').oninput=()=>{const v=$('login-code').value.replace(/\D/g,'').slice(0,6);$('login-code').value=v;if(v.length===6)$('login-code-form').requestSubmit();};
$('login-code-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button[type=submit]');b.disabled=true;$('login-code-error').textContent='';
 try{const r=await post('/auth/verify',{email:loginEmail,code:$('login-code').value});me.email=r.email;$('login-dialog').close();
  if(job){await attachJob(job.job_id);}renderAccount();if(!$('history-view').hidden)renderHistory();}
 catch(err){$('login-code-error').textContent=err.message;}finally{b.disabled=false;}};

/* мои проверки */
$('nav-history').onclick=()=>{if(busy)return;history.replaceState(null,'','/');view('history',-1);renderHistory();};
$('history-new').onclick=()=>reset();
async function renderHistory(){
 const box=$('history-content');box.replaceChildren();
 if(!me.email){const e=el('div','empty-state');e.append(icon('folder'),el('h3','','Здесь будут ваши проверки'),el('p','','Войдите по почте, и каждая проверенная ТЗ сохранится здесь вместе с отчетом и исправленным документом.'));const b=el('button','button primary','Войти по почте');b.onclick=openLogin;e.append(b);box.append(e);return;}
 box.append(el('p','empty','Загружаем...'));
 try{
  const data=await api('/my/jobs');box.replaceChildren();
  if(!data.jobs.length){const e=el('div','empty-state');e.append(icon('folder'),el('h3','','Пока пусто'),el('p','','Загрузите первое техническое задание. После проверки оно появится в этом списке.'));const b=el('button','button primary','Новая проверка');b.onclick=reset;e.append(b);box.append(e);return;}
  const t=el('table','table'),h=el('tr');for(const x of ['Документ','Дата','Замечаний','Высокий риск','Правок','Статус'])h.append(el('th','',x));t.append(h);
  const st={complete:['Готово','applied'],running:['Идет проверка','review'],prepared:['Не запускалась','']};
  data.jobs.forEach((j,k)=>{const r=el('tr','history-row');r.style.setProperty('animation-delay',(k*0.04)+'s');
   const d=el('td'),doc=el('div','doc'),ic=el('span','output-icon');ic.append(icon('doc-check'));doc.append(ic,(j.filename||'Документ').replace(/\.docx$/i,''));d.append(doc);
   const s=st[j.status]||st.prepared,sc=el('td');sc.append(el('span','state '+s[1],s[0]));
   r.append(d,el('td','muted',new Date(j.created*1000).toLocaleDateString('ru-RU',{day:'numeric',month:'long',hour:'2-digit',minute:'2-digit'})),el('td','num-cell',j.findings??'·'),el('td','num-cell red',j.high??'·'),el('td','num-cell',j.edits??'·'),sc);
   r.onclick=()=>{store.set('tz-name-'+j.job_id,j.filename);history.replaceState(null,'','?job='+j.job_id);openSaved(j.job_id);};t.append(r);});
  box.append(t);
 }catch(err){box.replaceChildren(el('p','empty',err.message));}
}

/* upload */
function reset(){if(busy)return;history.replaceState(null,'','/');job=null;report=null;$('result-save-note').replaceChildren();filename='';$('file-input').value='';$('privacy-approved').checked=false;$('preview-search').value='';alertMessage();view('upload',0);updateReady();}
$('new-document').onclick=reset;$('replace-file').onclick=reset;$('nav-documents').onclick=reset;
$('privacy-approved').onchange=updateReady;
$('file-input').addEventListener('change',e=>{if(e.target.files[0])upload(e.target.files[0]);});
for(const ev of ['dragenter','dragover'])$('dropzone').addEventListener(ev,e=>{e.preventDefault();$('dropzone').classList.add('drag-over');});
for(const ev of ['dragleave','drop'])$('dropzone').addEventListener(ev,e=>{e.preventDefault();$('dropzone').classList.remove('drag-over');});
$('dropzone').addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f)upload(f);});
async function upload(file){
 if(busy)return;
 if(!file.name.toLowerCase().endsWith('.docx'))return alertMessage('Выберите документ Word в формате .docx.');
 if(file.size>12*1024*1024)return alertMessage('Файл больше 12 МБ. Разделите документ на части.');
 busy=true;filename=file.name;alertMessage();view('loading',0);
 try{
  const data=new FormData();data.append('file',file);data.append('privacy_backend',$('privacy-backend').value);
  const manifest=await api('/jobs',{method:'POST',body:data});
  store.set('tz-name-'+manifest.job_id,filename);attachJob(manifest.job_id);
  job=await api('/jobs/'+manifest.job_id);renderPreview();view('preview',1);
 }catch(e){view('upload',0);alertMessage(e.message);}finally{busy=false;updateReady();}
}

/* preview: документ сплошным текстом */
const TOKEN=/⟦PRIVATE_\d{4,6}⟧/g;
function marked(text){const f=document.createDocumentFragment();let pos=0;for(const m of text.matchAll(TOKEN)){f.append(text.slice(pos,m.index),el('span','private-token','скрыто'));pos=m.index+m[0].length;}f.append(text.slice(pos));return f;}
const isHeading=t=>t.length<160&&(/^\d{1,2}(\.\d+)*\.?\s+\S/.test(t)||(t===t.toUpperCase()&&/[А-ЯA-Z]{4}/.test(t)));
function indexBlocks(){
 blockInfo.clear();let section='',table=0,prevTable=false;
 for(const b of job.public.blocks){
  if(b.kind==='table_row'){if(!prevTable)table++;prevTable=true;}else prevTable=false;
  if(b.kind!=='table_row'&&/^\d{1,2}(\.\d+)*\.?\s+\S/.test(b.text)&&b.text.length<160)section=b.text.replace(TOKEN,'').replace(/\.$/,'').slice(0,70);
  const cells=b.text.split(' | ').map(c=>c.trim()).filter(Boolean);const label=(/^\d+$/.test(cells[0]||'')?cells[1]:cells[0])||'';
  blockInfo.set(b.id,{kind:b.kind,section,where:b.kind==='table_row'?'Таблица '+table+' · '+label.slice(0,60):(section||'Начало документа'),text:b.text});
 }
}
function renderPreview(){
 indexBlocks();
 $('privacy-profile-label').textContent='Скрытие: '+((PRIVACY_LABELS[job.privacy_backend]||['стандартное'])[0]).toLowerCase();
 $('preview-name').textContent=filename||'Загруженный документ';
 const hidden=Object.values(job.redactions||{}).reduce((a,b)=>a+b,0)+(job.manual_redactions||0);countUp($('redactions-count'),hidden);
 const paper=$('document-preview');paper.replaceChildren();let table=null;
 for(const b of job.public.blocks){
  if(b.kind==='table_row'){
   if(!table){table=el('table');paper.append(table);}
   const tr=el('tr');tr.dataset.block=b.id;const cells=b.text.split(' | ');
   if(cells.length===1){tr.className='group';const td=el('td');td.colSpan=8;td.append(marked(cells[0]));tr.append(td);}
   else for(const c of cells){const td=el('td');td.append(marked(c));tr.append(td);}
   table.append(tr);
  }else{table=null;const p=el('p',isHeading(b.text)?'heading':'');p.dataset.block=b.id;p.append(marked(b.text));paper.append(p);}
 }
 applySearch();
}
function applySearch(){
 const q=$('preview-search').value.trim().toLowerCase();let hits=0,first=null;
 for(const n of $('document-preview').querySelectorAll('[data-block]')){const on=!!q&&n.textContent.toLowerCase().includes(q);n.classList.toggle('hit',on);if(on){hits++;first??=n;}}
 $('search-count').textContent=q?(hits?'Найдено: '+hits:'Совпадений нет'):'';
 if(first)first.scrollIntoView({block:'center',behavior:'smooth'});
}
$('preview-search').oninput=()=>{clearTimeout(applySearch.t);applySearch.t=setTimeout(applySearch,200);};

/* скрытие фрагмента выделением */
let pending=null;
document.addEventListener('mouseup',e=>{
 if($('redact-pop').contains(e.target))return;
 setTimeout(()=>{
  const sel=window.getSelection();const text=sel?.toString().trim();
  if(!text||!job||$('preview-view').hidden||!$('document-preview').contains(sel.anchorNode)){$('redact-pop').hidden=true;return;}
  const a=sel.anchorNode.parentElement?.closest('[data-block]'),f=sel.focusNode.parentElement?.closest('[data-block]');
  const pop=$('redact-pop');$('redact-error').textContent='';
  if(!a||a!==f){pending=null;$('redact-quote-preview').textContent='';$('redact-error').textContent='Выделите фрагмент внутри одного абзаца или одной ячейки.';$('redact-confirm').disabled=true;}
  else{pending={block_id:a.dataset.block,quote:text};$('redact-quote-preview').textContent='«'+(text.length>60?text.slice(0,60)+'...':text)+'»';$('redact-confirm').disabled=false;}
  const r=sel.getRangeAt(0).getBoundingClientRect();pop.hidden=false;
  pop.style.setProperty('left',Math.min(window.innerWidth-360,Math.max(12,r.left))+'px');
  pop.style.setProperty('top',(r.bottom+10)+'px');
 },10);
});
$('redact-cancel').onclick=()=>{$('redact-pop').hidden=true;window.getSelection()?.removeAllRanges();};
$('redact-confirm').onclick=async()=>{
 if(!pending)return;$('redact-confirm').disabled=true;
 try{await post('/jobs/'+job.job_id+'/redactions',pending);job=await api('/jobs/'+job.job_id);$('privacy-approved').checked=false;renderPreview();updateReady();$('redact-pop').hidden=true;window.getSelection()?.removeAllRanges();}
 catch(err){$('redact-error').textContent=err.message;}finally{$('redact-confirm').disabled=false;}
};

/* анализ */
$('demo-button').onclick=()=>run('offline');$('run-button').onclick=()=>run('openrouter');
function stageOf(m){
 if(/Формируем отч|итоговый документ|Применяем минимальные правки/i.test(m))return 5;
 if(/проверку цитат/i.test(m))return 4;
 if(/Первый проход завершён|Перепроверяем/i.test(m))return 3;
 if(/Операция инструмента|источник|выдержк/i.test(m))return 2;
 if(/ожидаем ответ|получен ответ|Ответ получен|уточн/i.test(m))return 1;
 return 0;
}
function buildStages(){const ol=$('stages');ol.replaceChildren();STAGES.forEach(([t,s],i)=>{const li=el('li');const d=el('span','dot');d.append(i<progress.stage?icon('check'):el('b','',String(i+1)));const box=el('div');box.append(t,el('small','',s));li.append(d,box);li.classList.toggle('done',i<progress.stage);li.classList.toggle('active',i===progress.stage);ol.append(li);});}
function setStage(s){if(s<=progress.stage)return;progress.stage=s;progress.pct=Math.max(progress.pct,STAGE_BASE[s]);buildStages();}
function paintProgress(){const p=Math.min(99,Math.round(progress.pct));$('progress-percent').textContent=p;$('progress-fill').style.setProperty('width',p+'%');document.title='('+p+'%) Анализ ТЗ';}
function renderProgress(state){
 $('live-stage').textContent=state.stage||'';
 for(const ev of state.events||[]){
  if(ev.seq<=lastEvent)continue;lastEvent=ev.seq;
  const step=/Шаг (\d+)\/(\d+)/.exec(ev.message);if(step){progress.step=+step[1];progress.max=+step[2];}
  setStage(stageOf(ev.message));
  const li=el('li'),t=el('time','',new Date(ev.time).toLocaleTimeString('ru-RU'));li.append(t,el('span','',ev.message));$('progress-log').prepend(li);
 }
 $('cancel-button').disabled=['cancelling','cancelled','complete','failed'].includes(state.status);
 if(state.status==='cancelling')$('cancel-label').textContent='Останавливаем...';
}
function startLive(){
 const pool=job.public.blocks.filter(b=>b.text.length>35&&b.text.length<400&&!b.text.includes('⟦PRIVATE_'));
 const topics=$('topics');topics.replaceChildren();const keys=Object.keys(coverageLabels);
 for(const k of keys){const c=el('span','chip');c.dataset.topic=k;c.append(icon('check'),coverageLabels[k]);topics.append(c);}
 let i=0,typing=null;
 const next=()=>{
  if(pool.length){const b=pool[Math.floor(Math.random()*pool.length)];const info=blockInfo.get(b.id)||{};
   $('reading-label').textContent=info.where||'Текст документа';
   const p=$('reading-text');const text=b.text.replace(/ \| /g,' · ').slice(0,190);p.textContent='';p.classList.remove('fade');void p.offsetWidth;p.classList.add('fade');
   let n=0;clearInterval(typing);const caret=el('span','caret');typing=setInterval(()=>{n+=3;p.textContent=text.slice(0,n);p.append(caret);if(n>=text.length)clearInterval(typing);},16);}
  const chips=[...topics.children];chips.forEach(c=>c.classList.remove('now'));
  const k=i%chips.length;chips[k].classList.add('now','on');i++;
  progress.pct=Math.min(STAGE_BASE[progress.stage+1]-1,progress.pct+(progress.stage===1||progress.stage===2?1.6:0.9));paintProgress();
 };
 next();ticker=setInterval(next,3200);
 let f=Math.floor(Math.random()*FACTS.length);const fact=()=>{const p=$('fact-text');p.textContent=FACTS[f++%FACTS.length];p.classList.remove('fade');void p.offsetWidth;p.classList.add('fade');};fact();factTimer=setInterval(fact,9000);
}
function stopLive(){clearInterval(ticker);clearInterval(factTimer);clearInterval(clock);ticker=factTimer=clock=null;document.title='Квантара · проверка технических заданий';}
$('cancel-button').onclick=async()=>{if(!job)return;$('cancel-button').disabled=true;try{renderProgress(await post('/jobs/'+job.job_id+'/cancel',{}));}catch(e){alertMessage(e.message);$('cancel-button').disabled=false;}};
$('notify-button').onclick=async()=>{try{const r=await Notification.requestPermission();$('notify-button').textContent=r==='granted'?'Уведомим, когда будет готово':'Уведомления запрещены в браузере';}catch{$('notify-button').textContent='Браузер не поддерживает уведомления';}$('notify-button').disabled=true;};
function notifyDone(){try{if(document.hidden&&Notification.permission==='granted')new Notification('Проверка ТЗ завершена',{body:baseName()});}catch{}}
async function monitor(){
 while(true){
  const state=await api('/jobs/'+job.job_id+'/status');renderProgress(state);paintProgress();
  if(state.status==='failed'){stopLive();view('preview',1);alertMessage(state.error);return;}
  if(state.status==='cancelled'){stopLive();view('preview',1);alertMessage('Проверка остановлена. Исходный файл сохранен, можно запустить снова.');return;}
  if(state.status==='complete')break;
  await new Promise(r=>setTimeout(r,1000));
 }
 progress.pct=100;$('progress-percent').textContent='100';$('progress-fill').style.setProperty('width','100%');setStage(6);
 await new Promise(r=>setTimeout(r,700));stopLive();notifyDone();
 report=await api('/jobs/'+job.job_id+'/report.json');renderResult();view('result',3);
}
function lostConnection(error){busy=true;stopLive();view('loading',2);$('loading-title').textContent='Связь с проверкой прервалась';$('loading-description').textContent='Сервер может продолжать работу. Восстановите связь, чтобы увидеть результат.';$('reconnect-button').hidden=false;alertMessage(error.message);updateReady();}
$('reconnect-button').onclick=async()=>{$('reconnect-button').disabled=true;try{beginAnalysisView('Продолжаем проверку');await monitor();busy=false;$('reconnect-button').hidden=true;alertMessage();updateReady();}catch(e){lostConnection(e);}finally{$('reconnect-button').disabled=false;}};
function beginAnalysisView(title){
 progress={stage:0,pct:2,step:0,max:12};lastEvent=0;$('progress-log').replaceChildren();
 $('analysis-title').textContent=title;$('cancel-label').textContent='Остановить';$('cancel-button').disabled=true;
 buildStages();paintProgress();view('analysis',2);
 const started=Date.now();clearInterval(clock);clock=setInterval(()=>{const s=Math.floor((Date.now()-started)/1000);$('elapsed').textContent='Прошло '+Math.floor(s/60)+':'+String(s%60).padStart(2,'0');},1000);
 startLive();
}
async function run(mode){
 if(busy||!job)return;busy=true;alertMessage();updateReady();
 beginAnalysisView(mode==='offline'?'Локальная проверка без модели':'Агент проверяет документ');
 let submitted=false,disconnected=false;
 try{
  await post('/jobs/'+job.job_id+'/run',{mode,approved_digest:job.release_digest,web_enabled:$('web-enabled').checked});
  submitted=true;history.replaceState(null,'','?job='+job.job_id);$('cancel-button').disabled=false;
  await monitor();
 }catch(e){if(submitted){disconnected=true;lostConnection(e);}else{stopLive();view('preview',1);alertMessage(e.message);}}
 finally{if(!disconnected)busy=false;updateReady();}
}

/* результат */
function countUp(node,to){const from=0,t0=performance.now(),d=900;const tick=t=>{const k=Math.min(1,(t-t0)/d);node.textContent=Math.round(from+(to-from)*(1-Math.pow(1-k,3)));if(k<1)requestAnimationFrame(tick);};requestAnimationFrame(tick);}
function downloadLinks(){
 const base=baseName();const rep=/(^|\s)ТЗ(\s|$)/i.test(base)?'Отчет проверки '+base:'Отчет проверки ТЗ '+base;
 $('report-download').href='/jobs/'+job.job_id+'/report.docx?name='+encodeURIComponent(rep);
 $('corrected-download').href='/jobs/'+job.job_id+'/corrected.docx?name='+encodeURIComponent(base+' (исправленное)');
}
function renderResult(){
 if(!blockInfo.size&&job?.public)indexBlocks();
 const f=report.review.findings;
 $('result-name').textContent=baseName();$('demo-notice').hidden=report.mode!=='offline';
 countUp($('stat-findings'),f.length);countUp($('stat-high'),f.filter(x=>x.severity==='high').length);countUp($('stat-edits'),report.stats?.applied_edits||0);
 countUp($('stat-review'),new Set([...f.flatMap((x,i)=>x.status==='needs_review'?[i+1]:[]),...(report.changes||[]).filter(c=>c.status==='skipped').map(c=>c.finding)]).size);
 $('download-description').textContent=report.stats?.applied_edits?'Правки в ТЗ выделены желтым и записаны исправлениями Word: их можно принять или отклонить.':'Автоматических правок нет: скачивается копия исходника, все замечания в отчете.';
 $('result-summary').textContent=report.review.summary;downloadLinks();
 selectedChanges.clear();activeTab='findings';activeFilter='all';renderTab();renderAccount();
}
for(const b of document.querySelectorAll('[data-tab]'))b.onclick=()=>{activeTab=b.dataset.tab;activeFilter='all';renderTab();};
function renderFilters(){
 const box=$('filters');box.replaceChildren();box.hidden=activeTab!=='findings';if(box.hidden)return;
 const counts={};for(const f of report.review.findings)counts[f.category]=(counts[f.category]||0)+1;
 const add=(key,label,n)=>{const c=el('button','chip'+(activeFilter===key?' active':''));c.append(label,' ',el('i','',String(n)));c.onclick=()=>{activeFilter=key;renderTab();};box.append(c);};
 add('all','Все',report.review.findings.length);add('high','Высокий риск',report.review.findings.filter(f=>f.severity==='high').length);
 for(const [k,n] of Object.entries(counts))add(k,categories[k]||k,n);
}
function renderTab(){
 for(const t of document.querySelectorAll('[data-tab]'))t.classList.toggle('active',t.dataset.tab===activeTab);
 renderFilters();const box=$('result-content');box.replaceChildren();
 if(activeTab==='findings')renderFindings(box);
 if(activeTab==='changes')renderChanges(box);
 if(activeTab==='coverage')renderCoverage(box);
 if(activeTab==='sources')renderSources(box);
}
function renderFindings(box){
 const list=report.review.findings.map((f,i)=>[f,i]).filter(([f])=>activeFilter==='all'||(activeFilter==='high'?f.severity==='high':f.category===activeFilter));
 if(!list.length)return box.append(el('p','empty','Замечаний не найдено. Это не подтверждает юридическое соответствие документа.'));
 const t=el('table','table'),head=el('tr');for(const h of ['№','Замечание','Где в документе','Риск','Статус',''])head.append(el('th','',h));t.append(head);
 list.forEach(([f,i],k)=>{
  const row=el('tr','row');row.style.setProperty('animation-delay',(k*0.04)+'s');
  const title=el('td');title.append(el('div','title',f.title),el('span','cat',categories[f.category]||f.category));
  const where=el('td','where',blockInfo.get(f.anchors[0]?.block_id)?.where||'Фрагмент '+(f.anchors[0]?.block_id||''));
  const st=el('td');st.append(el('span','state '+(f.status==='document_issue'?'issue':'review'),f.status==='document_issue'?'Ошибка в ТЗ':'Требует решения'));
  const ch=el('td','chev');ch.append(icon('chevron'));
  row.append(el('td','num',String(i+1)),title,where,el('td','sev '+f.severity,severity[f.severity]||f.severity),st,ch);
  const det=el('tr','detail'),td=el('td');td.colSpan=6;const inner=el('div','detail-inner'),wrap=el('div'),body=el('div','detail-body');
  const left=el('div');left.append(el('h4','','ФРАГМЕНТ ТЗ'));for(const a of f.anchors){const q=el('blockquote','quote');q.append(el('small','',blockInfo.get(a.block_id)?.where||a.block_id),a.quote.replace(/ \| /g,' · '));left.append(q);}
  const right=el('div');right.append(el('h4','','ПОЧЕМУ ЭТО РИСК'),el('p','',f.explanation),el('h4','','ЧТО СДЕЛАТЬ'),el('p','',f.recommendation));
  if(f.citations?.length){right.append(el('h4','','ОСНОВАНИЕ'));for(const c of f.citations){const s=report.sources.find(x=>x.id===c.source_id);right.append(el('p','','«'+c.quote+'»'));if(s){const a=el('a','src',s.title||s.url);a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';right.append(a);}}}
  else if(f.basis==='external')right.append(el('h4','','ОСНОВАНИЕ'),el('p','muted','Внешний источник не подтвержден: нужна ручная проверка.'));
  body.append(left,right);wrap.append(body);inner.append(wrap);td.append(inner);det.append(td);
  row.onclick=()=>{row.classList.toggle('open');det.classList.toggle('open');};
  t.append(row,det);
 });
 box.append(t);
}
function renderChanges(box){
 const changes=report.changes||[];if(!changes.length)return box.append(el('p','empty','Точечные правки не предложены. Решения по замечаниям есть в отчете.'));
 const list=el('div','list');
 changes.forEach((c,k)=>{
  const it=el('article','item');it.style.setProperty('animation-delay',(k*0.04)+'s');
  const h=el('div','item-head');h.append(el('b','','Замечание '+c.finding+' · '+(blockInfo.get(c.block_id)?.where||c.block_id)),el('span','state '+(c.status==='applied'?'applied':'review'),c.status==='applied'?'Внесено в Word':'Предложение'));
  const d=el('div','diff'),b=el('div','before'),a=el('div','after');b.append(el('small','','БЫЛО'),el('span','',c.before));a.append(el('small','','СТАЛО'),c.after);d.append(b,a);
  it.append(h,d,el('p','muted',c.reason||''));
  if(c.selectable&&c.status==='skipped'){const l=el('label','choice'),i=el('input');i.type='checkbox';i.checked=selectedChanges.has(c.id);i.disabled=busy;i.onchange=()=>{i.checked?selectedChanges.add(c.id):selectedChanges.delete(c.id);const btn=$('apply-selected');if(btn){btn.disabled=!selectedChanges.size||busy;btn.textContent='Применить выбранные ('+selectedChanges.size+')';}};l.append(i,'Включить эту правку в новую версию');it.append(l);}
  list.append(it);
 });
 box.append(list);
 if(changes.some(c=>c.selectable&&c.status==='skipped')){
  const bar=el('div','proposal-actions'),btn=el('button','button primary','Применить выбранные ('+selectedChanges.size+')');btn.id='apply-selected';btn.disabled=!selectedChanges.size||busy;
  btn.onclick=async()=>{if(busy||!selectedChanges.size)return;busy=true;btn.disabled=true;btn.textContent='Собираем документ...';updateReady();alertMessage();
   try{const cur=await api('/jobs/'+job.job_id+'/proposals');const r=await post('/jobs/'+job.job_id+'/apply-selected',{report_digest:cur.report_digest,change_ids:[...selectedChanges]});store.set('tz-name-'+r.job_id,filename);attachJob(r.job_id);job=await api('/jobs/'+r.job_id);report=await api('/jobs/'+r.job_id+'/report.json');history.replaceState(null,'','?job='+job.job_id);renderResult();activeTab='changes';}
   catch(e){alertMessage(e.message);}finally{busy=false;updateReady();renderTab();}};
  bar.append(el('p','muted','Выбранные правки попадут в новую версию Word. Предыдущая версия сохранится.'),btn);box.append(bar);
 }
}
function renderCoverage(box){
 const cov=report.review.coverage||[];if(!cov.length)return box.append(el('p','empty','Для этого результата второй проход не выполнялся.'));
 const list=el('div','list');const lab={checked:['Проверено','applied'],unverified:['Не хватает подтверждения','review'],not_applicable:['Не относится','']};
 cov.forEach((c,k)=>{const it=el('article','item');it.style.setProperty('animation-delay',(k*0.04)+'s');const h=el('div','item-head');h.append(el('b','',coverageLabels[c.topic]||c.topic),el('span','state '+lab[c.result][1],lab[c.result][0]));it.append(h,el('p','',c.note));list.append(it);});
 box.append(list);
}
function renderSources(box){
 const list=el('div','list');
 for(const s of report.sources||[]){const it=el('article','item'),a=el('a','src',s.url);a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';it.append(el('b','',s.title),a,el('p','muted',(s.retrieval_method==='search_excerpt'?'Выдержка из поискового индекса, страница целиком не прочитана. ':'')+'Получено '+new Date(s.retrieved_at).toLocaleString('ru-RU')+'. Применимость нормы требует проверки.'));list.append(it);}
 for(const fl of report.stats?.research_failures||[]){const it=el('article','item');it.append(el('b','','Источник не прочитан: '+fl.host),el('p','muted',fl.reason));list.append(it);}
 box.append(list.children.length?list:el('p','empty','Подтвержденных внешних источников нет.'));
}

/* init */
async function openSaved(saved){
 filename=store.get('tz-name-'+saved)||filename||'';alertMessage();
 try{
  job=await api('/jobs/'+saved);renderPreview();
  const state=await api('/jobs/'+saved+'/status');
  if(['queued','running','cancelling'].includes(state.status)){busy=true;beginAnalysisView('Проверка продолжается');$('cancel-button').disabled=false;try{await monitor();busy=false;updateReady();}catch(e){lostConnection(e);}}
  else if(state.status==='complete'||job.status==='complete'){report=await api('/jobs/'+saved+'/report.json');renderResult();view('result',3);}
  else{view('preview',1);if(state.status==='cancelled')alertMessage('Предыдущая проверка остановлена. Можно запустить ее снова.');}
 }catch(e){view('upload',0);alertMessage('Не удалось открыть проверку. '+e.message);}
}
async function init(){
 try{
  [settings,me]=await Promise.all([api('/settings'),api('/me').catch(()=>({email:null,mail_enabled:false}))]);
  if(settings.privacy_profiles){$('privacy-backend').replaceChildren();for(const p of settings.privacy_profiles){if(!p.available)continue;const o=el('option','',(PRIVACY_LABELS[p.id]||[p.label])[0]);o.value=p.id;o.selected=p.id===settings.privacy_default;$('privacy-backend').append(o);}$('privacy-backend').onchange();}
  updateReady();renderAccount();
  const saved=new URLSearchParams(location.search).get('job');
  if(saved&&/^[a-f0-9]{32}$/.test(saved))await openSaved(saved);else view('upload',0);
 }catch(e){alertMessage('Не удалось загрузить страницу. '+e.message);}
}
init();
