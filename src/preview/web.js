'use strict';
let musicDraft = null;

function renderMusic() {
  if (currentPage !== 'preview' || !state.music) return;
  const panel=el('section',undefined,$('voice-panel'),'card revision-panel');panel.id='music-settings-panel';
  el('h2','Nhạc nền',panel);
  el('p','Chọn một bài cho cả video. Nghe thử bản phối để kiểm tra nhạc hợp với lời đọc.',panel,'muted');
  const config=musicDraft || state.music.config;
  const row=el('div',undefined,panel,'voice-settings');
  const select=field(row,'Bài nhạc','select','','music-track');select.setAttribute('aria-label','Bài nhạc');
  const none=el('option','Không dùng nhạc',select);none.value='';
  for(const item of state.music.tracks){const option=el('option',item.title+' · '+item.artist,select);option.value=item.id;}
  select.value=config.track_id || '';
  const labelNode=el('label',undefined,row);
  const volumeText=el('span','Âm lượng nhạc · '+config.volume+'%',labelNode);
  const volume=el('input',undefined,labelNode);volume.type='range';volume.min='0';volume.max='30';volume.step='1';volume.value=config.volume;volume.setAttribute('aria-label','Âm lượng nhạc');
  const duckLabel=el('label','Tự hạ nhạc khi có lời đọc',panel);
  const duck=el('input',undefined,duckLabel);duck.type='checkbox';duck.checked=config.ducking;duck.setAttribute('aria-label','Tự hạ nhạc khi có lời đọc');
  const audio=el('audio',undefined,panel);audio.controls=true;audio.preload='none';audio.setAttribute('aria-label','Nghe thử nhạc nền');
  const sourceLink=el('a','Nguồn nhạc và giấy phép',panel);sourceLink.target='_blank';sourceLink.rel='noopener noreferrer';
  const updateSource=()=>{
    const item=state.music.tracks.find(item=>item.id===select.value);
    audio.hidden=!item;sourceLink.hidden=!item;
    if(item){audio.src='/music-media?id='+encodeURIComponent(item.id);sourceLink.href=item.license_url;}
    else {audio.removeAttribute('src');audio.load();}
  };
  const capture=()=>{musicDraft={track_id:select.value || null,volume:Number(volume.value),ducking:duck.checked};volumeText.textContent='Âm lượng nhạc · '+volume.value+'%';};
  select.onchange=()=>{capture();updateSource();};volume.oninput=capture;duck.onchange=capture;updateSource();
  const actions=el('div',undefined,panel,'actions');
  const apply=button(actions,'Lưu và phối nhạc',async()=>{
    capture();
    if(await action('music-settings',{config:musicDraft})){
      musicDraft=null;
      if(state.continuous_preview?.ready){previewMode='audio';await action('preview-build',{with_audio:true,keep_duration:false});}
    }
  },'primary');
  apply.disabled=workflowBusy || state.stage==='completed';
  for(const node of [select,volume,duck])node.disabled=workflowBusy || state.stage==='completed';
  el('p','Nhạc đã tải dùng tại máy. Đổi bài hoặc âm lượng dùng lại lời đọc và hình đã dựng.',panel,'preview-help');
}
function renderFinal(){
  if(currentPage !== 'preview')return;
  const artifact=latest(null,'final_video');if(!artifact || artifact.origin!=='c7')return;
  const panel=el('section',undefined,$('final-review'),'card revision-panel');el('h2','Duyệt toàn bộ video · '+artifact.version,panel);
  const video=el('video',undefined,panel);video.controls=true;video.preload='metadata';video.src=media(artifact.path);video.setAttribute('aria-label','Video toàn bộ');
  const review=reviewFor(artifact);badge(panel,review?.status);
  if(state.stage==='final_video' && review?.status==='pending'){
    const feedback=el('textarea',undefined,panel);feedback.placeholder='Góp ý nhịp chuyển cảnh hoặc bản ghép';feedback.setAttribute('aria-label','Góp ý video cuối');
    const actions=el('div',undefined,panel,'actions');button(actions,'Yêu cầu sửa bản ghép',()=>action('feedback',{stage:'final_video',version:artifact.version,sha256:artifact.sha256,feedback:feedback.value}));
    button(actions,'Duyệt video cuối',()=>action('approve',{stage:'final_video',version:artifact.version,sha256:artifact.sha256}),'primary');
  }
  const links=el('div',undefined,panel,'actions');downloadLink(links,'Tải MP4',artifact.path);downloadLink(links,'Danh sách phiên bản đã ghép',artifact.manifest_path);
}


function renderContinuousPreview() {
  if (currentPage !== 'preview') return;
  const parent = $('continuous-preview');
  const preview = state.continuous_preview;
  if (!preview) { emptyViewer(parent, 'Cần cập nhật máy chủ', 'Khởi động lại máy chủ để xem các cảnh trong một video liền mạch.'); return; }
  const item = previewMode === 'silent' ? preview.silent : preview.audio || preview.silent;
  const task = workflowTasks['continuous-preview'];
  const building = task && ['working','queued'].includes(task.state);
  const heading = el('div', undefined, parent, 'preview-heading');
  const intro = el('div', undefined, heading);
  el('h2', (item?.draft || preview.draft)?'Bản dựng thử · Chưa duyệt':'Bản dựng liền mạch', intro);
  el('p', item ? item.scenes.length + ' cảnh · ' + clock(item.duration) + ' · ' + (item.with_audio ? 'Có lời đọc'+(item.music?.track && item.music.config.volume>0?' · Nhạc: '+item.music.track.title:'') : 'Chưa có tiếng') : 'Các cảnh sẽ được ghép theo thứ tự kịch bản.', intro, 'muted');
  const actions = el('div', undefined, heading, 'actions');
  const addVoice = button(actions, preview.audio ? 'Dựng bản có tiếng' : 'Thêm tiếng', async () => {
    const config = state.audio_generation.config;
    if (config.provider==='fpt' && !config.allow_unpriced && (!config.vnd_per_million_chars || !config.vnd_per_usd)) {
      notice('Chưa đủ cấu hình FPT để thêm tiếng: thiếu đơn giá hoặc tỷ giá.', true); return;
    }
    previewMode = 'audio'; await action('preview-build', {with_audio:true, keep_duration:false});
  }, 'primary');
  addVoice.disabled = !preview.ready || workflowBusy;
  const captionToggle = button(actions, subtitlesVisible ? 'Ẩn phụ đề' : 'Hiện phụ đề', () => {
    subtitlesVisible = !subtitlesVisible;
    $('preview-captions')?.toggleAttribute('hidden', !subtitlesVisible);
    captionToggle.textContent = subtitlesVisible ? 'Ẩn phụ đề' : 'Hiện phụ đề';
    captionToggle.setAttribute('aria-pressed', String(subtitlesVisible));
  });
  captionToggle.setAttribute('aria-pressed', String(subtitlesVisible));
  captionToggle.disabled = !item;
  if (building) el('p', 'Đang ghép bản dựng' + (previewMode === 'audio' ? ' và thêm lời đọc' : '') + '… Video sẽ xuất hiện tại đây khi hoàn tất.', parent, 'preview-build-status');
  if (task?.state === 'failed') el('p', task.error || 'Chưa ghép được video. Hãy thử lại.', parent, 'preview-build-error');
  else if(!preview.audio && state.audio_generation.config.provider==='fpt' && state.audio_generation?.calls?.at(-1)?.http_status===429)el('p','FPT đang giới hạn tần suất gọi (HTTP 429). Đoạn giọng đã tạo được giữ lại; chưa có đủ lời đọc cho toàn bộ video.',parent,'preview-build-error');
  if (!item) {
    emptyViewer(parent, preview.ready ? 'Chưa có bản dựng' : 'Chưa đủ cảnh để ghép', preview.ready ? 'Bấm Ghép bản xem trước hoặc Thêm tiếng để tạo bản dựng.' : preview.message || 'Hoàn tất bản dựng của các cảnh trước.');
  } else {
    const video = el('video', undefined, parent, 'continuous-video');
    video.id = 'continuous-video'; video.dataset.path = item.path;
    video.controls = true; video.preload = 'metadata'; video.playsInline = true;
    video.src = media(item.path); video.setAttribute('aria-label', 'Video liền mạch tất cả các cảnh');
    // Captions are displayed below the image. The MP4 also carries a subtitle track.
    video.addEventListener('loadedmetadata', () => {
      for (const track of video.textTracks) track.mode = 'disabled';
      const saved = previewPlayback;
      if (saved?.path === item.path) {
        video.currentTime = Math.min(saved.time, Number.isFinite(video.duration) ? video.duration : item.duration);
        video.muted = saved.muted; video.volume = saved.volume;
        if (!saved.paused) video.play().catch(() => {});
      }
      update();
    });
    const caption = el('div', '\u00a0', parent, 'preview-captions');
    caption.id = 'preview-captions'; caption.hidden = !subtitlesVisible;
    caption.setAttribute('aria-label', 'Phụ đề lời đọc');
    const chapters = el('nav', undefined, parent, 'preview-chapters'); chapters.setAttribute('aria-label', 'Tua đến cảnh trong video');
    const chapterButtons = item.scenes.map((scene, index) => {
      const node = button(chapters, String(index+1).padStart(2,'0') + ' · ' + scene.title, () => { video.currentTime = scene.start; update(); });
      node.title = clock(scene.start) + ' – ' + clock(scene.end); return node;
    });
    const update = () => {
      const time = video.currentTime;
      const cue = item.cues.find(c => time >= c.start && time < c.end);
      caption.textContent = cue?.text || '\u00a0';
      item.scenes.forEach((scene, index) => {
        const active = time >= scene.start && time < scene.end;
        chapterButtons[index].classList.toggle('selected', active);
        if (active) chapterButtons[index].setAttribute('aria-current','true');
        else chapterButtons[index].removeAttribute('aria-current');
      });
    };
    video.addEventListener('timeupdate', update); video.addEventListener('seeked', update); update();
    video.addEventListener('error', () => notice('Không đọc được video. Hãy ghép lại bản dựng hoặc kiểm tra tệp trên máy.', true));
    const links = el('div', undefined, parent, 'actions preview-downloads');
    downloadLink(links, 'Tải video MP4', item.path);
    if (preview.audio && preview.silent) button(links, item.with_audio ? 'Xem bản không tiếng' : 'Xem bản có tiếng', () => { previewMode = item.with_audio ? 'silent' : 'audio'; render(); });
    el('p', item.with_audio ? 'Lời đọc giữ tốc độ đã chọn; phụ đề căn theo âm thanh thực. Cảnh được kéo dài khi cần để đọc rõ.' : 'Phụ đề đang chia theo thời lượng cảnh. Chọn Thêm tiếng để căn theo thời lượng lời đọc thực.', parent, 'preview-help');
  }
  const bottom = el('div', undefined, parent, 'actions preview-bottom');
  const build = button(bottom, item ? 'Ghép lại bản xem trước' : 'Ghép bản xem trước', () => { previewMode = 'silent'; return action('preview-build',{with_audio:false}); });
  build.disabled = !preview.ready || workflowBusy;
  button(bottom, sceneReviewOpen ? 'Thu gọn duyệt từng cảnh' : 'Duyệt từng cảnh', () => { sceneReviewOpen = !sceneReviewOpen; render(); });
  el('p','Ghép bản xem trước chạy tại máy. Thêm tiếng dùng '+(state.audio_generation.config.provider==='edge'?'giọng Microsoft Edge, không cần API key; cần Internet.':'dịch vụ giọng FPT đã cấu hình.'),parent,'preview-help');
  if (preview.auto_build !== false && !preview.silent && !preview.audio && preview.ready && !workflowBusy && task?.state !== 'failed') {
    const key = JSON.stringify([state.project_id, state.artifacts.filter(a => ['script','scene_design','scene_clip'].includes(a.stage) && !a.invalidated).map(a => a.sha256),Object.values(state.scene_drafts || {}).map(d=>d.reference.sha256)]);
    if (!previewAttempts.has(key)) { previewAttempts.add(key); queueMicrotask(() => action('preview-build',{with_audio:false})); }
  }
}
