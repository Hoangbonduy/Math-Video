'use strict';

const scenePhases = {draft_ready:'Bản nháp đã dựng · Chưa duyệt',planning_storyboard:'Đang lập storyboard',checking_relations:'Đang kiểm tra quan hệ và chuyển động',rendering_draft:'Đang render bản nháp',evaluating_frames:'AI đang xem khung hình và đối chiếu storyboard',repairing_meaning:'Đang sửa storyboard',repairing_relation:'Đang sửa quan hệ',repairing_layout:'Đang sửa bố cục',repairing_timing:'Đang sửa nhịp',quality_failed:'Chưa đạt sau các vòng sửa',checking_model:'Đang kiểm tra mô hình AI',planning_assets:'Đang chuẩn bị hình minh họa',designing_scene:'Đang thiết kế cảnh',repairing_design:'Đang sửa thiết kế',writing_code:'Đang viết mã Manim',repairing_code:'Đang sửa mã Manim',rendering_manim:'Đang render Manim',ready_for_review:'Render xong · Chờ duyệt'};
function sceneJob(id) { return [...(state.scene_generation?.jobs || [])].reverse().find(j => j.scene_id === id && j.current !== false && (j.stage === state.stage || j.kind === 'manim_design_preview')); }
function sceneTask(id) {
  const keys = sceneJob(id)?.kind === 'manim_design_preview' ? ['preview:'+id, 'generate:'+id] : ['generate:'+id, 'preview:'+id];
  const tasks = keys.map(key => workflowTasks[key]).filter(Boolean);
  const batch = workflowTasks.workflow;
  if (batch && ['queued','working'].includes(batch.state)) {
    const active = state.scene_ids.filter(scene => ['planned','working'].includes(sceneJob(scene)?.status));
    const next = active.length ? active : [state.scene_ids.find(scene => !sceneJob(scene) && !latest(scene, state.stage))];
    if (next.includes(id)) tasks.push(batch);
  }
  return tasks.find(t => ['queued','working'].includes(t.state)) || tasks[0];
}
function stoppedScene() {
  if (workflowTasks.workflow?.state !== 'failed') return null;
  return state.scene_ids.map(scene => sceneJob(scene)).filter(job => job?.status === 'failed')
    .sort((a,b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0] || null;
}
function sceneRepairLabel(job) {
  const limit=job?.max_repairs ?? (job?.trial_mode ? 0 : 3);
  if(limit===0)return 'Ch\u1ea1y th\u1eed 1 l\u01b0\u1ee3t \u00b7 Kh\u00f4ng t\u1ef1 s\u1eeda';
  return (job?.trial_mode?'Ch\u1ea1y th\u1eed \u00b7 ':'')+'V\u00f2ng s\u1eeda '+(job?.repair_round ?? 0)+' / '+limit;
}
function scenePhase(job) { return scenePhases[job?.progress] || 'Đang chuẩn bị cảnh Manim'; }
function renderSceneProgress(artifact) {
  const panel=$('scene-progress'); panel.replaceChildren();
  panel.hidden=!['design','preview'].includes(currentPage) || !sid || !['scene_design','scene_clip'].includes(state.stage);
  if(panel.hidden)return;
  const job=sceneJob(sid),task=sceneTask(sid);
  const running=task && ['queued','working'].includes(task.state);
  const failed=!running && (task?.state==='failed' || job?.status==='failed');
  const draft=state.scene_drafts?.[sid];
  const rendered=artifact?.manim_render || state.design_previews?.[sid+':'+latest(sid,'scene_design')?.version] || draft?.reference;
  const ready=!running && !failed && rendered?.renderer==='manim';
  panel.className='scene-progress'+(failed?' error':'');
  panel.setAttribute('aria-busy',String(Boolean(running)));
  if(running){const spinner=el('span',undefined,panel,'spinner');spinner.setAttribute('aria-hidden','true');}
  const body=el('div',undefined,panel,'scene-progress-body');
  el('strong',running ? task.state==='queued'?'Đang xếp hàng':scenePhase(job) : failed?(rendered?'Đã dựng bản nháp · Cần chỉnh sửa':'Đã dừng · Chưa render được cảnh'):ready?'Video Manim đã sẵn sàng':'Chưa chạy tạo cảnh Manim',body);
  const engine = job?.kind==='saved_design_render' ? 'Thiết kế Codex đã lưu · Dựng tại máy' : (job?.kind === 'manim_design_preview' || job?.preview_engine === 'json') ? 'Preview từ thiết kế · Chạy tại máy' : 'AI: '+(job?.model || state.scene_generation?.model || 'Đang đọc cấu hình');
  el('p','Cảnh '+String(state.scene_ids.indexOf(sid)+1).padStart(2,'0')+' · '+engine+' · Render: Manim / Cairo',body);
  if(job?.design_pipeline===2)el('p','Storyboard → thiết kế → render → đánh giá hình · '+sceneRepairLabel(job),body);
  if(running)el('small',job?.updated_at?'Cập nhật tiến trình: '+new Date(job.updated_at).toLocaleTimeString('vi-VN')+' · Trang tự kiểm tra mỗi 2 giây.':'Yêu cầu đã được tiếp nhận. Trang tự kiểm tra mỗi 2 giây.',body);
  else if(failed){
    const error=job?.last_error || task?.error || 'Không hoàn tất tạo cảnh.';
    if(job?.quality_issues?.length){
      el('p',draft?.review?.summary || 'Bản nháp đã dựng nhưng chưa đạt các tiêu chí hình ảnh dưới đây.',body,'scene-error');
      const issues=el('ol',undefined,body,'scene-quality-issues');
      for(const issue of job.quality_issues){
        const row=el('li',undefined,issues);
        const when=Number.isFinite(issue.time) ? issue.time.toLocaleString('vi-VN',{maximumFractionDigits:3})+' giây · ' : '';
        el('strong',when+issue.problem,row);
        if(issue.fix)el('p','Gợi ý sửa: '+issue.fix,row);
      }
      const details=el('details',undefined,body);el('summary','Chi tiết kỹ thuật',details);
      el('pre',error+'\n\n'+JSON.stringify(job.quality_issues,null,2),details);
    }else el('p',error.length>900?error.slice(0,900)+'…':error,body,'scene-error');
    if(draft?.review?.criteria_total)el('p',draft.review.criteria_passed+'/'+draft.review.criteria_total+' tiêu chí storyboard đạt'+(job?.quality_issues?.length?' · Còn '+job.quality_issues.length+' lỗi cần sửa':'')+' · Bản nháp vẫn xem được bên dưới.',body);
  }
  else if(ready && draft?.review?.status==='not_run')el('p','Dựng từ thiết kế đã lưu · Chưa đánh giá lại bằng AI · Không gọi thêm Codex.',body);
  else if(!ready){
    const stopped=stoppedScene();
    if(!job && stopped){
      const number=String(state.scene_ids.indexOf(stopped.scene_id)+1).padStart(2,'0');
      el('p','Cảnh này chưa được tạo nên chưa có kết quả kiểm tra riêng. Lượt chạy trước đã dừng ở Cảnh '+number+'.',body);
    }
    el('small','Chọn “Tạo liên tiếp từ cảnh này” một lần; các cảnh sau sẽ tự chạy theo thứ tự.',body);
  }
}
function renderScenes() {
  $('scene-count').textContent = state.scene_ids.length;
  $('scenes').replaceChildren(); $('scene-strip').replaceChildren();
  $('scene-strip').hidden = currentPage !== 'preview';
  for (const [index, id] of state.scene_ids.entries()) {
    const artifact = latest(id, currentPage === 'preview' ? 'scene_clip' : 'scene_design');
    const review = reviewFor(artifact);
    const node = button($('scenes'), '', () => chooseScene(id), 'scene-button' + (id === sid ? ' selected' : ''));
    if (id === sid) node.setAttribute('aria-current', 'true');
    el('span', String(index + 1).padStart(2, '0'), node, 'scene-index');
    el('span', shortTitle(id), node, 'scene-name');
    const job = sceneJob(id), active = sceneTask(id);
    const running = active && ['queued','working'].includes(active.state);
    const status=running ? active.state === 'queued' ? 'Đang xếp hàng' : scenePhase(job) : job?.status === 'failed' && !artifact ? 'Đã dừng do lỗi' : !artifact && state.scene_drafts?.[id] ? 'Bản nháp · Chưa duyệt' : label(review?.status || 'Chưa tạo');
    node.setAttribute('aria-label', id + ' · ' + status);
    el('span', timing(id) + '\n' + status, node, 'scene-meta');
    if (running) { const spinner=el('span',undefined,node,'spinner scene-spinner'); spinner.setAttribute('aria-hidden','true'); }
    if (job?.last_error) node.title = job.last_error;
    const strip = button($('scene-strip'), String(index + 1).padStart(2, '0') + ' · ' + shortTitle(id), () => chooseScene(id), id === sid ? 'selected' : '');
    strip.title = shortTitle(id);
    if (id === sid) strip.setAttribute('aria-current', 'true');
  }
}
function showMedia(artifact, parent) {
  if (!artifact) return;
  const box = el('div', undefined, parent, 'candidate');
  el('h3', label(artifact.stage || 'scene_design') + ' · ' + artifact.version, box);
  if (artifact.stage === 'scene_clip' || artifact.manim_render) { const video = el('video', undefined, box); video.controls = true; video.preload = 'metadata'; video.src = media(artifact.manim_render?.path || artifact.path); video.setAttribute('aria-label', 'Clip ' + artifact.version); }
  else if (artifact.preview_path) { const image = el('img', undefined, box); image.src = media(artifact.preview_path); image.alt = 'Ảnh phác bố cục ' + artifact.scene_id + ' ' + artifact.version; }
  showDescription(artifact, box);
}
function showDescription(artifact, parent) {
  if (!artifact?.review_path) return;
  const details = el('details', undefined, parent, 'technical-details'); el('summary', 'Xem mô tả thiết kế đầy đủ', details);
  details.addEventListener('toggle', async () => {
    if (!details.open || details.dataset.loaded) return;
    details.dataset.loaded = 'true'; const pre = el('pre', 'Đang đọc mô tả…', details);
    try { const response = await fetch(media(artifact.review_path)); if (!response.ok) throw Error('Không đọc được mô tả.'); pre.textContent = await response.text(); }
    catch (error) { pre.textContent = error.message; }
  });
}
function renderViewer(artifact) {
  const viewer = $('viewer');
  const heading = el('div', undefined, viewer, 'viewer-heading');
  el('span', 'Cảnh ' + String(state.scene_ids.indexOf(sid) + 1).padStart(2, '0') + ' · ' + shortTitle(sid), heading);
  const draft=!artifact ? state.scene_drafts?.[sid] : null;
  const reference = draft?.reference || (artifact?.stage === 'scene_design' && (state.design_previews?.[sid + ':' + artifact.version] || artifact.manim_render));
  const motion = reference?.renderer === 'manim' ? reference : null;
  const isClip = ['scene_clip','scene_av'].includes(artifact?.stage) || Boolean(motion);
  const playPath = motion?.path || artifact?.path;
  el('span', draft ? 'Bản nháp · Chưa duyệt' : motion ? 'Video thiết kế · Manim Cairo' : isClip ? label(artifact.stage) + ' · ' + artifact.version : artifact ? 'Thiết kế cũ · Chưa render Manim' : 'Chưa có video Manim', heading, 'viewer-kind');
  const canvas = el('div', undefined, viewer, 'scene-canvas');
  canvas.classList.toggle('portrait', state.idea?.format === 'portrait');
  let video;
  if (isClip) {
    video = el('video', undefined, canvas); video.controls = true; video.preload = 'metadata'; video.src = media(playPath); video.dataset.current = 'true'; video.setAttribute('aria-label', 'Bản dựng của ' + sid);
    video.onerror = () => notice('Không tải được clip. Hãy kiểm tra tệp của phiên bản đang xem.', true);
  } else if (artifact?.preview_path) {
    const image = el('img', undefined, canvas); image.src = media(artifact.preview_path); image.alt = 'Phác thảo bố cục ' + shortTitle(sid) + ' · ' + artifact.version;
    image.onerror = () => { image.remove(); emptyViewer(canvas, 'Không tải được ảnh phác', 'Kiểm tra lại tệp ảnh của phiên bản này.'); };
  } else if (sceneTask(sid) && ['queued','working'].includes(sceneTask(sid).state)) {
    emptyViewer(canvas, scenePhase(sceneJob(sid)), 'Video Manim sẽ xuất hiện tại đây sau khi render và kiểm tra thành công. Tiến trình đang được cập nhật mỗi 2 giây.');
    canvas.setAttribute('aria-busy','true');
  } else if (sceneJob(sid)?.status === 'failed' || sceneTask(sid)?.state === 'failed') {
    emptyViewer(canvas, 'Tạo cảnh đã dừng do lỗi', 'Xem nguyên nhân ở bảng tiến trình phía trên rồi chọn tạo / tiếp tục để thử lại.');
  } else emptyViewer(canvas, currentPage === 'preview' ? 'Cảnh này chưa có bản dựng' : 'Cảnh này chưa có thiết kế', currentPage === 'preview' ? 'Duyệt thiết kế cảnh, sau đó chọn “Tạo / tiếp tục cảnh bằng Manim” để dựng clip.' : 'Chọn “Tạo / tiếp tục cảnh bằng Manim” để lập thiết kế từ kịch bản đã duyệt; preview được dựng tại máy từ JSON thiết kế.');
  const design = designFor(sid, artifact?.stage === 'scene_design' ? artifact.version : latest(sid, 'scene_design')?.version);
  const narration = design.narration || sceneData(sid).narration;
  if (narration) el('p', narration, viewer, 'scene-caption');
  const transport = el('div', undefined, viewer, 'transport');
  const play = button(transport, isClip ? '▶  Phát bản dựng' : '◇  Ảnh phác tĩnh', async () => { if (video.paused) { try { await video.play(); } catch { notice('Trình duyệt chưa phát được clip.', true); } } else video.pause(); });
  play.disabled = !isClip;
  const range = el('input', undefined, transport); range.type = 'range'; range.min = '0'; range.max = '100'; range.value = '0'; range.step = '.1'; range.disabled = !isClip; range.setAttribute('aria-label', 'Tua bản dựng');
  const time = el('span', isClip ? '00:00 / —' : (design.duration ? clock(design.duration) + ' · dự kiến' : 'Chưa dựng'), transport, 'time');
  if (video) {
    const update = () => { range.max = Number.isFinite(video.duration) ? video.duration : 100; range.value = video.currentTime; time.textContent = clock(video.currentTime) + ' / ' + (Number.isFinite(video.duration) ? clock(video.duration) : '—'); };
    video.addEventListener('loadedmetadata', update); video.addEventListener('timeupdate', update);
    video.addEventListener('play', () => { play.textContent = 'Ⅱ  Tạm dừng'; }); video.addEventListener('pause', () => { play.textContent = '▶  Phát bản dựng'; });
    range.oninput = () => { video.currentTime = Number(range.value); update(); };
  }
}
function emptyViewer(parent, title, description) {
  const empty = el('div', undefined, parent, 'empty-state'); el('span', '▧', empty, 'empty-icon'); el('h2', title, empty); el('p', description, empty);
}
function focusFeedback() { $('feedback').scrollIntoView({behavior:'smooth', block:'center'}); $('feedback-text')?.focus(); }
function renderEditor(artifact) {
  const editor = $('editor'); editor.hidden = currentPage === 'preview';
  if(currentPage === 'design') renderDesignQuality(artifact,editor);
  const tabs = el('div', undefined, editor, 'tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Chi tiết thiết kế');
  const entries = [['narration', 'Lời dẫn'], ['visual', 'Hình minh họa'], ['math', 'Đồ thị & liên kết']];
  for (const [key, title] of entries) {
    const tab = button(tabs, title, () => { editorTab = key; editor.replaceChildren(); renderEditor(artifact); $('tab-' + key)?.focus(); });
    tab.id = 'tab-' + key; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(editorTab === key)); tab.setAttribute('aria-controls', 'editor-panel'); tab.tabIndex = editorTab === key ? 0 : -1;
    tab.onkeydown = e => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return; e.preventDefault(); const index = entries.findIndex(item => item[0] === key); const next = e.key === 'Home' ? 0 : e.key === 'End' ? 2 : (index + (e.key === 'ArrowRight' ? 1 : 2)) % 3; tabs.children[next].click(); };
  }
  const panel = el('div', undefined, editor, 'tab-content'); panel.id = 'editor-panel'; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', 'tab-' + editorTab);
  const design = designFor(sid, artifact?.stage === 'scene_design' ? artifact.version : latest(sid, 'scene_design')?.version);
  if (editorTab === 'narration') {
    el('h3', 'Lời thuyết minh của cảnh', panel);
    el('div', design.narration || sceneData(sid).narration || 'Chưa có lời dẫn cho cảnh này.', panel, 'narration-text');
    const bottom = el('div', undefined, panel, 'editor-bottom');
    el('small', 'Lời dẫn lấy từ kịch bản đã duyệt. Góp ý tại đây áp dụng cho thiết kế và bản dựng.', bottom);
    button(bottom, 'Góp ý thiết kế', focusFeedback);
  } else if (editorTab === 'visual') {
    el('h3', 'Hình minh họa & chuyển động', panel);
    el('p', sceneData(sid).visual_intent || design.purpose || 'Chưa có mô tả hình ảnh.', panel);
    const list = el('ul', undefined, panel, 'object-list');
    const kinds = {text:'Văn bản', line:'Đường thẳng', plot:'Đồ thị', image:'Hình ảnh', circle:'Hình tròn', rectangle:'Hình chữ nhật'};
    for (const object of design.objects || []) { const row = el('li', undefined, list); el('strong', object.label || object.id, row); el('span', kinds[object.kind] || object.kind, row); }
    if (design.timeline?.length) el('small', design.timeline.length + ' nhịp chuyển động · ' + clock(design.duration) + ' dự kiến', panel);
    showDescription(artifact, panel);
  } else {
    el('h3', 'Dữ liệu toán của cảnh', panel);
    const datasets=design.schema_version===2 ? (design.graphs||[]).map(g=>({...g,start:g.x_min,end:g.x_max,count:g.samples})) : design.math||[];
    if (!datasets.length) el('p', 'Cảnh này chưa khai báo bộ dữ liệu toán. Không có liên kết được suy ra tự động.', panel);
    if(design.schema_version===2){el('p',`${design.groups.length} nhóm · ${design.parameters.length} tham số · ${design.bindings.length} quan hệ phụ thuộc`,panel);for(const relation of design.bindings)el('p',relation.target+' ← '+relation.kind+' · '+[relation.graph,relation.source_a,relation.source_b,relation.expression,relation.x_expression].filter(Boolean).join(', '),panel,'muted');}
    for (const dataset of datasets) { const item = el('div', undefined, panel, 'math-item'); el('code', dataset.expression, item); el('span', `${dataset.variable}: ${dataset.start} → ${dataset.end} · ${dataset.count} điểm · ${dataset.unit || 'không có đơn vị'}`, item); }
    const refs = artifact?.resource_refs || [];
    el('p', refs.length ? 'Tài nguyên liên kết: ' + refs.map(r => typeof r === 'string' ? r : r.resource_id || r.id || '').join(', ') : 'Tài nguyên của từng cảnh được theo dõi riêng. Khi góp ý ảnh hưởng nhiều cảnh, hệ thống sẽ đề nghị bạn duyệt phạm vi.', panel, 'muted');
  }
}
function renderFeedback(artifact, artifacts) {
  const parent = $('feedback');
  const review = reviewFor(artifact);
  const title = el('div', undefined, parent, 'feedback-title'); el('h2', 'Ghi chú duyệt', title); badge(title, review?.status || 'Chưa có bản');
  const visual = el('div', undefined, parent, 'review-section'); el('h3', 'Hình minh họa ↔ đồ thị', visual); el('p', 'Kiểm tra bố cục, chuyển động và sự khớp nhau giữa hình ảnh với dữ liệu.', visual);
  button(visual, 'Mở thiết lập liên kết', () => { editorTab = 'math'; if (currentPage !== 'design') navigate('design'); else render(); $('editor').scrollIntoView({behavior:'smooth', block:'center'}); }, 'text-button');
  const math = el('div', undefined, parent, 'review-section'); el('h3', 'Nội dung toán', math); el('p', 'Kiểm tra giả định, đơn vị và ý nghĩa của từng bước minh họa.', math);
  if (!artifact) {
    el('p', currentPage === 'preview' ? 'Chưa có clip để duyệt. Hãy quay lại thiết kế cảnh.' : 'Chưa có phiên bản để góp ý hoặc duyệt.', parent, 'muted');
  } else {
    const active = state.scene_revisions.find(r => r.affected.includes(sid) && !['completed','cancelled'].includes(r.status));
    const isLatest = artifact === latest(sid,artifact.stage);
    if (artifact.invalidated) el('p','Đầu vào đã thay đổi. Phiên bản này chỉ dùng để xem lịch sử.',parent,'feedback-help');
    if (!isLatest) el('p', 'Bạn đang xem bản cũ. Chọn bản mới nhất để gửi góp ý hoặc duyệt.', parent, 'feedback-help');
    const designLocked = artifact.stage === 'scene_design' && (review?.status === 'approved' || state.stage !== 'scene_design');
    if (!active && isLatest && !designLocked && !artifact.invalidated && state.stage !== 'completed') {
      const wrapper = el('label', undefined, parent, 'field'); el('span', 'Điều cần chỉnh sửa', wrapper);
      const text = el('textarea', undefined, wrapper); text.id = 'feedback-text'; text.placeholder = 'Ví dụ: giữ phần minh họa lâu hơn để người xem theo kịp…'; text.setAttribute('aria-label', 'Góp ý'); text.maxLength = 10000;
      const key = sid + ':' + selection;
      const draft = feedbackDrafts.get(key) || {};
      text.value = draft.text || '';
      const save = () => feedbackDrafts.set(key, {text:text.value, time:time?.value || ''});
      let time;
      if (['scene_clip','scene_av'].includes(artifact.stage)) {
        const row = el('div', undefined, parent, 'time-input-row'); time = el('input', undefined, row); time.type = 'number'; time.min = '0'; time.step = '.1'; time.placeholder = 'Số giây'; time.value = draft.time || ''; time.setAttribute('aria-label', 'Thời điểm trong clip'); time.oninput = save;
        button(row, 'Lấy thời điểm đang xem', () => { const video = $('viewer').querySelector('video'); if (video) { time.value = video.currentTime.toFixed(1); save(); } }, 'text-button');
      }
      text.oninput = save;
      const actions = el('div', undefined, parent, 'actions');
      button(actions, 'Gửi góp ý', async () => {
        if (!text.value.trim()) { notice('Hãy nhập điều cần chỉnh sửa trước khi gửi.', true); text.focus(); return; }
        if (time?.value && (!Number.isFinite(Number(time.value)) || Number(time.value) < 0)) { notice('Thời điểm góp ý phải là số giây không âm.', true); time.focus(); return; }
        const submitted = await action('feedback', {stage:artifact.stage, scene_id:sid, version:artifact.version, sha256:artifact.sha256, feedback:text.value, time_seconds:time?.value ? Number(time.value) : null});
        if (submitted) feedbackDrafts.delete(key);
      });
      if (review?.status === 'pending') button(actions, artifact.stage === 'scene_design' ? 'Duyệt thiết kế' : 'Duyệt bản dựng', () => action('approve', {stage:artifact.stage, scene_id:sid, version:artifact.version, sha256:artifact.sha256,preview_sha256:state.design_previews?.[sid + ':' + artifact.version]?.sha256}), 'primary');
      el('small', state.ai_backend === 'codex' ? 'Góp ý dùng hạn mức Codex để tạo bản mới. Thiết kế thay đổi sẽ cần bạn duyệt trước khi dựng.' : 'Góp ý dùng ngân sách dự án để tạo bản mới. Thiết kế thay đổi sẽ cần bạn duyệt trước khi dựng.', parent, 'feedback-help');
    } else if (designLocked) {
      el('p', 'Thiết kế này đã được chốt. Để yêu cầu thay đổi, hãy góp ý trên bản dựng của cảnh.', parent, 'feedback-help');
      button(parent, 'Xem bản dựng →', () => { userHistory = false; selection = null; navigate('preview'); });
    } else if (active) {
      el('p', 'Đang có vòng sửa: ' + label(active.status) + '. Xem tiến độ và thao tác tiếp ở bên dưới.', parent, 'feedback-help');
      if (review?.status === 'pending' && active.clips[sid]?.version === artifact.version) button(el('div', undefined, parent, 'actions'), 'Duyệt bản dựng', () => action('approve', {stage:artifact.stage, scene_id:sid, version:artifact.version, sha256:artifact.sha256}), 'primary');
    }
  }
  if (currentPage === 'preview') {
    const output = el('div', undefined, parent, 'export-section'); el('h3', 'Bố cục xuất', output);
    el('div', (state.idea?.format === 'portrait' ? '1080 × 1920' : '1920 × 1080') + ' · 30 FPS · Có tiếng & phụ đề', output, 'export-format');
    const ready = state.scene_ids.length > 0 && state.scene_ids.every(id => reviewFor(latest(id,'scene_av'))?.status === 'approved');
    const exportButton = button(output, ready ? 'Ghép bản xem trước cuối' : 'Chờ đủ cảnh có tiếng được duyệt', () => action('assemble', {}), 'primary'); exportButton.disabled = !ready || state.stage === 'completed';
    el('small', 'Duyệt từng cảnh có tiếng, sau đó duyệt toàn bộ video trước khi chốt đầu ra.', output, 'feedback-help');
  }
}
function renderRevisions() {
  for (const r of state.scene_revisions.filter(item => item.affected.includes(sid))) {
    const panel = el('section', undefined, $('revisions'), 'card revision-panel');
    el('h3', 'Vòng sửa · ' + label(r.status), panel); el('p', r.feedback, panel);
    if (r.plan) { el('p', r.plan.summary, panel); el('p', 'Phạm vi: ' + r.affected.map(id => shortTitle(id)).join(', '), panel, 'muted'); }
    if (r.last_error) el('pre', r.last_error, panel);
    const actions = el('div', undefined, panel, 'actions');
    if (r.status === 'awaiting_scope') { button(actions, 'Đồng ý phạm vi và thực hiện', () => action('scope', {revision_id:r.id, round:r.round || 0, plan_sha256:r.plan_sha256}), 'primary'); el('p', 'Muốn thu hẹp phạm vi: hủy yêu cầu này rồi gửi góp ý cụ thể hơn.', panel, 'muted'); }
    if (['awaiting_scope','out_of_scope','failed','stale','planned'].includes(r.status) && !Object.keys(r.designs).length && !Object.keys(r.clips).length) button(actions, 'Hủy trước khi tạo bản mới', () => action('cancel', {revision_id:r.id}));
    if (r.designs[sid]) { showMedia(r.designs[sid], panel); const d=r.designs[sid]; button(panel,'Xem preview từ thiết kế',()=>action('design-preview',{scene_id:sid,version:d.version,sha256:d.sha256})); const motion=state.design_previews?.[sid+':'+d.version]; if(motion) showMedia({...d,stage:'scene_clip',path:motion.path},panel); if (r.status === 'awaiting_design' && !r.designs[sid].approved) button(panel, 'Duyệt thiết kế và dựng lại', () => action('approve-design', {revision_id:r.id, round:r.round || 0, scene_id:sid, sha256:r.designs[sid].sha256}), 'primary'); }
    if (r.status === 'awaiting_clip' && r.clips[sid]) button(actions, 'Duyệt clip mới', () => action('approve', {stage:'scene_clip', scene_id:sid, version:r.clips[sid].version, sha256:r.clips[sid].sha256}), 'primary');
    if (r.clips[sid]) { const compare = el('div', undefined, panel, 'compare'); showMedia(r.snapshot.scenes[sid].scene_clip, el('div', undefined, compare)); showMedia(r.clips[sid], el('div', undefined, compare)); }
    if (['failed','stale','out_of_scope','awaiting_scope','awaiting_design','awaiting_clip'].includes(r.status)) {
      const key = 'revision:' + r.id; const more = el('textarea', undefined, panel); more.placeholder = 'Góp ý bổ sung hoặc yêu cầu sửa bản mới'; more.setAttribute('aria-label', 'Góp ý tiếp'); more.value = feedbackDrafts.get(key) || ''; more.oninput = () => feedbackDrafts.set(key, more.value);
      button(el('div', undefined, panel, 'actions'), 'Yêu cầu sửa tiếp', async () => { if (!more.value.trim()) { notice('Hãy nhập góp ý bổ sung.', true); more.focus(); return; } if (await action('amend', {revision_id:r.id, round:r.round || 0, feedback:more.value})) feedbackDrafts.delete(key); });
    }
    for (const round of r.round_history || []) el('p', 'Góp ý trước: ' + round.feedback, panel, 'muted');
    if (r.status === 'failed') {
      button(actions, 'Tiếp tục từ tiến độ đã lưu', () => action('continue', {revision_id:r.id}));
      const uncertain = state.scene_generation?.jobs.filter(j => j.job_id.startsWith('c6_' + r.id)).flatMap(j => j.uncertain_requests) || [];
      for (const call of uncertain) button(actions, 'Thử lại request chưa rõ kết quả ' + call.slice(0,8), () => action('continue', {revision_id:r.id, retry_uncertain:call}));
    }
  }
  const history = state.review_history.filter(r => r.scene_id === sid);
  if (history.length) {
    const panel = el('details', undefined, $('revisions'), 'card history-panel'); el('summary', 'Lịch sử phiên bản & quyết định · ' + history.length, panel);
    for (const item of [...history].reverse()) {
      const row = el('div', undefined, panel, 'history-row');
      el('span', label(item.stage) + ' · ' + item.version, row); badge(row, item.status);
      if (item.feedback) el('span', item.feedback, row);
      button(row, 'Xem bản này', () => { selection = item.stage + ':' + item.version; userHistory = true; render(); }, 'text-button');
    }
  }
  if (state.assembly_manifest) el('p', 'Tất cả clip đã duyệt. Danh sách clip đã sẵn sàng cho bước ghép video.', $('revisions'), 'feedback-help');
}

function renderDesignQuality(artifact,parent) {
  if (!artifact || artifact.design_pipeline !== 2) return;
  const panel=el('details',undefined,parent,'design-quality');
  el('summary','Storyboard và kết quả kiểm tra cảnh',panel);
  el('p','Cảnh đã qua kiểm tra quan hệ trong chuyển động và AI đánh giá các khung hình. Bạn vẫn là người quyết định duyệt.',panel,'muted');
  const content=el('div',undefined,panel);
  panel.addEventListener('toggle',async()=>{
    if(!panel.open || panel.dataset.loaded)return;
    panel.dataset.loaded='true';content.textContent='Đang đọc bằng chứng…';
    try {
      const responses=await Promise.all([fetch(media(artifact.storyboard_path)),fetch(media(artifact.quality_path))]);
      if(responses.some(r=>!r.ok))throw Error('Không đọc được bằng chứng thiết kế.');
      const [story,quality]=await Promise.all(responses.map(r=>r.json()));content.replaceChildren();
      el('p',quality.summary || 'Đã qua kiểm tra',content);
      el('small','Số vòng sửa: '+quality.repairs+' / 3 · Model đánh giá: '+quality.review_model,content);
      for(const beat of story.beats){
        const row=el('article',undefined,content,'storyboard-beat');
        el('h3',clock(beat.start)+'–'+clock(beat.end)+' · '+beat.understand,row);
        el('p',beat.narration_excerpt,row);el('p','Hình: '+beat.show,row);el('p','Chuyển động: '+beat.motion,row);
        const criteria=el('ul',undefined,row);for(const text of beat.criteria)el('li',text,criteria);
      }
      const sheets=quality.evidence_files.filter(f=>f.path.includes('contact_sheet_') && f.path.endsWith('.jpg'));
      for(const ref of sheets){const img=el('img',undefined,content);img.src=media(ref.path);img.alt='Khung hình thực sau render theo các mốc storyboard';img.loading='lazy';}
      const links=el('div',undefined,content,'actions');downloadLink(links,'Tải storyboard JSON',artifact.storyboard_path);downloadLink(links,'Tải báo cáo kiểm tra',artifact.quality_path);
    }catch(error){content.textContent=error.message;panel.dataset.loaded='';}
  });
}
