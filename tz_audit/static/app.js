'use strict';
const $ = id => document.getElementById(id);
let settings = {}, me = {email:null}, job = null, report = null, filename = '', activeTab = 'findings', activeFilter = 'all', busy = false;
let lastEvent = 0, ticker = null, factTimer = null, progress = {stage: 0, pct: 0, step: 0, max: 12, est: 300, started: 0, last: 0, done: false};
const selectedChanges = new Set();
const blockInfo = new Map();

const coverageLabels = {object:'Предмет и заголовки',numbers:'Числа и размеры',scope:'Комплектность',warranty_acceptance:'Гарантия и приемка',classification:'Коды ОКПД 2',standards:'Стандарты и ГОСТ',language:'Опечатки и грамматика',competition:'Конкуренция и эквиваленты',regime:'Закон, СМП, нацрежим'};
const categories = {contradiction:'Противоречие',competition:'Ограничение конкуренции',characteristics:'Характеристики',classification:'Код ОКПД 2',warranty:'Гарантия',standards:'Стандарты',completeness:'Комплектность',acceptance:'Приемка',language:'Опечатки и оформление',other:'Прочее'};
const severity = {high:'Высокий',medium:'Средний',low:'Низкий'};
const STAGES = [
 ['Подготовка пакета','обезличенный текст и правила отправки'],
 ['Анализ документа','модель читает требования и таблицы'],
 ['Нормативные источники','поиск оснований на официальных сайтах'],
 ['Перепроверка','второй проход по девяти темам'],
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
const baseName=()=> (filename||'документ').replace(/\.(docx|pdf)$/i,'').trim();
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
 const est=job?.estimate_seconds;$('estimate-hint').hidden=!est;
 if(est)$('estimate-hint').textContent='Примерное время проверки этого документа: '+fmtDuration(est.openrouter)+'. Демо без модели: '+fmtDuration(est.offline)+'.';
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
function loginStep(name){for(const f of ['login-email-form','login-code-form','login-password-form'])$(f).hidden=f!==name;}
function openLogin(){
 const pw=!!me.password_enabled;
 $('login-password-row').hidden=!pw;$('login-by-code').hidden=!pw;$('login-password').value='';
 $('login-submit-label').textContent=pw?'Войти':'Получить код';
 for(const id of ['login-email-error','login-code-error','set-password-error'])$(id).textContent='';
 loginStep('login-email-form');$('login-dialog').showModal();setTimeout(()=>$('login-email').focus(),50);
}
function loginConsentOk(){if($('login-consent').checked)return true;$('login-email-error').textContent='Отметьте согласие на обработку адреса почты.';return false;}
async function loggedIn(r,viaCode){
 me.email=r.email;me.has_password=!!r.has_password;
 if(job){await attachJob(job.job_id);}renderAccount();if(!$('history-view').hidden)renderHistory();
 // После входа по коду предлагаем задать пароль (или новый, если старый забыт).
 if(viaCode&&me.password_enabled){$('set-password-title').textContent=me.has_password?'Задайте новый пароль':'Придумайте пароль';$('set-password-user').value=r.email;$('new-password').value='';loginStep('login-password-form');setTimeout(()=>$('new-password').focus(),50);}
 else $('login-dialog').close();
}
async function requestCode(){
 const b=$('login-submit');b.disabled=true;$('login-email-error').textContent='';
 try{loginEmail=$('login-email').value.trim();const r=await post('/auth/request',{email:loginEmail});loginStep('login-code-form');
  $('login-code-hint').textContent='Мы отправили 6 цифр на '+loginEmail+'. Код действует 10 минут. Если письма нет, проверьте папку «Спам».';$('login-code').value='';
  const old=document.querySelector('.demo-code');old?.remove();if(r.demo_code){const d=el('div','demo-code','Демо-режим, письмо не отправлялось. Ваш код: '+r.demo_code);$('login-code-hint').after(d);}
  setTimeout(()=>$('login-code').focus(),50);}
 catch(err){$('login-email-error').textContent=err.message;}finally{b.disabled=false;}
}
$('account-action').onclick=async()=>{if(!me.email)return openLogin();try{await post('/auth/logout',{});}catch{}me.email=null;me.has_password=false;renderAccount();if(!$('history-view').hidden)renderHistory();};
$('login-email-form').onsubmit=async e=>{e.preventDefault();if(!loginConsentOk())return;
 const password=$('login-password').value;
 if(!me.password_enabled||!password)return requestCode();
 const b=$('login-submit');b.disabled=true;$('login-email-error').textContent='';
 try{loginEmail=$('login-email').value.trim();await loggedIn(await post('/auth/password',{email:loginEmail,password}),false);}
 catch(err){$('login-email-error').textContent=err.message;}finally{b.disabled=false;}};
$('login-by-code').onclick=()=>{if(loginConsentOk())requestCode();};
$('login-back').onclick=()=>loginStep('login-email-form');
$('login-code').oninput=()=>{const v=$('login-code').value.replace(/\D/g,'').slice(0,6);$('login-code').value=v;if(v.length===6)$('login-code-form').requestSubmit();};
$('login-code-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button[type=submit]');b.disabled=true;$('login-code-error').textContent='';
 try{await loggedIn(await post('/auth/verify',{email:loginEmail,code:$('login-code').value}),true);}
 catch(err){$('login-code-error').textContent=err.message;}finally{b.disabled=false;}};
$('login-password-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button[type=submit]');b.disabled=true;$('set-password-error').textContent='';
 try{const r=await post('/auth/set-password',{password:$('new-password').value});me.has_password=r.has_password;$('login-dialog').close();noteHTML($('save-note'),'check','Пароль сохранен. В следующий раз входите по почте и паролю.',null,true);}
 catch(err){$('set-password-error').textContent=err.message;}finally{b.disabled=false;}};
$('set-password-skip').onclick=()=>$('login-dialog').close();

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
  const st={complete:['Готово','applied'],running:['Идет проверка','review'],prepared:['Не запускалась',''],deleted:['Файлы удалены','']};
  data.jobs.forEach((j,k)=>{const r=el('tr','history-row');r.style.setProperty('animation-delay',(k*0.04)+'s');
   const d=el('td'),doc=el('div','doc'),ic=el('span','output-icon');ic.append(icon('doc-check'));doc.append(ic,(j.filename||'Документ').replace(/\.docx$/i,''));d.append(doc);
   const s=st[j.status]||st.prepared,sc=el('td');sc.append(el('span','state '+s[1],s[0]));
   r.append(d,el('td','muted',new Date(j.created*1000).toLocaleDateString('ru-RU',{day:'numeric',month:'long',hour:'2-digit',minute:'2-digit'})),el('td','num-cell',j.findings??'·'),el('td','num-cell red',j.high??'·'),el('td','num-cell',j.edits??'·'),sc);
   if(j.status==='deleted')r.classList.add('gone');else r.onclick=()=>{store.set('tz-name-'+j.job_id,j.filename);history.replaceState(null,'','?job='+j.job_id);openSaved(j.job_id);};t.append(r);});
  box.append(t);
 }catch(err){box.replaceChildren(el('p','empty',err.message));}
}

/* upload */
function reset(){if(busy)return;resetLoadingView();history.replaceState(null,'','/');job=null;report=null;$('result-save-note').replaceChildren();filename='';$('file-input').value='';$('privacy-approved').checked=false;$('preview-search').value='';alertMessage();view('upload',0);updateReady();}
$('new-document').onclick=reset;$('replace-file').onclick=reset;$('nav-documents').onclick=reset;
$('privacy-approved').onchange=updateReady;
const REGIME_IDS={law:'regime-law',smp:'regime-smp',national_regime:'regime-national'};
for(const [k,id] of Object.entries(REGIME_IDS)){const v=store.get('tz-regime-'+k);if(v&&[...$(id).options].some(o=>o.value===v))$(id).value=v;$(id).onchange=()=>store.set('tz-regime-'+k,$(id).value);}
function procurement(){const r={};for(const [k,id] of Object.entries(REGIME_IDS))r[k]=$(id).value;return r;}
const REGIME_TEXT={law:{'44-ФЗ':'44-ФЗ','223-ФЗ':'223-ФЗ',unknown:'закон не указан'},smp:{yes:'только СМП',no:'без ограничения для СМП',unknown:'СМП не указано'},national_regime:{yes:'нацрежим применяется',no:'нацрежим не применяется',unknown:'нацрежим определен сервисом'}};
function regimeLine(p){if(!p)return '';return 'Режим проверки: '+Object.keys(REGIME_TEXT).map(k=>REGIME_TEXT[k][p[k]]||'').filter(Boolean).join(', ')+'.';}
$('start-check').onclick=()=>{
 // Без согласия сначала ведем к галочке, иначе сразу к блоку загрузки.
 const target=$('terms-consent').checked?$('dropzone'):$('terms-consent').closest('.legal-card');
 target.scrollIntoView({block:'center',behavior:'smooth'});
 target.classList.remove('flash');void target.offsetWidth;target.classList.add('flash');
};
function syncConsent(){const ok=$('terms-consent').checked;$('dropzone').classList.toggle('locked',!ok);$('file-input').disabled=!ok;store.set('tz-terms',ok?'1':'');}
$('terms-consent').checked=store.get('tz-terms')==='1';syncConsent();$('terms-consent').onchange=syncConsent;
$('dropzone').addEventListener('click',e=>{if(!$('terms-consent').checked){e.preventDefault();alertMessage('Отметьте согласие с условиями сервиса, чтобы загрузить документ.');$('terms-consent').closest('.legal-card').scrollIntoView({block:'center',behavior:'smooth'});}});
$('file-input').addEventListener('change',e=>{if(e.target.files[0])upload(e.target.files[0]);});
for(const ev of ['dragenter','dragover'])$('dropzone').addEventListener(ev,e=>{e.preventDefault();$('dropzone').classList.add('drag-over');});
for(const ev of ['dragleave','drop'])$('dropzone').addEventListener(ev,e=>{e.preventDefault();$('dropzone').classList.remove('drag-over');});
$('dropzone').addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f)upload(f);});
function resetLoadingView(){$('loading-title').textContent='Готовим документ';$('loading-description').textContent='Извлекаем текст и таблицы и скрываем персональные данные. Внешних запросов на этом этапе нет.';$('reconnect-button').hidden=true;}
async function upload(file){
 if(busy)return;
 if(!$('terms-consent').checked)return alertMessage('Отметьте согласие с условиями сервиса, чтобы загрузить документ.');
 if(!/\.(docx|pdf)$/i.test(file.name))return alertMessage('Выберите документ Word (.docx) или PDF с текстовым слоем.');
 if(file.size>12*1024*1024)return alertMessage('Файл больше 12 МБ. Разделите документ на части.');
 busy=true;filename=file.name;alertMessage();resetLoadingView();view('loading',0);
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
// Доля общего времени, к которой обычно начинается этап: по ней уточняем прогноз на лету.
const STAGE_SHARE=[0,0,0.3,0.65,0.85,0.93]; // этап 1 начинается за секунды: по нему прогноз не уточняем
const CAP=97;
function fmtDuration(s){s=Math.max(0,Math.round(s));if(s<60)return 'меньше минуты';const m=Math.round(s/60);return '~'+m+' мин';}
function fmtClock(s){s=Math.floor(s);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}
function estimateFor(mode){const e=job?.estimate_seconds?.[mode];return e>0?e:(mode==='offline'?5:300);}
function buildStages(){const ol=$('stages');ol.replaceChildren();STAGES.forEach(([t,s],i)=>{const li=el('li');const d=el('span','dot');d.append(i<progress.stage?icon('check'):el('b','',String(i+1)));const box=el('div');box.append(t,el('small','',s));li.append(d,box);li.classList.toggle('done',i<progress.stage);li.classList.toggle('active',i===progress.stage);ol.append(li);});}
function setStage(s,at=Date.now()){
 if(s<=progress.stage)return;progress.stage=s;buildStages();
 const t=(at-progress.started)/1000,share=STAGE_SHARE[s];
 // Пересчет прогноза по фактическому темпу: без скачков полосы, меняется только скорость.
 if(share&&t>3){const implied=t/share;progress.est=Math.max(t+5,Math.min(progress.est*2,Math.max(progress.est/2,(progress.est+implied)/2)));}
}
function tickProgress(){
 const now=Date.now(),t=(now-progress.started)/1000,dt=Math.max(0,(now-progress.last)/1000);progress.last=now;
 if(progress.done){progress.pct=100;}
 else{
  if(t>progress.est-3)progress.est=t+20; // дольше прогноза: полоса замедляется, но не стоит
  // Остаток считается от начала интервала: тогда полоса растет строго линейно до прогноза.
  const remaining=Math.max(1,progress.est-(t-dt));
  progress.pct=Math.min(CAP,progress.pct+(CAP-progress.pct)*Math.min(1,dt/remaining));
 }
 paintProgress(t);
}
function paintProgress(t=0){
 const p=progress.done?100:Math.min(CAP,Math.floor(progress.pct));
 $('progress-percent').textContent=p;$('progress-fill').style.setProperty('width',p+'%');document.title='('+p+'%) Анализ ТЗ';
 $('elapsed').textContent='Прошло '+fmtClock(t);
 $('remaining').textContent=progress.done?'Готово':(progress.est-t>25?'Осталось '+fmtDuration(progress.est-t):'Почти готово');
}
// Понятное описание события сервера для ленты «Сейчас в работе». null: событие только в техническом журнале.
function describe(m){
 let s;
 if(/^Загружаем обезличенный/.test(m))return ['Документ передан агенту','Используется только обезличенный текст, который вы подтвердили'];
 if(/локальные правила/.test(m))return ['Проверяем правила отправки','Убеждаемся, что скрытые данные не уйдут из сервиса'];
 if((s=/Шаг (\d+)\/(\d+)/.exec(m))){counters.steps=+s[1];return ['Модель читает документ','Шаг '+s[1]+' из '+s[2]+': требования, таблицы, сроки и формулировки'];}
 if(/Ищем нормативные/.test(m)){counters.sources++;return ['Ищем нормативные основания','Законы о закупках, ГОСТ и ОКПД 2 на официальных сайтах'];}
 if(/Читаем норму из локальной базы/.test(m)){counters.sources++;return ['Читаем норму','Дословный текст 44-ФЗ, 223-ФЗ или ГК РФ из локальной базы'];}
 if(/Сверяем коды по справочнику ОКПД 2/.test(m)){counters.sources++;return ['Сверяем коды ОКПД 2','Официальный классификатор и перечни национального режима ПП № 1875'];}
 if(/Читаем и обезличиваем источник/.test(m)){counters.sources++;return ['Читаем найденный источник','Берем только выдержки, которые подтверждают замечание'];}
 if(/выдержк/i.test(m))return ['Сверяем выдержку из поиска','Страница источника недоступна, используем выдержку с пометкой'];
 if(/Проверяем цитаты и предложенные правки/.test(m)){counters.checks++;return ['Проверяем черновик замечаний','Каждая цитата сверяется с исходным текстом ТЗ'];}
 if(/Первый проход завершён/.test(m))return ['Первый проход завершен','Перепроверяем документ по девяти темам в свежем контексте'];
 if(/прошёл проверку цитат/.test(m)){counters.checks++;return ['Замечания подтверждены','Цитаты совпадают с текстом документа'];}
 if(/Ответ требует уточнения|слишком много операций/.test(m))return ['Агент уточняет ответ','Часть замечаний не прошла проверку и отправлена на доработку'];
 if(/HTTP 429/.test(m))return ['Модель перегружена','Повторяем запрос через несколько секунд'];
 if(/Применяем минимальные правки/.test(m))return ['Вносим правки в Word','Исправления записываются в копию ТЗ и выделяются желтым'];
 if(/Формируем отч/.test(m))return ['Собираем отчет','Риски, рекомендации и ссылки на нормы'];
 if(/^Готово/.test(m))return ['Проверка завершена','Отчет и исправленное ТЗ готовы'];
 return null;
}
let counters={steps:0,sources:0,checks:0};
function addFeed(title,detail,time){
 $('now-title').textContent=title;$('now-detail').textContent=detail;
 const feed=$('feed'),last=feed.firstElementChild;
 if(last&&last.dataset.title===title){last.querySelector('small').textContent=detail;return;}
 const li=el('li');li.dataset.title=title;const tm=el('time','',time);const box=el('div');box.append(el('b','',title),el('small','',detail));li.append(tm,box);
 feed.prepend(li);while(feed.children.length>8)feed.lastElementChild.remove();
}
function renderProgress(state){
 for(const ev of state.events||[]){
  if(ev.seq<=lastEvent)continue;lastEvent=ev.seq;
  const at=Date.parse(ev.time)||Date.now();
  // При повторном открытии страницы время считается от фактического начала проверки.
  if(ev.seq===1&&at<progress.started&&!progress.done){progress.started=at;progress.last=Date.now();progress.pct=Math.min(CAP*0.9,CAP*(Date.now()-at)/1000/progress.est);}
  const step=/Шаг (\d+)\/(\d+)/.exec(ev.message);if(step){progress.step=+step[1];progress.max=+step[2];}
  setStage(stageOf(ev.message),at);
  const time=new Date(ev.time).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const li=el('li'),t=el('time','',time);li.append(t,el('span','',ev.message));$('progress-log').prepend(li);
  const d=describe(ev.message);if(d)addFeed(d[0],d[1],time);
  else if(/ожидаем ответ модели/.test(ev.message))$('now-detail').textContent='Ждем ответ модели. Обычно это занимает от 10 секунд до пары минут';
  if(/Первый проход завершён/.test(ev.message))for(const c of $('topics').children)c.classList.add('on');
 }
 $('count-steps').textContent=counters.steps;$('count-sources').textContent=counters.sources;$('count-checks').textContent=counters.checks;
 $('cancel-button').disabled=['cancelling','cancelled','complete','failed'].includes(state.status);
 if(state.status==='cancelling')$('cancel-label').textContent='Останавливаем...';
}
function startLive(){
 const topics=$('topics');topics.replaceChildren();
 for(const k of Object.keys(coverageLabels)){const c=el('span','chip');c.dataset.topic=k;c.append(icon('check'),coverageLabels[k]);topics.append(c);}
 ticker=setInterval(tickProgress,250);
 let f=Math.floor(Math.random()*FACTS.length);const fact=()=>{const p=$('fact-text');p.textContent=FACTS[f++%FACTS.length];p.classList.remove('fade');void p.offsetWidth;p.classList.add('fade');};fact();factTimer=setInterval(fact,15000);
}
function stopLive(){clearInterval(ticker);clearInterval(factTimer);ticker=factTimer=null;document.title='Квантара.Закупки';}
$('cancel-button').onclick=async()=>{if(!job)return;$('cancel-button').disabled=true;try{renderProgress(await post('/jobs/'+job.job_id+'/cancel',{}));}catch(e){alertMessage(e.message);$('cancel-button').disabled=false;}};
$('notify-button').onclick=async()=>{try{const r=await Notification.requestPermission();$('notify-button').textContent=r==='granted'?'Уведомим, когда будет готово':'Уведомления запрещены в браузере';}catch{$('notify-button').textContent='Браузер не поддерживает уведомления';}$('notify-button').disabled=true;};
function notifyDone(){try{if(document.hidden&&Notification.permission==='granted')new Notification('Проверка ТЗ завершена',{body:baseName()});}catch{}}
// Мобильная сеть может пропасть на несколько секунд: до потери связи повторяем запрос до 6 раз.
async function statusWithRetry(id){
 for(let attempt=0;;attempt++){
  try{return await api('/jobs/'+id+'/status');}
  catch(e){if(attempt>=5||/не найден/i.test(e.message))throw e;$('now-detail').textContent='Связь нестабильна, переподключаемся...';await new Promise(r=>setTimeout(r,1000*(attempt+1)));}
 }
}
async function monitor(){
 while(true){
  const id=job?.job_id;if(!id)throw new Error('Проверка не найдена. Загрузите документ заново.');
  const state=await statusWithRetry(id);renderProgress(state);
  if(state.status==='failed'){stopLive();view('preview',1);alertMessage(state.error);return;}
  if(state.status==='cancelled'){stopLive();view('preview',1);alertMessage('Проверка остановлена. Исходный файл сохранен, можно запустить снова.');return;}
  if(state.status==='complete')break;
  await new Promise(r=>setTimeout(r,1000));
 }
 progress.done=true;setStage(6);paintProgress((Date.now()-progress.started)/1000);
 await new Promise(r=>setTimeout(r,700));stopLive();notifyDone();
 const doneId=job?.job_id;if(!doneId)throw new Error('Проверка не найдена. Загрузите документ заново.');
 report=await api('/jobs/'+doneId+'/report.json');renderResult();view('result',3);
}
function lostConnection(error){busy=false;stopLive();view('loading',2);$('loading-title').textContent='Связь с проверкой прервалась';$('loading-description').textContent='Сервер может продолжать работу. Восстановите связь, чтобы увидеть результат.';$('reconnect-button').hidden=false;alertMessage(error.message);updateReady();}
$('reconnect-button').onclick=async()=>{if(!job){busy=false;resetLoadingView();reset();return;}busy=true;updateReady();$('reconnect-button').disabled=true;try{beginAnalysisView('Продолжаем проверку','openrouter');await monitor();busy=false;$('reconnect-button').hidden=true;alertMessage();updateReady();}catch(e){lostConnection(e);}finally{$('reconnect-button').disabled=false;}};
function beginAnalysisView(title,mode){
 const now=Date.now();
 progress={stage:0,pct:0,step:0,max:12,est:estimateFor(mode),started:now,last:now,done:false};counters={steps:0,sources:0,checks:0};lastEvent=0;
 $('progress-log').replaceChildren();$('feed').replaceChildren();
 $('now-title').textContent='Запускаем проверку';$('now-detail').textContent='Готовим обезличенный текст к отправке';
 $('analysis-title').textContent=title;$('cancel-label').textContent='Остановить';$('cancel-button').disabled=true;
 buildStages();paintProgress(0);view('analysis',2);
 startLive();
}
async function run(mode){
 if(busy||!job)return;busy=true;alertMessage();updateReady();
 beginAnalysisView(mode==='offline'?'Локальная проверка без модели':'Агент проверяет документ',mode);
 let submitted=false,disconnected=false;
 try{
  await post('/jobs/'+job.job_id+'/run',{mode,approved_digest:job.release_digest,web_enabled:$('web-enabled').checked,procurement:procurement()});
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
 $('corrected-download').hidden=job.source_format==='pdf';
}
function renderResult(){
 if(!blockInfo.size&&job?.public)indexBlocks();
 const f=report.review.findings;
 $('result-name').textContent=baseName();$('demo-notice').hidden=report.mode!=='offline';
 countUp($('stat-findings'),f.length);countUp($('stat-high'),f.filter(x=>x.severity==='high').length);countUp($('stat-edits'),report.stats?.applied_edits||0);
 countUp($('stat-review'),new Set([...f.flatMap((x,i)=>x.status==='needs_review'?[i+1]:[]),...(report.changes||[]).filter(c=>c.status==='skipped').map(c=>c.finding)]).size);
 $('download-description').textContent=job?.source_format==='pdf'?'Исходник в PDF: правки не вносятся в файл, перенесите их по отчету и вкладке «Правки».':report.stats?.applied_edits?'Правки в ТЗ выделены желтым и записаны исправлениями Word: их можно принять или отклонить.':'Автоматических правок нет: скачивается копия исходника, все замечания в отчете.';
 $('result-summary').textContent=report.review.summary;$('result-regime').textContent=regimeLine(report.stats?.procurement);$('result-regime').hidden=!report.stats?.procurement;downloadLinks();
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

function retentionText(){const h=settings.retention_hours;if(!(h>0))return 'Файлы удаляются по кнопке «Удалить файлы»';return 'Документ и результаты удаляются автоматически через '+(h%24===0?(h/24===1?'24 часа':h/24+' сут.'):h+' ч')+' после последнего изменения';}
$('delete-files').onclick=async()=>{
 if(!job||busy)return;if(!confirm('Удалить исходный документ, отчет и исправленное ТЗ с сервера? Скачанные файлы останутся у вас.'))return;
 try{await api('/jobs/'+job.job_id,{method:'DELETE'});job=null;report=null;history.replaceState(null,'','/');view('upload',0);alertMessage();
  noteHTML($('save-note'),'check','Файлы проверки удалены с сервера.',null,true);}
 catch(e){alertMessage(e.message);}
};

/* init */
async function openSaved(saved){
 filename=store.get('tz-name-'+saved)||filename||'';alertMessage();
 try{
  job=await api('/jobs/'+saved);renderPreview();
  const state=await api('/jobs/'+saved+'/status');
  if(['queued','running','cancelling'].includes(state.status)){busy=true;beginAnalysisView('Проверка продолжается','openrouter');$('cancel-button').disabled=false;try{await monitor();busy=false;updateReady();}catch(e){lostConnection(e);}}
  else if(state.status==='complete'||job.status==='complete'){report=await api('/jobs/'+saved+'/report.json');renderResult();view('result',3);}
  else{view('preview',1);if(state.status==='cancelled')alertMessage('Предыдущая проверка остановлена. Можно запустить ее снова.');}
 }catch(e){view('upload',0);alertMessage('Не удалось открыть проверку. '+e.message);}
}
async function init(){
 try{
  [settings,me]=await Promise.all([api('/settings'),api('/me').catch(()=>({email:null,mail_enabled:false}))]);
  if(settings.privacy_profiles){$('privacy-backend').replaceChildren();for(const p of settings.privacy_profiles){if(!p.available)continue;const o=el('option','',(PRIVACY_LABELS[p.id]||[p.label])[0]);o.value=p.id;o.selected=p.id===settings.privacy_default;$('privacy-backend').append(o);}$('privacy-backend').onchange();}
  updateReady();renderAccount();$('retention-text').textContent=retentionText();$('retention-note').textContent=retentionText()+'.';
  const saved=new URLSearchParams(location.search).get('job');
  if(saved&&/^[a-f0-9]{32}$/.test(saved))await openSaved(saved);else view('upload',0);
 }catch(e){alertMessage('Не удалось загрузить страницу. '+e.message);}
}
init();
