/* Status only: source data is collected on the server from this card. */
(() => {
  document.querySelectorAll('.aviandr-status-chart a[href^="#av-"]').forEach(link => link.addEventListener('click',()=>{
    const details=document.querySelector(link.getAttribute('href'));
    if(details?.tagName==='DETAILS') details.open=true;
  }));
  document.querySelectorAll('[data-patient-ai]').forEach(panel => {
  if(!['queued','running'].includes(panel.dataset.status)) return;
  async function refresh() {
    try {
      const response=await fetch(panel.dataset.statusUrl,{cache:'no-store'});
      if(!response.ok) throw Error();
      const job=await response.json();
      panel.querySelector('[data-stage]').textContent=job.stage_label;
      panel.querySelector('[data-heartbeat]').textContent=`Связь с обработчиком: ${job.heartbeat_at || 'ещё не было'}${job.heartbeat_at ? ' (UTC)' : ''}`;
      const error=panel.querySelector('[data-error]');
      error.textContent=job.error_message;error.hidden=!job.error_message;
      if(['completed','error'].includes(job.status)) {
        panel.querySelector('[data-result-link]').hidden=false;
        panel.querySelectorAll('[data-job-pending]').forEach(item=>item.hidden=true);
        const failure=panel.querySelector('[data-job-failure]');
        if(failure) failure.hidden=job.status!=='error';
        return;
      }
    } catch {
      panel.querySelector('[data-heartbeat]').textContent='Нет связи с приложением. Обновите страницу.';
    }
    setTimeout(refresh,4000);
  }
  refresh();
  });
})();
