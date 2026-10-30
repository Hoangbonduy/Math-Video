'use strict';
let state, context = {}, token, sid, selection, userHistory = false, revisionSeen = -1;
let currentPage = ['idea', 'design', 'preview', 'history'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'idea';
let editorTab = 'narration', refreshing = false, taskSignature = ''; 
let ideaDraft, ideaBusy = false, workflowBusy = false, apiCompatible = false;
let manimRuntime, workflowTasks = {};
let aiProvider = null, providerPending = false;
let codexAuth = null, codexAuthPending = false, codexLoginMethod = 'device', codexAuthSignature = '';
let previewMode = null, previewPlayback = null, subtitlesVisible = true, sceneReviewOpen = false, voiceDraft = null, voiceSettingsOpen = false;
const previewAttempts = new Set();
let scriptExpanded = false, scriptHistory = {items:[]}, selectedScriptVersion = null, historyQuery = '';
const scriptDetails = new Map(), loadingScripts = new Set();
let deletingScript = null;
const SERVER_UPDATE_MESSAGE = 'Ứng dụng vừa được cập nhật. Hãy khởi động lại máy chủ và tải lại trang để dùng chức năng tạo kịch bản.';
const feedbackDrafts = new Map();
const labels = {draft_ready:'Bản nháp sẵn sàng',pending:'Chờ duyệt', approved:'Đã duyệt', changes_requested:'Cần chỉnh sửa', superseded:'Bản cũ', ready:'Sẵn sàng', needs_revision:'Cần chỉnh sửa', awaiting_review:'Chờ duyệt', scene_design:'Thiết kế', scene_av:'Cảnh có tiếng', scene_clip:'Bản dựng không tiếng', script:'Kịch bản', final_video:'Chờ ghép video', analyzing:'Đang phân tích', planned:'Chuẩn bị sửa', awaiting_scope:'Chờ duyệt phạm vi', awaiting_design:'Chờ duyệt thiết kế mới', rendering:'Đang dựng', awaiting_clip:'Chờ duyệt clip mới', completed:'Hoàn tất', failed:'Đã dừng do lỗi', stale:'Đầu vào đã thay đổi', out_of_scope:'Ngoài phạm vi', cancelled:'Đã hủy'};
const label = value => labels[value] || value;
const $ = id => document.getElementById(id);
function el(tag, text, parent, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  if (parent) parent.append(node);
  return node;
}
function button(parent, text, fn, className = '') {
  const node = el('button', text, parent, className);
  node.type = 'button';
  node.onclick = async () => { node.disabled = true; try { await fn(); } finally { node.disabled = false; } };
  return node;
}
function notice(text, error = false) { $('notice').textContent = text; $('notice').classList.toggle('error', error); }
function relative(path) { return path.startsWith(state.project_dir + '/') ? path.slice(state.project_dir.length + 1) : path; }
function media(path) { return '/media?path=' + encodeURIComponent(relative(path)); }
function reviewFor(a) { return a && state.review_history.find(r => r.scene_id === a.scene_id && r.stage === a.stage && r.version === a.version); }
function latest(id, stage) { return state.artifacts.filter(a => a.scene_id === id && a.stage === stage && !a.invalidated).at(-1); }
function designFor(id, version) { return context.designs?.[id + ':' + version] || {}; }
function sceneData(id) { return context.script?.scenes?.find(s => s.scene_id === id) || {}; }
function shortTitle(id) {
  const design = designFor(id, latest(id, 'scene_design')?.version);
  const title = design.objects?.find(o => o.kind === 'text' && o.label.trim());
  return title?.label || sceneData(id).title || 'Cảnh ' + String(state.scene_ids.indexOf(id) + 1).padStart(2, '0');
}
function clock(seconds) { const s = Math.max(0, Math.round(Number(seconds) || 0)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); }
function timing(id) {
  let start = 0;
  for (const scene of state.scene_ids) {
    const duration = Number(designFor(scene, latest(scene, 'scene_design')?.version).duration) || 0;
    if (scene === id) return duration ? clock(start) + '–' + clock(start + duration) : 'Chưa có thời lượng';
    start += duration;
  }
  return '';
}
function formatText() {
  const resolution = context.render?.resolution;
  const ratio = resolution ? (resolution[0] >= resolution[1] ? '16:9' : '9:16') : '';
  return [ratio ? 'Video ' + (ratio === '16:9' ? 'ngang' : 'dọc') + ' · ' + ratio : 'Định dạng theo dự án', context.brief?.language === 'vi' ? 'Tiếng Việt' : context.brief?.language || ''].filter(Boolean).join('\n');
}
async function action(route, data) {
  if (!apiCompatible) { notice(SERVER_UPDATE_MESSAGE, true); return false; }
  notice('Đang lưu yêu cầu…');
  try {
    const response = await fetch('/api/' + route, {method:'POST', headers:{'Content-Type':'application/json', 'X-Review-Token':token}, body:JSON.stringify({...data, request_id:crypto.randomUUID()})});
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    notice(route === 'approve' ? 'Đã duyệt đúng phiên bản đang xem.' : 'Đã lưu yêu cầu. Tiến độ sẽ cập nhật ngay trên trang.');
    revisionSeen = -1;
    await refresh();
    return true;
  } catch (error) { notice(error.message, true); return false; }
}
function navigate(page) {
  if (location.hash !== '#' + page) location.hash = page;
  else { currentPage = page; render(); }
}
window.addEventListener('hashchange', () => {
  const page = location.hash.slice(1);
  currentPage = ['idea', 'design', 'preview', 'history'].includes(page) ? page : 'idea';
  if (state) render();
});
function chooseScene(id) { sid = id; selection = null; userHistory = false; render(); }
function badge(parent, status) { return el('span', label(status || 'Chưa tạo'), parent, 'badge ' + (status || '')); }
function renderShell() {
  const title = context.script?.title || state.topic;
  document.title = title + ' · Xưởng video toán';
  $('project').textContent = title;
  $('format').textContent = formatText();
  $('project-status').textContent = codexAuth?.busy ? 'Đang đăng nhập' : workflowBusy ? 'Đang thực hiện' : label(state.status);
  $('project-status').className = 'badge ' + (state.status === 'awaiting_review' ? 'pending' : '');
  const budget = state.stage === 'script' ? state.generation : state.scene_generation;
  $('budget-stage').textContent = state.stage === 'script' ? 'Tạo kịch bản' : 'Thiết kế & dựng cảnh';
  const codex = (state.ai_backend === 'codex') || (budget?.billing_source === 'codex_subscription');
  const providerName = 'Codex';
  $('budget-progress').hidden = codex;
  if (codex) {
    const calls = budget?.billing_source === state.ai_backend + '_subscription' ? budget.request_count : 0;
    $('budget').textContent = `${providerName} · ${calls || 0} lượt`;
    $('budget').title = 'Dùng hạn mức tài khoản đã đăng nhập trong ' + providerName + '. Giọng đọc dùng nguồn đã chọn trong Cấu hình giọng đọc.';
  } else {
    $('budget').textContent = budget ? `${budget.budget_used_usd.toFixed(4)} / ${budget.budget_usd.toFixed(2)} USD` : 'Chưa sử dụng';
    $('budget-progress').value = budget ? budget.budget_used_usd / budget.budget_usd : 0;
  }
  for (const step of document.querySelectorAll('.steps a')) {
    if (step.dataset.page === currentPage) step.setAttribute('aria-current', 'page');
    else step.removeAttribute('aria-current');
  }
  const pageLabels = {idea:'Ý tưởng', design:'Thiết kế cảnh', preview:'Bản xem trước', history:'Lịch sử kịch bản'};
  $('breadcrumb').textContent = currentPage === 'idea' ? 'Bắt đầu từ câu hỏi' : title + ' / ' + pageLabels[currentPage];
  $('page-title').textContent = {idea:'Bạn muốn giải thích điều gì?', design:'Thiết kế cảnh bằng Manim', preview:'Kiểm tra trước khi xuất', history:'Lịch sử kịch bản'}[currentPage];
  $('page-number').textContent = {idea:'01 / 03', design:'02 / 03', preview:'03 / 03', history:'Lịch sử trên máy'}[currentPage];
  $('footer-status').textContent = 'Dự án · ' + title;
  $('page-actions').replaceChildren();
  if (currentPage === 'history') {
    button($('page-actions'), 'Ý tưởng mới', newIdea, 'primary');
  } else if (currentPage !== 'idea' && state.stage === 'script') {
    button($('page-actions'), '← Tạo và duyệt kịch bản', () => navigate('idea'), 'primary');
  } else if (currentPage === 'design') {
    button($('page-actions'), 'Ghi chú duyệt', () => { $('feedback').scrollIntoView({behavior:'smooth', block:'center'}); $('feedback-text')?.focus(); });
    button($('page-actions'), 'Xem bản dựng →', () => { userHistory = false; selection = null; navigate('preview'); }, 'primary');
  } else if (currentPage === 'preview') {
    button($('page-actions'), '← Chỉnh sửa cảnh', () => { userHistory = false; selection = null; navigate('design'); });
  } else {
    button($('page-actions'), 'Ý tưởng mới', newIdea);
    button($('page-actions'), 'Lưu bản nháp', saveIdea);
  }
  $('idea-page').hidden = currentPage !== 'idea';
  $('scene-page').hidden = ['idea','history'].includes(currentPage);
  $('script-history-page').hidden = currentPage !== 'history';
  $('manim-status').hidden = currentPage !== 'design';
  if (manimRuntime) {
    $('manim-status').textContent = manimRuntime.available ? 'Môi trường sẵn sàng · Manim ' + manimRuntime.version + ' · Cairo · ' + (context.render?.resolution?.join(' × ') || '') + ' · ' + (context.render?.fps || '') + ' FPS · ' + manimRuntime.python : manimRuntime.error;
    $('manim-status').classList.toggle('error', !manimRuntime.available);
  }
  $('scene-page').classList.toggle('preview-page', currentPage === 'preview');
  $('scene-page').classList.toggle('show-scene-review', sceneReviewOpen);
  $('continuous-preview').hidden = currentPage !== 'preview';
  $('review-layout').classList.toggle('is-preview', currentPage === 'preview');
  document.querySelector('.scene-rail').hidden = currentPage === 'preview';
  if (state.integrity_issues?.length) notice('Có nội dung đã thay đổi sau khi đăng ký. Kiểm tra dữ liệu trước khi duyệt phiên bản này.', true);
}
function openCodexAccount() {
  const panel = $('codex-account');
  panel.scrollIntoView({behavior:'smooth', block:'center'});
  panel.querySelector('button')?.focus();
}
async function codexAccountAction(route) {
  if (codexAuthPending) return;
  codexAuthPending = true; renderCodexAccount();
  try {
    const response = await fetch('/api/' + route, {method:'POST', headers:{'Content-Type':'application/json','X-Review-Token':token}, body:JSON.stringify({request_id:crypto.randomUUID(),method:codexLoginMethod})});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Không cập nhật được đăng nhập Codex.');
    codexAuth = data;
    await refresh();
  } catch (error) { notice(error.message, true); }
  finally { codexAuthPending = false; renderCodexAccount(); }
}
function renderCodexAccount() {
  renderAIProvider();
  const panel = $('codex-account');
  panel.hidden = !codexAuth || state?.ai_backend !== 'codex';
  panel.replaceChildren();
  if (panel.hidden) return;
  el('h2','Tài khoản Codex',panel);
  el('p',codexAuth.logged_in ? 'Đã đăng nhập ChatGPT' : 'Chưa đăng nhập ChatGPT',panel,'codex-account-status');
  el('p',codexAuth.message,panel,'codex-account-message');
  if (codexAuth.busy) {
    if (codexAuth.verification_url) {
      const link = el('a','Mở trang đăng nhập OpenAI ↗',panel,'codex-login-link');
      link.href = codexAuth.verification_url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      if (codexAuth.method === 'device') el('small','Đăng nhập tài khoản mới, rồi nhập mã bên dưới:',panel);
      else el('small','Nếu trình duyệt đang giữ tài khoản cũ, đăng xuất tài khoản đó trên trang OpenAI rồi chọn tài khoản mới.',panel);
    }
    if (codexAuth.user_code) {
      const code = el('code',codexAuth.user_code,panel,'codex-device-code'); code.setAttribute('aria-label','Mã đăng nhập một lần');
      button(panel,'Sao chép mã',async()=>{try{await navigator.clipboard.writeText(codexAuth.user_code);notice('Đã sao chép mã đăng nhập.');}catch{notice('Hãy chọn và sao chép mã hiển thị.',true);}}).disabled=codexAuthPending;
    }
    button(panel,'Hủy đăng nhập',()=>codexAccountAction('codex-login-cancel')).disabled=codexAuthPending || codexAuth.state === 'cancelling';
    el('small','Đang chờ bạn hoàn tất. Mã đăng nhập chỉ hiển thị trong phiên này.',panel);
    return;
  }
  const method = field(panel,'Cách đăng nhập','select','','codex-login-method');
  for (const [value,text] of [['device','Mã thiết bị (WSL)'],['browser','Trình duyệt']]) { const option=el('option',text,method);option.value=value; }
  method.value=codexLoginMethod;method.onchange=()=>{codexLoginMethod=method.value;};
  method.disabled=codexAuthPending || workflowBusy;
  button(panel,codexAuth.logged_in ? 'Đổi tài khoản Codex' : 'Đăng nhập Codex',()=>codexAccountAction('codex-login'),'primary').disabled=codexAuthPending || workflowBusy || !codexAuth.available;
  if (codexAuth.logged_in) button(panel,'Đăng xuất Codex',()=>codexAccountAction('codex-logout'),'text-button').disabled=codexAuthPending || workflowBusy;
  button(panel,'Kiểm tra đăng nhập',async()=>{
    try {
      const response=await fetch('/api/codex-auth',{headers:{'X-Review-Token':token}});
      const data=await response.json();if(!response.ok)throw Error(data.error);
      codexAuth=data;renderCodexAccount();
    } catch(error){notice(error.message,true);}
  },'text-button').disabled=codexAuthPending || workflowBusy;
  if (workflowBusy) el('small','Chờ tác vụ đang chạy hoàn tất để đổi tài khoản.',panel);
  else el('small','Dùng phiên Codex trong WSL. Đổi tài khoản sẽ đăng xuất phiên hiện tại; lịch sử video vẫn được giữ.',panel);
  if (codexAuth.state === 'complete' && codexAuth.logged_in) el('small','Chọn “Thử lại / tiếp tục tạo kịch bản” hoặc chạy lại cảnh để tiếp tục.',panel);
}
function renderAIProvider() {
  const panel=$('ai-provider');panel.hidden=!aiProvider;panel.replaceChildren();
  if(panel.hidden)return;
  el('h2','Nguồn AI',panel);
  const select=field(panel,'Dùng AI cho kịch bản và cảnh','select','','ai-provider');
  for(const [value,text] of [['codex','Codex · ChatGPT']]) {const option=el('option',text,select);option.value=value;}
  if(state.ai_backend !== 'codex'){const option=el('option','OpenRouter (đã cấu hình)',select);option.value=state.ai_backend;}
  select.value=state.ai_backend;select.disabled=workflowBusy || providerPending || codexAuthPending;
  select.onchange=async()=>{
    providerPending=true;select.disabled=true;
    try {
      const response=await fetch('/api/ai-provider',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify({request_id:crypto.randomUUID(),backend:select.value})});
      const data=await response.json();if(!response.ok)throw Error(data.error);
      revisionSeen=-1;await refresh();notice('Đã chọn Codex. Bạn có thể đăng nhập hoặc tiếp tục tạo video.');
    }catch(error){select.value=state.ai_backend;notice(error.message,true);}
    finally{providerPending=false;renderAIProvider();}
  };
  el('small','Model: '+aiProvider.model,panel);
}
function field(parent, title, tag, value, name) {
  const wrapper = el('label', undefined, parent, 'field');
  el('span', title, wrapper);
  const input = el(tag, undefined, wrapper);
  input.name = name; input.id = 'idea-' + name; input.value = value || '';
  return input;
}

function render() {
  const playing = $('continuous-video');
  if (playing) previewPlayback = {path:playing.dataset.path, time:playing.currentTime, paused:playing.paused, muted:playing.muted, volume:playing.volume};
  if ($('idea-form')) captureIdea();
  renderShell();
  renderCodexAccount();
  for (const id of ['idea-page','script-history-page','controls','scene-progress','viewer','editor','feedback','revisions','voice-panel','final-review','continuous-preview']) $(id).replaceChildren();
  if (currentPage === 'idea') { renderIdea(); return; }
  if (currentPage === 'history') { renderScriptHistory(); return; }
  if (state.stage === 'script') {
    if (currentPage === 'preview') { renderVoice(); renderContinuousPreview(); return; }
    $('scenes').replaceChildren(); $('scene-strip').replaceChildren(); $('scene-count').textContent = '0';
    emptyViewer($('viewer'), 'Cần tạo và duyệt kịch bản', 'Trở về bước Ý tưởng để tạo kịch bản từ nội dung bạn nhập, sau đó duyệt trước khi thiết kế cảnh.');
    button($('viewer'), '← Về ý tưởng và kịch bản', () => navigate('idea'), 'primary');
    $('editor').hidden = true; $('feedback').hidden = true; return;
  }
  renderVoice(); renderFinal(); renderContinuousPreview();
  renderScenes();
  if (!sid) { emptyViewer($('viewer'), 'Chưa có cảnh', 'Duyệt kịch bản trước khi tạo thiết kế cảnh.'); $('editor').hidden = true; $('feedback').hidden = true; return; }
  $('feedback').hidden = false;
  const artifacts = state.artifacts.filter(a => a.scene_id === sid && ['scene_design','scene_clip','scene_av'].includes(a.stage));
  const preferred = currentPage === 'design' ? latest(sid,'scene_design') : latest(sid,'scene_av') || latest(sid,'scene_clip');
  if (!userHistory) selection = preferred ? preferred.stage + ':' + preferred.version : null;
  if (selection && !artifacts.some(a => a.stage + ':' + a.version === selection)) { selection = null; userHistory = false; }
  const artifact = artifacts.find(a => a.stage + ':' + a.version === selection);
  const versions = el('div', undefined, $('controls'), 'version-details');
  el('small', sid, versions);
  if (artifacts.length) {
    const chooser = el('select', undefined, versions); chooser.setAttribute('aria-label', 'Lịch sử phiên bản');
    if (!selection) { const option = el('option', 'Chưa có bản dựng', chooser); option.value = ''; }
    for (const a of artifacts) { const option = el('option', label(a.stage) + ' · ' + a.version + ' · ' + (a.invalidated ? 'Đã thay thế đầu vào' : label(reviewFor(a)?.status || 'pending')), chooser); option.value = a.stage + ':' + a.version; }
    chooser.value = selection || ''; chooser.onchange = () => { selection = chooser.value; userHistory = true; render(); };
  }
  if (['scene_design','scene_clip'].includes(state.stage)) {
    const retry=sceneJob(sid)?.status==='failed' || Boolean(state.scene_drafts?.[sid]);
    const text=retry?'Chạy lại và tiếp tục các cảnh sau':'Tạo liên tiếp từ cảnh này';
    button($('controls'),text,()=>action('continue',{scene_id:sid,new_attempt:retry}),'text-button').disabled=workflowBusy || manimRuntime?.available===false;
  }
  if(state.scene_generation?.codex_usage_limited){el('p','Lượt Codex mới nhất báo hết hạn mức. Đổi tài khoản Codex hoặc dựng lại thiết kế đã lưu.',$('controls'),'feedback-help');button($('controls'),'Đổi tài khoản Codex',openCodexAccount);}
  if((state.saved_design_scenes || []).includes(sid))button($('controls'),'Dựng lại thiết kế đã lưu',()=>action('render-saved',{scene_id:sid}),'text-button').disabled=workflowBusy || manimRuntime?.available===false;
  const draft=state.scene_drafts?.[sid];
  if(!artifact && draft){
    downloadLink($('controls'),'Tải video nháp',draft.reference.path);
    downloadLink($('controls'),'Tải thiết kế nháp',draft.design_path);
    downloadLink($('controls'),'Xem log render',draft.reference.log_path);
  }
  if (artifact?.stage === 'scene_design') {
    const rendered = state.design_previews?.[sid + ':' + artifact.version] || artifact.manim_render;
    if (rendered?.renderer !== 'manim') button($('controls'), 'Dựng preview từ thiết kế', () => action('design-preview',{scene_id:sid,version:artifact.version,sha256:artifact.sha256}), 'primary').disabled = workflowBusy || manimRuntime?.available === false;
    else {
      downloadLink($('controls'), 'Tải mã Manim .py', rendered.code_path);
      downloadLink($('controls'), 'Tải video cảnh', rendered.path);
      downloadLink($('controls'), 'Xem log render', rendered.log_path);
    }
  }
  if (currentPage === 'preview') {
    const ready = state.scene_ids.length > 0 && state.scene_ids.every(id => reviewFor(latest(id,'scene_clip'))?.status === 'approved');
    const av = button($('controls'),'Tạo/tiếp tục cảnh có tiếng', () => action('av-generate',{scene_id:sid}), 'primary'); av.disabled = !ready || workflowBusy || state.stage === 'completed';
    if (artifact?.stage === 'scene_av') button($('controls'),'Xem clip không tiếng', () => { const a=latest(sid,'scene_clip');selection=a.stage+':'+a.version;userHistory=true;render(); });
    else if (latest(sid,'scene_av')) button($('controls'),'Xem cảnh có tiếng', () => { const a=latest(sid,'scene_av');selection=a.stage+':'+a.version;userHistory=true;render(); });
  }
  if(currentPage==='design' && artifact?.stage==='scene_design' && artifact.design_pipeline!==2){
    const clip=latest(sid,'scene_clip');
    if(clip && state.stage!=='completed')button($('controls'),'Thiết kế lại cảnh theo quy trình mới',()=>action('feedback',{stage:'scene_clip',scene_id:sid,version:clip.version,sha256:clip.sha256,feedback:'Thiết kế lại cảnh theo quy trình storyboard và JSON v2: làm rõ ý trong lời dẫn đã duyệt, liên kết nhóm/điểm/đường/nhãn bằng quan hệ và tham số chung; giữ nguyên lời dẫn và thời lượng ước tính. Kiểm tra hình sau render trước khi đưa vào duyệt.'}),'primary').disabled=workflowBusy;
  }
  renderSceneProgress(artifact); renderViewer(artifact); renderEditor(artifact); renderFeedback(artifact, artifacts); renderRevisions();
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const response = await fetch('/api/state');
    if (!response.ok) throw Error('Không đọc được trạng thái dự án.');
    const data = await response.json(); token = data.token;
    const compatibilityChanged = apiCompatible !== ((data.api_version === 1 && data.capabilities?.includes('script_history') && data.capabilities?.includes('new_generation') && data.capabilities?.includes('manim_design')));
    apiCompatible = (data.api_version === 1 && data.capabilities?.includes('script_history') && data.capabilities?.includes('new_generation') && data.capabilities?.includes('manim_design'));
    if (state && state.project_id !== data.project.project_id) {
      previewMode = null; previewPlayback = null; voiceDraft = null; musicDraft = null; sceneReviewOpen = false; previewAttempts.clear();
      ideaDraft = undefined; scriptExpanded = false; scriptHistory = {items:[]}; selectedScriptVersion = null; scriptDetails.clear(); feedbackDrafts.clear(); selection = null; userHistory = false; sid = undefined;
      $('idea-form')?.remove(); revisionSeen = -1;
    }
    state = data.project;
    sid = state.scene_ids.includes(sid) ? sid : state.scene_ids[0];
    $('connection').textContent = 'Không gian làm việc cục bộ';
    workflowTasks = data.tasks || {};
    const tasks = Object.values(workflowTasks);
    const wasBusy = workflowBusy;
    codexAuth = data.codex_auth || null;
    aiProvider = data.ai_provider || null;
    const authSignature = JSON.stringify([codexAuth,aiProvider]);
    const authChanged = authSignature !== codexAuthSignature; codexAuthSignature = authSignature;
    workflowBusy = tasks.some(t => ['working','queued'].includes(t.state)) || Boolean(codexAuth?.busy);
    if (authChanged) renderCodexAccount();
    if (!apiCompatible) notice(SERVER_UPDATE_MESSAGE, true);
    else if (codexAuth?.busy) notice(codexAuth.message);
    else if (workflowBusy) notice(state.stage === 'script' ? 'Đang nghiên cứu và viết kịch bản từ ý tưởng đã gửi…' : 'Đang xử lý cảnh. Bạn có thể tiếp tục xem và ghi chú.');
    else if (tasks.some(t => t.error)) notice('Tác vụ đã dừng. Xem lỗi và chi tiết kiểm tra tại cảnh tương ứng.', true);
    else if (wasBusy) notice(state.stage === 'script' ? 'Kịch bản đã sẵn sàng. Hãy đọc, góp ý hoặc duyệt để tạo thiết kế cảnh.' : 'Đã xử lý xong. Bạn có thể xem và duyệt nội dung mới.');
    else if (compatibilityChanged) notice('');
    const nextSignature=JSON.stringify([data.tasks,state.scene_generation?.jobs?.map(job => [job.job_id,job.status,job.progress])]);
    const tasksChanged=taskSignature !== nextSignature;taskSignature=nextSignature;
    if (currentPage === 'design' || currentPage === 'preview') renderSceneProgress();
    if (revisionSeen !== state.revision || tasksChanged || compatibilityChanged || authChanged) {
      const [extra, historyResponse] = await Promise.all([fetch('/api/context'), apiCompatible ? fetch('/api/script-history') : Promise.resolve(null)]);
      if (!extra.ok) throw Error('Không đọc được nội dung cảnh.');
      context = await extra.json();
      if (apiCompatible && !manimRuntime) {
        const runtimeResponse = await fetch('/api/manim-runtime');
        if (runtimeResponse.ok) manimRuntime = await runtimeResponse.json();
      }
      if (historyResponse) {
        if (!historyResponse.ok) throw Error('Không đọc được lịch sử kịch bản.');
        scriptHistory = await historyResponse.json();
      }
      revisionSeen = state.revision; render();
    }
  } catch (error) {
    $('connection').textContent = 'Chưa kết nối'; notice(error.message, true);
    const panel=$('scene-progress');
    if(panel && !panel.hidden){panel.replaceChildren();panel.className='scene-progress error';panel.setAttribute('aria-busy','false');el('strong','Mất kết nối máy chủ · Chưa xác định được tiến trình Manim',panel);}
  }
  finally { refreshing = false; }
}
refresh(); setInterval(refresh, 2000);

function downloadLink(parent,text,path) { if (!path) return; const a=el('a',text,parent);a.href=media(path);a.download=path.split('/').at(-1); }
