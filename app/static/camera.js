/* Capture stays in this explicit window while server-rendered questions navigate. */
(() => {
  const root = document.querySelector('[data-camera]');
  if (!root) return;
  const aid = root.dataset.camera;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const el = name => root.querySelector(`[data-camera-${name}]`);
  const consent = el('consent'), start = el('start'), stop = el('stop');
  const save = el('save'), discard = el('discard'), status = el('status');
  const live = el('live'), preview = el('preview');
  let stream, recorder, blob, chunks = [], previewURL, began = 0, timer, poll;
  let epoch = 0, busy = false, dropping = false, uploading = false, bytes = 0, uploadID;
  const supported = navigator.mediaDevices?.getUserMedia && window.MediaRecorder;
  function controls() {
    start.disabled = !supported || !consent.checked || busy || !!stream || !!blob || uploading;
    stop.disabled = !stream || !recorder || recorder.state !== 'recording';
    save.disabled = !blob || uploading;
    discard.disabled = !blob || uploading;
    root.classList.toggle('is-recording', !!stream);
  }
  function release() {
    clearInterval(timer); clearInterval(poll);
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null; live.srcObject = null; live.hidden = true;
  }
  function clearPreview() {
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null; blob = null;
    preview.removeAttribute('src'); preview.load(); preview.hidden = true;
  }
  function cancelCapture() {
    epoch++; dropping = true;
    if (recorder?.state === 'recording') recorder.stop();
    release(); clearPreview(); busy = false; controls();
  }
  async function choice(intent) {
    const form = new FormData();
    form.set('csrf', csrf); form.set('intent', intent);
    form.set('video_consent', consent.checked ? 'true' : 'false');
    const response = await fetch(`/assessments/${aid}/video/choice`, {method:'POST',body:form,headers:{Accept:'application/json'}});
    if (!response.ok) throw new Error('Не удалось сохранить выбор. Проверьте участие и обновите окно.');
  }
  consent.addEventListener('change', () => {
    if (!consent.checked && (stream || busy || blob)) {
      cancelCapture(); status.textContent = 'Камера выключена, неотправленный фрагмент удалён.';
      choice('decline').catch(() => { status.textContent += ' Не удалось обновить выбор на сервере.'; });
    }
    controls();
  });
  start.addEventListener('click', async () => {
    busy = true; const generation = ++epoch; dropping = false; controls();
    status.textContent = 'Запрашиваем разрешение на камеру…';
    try {
      await choice('accept');
      if (generation !== epoch) return;
      const captured = await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480},frameRate:{ideal:15}},audio:false});
      if (generation !== epoch || !consent.checked) { captured.getTracks().forEach(t => t.stop()); return; }
      stream = captured; live.srcObject = stream; live.hidden = false;
      const mimeType = ['video/webm;codecs=vp8','video/webm','video/mp4'].find(type => MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw new Error('Браузер не поддерживает подходящий формат видеозаписи.');
      chunks = []; bytes = 0; uploadID = crypto.randomUUID().replaceAll('-','');
      recorder = new MediaRecorder(stream, {mimeType, videoBitsPerSecond:300000});
      recorder.ondataavailable = event => {
        if (generation !== epoch) return;
        if (event.data.size) { chunks.push(event.data); bytes += event.data.size; }
        if (bytes >= 24 * 1024 * 1024 && recorder.state === 'recording') recorder.stop();
      };
      recorder.onstop = () => {
        if (generation !== epoch) return;
        const duration = Math.min(600,(performance.now()-began)/1000);
        release(); busy = false;
        if (!dropping) {
          blob = new Blob(chunks,{type:recorder.mimeType.split(';')[0]});
          blob.captureDuration = duration;
          previewURL = URL.createObjectURL(blob); preview.src = previewURL; preview.hidden = false;
          status.textContent = 'Камера выключена. Посмотрите фрагмент и решите, отправлять ли его специалисту.';
        }
        chunks = []; controls();
      };
      recorder.onerror = () => { if (generation !== epoch) return; cancelCapture(); status.textContent = 'Запись прервалась. Анкету можно продолжить без видео.'; };
      began = performance.now(); recorder.start(1000); busy = false; controls();
      const tick = () => {
        const seconds = Math.floor((performance.now()-began)/1000);
        status.textContent = `Идёт видеозапись · ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}. Заполняйте анкету в основном окне.`;
        if (seconds >= 600 && recorder.state === 'recording') recorder.stop();
      };
      tick(); timer = setInterval(tick,1000);
      poll = setInterval(async () => {
        try {
          const response = await fetch(`/assessments/${aid}/video/status`,{cache:'no-store'});
          if (!response.ok) throw new Error();
          const data = await response.json();
          if (!data.participating || data.status !== 'accepted') {
            cancelCapture(); consent.checked = false; controls();
            status.textContent = 'Согласие отозвано. Камера выключена, неотправленный фрагмент удалён.';
          }
        } catch { cancelCapture(); status.textContent = 'Не удалось проверить согласие. Камера выключена; запись можно повторить.'; }
      },3000);
    } catch (error) {
      if (generation === epoch) {
        cancelCapture();
        status.textContent = error.name === 'NotAllowedError' ? 'Доступ к камере не разрешён. Это не мешает заполнению анкеты.' : error.message || 'Не удалось включить камеру. Анкету можно заполнить без видео.';
      }
    }
  });
  stop.addEventListener('click', () => { if (recorder?.state === 'recording') recorder.stop(); });
  discard.addEventListener('click', () => { clearPreview(); controls(); status.textContent = 'Неотправленный фрагмент удалён. Камера выключена.'; });
  el('decline').addEventListener('click', async () => {
    cancelCapture(); consent.checked = false; controls();
    try { await choice('decline'); status.textContent = 'Вы выбрали прохождение без видео. Можно вернуться к анкете.'; }
    catch (error) { status.textContent = error.message; }
  });
  save.addEventListener('click', async () => {
    if (!blob || uploading) return;
    uploading = true; controls(); status.textContent = 'Сохраняем выбранный фрагмент…';
    const form = new FormData(); form.set('csrf',csrf); form.set('duration',blob.captureDuration);
    form.set('upload_id',uploadID); form.set('file',blob,'recording.'+(blob.type === 'video/mp4' ? 'mp4' : 'webm'));
    try {
      const response = await fetch(`/assessments/${aid}/video/recordings`,{method:'POST',body:form});
      if (!response.ok) throw new Error('Не удалось сохранить фрагмент. Проверьте согласие, подключение и размер до 25 МБ.');
      clearPreview(); location.reload();
    } catch (error) { uploading = false; controls(); status.textContent = error.message; }
  });
  window.addEventListener('pagehide', () => { dropping = true; release(); clearPreview(); });
  if (!supported) status.textContent = 'Запись камерой в этом браузере недоступна. Можно заполнить анкету без видео.';
  controls();
})();
