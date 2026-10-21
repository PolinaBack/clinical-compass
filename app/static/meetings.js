document.querySelectorAll('[data-meeting-seek]').forEach(button => {
  button.addEventListener('click', async () => {
    const media = document.getElementById(button.dataset.media);
    if (!media) return;
    const time = Number(button.dataset.meetingSeek);
    if (!Number.isFinite(time) || time < 0) return;
    if (media.readyState === 0) {
      media.load();
      await new Promise(resolve => {
        media.addEventListener('loadedmetadata', resolve, {once:true});
        media.addEventListener('error', resolve, {once:true});
      });
    }
    try { media.currentTime = time; media.focus(); media.scrollIntoView({block:'center'}); }
    catch (_) { /* Native controls remain available if media cannot be decoded. */ }
  });
});
