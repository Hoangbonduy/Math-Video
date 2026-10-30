'use strict';

function dateTime(value) {
  if (!value) return 'Chưa có thời gian lưu';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Thời gian không hợp lệ' :
    new Intl.DateTimeFormat('vi-VN', {dateStyle:'medium', timeStyle:'medium', timeZone:'Asia/Ho_Chi_Minh', hour12:false}).format(date) + ' (GMT+7)';
}

async function openScriptVersion(version) {
  selectedScriptVersion = version;
  if (scriptDetails.has(version) || loadingScripts.has(version)) { render(); return; }
  loadingScripts.add(version);
  render();
  try {
    const response = await fetch('/api/script-history?version=' + encodeURIComponent(version));
    const value = await response.json();
    if (!response.ok) throw Error(value.error || 'Không đọc được phiên bản đã lưu.');
    if (scriptHistory.items.some(item => item.version === version)) scriptDetails.set(version, value);
  } catch (error) { notice(error.message, true); }
  finally { loadingScripts.delete(version); if (currentPage === 'history') render(); }
}
async function deleteScriptVersion(item) {
  if (deletingScript || workflowBusy || !apiCompatible) return;
  const consequence = item.current
    ? '\n\nĐây là kịch bản đang sử dụng. Dự án sẽ quay về bước tạo kịch bản; các bản dựng đã có vẫn được giữ lại.'
    : '';
  if (!window.confirm('Xóa kịch bản “' + item.title + '” (' + item.version + ')?\nTệp kịch bản sẽ bị xóa và không thể hoàn tác.' + consequence)) return;
  deletingScript = item.version;
  render();
  try {
    const response = await fetch('/api/script-delete', {
      method:'POST', headers:{'Content-Type':'application/json','X-Review-Token':token},
      body:JSON.stringify({request_id:crypto.randomUUID(),version:item.version,sha256:item.sha256})
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Không xóa được kịch bản.');
    scriptDetails.delete(item.version);
    scriptHistory.items = scriptHistory.items.filter(saved => saved.version !== item.version);
    if (selectedScriptVersion === item.version) selectedScriptVersion = null;
    revisionSeen = -1;
    await refresh();
    notice('Đã xóa kịch bản ' + item.version + (result.reset_to_script ? '. Bạn có thể tạo kịch bản mới từ mục Ý tưởng.' : '.'));
  } catch (error) { notice(error.message, true); }
  finally { deletingScript = null; if (currentPage === 'history') render(); }
}
function scriptDeleteButton(parent, item) {
  const remove = button(parent, deletingScript === item.version ? 'Đang xóa…' : 'Xóa kịch bản', () => deleteScriptVersion(item), 'danger-button');
  remove.disabled = Boolean(deletingScript) || workflowBusy || !apiCompatible;
  remove.setAttribute('aria-label', 'Xóa kịch bản ' + item.version);
  if (workflowBusy) remove.title = 'Chờ tác vụ đang chạy hoàn tất để xóa.';
  return remove;
}
function renderScriptHistory() {
  const page = $('script-history-page');
  const introduction = el('div', undefined, page, 'card history-introduction');
  el('p', 'Mỗi phiên bản được lưu trực tiếp trên máy, kèm thời gian tạo xong. Mở lại ứng dụng vẫn xem được lịch sử.', introduction);
  const location = el('p', undefined, introduction, 'muted storage-location');
  el('span', 'Thư mục lưu: ', location);
  el('code', scriptHistory.storage_path || state.project_dir + '/scripts', location);
  el('small', 'Kịch bản: scripts/ · Dữ liệu AI và nguồn: generation/ · Thời gian hiển thị theo giờ Việt Nam (GMT+7).', introduction);
  const search = el('input', undefined, page, 'history-search');
  search.type = 'search'; search.placeholder = 'Tìm theo tên hoặc phiên bản…';
  search.setAttribute('aria-label', 'Tìm kịch bản trong lịch sử'); search.value = historyQuery;
  const layout = el('div', undefined, page, 'script-history-layout');
  const list = el('div', undefined, layout, 'script-history-list');
  list.setAttribute('aria-label', 'Danh sách kịch bản đã lưu');
  const viewer = el('section', undefined, layout, 'card script-history-detail');
  viewer.id = 'history-script-detail'; viewer.setAttribute('aria-label', 'Nội dung kịch bản đã lưu');
  const items = scriptHistory.items;
  function rows() {
    list.replaceChildren();
    const matches = items.filter(item => (item.title + ' ' + item.version).toLocaleLowerCase('vi').includes(historyQuery.toLocaleLowerCase('vi')));
    if (!matches.length) el('p', items.length ? 'Không tìm thấy kịch bản phù hợp.' : 'Chưa có lịch sử. Tạo kịch bản đầu tiên từ mục Ý tưởng.', list, 'empty-state');
    for (const item of matches) {
      const card = el('article', undefined, list, 'card script-history-item' + (item.version === selectedScriptVersion ? ' selected' : ''));
      el('h2', item.title, card);
      const meta = el('p', undefined, card, 'history-meta');
      el('strong', item.version, meta); badge(meta, item.status);
      if (item.current) el('span', 'Đang sử dụng', meta, 'badge');
      else if (item.archived) el('span', 'Lượt cũ', meta, 'badge superseded');
      const time = el('time', 'Tạo xong: ' + dateTime(item.created_at), card);
      if (item.created_at) time.dateTime = item.created_at;
      if (!item.available) el('p', 'Tệp không còn nguyên vẹn. Kiểm tra thư mục lưu trên máy.', card, 'muted');
      const actions = el('div', undefined, card, 'actions');
      const open = button(actions, item.version === selectedScriptVersion ? 'Đang xem' : 'Xem kịch bản', () => openScriptVersion(item.version), 'primary');
      open.disabled = !item.available || item.version === selectedScriptVersion;
      if (item.available) downloadLink(actions, 'Tải .md', item.path);
      scriptDeleteButton(actions, item);
    }
  }
  search.oninput = () => { historyQuery = search.value; rows(); };
  rows();
  const item = items.find(item => item.version === selectedScriptVersion);
  const content = scriptDetails.get(selectedScriptVersion);
  if (!item) { el('p', 'Chọn một phiên bản để xem nội dung và tải về máy.', viewer, 'empty-state'); return; }
  el('h2', item.title, viewer);
  el('p', item.version + ' · Tạo xong: ' + dateTime(item.created_at), viewer, 'muted');
  el('p', 'Bản lưu chỉ để xem. Để duyệt hoặc góp ý, mở kịch bản đang sử dụng.', viewer, 'muted');
  const actions = el('div', undefined, viewer, 'actions');
  if (item.current) button(actions, 'Mở kịch bản hiện tại để duyệt →', showCurrentScript, 'primary');
  if (item.available) {
    downloadLink(actions, 'Tải kịch bản .md', item.path);
    if (content?.json_path) downloadLink(actions, 'Tải dữ liệu .json', content.json_path);
  }
  scriptDeleteButton(actions, item);
  if (!content) {
    el('p', loadingScripts.has(item.version) ? 'Đang đọc bản lưu trên máy…' : 'Chưa đọc được kịch bản.', viewer);
    if (!loadingScripts.has(item.version)) button(viewer, 'Đọc lại bản lưu', () => openScriptVersion(item.version));
    return;
  }
  if (content.model) el('p', 'Model: ' + content.model, viewer, 'muted');
  const pre = el('pre', content.markdown, viewer, 'saved-script-text');
  pre.setAttribute('aria-label', 'Kịch bản đầy đủ');
}
