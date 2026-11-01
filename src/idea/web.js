'use strict';

const editingScriptScenes = new Set();
const scriptDrafts = new Map();
let savingScriptScene = null;

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
  const goal = field(content, 'Ý tưởng kịch bản', 'textarea', draft.goal ?? context.script?.main_idea, 'goal');
  goal.className = 'tall';
  goal.placeholder = 'Viết 6 dòng, mỗi dòng 1 ý, đọc liền không khựng:\n1. Hook: điều nghe vô lý [1 câu]\n2. Xuất phát: chỉ 1 quy tắc / 1 con số gốc\n3. Leo: 2–3 mốc quen thuộc tại Việt Nam (Landmark 81, Fansipan…), tăng dần, ghi rõ từ nối: rồi / đến lần… / gấp tiếp thì…\n4. Payoff: trả lời đúng câu đã hỏi ở Hook\n5. Twist bắt buộc + Câu hỏi cuối tùy chọn: 1 câu twist; chỉ thêm 1 câu hỏi khi dòng 5 có sẵn câu hỏi. Nếu có, câu hỏi có đáp án là 1 con số hoặc 1 tên gọi. Ghi đáp án trong ngoặc, không đọc ra.\n6. Câu chốt: “Đăng ký kênh Axiora để biết thêm chi tiết.” (kèm ghi chú số hiện hình nếu cần).';
  const goalGuide = el('small', undefined, goal.parentElement, 'field-guide');
  el('span', 'Gồm đúng 6 dòng, mỗi dòng 1 ý, đọc liền không khựng:', goalGuide);
  const goalList = el('ol', undefined, goalGuide);
  el('li', 'Hook: điều nghe vô lý [1 câu]', goalList);
  el('li', 'Xuất phát: chỉ 1 quy tắc / 1 con số gốc', goalList);
  el('li', 'Leo: 2–3 mốc quen thuộc tại Việt Nam (Landmark 81, Fansipan…), tăng dần, ghi rõ từ nối: rồi / đến lần… / gấp tiếp thì…', goalList);
  el('li', 'Payoff: trả lời đúng câu đã hỏi ở Hook', goalList);
  el('li', 'Twist bắt buộc + Câu hỏi cuối tùy chọn: 1 câu twist; chỉ thêm 1 câu hỏi khi dòng 5 có sẵn câu hỏi. Nếu có, câu hỏi có đáp án là 1 con số hoặc 1 tên gọi. Ghi đáp án trong ngoặc, không đọc ra.', goalList);
  el('li', 'Câu chốt: “Đăng ký kênh Axiora để biết thêm chi tiết.” (kèm ghi chú số hiện hình nếu cần).', goalList);
  const row = el('div', undefined, content, 'field-row');
  field(row, 'Người xem', 'input', draft.audience ?? brief.audience, 'audience');
  const duration = field(row, 'Độ dài dự kiến', 'input', draft.duration ?? (brief.min_seconds ? brief.min_seconds + '–' + brief.max_seconds + ' giây' : '30–60 giây'), 'duration');
  duration.required = true; duration.placeholder = '30–60 giây';
  el('small', 'Thời lượng video trong 30–60 giây, gồm lời đọc và nhịp nghỉ xem hình Manim; ví dụ 30–45 giây.', duration.parentElement);
  const expression = field(content, 'Cách thể hiện', 'select', '', 'expression');
  for (const value of ['Hình minh họa + hoạt họa + đồ thị', 'Hoạt họa + lời dẫn', 'Đồ thị & mô hình toán']) { const option = el('option', value, expression); option.value = value; }
  expression.value = draft.expression || expression.options[0].value;
  const direction = el('section', undefined, form, 'card idea-card');
  el('h2', 'Định hướng hình ảnh', direction);
  const style = field(direction, 'Cách hiện bằng Manim', 'textarea', draft.style ?? brief.requirements, 'style');
  style.className = 'tall';
  style.placeholder = '1. Đối tượng chính + chuyển động: là gì, biến đổi thế nào?\n2. Khi scale vỡ thì chuyển sang mốc so sánh nào?';
  const styleGuide = el('small', undefined, style.parentElement, 'field-guide');
  el('span', 'Gồm 2 dòng:', styleGuide);
  const styleList = el('ol', undefined, styleGuide);
  el('li', 'Đối tượng chính + chuyển động: là gì, biến đổi thế nào?', styleList);
  el('li', 'Khi scale vỡ thì chuyển sang mốc so sánh nào?', styleList);
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
  const canEdit = state.stage === 'script' && !workflowBusy;
  const scenes = context.script?.scenes || [];
  const allEditing = scenes.length > 0 && scenes.every(s => editingScriptScenes.has(s.scene_id));
  if (canEdit && scenes.length > 0) {
    button(panel, allEditing ? 'Hủy sửa kịch bản' : 'Sửa tất cả lời đọc & hình ảnh', () => {
      if (allEditing) {
        editingScriptScenes.clear();
      } else {
        scenes.forEach(s => editingScriptScenes.add(s.scene_id));
      }
      render();
    }, 'text-button');
  }
  badge(panel, review?.status);
  el('p', artifact.version + (context.duration_estimate?.estimated_seconds ? ' · Ước tính ' + context.duration_estimate.estimated_seconds.toFixed(1) + ' giây' : ''), panel, 'muted');
  if (context.script?.title) el('h3', context.script.title, panel);
  if (context.script?.main_idea) el('p', context.script.main_idea, panel);
  for (const scene of scenes) {
    const section = el('div', undefined, panel, 'script-scene');
    const header = el('div', undefined, section, 'script-scene-header');
    el('h3', scene.scene_id, header);
    const isEditing = editingScriptScenes.has(scene.scene_id);
    const draftKey = artifact.version + ':' + scene.scene_id;
    const currentDraft = scriptDrafts.get(draftKey) || {
      narration: scene.narration,
      visual_intent: scene.visual_intent
    };

    if (canEdit && !isEditing) {
      button(header, 'Sửa lời đọc & hình ảnh', () => {
        editingScriptScenes.add(scene.scene_id);
        render();
      }, 'text-button');
    }

    if (isEditing) {
      const editBox = el('div', undefined, section, 'script-scene-edit');

      const narrField = el('label', undefined, editBox, 'field');
      el('span', 'Lời đọc', narrField);
      const narrArea = el('textarea', undefined, narrField, 'script-edit-narration');
      narrArea.id = 'edit-narration-' + scene.scene_id;
      narrArea.value = currentDraft.narration ?? scene.narration;
      narrArea.placeholder = 'Nhập lời đọc cho cảnh…';
      narrArea.oninput = () => {
        const d = scriptDrafts.get(draftKey) || { narration: scene.narration, visual_intent: scene.visual_intent };
        d.narration = narrArea.value;
        scriptDrafts.set(draftKey, d);
      };

      const visField = el('label', undefined, editBox, 'field');
      el('span', 'Hình ảnh (ý tưởng minh họa)', visField);
      const visArea = el('textarea', undefined, visField, 'script-edit-visual');
      visArea.id = 'edit-visual-' + scene.scene_id;
      visArea.value = currentDraft.visual_intent ?? scene.visual_intent;
      visArea.placeholder = 'Nhập mô tả hình ảnh cho cảnh…';
      visArea.oninput = () => {
        const d = scriptDrafts.get(draftKey) || { narration: scene.narration, visual_intent: scene.visual_intent };
        d.visual_intent = visArea.value;
        scriptDrafts.set(draftKey, d);
      };

      const sceneActions = el('div', undefined, editBox, 'actions script-scene-actions');
      const saveBtn = button(sceneActions, 'Lưu cảnh ' + scene.scene_id, async () => {
        const narr = (narrArea.value || '').trim();
        const vis = (visArea.value || '').trim();
        if (!narr) { notice('Lời đọc của cảnh ' + scene.scene_id + ' không được để trống.', true); narrArea.focus(); return; }
        if (!vis) { notice('Mô tả hình ảnh của cảnh ' + scene.scene_id + ' không được để trống.', true); visArea.focus(); return; }
        saveBtn.textContent = 'Đang lưu…';
        saveBtn.disabled = true;
        const ok = await action('script-edit', {
          version: artifact.version,
          sha256: artifact.sha256,
          scene_id: scene.scene_id,
          narration: narr,
          visual_intent: vis
        });
        if (ok) {
          scriptDrafts.delete(draftKey);
          editingScriptScenes.delete(scene.scene_id);
          notice('Đã lưu lời đọc và hình ảnh cảnh ' + scene.scene_id + '.');
          render();
        } else {
          saveBtn.textContent = 'Lưu cảnh ' + scene.scene_id;
          saveBtn.disabled = false;
        }
      }, 'primary');

      const cancelBtn = button(sceneActions, 'Hủy', () => {
        scriptDrafts.delete(draftKey);
        editingScriptScenes.delete(scene.scene_id);
        render();
      });
    } else {
      el('p', scene.narration, section);
      el('small', 'Hình ảnh: ' + scene.visual_intent, section);
    }
  }

  if (editingScriptScenes.size > 1 && canEdit) {
    const bulkBox = el('div', undefined, panel, 'card script-bulk-edit-bar');
    el('strong', 'Đang sửa ' + editingScriptScenes.size + ' cảnh', bulkBox);
    const bulkActions = el('div', undefined, bulkBox, 'actions');
    const saveAllBtn = button(bulkActions, 'Lưu tất cả cảnh đang sửa', async () => {
      const updates = [];
      for (const sid of editingScriptScenes) {
        const sc = scenes.find(s => s.scene_id === sid);
        if (!sc) continue;
        const narrEl = $('edit-narration-' + sid);
        const visEl = $('edit-visual-' + sid);
        const narr = (narrEl ? narrEl.value : sc.narration).trim();
        const vis = (visEl ? visEl.value : sc.visual_intent).trim();
        if (!narr) { notice('Lời đọc của cảnh ' + sid + ' không được để trống.', true); narrEl?.focus(); return; }
        if (!vis) { notice('Mô tả hình ảnh của cảnh ' + sid + ' không được để trống.', true); visEl?.focus(); return; }
        updates.push({ scene_id: sid, narration: narr, visual_intent: vis });
      }
      if (!updates.length) return;
      saveAllBtn.textContent = 'Đang lưu tất cả…';
      saveAllBtn.disabled = true;
      const ok = await action('script-edit', {
        version: artifact.version,
        sha256: artifact.sha256,
        scenes: updates
      });
      if (ok) {
        for (const sid of editingScriptScenes) {
          scriptDrafts.delete(artifact.version + ':' + sid);
        }
        editingScriptScenes.clear();
        notice('Đã lưu thay đổi cho ' + updates.length + ' cảnh.');
        render();
      } else {
        saveAllBtn.textContent = 'Lưu tất cả cảnh đang sửa';
        saveAllBtn.disabled = false;
      }
    }, 'primary');

    const cancelAllBtn = button(bulkActions, 'Hủy tất cả', () => {
      for (const sid of editingScriptScenes) {
        scriptDrafts.delete(artifact.version + ':' + sid);
      }
      editingScriptScenes.clear();
      render();
    });
    cancelAllBtn.disabled = Boolean(savingScriptScene);
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
