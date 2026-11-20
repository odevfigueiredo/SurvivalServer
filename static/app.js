'use strict';
const $ = (selector) => document.querySelector(selector);
const $all = (selector) => Array.from(document.querySelectorAll(selector));
const cats = [
  { name:'Documentos', icon:'▤', sub:'Arquivos gerais' },
  { name:'Manuais', icon:'▣', sub:'Guias técnicos' },
  { name:'Mapas', icon:'⌖', sub:'Navegação' },
  { name:'Vídeos', icon:'▷', sub:'Materiais em vídeo' },
  { name:'Wikipedia/ZIM', icon:'◈', sub:'Enciclopédias' },
  { name:'Guias', icon:'◇', sub:'Preparação familiar' },
  { name:'Outros', icon:'▧', sub:'Outros arquivos' },
];
let session = null;
let currentStats = { files:0, bytes:0, categories:{} };
const FILES_PAGE_SIZE = 50;
let filePage = 1;
let fileRequest = null;
let searchTimer = null;
let uploadController = null;
let pendingUpload = [];
let uploadedCount = 0;

function formatBytes(n){
  if(n < 1024) return `${n} B`;
  let i = 0; while(n >= 1024 && i < 4){ n /= 1024; i++; }
  return `${n.toLocaleString('pt-BR',{maximumFractionDigits:1})} ${['B','KB','MB','GB','TB'][i]}`;
}
function toast(message){
  const el = document.createElement('div'); el.className='toast'; el.textContent=message;
  $('#toastContainer').appendChild(el);
  el.addEventListener('click',()=>el.remove());
  setTimeout(()=>el.remove(),4400);
}
async function api(path, options={}){
  const requestSession = session;
  const headers = new Headers(options.headers||{});
  if(options.json!==undefined){ headers.set('Content-Type','application/json'); options.body=JSON.stringify(options.json); }
  if(session?.csrf && options.method && !['GET','HEAD'].includes(options.method.toUpperCase())) headers.set('X-CSRF-Token',session.csrf);
  const response=await fetch(path,{...options,headers,credentials:'same-origin',cache:'no-store'});
  if(!response.ok){
    let msg=`Erro HTTP ${response.status}`;
    try{ const body=await response.json(); msg=body.error||msg; }catch{}
    if(response.status===401 && session && session===requestSession){ session=null; showAuth(); }
    throw new Error(msg);
  }
  return response.json();
}
function showAuth(){
  clearTimeout(searchTimer);
  fileRequest?.abort();
  uploadController?.abort();
  $('#shell').hidden=true;
  toggleMenu(false);
  document.dispatchEvent(new Event('survival:logout'));
  if (!$('#uploadModal').hidden) closeUploadModal();
  $('#loginScreen').hidden=false;
  $('#loginPassword').value='';
}
async function showApp(){
  $('#loginScreen').hidden=true; $('#shell').hidden=false;
  $('#username').textContent=session.username; $('#userrole').textContent=session.role==='admin'?'Administrador':'Somente leitura';
  $('#avatar').textContent=session.username.slice(0,1).toUpperCase();
  $all('.admin-only').forEach(el=>el.hidden=session.role!=='admin');
  await refresh();
  if (session) showPage('home', false);
}
async function refresh(){
  const refreshSession = session;
  try{
    const stats = await api('/api/stats');
    if (session !== refreshSession) return;
    currentStats = stats;
    drawDashboard();
    await loadFiles();
  }
  catch(err){ toast(err.message); }
}
function drawDashboard(){
  $('#statFiles').textContent=currentStats.files.toLocaleString('pt-BR');
  $('#statBytes').textContent=formatBytes(currentStats.bytes);
  const grid=$('#categoryGrid'); grid.replaceChildren();
  const quick=$('#quickCats'); quick.replaceChildren();
  cats.forEach(item=>{
    const count=currentStats.categories[item.name]||0;
    const card=document.createElement('button');card.className='category-card';
    const icon=document.createElement('span');icon.className='glyph';icon.textContent=item.icon;
    const title=document.createElement('strong');title.textContent=item.name;
    const small=document.createElement('small');small.textContent=`${count} arquivo(s) · ${item.sub}`;
    card.append(icon,title,small);card.addEventListener('click',()=>openCategory(item.name));grid.append(card);
    const nav=document.createElement('button');nav.className='nav-link';
    const g=document.createElement('span');g.className='nav-emoji';g.textContent=item.icon;
    nav.append(g,document.createTextNode(item.name));nav.addEventListener('click',()=>openCategory(item.name));quick.append(nav);
  });
}
function toggleMenu(open){
  $('#sidebar').classList.toggle('open', open);
  $('#navScrim').hidden = !open;
  $('#menuBtn').setAttribute('aria-expanded', String(open));
  $('#menuBtn').setAttribute('aria-label', open ? 'Fechar menu' : 'Abrir menu');
}
function showPage(page, moveFocus = true){
  const target = $(`#page-${page}`);
  if (!target || target.hidden) return;
  $all('.page').forEach(el=>el.classList.toggle('active',el.id===`page-${page}`));
  $all('[data-page]').forEach(el=>{
    const active = el.dataset.page === page;
    el.classList.toggle('active', active);
    if (active) el.setAttribute('aria-current', 'page');
    else el.removeAttribute('aria-current');
  });
  $('#breadcrumb').textContent={home:'VISÃO GERAL',library:'BIBLIOTECA',emergency:'PLANO FAMILIAR',health:'INTEGRIDADE',guide:'ACESSO NA REDE',accounts:'CONTAS'}[page]||page;
  toggleMenu(false);
  if(page==='library') loadFiles();
  if(page==='emergency') loadEmergency();
  if(page==='health' && session?.role==='admin') loadDiagnostics();
  if(page==='guide') document.dispatchEvent(new CustomEvent('survival:network-open'));
  if(page==='accounts') document.dispatchEvent(new CustomEvent('survival:accounts-open'));
  if (moveFocus) {
    const heading = target.querySelector('h1');
    heading.tabIndex = -1;
    heading.focus({preventScroll:true});
  }
  window.scrollTo({top:0,behavior:'instant'});
}
function openCategory(category){
  $('#categoryFilter').value = category;
  filePage = 1;
  showPage('library');
}
function text(tag,cls,value){const el=document.createElement(tag);el.className=cls;el.textContent=value;return el;}
function btn(label,action,cls='file-action'){
  const el=document.createElement('button'); el.type='button';el.className=cls;el.textContent=label;
  el.addEventListener('click',action);return el;
}
function startLibraryLoading() {
  fileRequest?.abort();
  $('#filesList').setAttribute('aria-busy', 'true');
  $('#filesPrevious').disabled = true;
  $('#filesNext').disabled = true;
  $('#emptyState').hidden = true;
  $('#libraryNotice').hidden = true;
}

async function loadFiles(page = filePage) {
  clearTimeout(searchTimer);
  if (!session) return;
  filePage = page;
  startLibraryLoading();
  const controller = new AbortController();
  fileRequest = controller;
  const params = new URLSearchParams({
    q: $('#searchInput').value.trim(),
    category: $('#categoryFilter').value,
    page,
    page_size: FILES_PAGE_SIZE,
  });
  try {
    const data = await api(`/api/files?${params}`, {signal: controller.signal});
    if (controller.signal.aborted || fileRequest !== controller) return;
    const pageCount = Math.max(1, Math.ceil(data.total / data.page_size));
    if (data.page > pageCount) {
      await loadFiles(pageCount);
      return;
    }
    filePage = data.page;
    $('#filesList').replaceChildren(...data.files.map(fileCard));
    $('#resultCount').textContent = data.total === 1
      ? '1 arquivo encontrado'
      : `${data.total.toLocaleString('pt-BR')} arquivos encontrados`;
    $('#emptyState').hidden = data.total > 0;
    $('#filesPagination').hidden = pageCount === 1;
    $('#filesPageStatus').textContent = `Página ${filePage} de ${pageCount}`;
    $('#filesPrevious').disabled = filePage === 1;
    $('#filesNext').disabled = filePage === pageCount;
  } catch (err) {
    if (controller.signal.aborted || fileRequest !== controller) return;
    $('#filesList').replaceChildren();
    $('#resultCount').textContent = '';
    $('#filesPagination').hidden = true;
    $('#libraryNotice').textContent = `Não foi possível carregar a biblioteca. ${err.message}`;
    $('#libraryNotice').hidden = false;
  } finally {
    if (fileRequest === controller && !controller.signal.aborted) {
      fileRequest = null;
      $('#filesList').setAttribute('aria-busy', 'false');
    }
  }
}

function fileCard(file) {
  const card = text('article', 'file-card', '');
  const icon = text('div', 'file-icon', cats.find(c => c.name === file.category)?.icon || '▧');
  const info = text('div', 'file-info', '');
  info.append(
    text('h3', '', file.filename),
    text('p', '', file.description || file.category),
    text('small', '', `${file.category} · ${formatBytes(file.size)} · ${new Date(file.created_at * 1000).toLocaleDateString('pt-BR')}`),
  );
  const actions = text('div', 'file-controls', '');
  if (['application/pdf','image/png','image/jpeg','image/webp','image/gif','video/mp4','video/webm','audio/mpeg','text/plain'].includes(file.mime)) {
    actions.append(btn('Visualizar', () => window.open(`/api/files/${file.id}/view`, '_blank', 'noopener')));
  }
  const link = text('a', 'file-action', '↓ Baixar');
  link.href = `/api/files/${file.id}/download`;
  link.download = file.filename;
  actions.append(link);
  if (session.role === 'admin') {
    actions.append(btn('Editar', () => editFile(file)));
    actions.append(btn('Excluir', () => deleteFile(file), 'file-action danger'));
  }
  card.append(icon, info, actions);
  return card;
}
async function deleteFile(file){
  if(!confirm(`Excluir permanentemente "${file.filename}"? Essa ação não pode ser desfeita.`)) return;
  try{await api(`/api/files/${file.id}`,{method:'DELETE'});toast('Arquivo excluído');await refresh();}
  catch(err){toast(err.message);}
}
async function editFile(file){
  const description=prompt('Nova descrição (até 500 caracteres):',file.description);
  if(description===null) return;
  const category=prompt(`Categoria (${cats.map(c=>c.name).join(', ')}):`,file.category);
  if(category===null)return;
  try{await api(`/api/files/${file.id}/metadata`,{method:'PUT',json:{category,description}});toast('Arquivo atualizado');await refresh();}
  catch(err){toast(err.message);}
}
function updateUploadProgress() {
  const total = uploadedCount + pendingUpload.length;
  const progress = $('#uploadProgress');
  progress.max = total || 1;
  progress.value = uploadedCount;
  const selection = $('#uploadSelection');
  selection.hidden = total === 0;
  selection.textContent = uploadedCount
    ? `Enviados: ${uploadedCount}/${total}. Pendentes: ${pendingUpload.length}.`
    : `${total} arquivo(s) selecionado(s).`;
}

function setUploadSelection(files) {
  pendingUpload = Array.from(files);
  uploadedCount = 0;
  $('#startUpload').textContent = 'Enviar arquivos';
  $('#uploadProgress').hidden = true;
  $('#uploadError').hidden = true;
  updateUploadProgress();
}

function closeUploadModal() {
  $('#uploadModal').hidden = true;
  $('#uploadForm').reset();
  setUploadSelection([]);
  if (!$('#shell').hidden) $('#uploadButton').focus();
}

function cancelUpload() {
  if (uploadController) uploadController.abort();
  else closeUploadModal();
}

async function uploadFiles(event) {
  event.preventDefault();
  if (uploadController || !pendingUpload.length) return;
  const controller = new AbortController();
  uploadController = controller;
  const uploadSession = session;
  const category = $('#uploadCategory').value;
  const description = $('#uploadDescription').value;
  const controls = ['#uploadFiles', '#uploadCategory', '#uploadDescription', '#startUpload'].map($);
  for (const control of controls) control.disabled = true;
  $('#uploadProgress').hidden = false;
  $('#uploadError').hidden = true;

  try {
    while (pendingUpload.length && !controller.signal.aborted) {
      const file = pendingUpload[0];
      const params = new URLSearchParams({filename: file.name, category, description});
      await api(`/api/files/upload?${params}`, {
        method: 'POST',
        body: file,
        signal: controller.signal,
        headers: {'Content-Type': 'application/octet-stream'},
      });
      pendingUpload.shift();
      uploadedCount++;
      updateUploadProgress();
    }
  } catch (err) {
    if (!controller.signal.aborted) {
      $('#uploadError').textContent = `Falha ao enviar “${pendingUpload[0].name}”. ${err.message}`;
      $('#uploadError').hidden = false;
      $('#startUpload').textContent = 'Tentar novamente';
    }
  } finally {
    uploadController = null;
    for (const control of controls) control.disabled = false;
  }

  if (session !== uploadSession) return;
  if (controller.signal.aborted) {
    toast(uploadedCount ? `Envio cancelado. ${uploadedCount} arquivo(s) confirmado(s).` : 'Envio cancelado.');
    closeUploadModal();
  } else if (!pendingUpload.length) {
    toast(`${uploadedCount} arquivo(s) adicionado(s).`);
    closeUploadModal();
  }
  await refresh();
}
async function login(event){
  event.preventDefault(); const button=$('#loginForm button[type=submit]');button.disabled=true;$('#loginError').hidden=true;
  try{
    await api('/api/login',{method:'POST',json:{username:$('#loginUsername').value.trim(),password:$('#loginPassword').value}});
    const result=await api('/api/me');session=result.session;await showApp();
  }catch(err){$('#loginError').textContent=err.message;$('#loginError').hidden=false;}
  finally{button.disabled=false;}
}

const planOrder = ['contacts','meeting','departure','supplies','instructions'];
const PLAN_BODY_LIMIT = 4000;

function renderPlanEntry(card, key, entry) {
  const title = text('h2', '', entry.title);
  title.id = `plan-title-${key}`;
  const body = text('pre', 'plan-body', entry.body || 'Ainda não preenchido pelo administrador.');
  card.replaceChildren(title, body);
  if (entry.updated_at) {
    card.append(text('small', 'plan-date', `Atualizado em ${new Date(entry.updated_at * 1000).toLocaleString('pt-BR')}`));
  }
  if (session?.role !== 'admin') return;
  const edit = btn('Editar seção', () => editPlanEntry(card, key, entry, body, edit), 'secondary-button');
  edit.setAttribute('aria-describedby', title.id);
  card.append(edit);
  return edit;
}

function editPlanEntry(card, key, entry, body, edit) {
  const form = text('form', 'plan-editor', '');
  const label = text('label', '', 'Conteúdo da seção (até 4.000 caracteres)');
  const field = text('textarea', '', '');
  field.id = `plan-body-${key}`;
  field.rows = 7;
  field.value = entry.body;
  label.htmlFor = field.id;
  field.addEventListener('input', () => field.setCustomValidity(''));
  const error = text('p', 'form-error', '');
  error.setAttribute('role', 'alert');
  error.hidden = true;

  const cancel = btn('Cancelar', () => {
    form.remove();
    body.hidden = false;
    edit.hidden = false;
    edit.focus();
  }, 'secondary-button');
  const save = text('button', 'primary-button', 'Salvar');
  save.type = 'submit';
  const actions = text('div', 'plan-editor-actions', '');
  actions.append(cancel, save);
  form.append(label, field, error, actions);

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (save.disabled) return;
    if (Array.from(field.value).length > PLAN_BODY_LIMIT) {
      field.setCustomValidity('O texto deve ter até 4.000 caracteres.');
      field.reportValidity();
      return;
    }
    const value = field.value.trim();
    field.readOnly = true;
    save.disabled = cancel.disabled = true;
    error.hidden = true;
    try {
      const result = await api(`/api/emergency/${key}`, {method: 'PUT', json: {body: value}});
      renderPlanEntry(card, key, {...entry, body: value, updated_at: result.updated_at})?.focus();
      toast('Plano atualizado');
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      field.readOnly = false;
      save.disabled = cancel.disabled = false;
    }
  });

  body.hidden = true;
  edit.hidden = true;
  card.append(form);
  field.focus();
}

async function loadEmergency() {
  const planSession = session;
  try {
    const result = await api('/api/emergency');
    if (session !== planSession) return;
    const cards = planOrder.map(key => {
      const card = text('article', 'plan-card', '');
      renderPlanEntry(card, key, result.plan[key]);
      return card;
    });
    $('#emergencyCards').replaceChildren(...cards);
  } catch (err) {
    toast(err.message);
  }
}
async function loadDiagnostics(){
  if(session?.role!=='admin')return;
  try{
    const d=await api('/api/diagnostics');
    $('#diagnosticInfo').textContent=`Banco SQLite: ${d.sqlite_integrity} · Arquivos: ${d.files} · Livre: ${formatBytes(d.disk_free_bytes)} / ${formatBytes(d.disk_total_bytes)}`;
    const r=await api('/api/audit');const list=$('#auditList');list.replaceChildren();
    for(const entry of r.events){list.append(text('div','audit-event',`${new Date(entry.ts*1000).toLocaleString('pt-BR')}  •  ${entry.username}  •  ${entry.action}  •  ${entry.detail}`));}
    if(!r.events.length)list.textContent='Nenhuma atividade registrada.';
  }catch(err){toast(err.message);}
}
async function verifyAll(){
  $('#verificationOutput').textContent='Verificando conteúdo local; bibliotecas grandes podem demorar...';
  $('#verifyFiles').disabled=true;
  try{
    const r=await api('/api/verify',{method:'POST',json:{}});
    $('#verificationOutput').textContent=r.ok?`✓ ${r.files} arquivo(s) verificados sem divergências.`:`${r.failures.length} problema(s) reportado(s):\n${r.failures.join('\n')}${r.truncated?'\nLista truncada.':''}`;
  }catch(err){$('#verificationOutput').textContent='Erro: '+err.message;}
  finally{$('#verifyFiles').disabled=false;}
}
async function exportManifest(){
  try{
    const data=await api('/api/manifest');
    const file=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
    const url=URL.createObjectURL(file);const a=document.createElement('a');a.href=url;a.download='survival_manifesto_'+new Date().toISOString().slice(0,10)+'.json';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),2000);
  }catch(err){toast(err.message);}
}

async function init(){
  cats.forEach(c=>{
    const a=document.createElement('option');a.value=c.name;a.textContent=c.name;$('#categoryFilter').append(a);
    const b=document.createElement('option');b.value=c.name;b.textContent=c.name;$('#uploadCategory').append(b);
  });
  $all('[data-page]').forEach(el=>el.addEventListener('click',()=>showPage(el.dataset.page)));
  $all('[data-go]').forEach(el=>el.addEventListener('click',()=>showPage(el.dataset.go)));
  $('#loginForm').addEventListener('submit',login);
  $('#logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'});}catch{}session=null;showAuth();});
  $('#searchInput').addEventListener('input', () => {
    clearTimeout(searchTimer);
    filePage = 1;
    startLibraryLoading();
    searchTimer = setTimeout(() => loadFiles(), 250);
  });
  $('#categoryFilter').addEventListener('change', () => loadFiles(1));
  $('#filesPrevious').addEventListener('click', () => loadFiles(filePage - 1));
  $('#filesNext').addEventListener('click', () => loadFiles(filePage + 1));
  $('#uploadButton').addEventListener('click', () => {
    $('#uploadModal').hidden = false;
    $('#uploadFiles').focus();
  });
  $('#closeUpload').addEventListener('click',cancelUpload);
  $('#cancelUpload').addEventListener('click',cancelUpload);
  $('#uploadForm').addEventListener('submit',uploadFiles);
  $('#uploadFiles').addEventListener('change', event => setUploadSelection(event.currentTarget.files));
  $('#uploadModal').addEventListener('keydown', event => {
    if (event.key === 'Escape') cancelUpload();
  });
  $('#dropzone').addEventListener('dragover', event => {
    event.preventDefault();
    if (!uploadController) event.currentTarget.classList.add('dragging');
  });
  $('#dropzone').addEventListener('dragleave',e=>e.currentTarget.classList.remove('dragging'));
  $('#dropzone').addEventListener('drop', event => {
    event.preventDefault();
    event.currentTarget.classList.remove('dragging');
    if (uploadController || !event.dataTransfer.files.length) return;
    $('#uploadFiles').files = event.dataTransfer.files;
    setUploadSelection($('#uploadFiles').files);
  });
  $('#menuBtn').addEventListener('click',()=>{
    const open = !$('#sidebar').classList.contains('open');
    toggleMenu(open);
    if (open) $('#sidebar [aria-current="page"]').focus();
  });
  $('#navScrim').addEventListener('click',()=>{toggleMenu(false);$('#menuBtn').focus();});
  document.addEventListener('keydown', event=>{
    if(event.key==='Escape' && $('#sidebar').classList.contains('open')){
      toggleMenu(false);
      $('#menuBtn').focus();
    }
  });
  $('#refreshDiagnostic').addEventListener('click',loadDiagnostics);
  $('#verifyFiles').addEventListener('click',verifyAll);
  $('#exportManifest').addEventListener('click',exportManifest);
  try{const me=await api('/api/me');session=me.session;if(session)await showApp();else showAuth();}catch{showAuth();}
}
init();

window.survivalQrMatrix = value => {
  const encoder = qrcode(0, 'M');
  encoder.addData(value);
  encoder.make();
  const count = encoder.getModuleCount();
  return Array.from({length:count}, (_, row) =>
    Array.from({length:count}, (_, column) => encoder.isDark(row, column)));
};
