'use strict';

function ideaStorageKey() { return 'math-video-idea:' + state.project_id; }
function captureIdea() {
  if (!$('idea-form')) return ideaDraft;
  const values = Object.fromEntries(new FormData($('idea-form')));
  const file = $('idea-reference').files[0];
  values.reference = file?.name || $('idea-reference-name').textContent;
  ideaDraft = values;
  try { localStorage.setItem(ideaStorageKey(), JSON.stringify(values)); } catch { /* Server persistence remains available. */ }
  return values;
}
async function saveIdea() {
  return action('idea-draft', {idea:captureIdea()});
}
async function startIdea() {
  const form = $('idea-form');
  if (!form.reportValidity() || ideaBusy) return;
  const idea = captureIdea();
  ideaBusy = true;
  scriptExpanded = true;
  const ok = await action('start', {idea});
  ideaBusy = false;
  if (ok) notice('Đã nhận ý tưởng. Đang tạo kịch bản từ nội dung bạn nhập; tiến độ hiển thị bên dưới.');
  render();
}
function renderIdea() {
  let draft = ideaDraft || state.idea_draft || state.idea || state.idea_defaults || {};
  if (!ideaDraft && !state.idea_draft) {
    try { draft = JSON.parse(localStorage.getItem(ideaStorageKey()) || 'null') || draft; } catch { /* Server draft is still available. */ }
  }
  const brief = context.brief || {};
  const form = el('form', undefined, $('idea-page'), 'idea-grid'); form.id = 'idea-form'; form.onsubmit = e => e.preventDefault();
  form.addEventListener('input', captureIdea);
  form.addEventListener('change', captureIdea);
  const content = el('section', undefined, form, 'card idea-card');
  el('h2', 'Nội dung video', content);
  field(content, 'Chủ đề / câu hỏi mở đầu', 'textarea', draft.topic ?? state.topic, 'topic').required = true;
  field(content, 'Sau khi xem, người xem hiểu được gì?', 'textarea', draft.goal ?? context.script?.main_idea, 'goal');
  const row = el('div', undefined, content, 'field-row');
  field(row, 'Người xem', 'input', draft.audience ?? brief.audience, 'audience');
  const duration = field(row, 'Độ dài dự kiến', 'input', draft.duration ?? (brief.min_seconds ? brief.min_seconds + '–' + brief.max_seconds + ' giây' : '30–60 giây'), 'duration');
  duration.required = true; duration.placeholder = '30–60 giây';
  el('small', 'Khoảng thời lượng trong 30–60 giây, ví dụ 30–45 giây.', duration.parentElement);
  const expression = field(content, 'Cách thể hiện', 'select', '', 'expression');
  for (const value of ['Hình minh họa + hoạt họa + đồ thị', 'Hoạt họa + lời dẫn', 'Đồ thị & mô hình toán']) { const option = el('option', value, expression); option.value = value; }
  expression.value = draft.expression || expression.options[0].value;
  const direction = el('section', undefined, form, 'card idea-card');
  el('h2', 'Định hướng hình ảnh', direction);
  field(direction, 'Mô tả phong cách', 'textarea', draft.style ?? brief.requirements, 'style').className = 'tall';
  const reference = el('label', undefined, direction, 'field'); el('span', 'Tài liệu / video tham khảo', reference);
  const file = el('input', undefined, reference, 'reference-file'); file.type = 'file'; file.id = 'idea-reference';
  const fileName = el('small', draft.reference || 'Chưa chọn tệp', reference); fileName.id = 'idea-reference-name';
  file.onchange = () => { fileName.textContent = file.files[0]?.name || 'Chưa chọn tệp'; };
  el('small', 'Bản nháp chỉ ghi nhận tên tệp; tài liệu chưa được tải lên hoặc đưa vào kịch bản.', reference);
  const formats = el('fieldset', undefined, direction); formats.className = 'format-choices';
  el('legend', 'Phiên bản chính', formats);
  for (const [value, text] of [['landscape', 'YouTube · video ngang'], ['portrait', 'Facebook · video dọc']]) {
    const choice = el('label', undefined, formats, 'radio-choice'); const radio = el('input', undefined, choice); radio.type = 'radio'; radio.name = 'format'; radio.value = value; radio.checked = (draft.format || (context.render?.resolution?.[0] < context.render?.resolution?.[1] ? 'portrait' : 'landscape')) === value; el('span', text, choice);
  }
  const bottom = el('div', undefined, $('idea-page'), 'idea-bottom');
  el('small', 'Nhập ý tưởng rồi tạo kịch bản. Sau khi bạn duyệt kịch bản, phần mềm sẽ tạo thiết kế cảnh từ nội dung đó. Mỗi lần bấm tạo sẽ bắt đầu một lượt mới và lưu phiên bản riêng. Xem lại bản cũ trong Lịch sử kịch bản.', bottom);
  const start = button(bottom, state.artifacts.some(a => a.stage === 'script') ? 'Tạo kịch bản mới từ ý tưởng →' : 'Tạo kịch bản từ ý tưởng →', startIdea, 'primary');
  start.disabled = ideaBusy || workflowBusy || !apiCompatible;
  renderScript();
}
function renderScript() {
  const panel = el('section', undefined, $('idea-page'), 'card revision-panel');
  panel.id = 'script-review';
  el('h2', 'Kịch bản từ ý tưởng', panel);
  const artifact = latest(null, 'script');
  if (!artifact) {
    el('p', ['working','queued'].includes(workflowTasks.workflow?.state) ? 'Đang nghiên cứu và viết kịch bản từ ý tưởng đã gửi…' : 'Chưa có kịch bản. Nhập chủ đề phía trên rồi chọn “Tạo kịch bản từ ý tưởng”.', panel);
    if (!workflowBusy && state.generation?.status === 'failed' && state.generation.last_error) {
      if ((state.ai_backend === 'codex') && state.generation.billing_source !== state.ai_backend + '_subscription')
        el('p', 'Lỗi của lượt tạo trước. Thử lại sẽ dùng nguồn AI đang chọn.', panel, 'feedback-help');
      el('pre', state.generation.last_error, panel);
      if (/CODEX_(USAGE_LIMIT|LOGIN_REQUIRED|MODEL_UNAVAILABLE)/.test(state.generation.last_error)) button(panel,'Đăng nhập / đổi tài khoản AI',openCodexAccount);
    }
    if (state.idea) button(panel, 'Thử lại / tiếp tục tạo kịch bản', () => action('continue', {}), 'primary').disabled = workflowBusy;
    return;
  }
  const review = reviewFor(artifact);
  el('p', 'Tạo xong: ' + dateTime(artifact.created_at) + ' · Đã lưu trên máy', panel, 'muted');
  if (!scriptExpanded) {
    el('h3', context.script?.title || state.topic, panel);
    el('p', artifact.version + ' · ' + label(review?.status), panel, 'muted');
    const actions = el('div', undefined, panel, 'actions');
    button(actions, 'Mở kịch bản hiện tại', showCurrentScript, 'primary');
    button(actions, 'Xem lịch sử (' + scriptHistory.items.length + ')', () => navigate('history'));
    return;
  }
  button(panel, 'Thu gọn kịch bản', () => { scriptExpanded = false; render(); }, 'text-button');
  button(panel, 'Xem lịch sử (' + scriptHistory.items.length + ')', () => navigate('history'), 'text-button');
  badge(panel, review?.status);
  el('p', artifact.version + (context.duration_estimate?.estimated_seconds ? ' · Ước tính ' + context.duration_estimate.estimated_seconds.toFixed(1) + ' giây' : ''), panel, 'muted');
  if (context.script?.title) el('h3', context.script.title, panel);
  if (context.script?.main_idea) el('p', context.script.main_idea, panel);
  for (const scene of context.script?.scenes || []) {
    const section = el('div', undefined, panel, 'script-scene');
    el('h3', scene.scene_id, section);
    el('p', scene.narration, section);
    el('small', 'Hình ảnh: ' + scene.visual_intent, section);
  }
  downloadLink(panel, 'Đọc kịch bản đầy đủ và đối chiếu nguồn', artifact.path);
  for (const source of context.sources || []) {
    if (!/^https?:\/\//i.test(source.final_url || '')) continue;
    const row = el('p', undefined, panel); const link = el('a', source.title || source.final_url, row);
    link.href = source.final_url; link.target = '_blank'; link.rel = 'noopener noreferrer';
  }
  if (state.stage !== 'script') {
    button(panel, 'Mở thiết kế cảnh →', () => navigate('design'), 'primary'); return;
  }
  if (review?.status === 'pending') {
    const read = field(panel, 'Thời gian bạn đọc thử lời dẫn (giây)', 'input', '', 'read-seconds');
    read.type = 'number'; read.min = String(context.brief?.min_seconds || 30); read.max = String(context.brief?.max_seconds || 60); read.step = '.1';
    const feedback = field(panel, 'Góp ý kịch bản', 'textarea', '', 'script-feedback');
    const key = 'script:' + artifact.version;
    const saved = feedbackDrafts.get(key) || {};
    feedback.value = saved.text || ''; read.value = saved.time || '';
    const save = () => feedbackDrafts.set(key, {text:feedback.value, time:read.value});
    feedback.oninput = save; read.oninput = save;
    const actions = el('div', undefined, panel, 'actions');
    button(actions, 'Yêu cầu sửa kịch bản', async () => {
      if (!feedback.value.trim()) { notice('Hãy nhập góp ý kịch bản.', true); feedback.focus(); return; }
      await action('feedback', {stage:'script', version:artifact.version, sha256:artifact.sha256, feedback:feedback.value});
    }).disabled = workflowBusy;
    button(actions, 'Duyệt kịch bản và tạo thiết kế cảnh →', async () => {
      if (artifact.origin === 'ai' && (!read.value || !read.reportValidity())) { notice('Hãy đọc thử lời dẫn và nhập thời gian từ 30 đến 60 giây.', true); read.focus(); return; }
      if (await action('approve', {stage:'script', version:artifact.version, sha256:artifact.sha256, ...(artifact.origin === 'ai' ? {read_seconds:Number(read.value)} : {})})) navigate('design');
    }, 'primary').disabled = workflowBusy;
  } else {
    if (review?.feedback) el('p', review.feedback, panel);
    button(panel, 'Tiếp tục sửa kịch bản', () => action('continue', {}), 'primary').disabled = workflowBusy;
  }
}

function showCurrentScript() {
  scriptExpanded = true;
  navigate('idea');
  requestAnimationFrame(() => $('script-review')?.scrollIntoView({behavior:'smooth', block:'start'}));
}
async function newIdea() {
  ideaDraft = {...(state.idea_defaults || {topic:'', goal:'', audience:'', duration:'30–60 giây', style:'', reference:'', format:'landscape'})};
  $('idea-form')?.remove();
  scriptExpanded = false;
  navigate('idea');
  await action('idea-draft', {idea:ideaDraft});
  $('idea-topic')?.focus();
}
