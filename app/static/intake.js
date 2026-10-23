/* Local drafts and local audio only: no SpeechRecognition or external AI calls. */
(() => {
  document.documentElement.classList.add('intake-enhanced');
  document.querySelectorAll('[data-intake-autosave],[data-intake-entity]').forEach(form => {
    let dirty=false, busy=false, paused=false, conflict=false, timer, edit=0, queuedSubmitter=null;
    const status=form.querySelector('[data-intake-save-status]');
    const show=message=>{if(status) status.textContent=message;};
    const state=key=>{
      const box=form.querySelector(`[data-intake-field="${key}"]`);
      const choice=box?.querySelector('[data-intake-state]');
      if(choice?.value!=='answered')return [];
      return [...box.querySelectorAll(`[name="a_${key}"]`)].filter(e=>e.type!=='checkbox'||e.checked).map(e=>e.value);
    };
    function branches(){
      form.querySelectorAll('[data-intake-field]').forEach(box=>{
        const condition=box.dataset.intakeWhen ? JSON.parse(box.dataset.intakeWhen) : null;
        const active=!condition||condition.values.some(v=>state(condition.key).includes(v));
        box.hidden=!active;
        box.querySelectorAll('input,textarea,select').forEach(e=>e.disabled=!active);
        const choice=box.querySelector('[data-intake-state]');
        const inputs=box.querySelector('[data-intake-inputs]');
        if(inputs){const enabled=active&&['unanswered','answered'].includes(choice?.value);inputs.hidden=!enabled;inputs.querySelectorAll('input,textarea,select').forEach(e=>e.disabled=!enabled);}
      });
    }
    async function save(){
      if(!dirty||busy||paused||!form.hasAttribute('data-intake-autosave'))return;
      busy=true;dirty=false;const savedEdit=edit;show('Сохраняю черновик…');
      try{
        const response=await fetch(form.action,{method:'POST',body:new FormData(form),credentials:'same-origin',headers:{Accept:'application/json'}});
        if(!response.ok){paused=true;conflict=response.status===409;dirty=true;show(response.status===409?'Анкета изменилась в другой вкладке. Автосохранение остановлено. Скопируйте последние правки и обновите страницу.':'Не удалось сохранить ответы. Они остаются в форме; используйте кнопку сохранения или проверьте связь.');return;}
        const data=await response.json();
        if(data.progress&&edit===savedEdit){const root=form.closest('[data-intake-page]'),bar=root?.querySelector('[data-section-progress]'),label=root?.querySelector('[data-section-progress-label]');if(bar){bar.max=data.progress.total||1;bar.value=data.progress.done;}if(label)label.textContent=`${data.progress.done} из ${data.progress.total} вопросов`;}
        form.closest('[data-intake-page]')?.querySelectorAll('input[name="revision"]').forEach(e=>e.value=data.revision);
        if(data.calculations&&edit===savedEdit){const root=form.closest('[data-intake-page]');[['age',data.calculations.age??'нужна дата рождения'],['bmi',data.calculations.bmi??'не рассчитывается'],['bmi-inputs',data.calculations.bmi_inputs],['bmi-note',data.calculations.bmi_note]].forEach(([key,value])=>{const e=root?.querySelector('[data-intake-'+key+']');if(e)e.textContent=value;});}
        if(data.entity_id){
          const hidden=(name,value)=>{let e=form.querySelector(`[name="${name}"]`);if(!e){e=document.createElement('input');e.type='hidden';e.name=name;form.append(e);}e.value=value;};
          hidden('entity_id',data.entity_id);hidden('entity_version',data.entity_version);
          const path=form.action.replace(/\/entities\/[^/]+\/save$/,'/entities/'+data.entity_id);
          history.replaceState(null,'',path);
          const link=form.querySelector('[data-saved-entity-link]');if(link){link.href=path;link.hidden=false;}
        }
        show(edit===savedEdit?'Черновик сохранён. Врач увидит его только после отправки анкеты.':'Сохраняю последние изменения…');
      }catch(_){dirty=true;show('Нет связи: изменения пока не сохранены. Оставьте страницу открытой или нажмите «Сохранить раздел».');}
      finally{
        busy=false;
        if(queuedSubmitter&&!dirty&&!paused){const submitter=queuedSubmitter;queuedSubmitter=null;form.requestSubmit(submitter);}
        else if(dirty&&!paused)timer=setTimeout(save,queuedSubmitter?0:1500);
        else queuedSubmitter=null;
      }
    }
    function changed(){
      dirty=true;edit++;
      const root=form.closest('[data-intake-page]');
      const bmi=root?.querySelector('[data-intake-bmi]');
      if(bmi){bmi.textContent='обновляется…';root.querySelector('[data-intake-bmi-inputs]').textContent='';root.querySelector('[data-intake-bmi-note]').textContent='Расчёт появится после сохранения текущих значений.';}
    }
    form.addEventListener('input',event=>{
      const input=event.target;if(!conflict)paused=false;
      if(input.name?.startsWith('a_')){
        const choice=input.closest('[data-intake-field]')?.querySelector('[data-intake-state]');
        if(choice){const box=input.closest('[data-intake-field]');const filled=[...box.querySelectorAll('[name="'+input.name+'"]')].some(e=>e.type==='checkbox'?e.checked:e.value!=='');choice.value=filled?'answered':'unanswered';}
      }
      branches();changed();clearTimeout(timer);timer=setTimeout(save,700);
    });
    form.addEventListener('change',()=>{if(!conflict)paused=false;branches();changed();clearTimeout(timer);timer=setTimeout(save,300);});
    form.addEventListener('submit',event=>{clearTimeout(timer);if(busy){event.preventDefault();queuedSubmitter=event.submitter;show('Завершаю сохранение перед переходом…');}else if(conflict){event.preventDefault();show('Анкета изменилась в другой вкладке. Скопируйте последние правки и обновите страницу перед переходом.');}else{dirty=false;paused=true;}});
    window.addEventListener('beforeunload',event=>{if((dirty||busy)&&form.hasAttribute('data-intake-autosave')){event.preventDefault();event.returnValue='';}});
    branches();
  });
  document.querySelectorAll('[data-memory-search]').forEach(input=>input.addEventListener('input',()=>{
    const words=input.value.toLocaleLowerCase('ru').replaceAll('ё','е').split(/\s+/).filter(Boolean);
    input.closest('details').querySelectorAll('[data-memory-item]').forEach(item=>{const text=item.textContent.toLocaleLowerCase('ru').replaceAll('ё','е');item.hidden=!words.every(word=>text.includes(word));});
  }));
  document.querySelectorAll('[data-option-search]').forEach(input=>input.addEventListener('input',()=>{
    const query=input.value.toLocaleLowerCase('ru');input.closest('fieldset').querySelectorAll('.intake-options label').forEach(label=>label.hidden=!label.textContent.toLocaleLowerCase('ru').includes(query));
  }));
  document.querySelectorAll('[data-medication-names-url]').forEach(form=>{
    const input=form.querySelector('[name="a_name"]');if(!input)return;
    const list=document.createElement('datalist');list.id='intake-medication-names';input.setAttribute('list',list.id);form.append(list);
    const note=document.createElement('p');note.className='caption';note.textContent='Подсказки названий из локальной библиотеки — только для самоотчёта. Это не назначение; сведения требуют проверки врача.';input.after(note);
    let timeout,controller;
    input.addEventListener('input',()=>{clearTimeout(timeout);controller?.abort();timeout=setTimeout(async()=>{
      if(input.value.trim().length<2){list.replaceChildren();return;}
      controller=new AbortController();try{const response=await fetch(form.dataset.medicationNamesUrl+'?q='+encodeURIComponent(input.value),{signal:controller.signal,credentials:'same-origin'});if(!response.ok)return;const data=await response.json();list.replaceChildren(...data.names.map(name=>{const option=document.createElement('option');option.value=name;return option;}));}catch(_){}
    },350);});
  });
  document.querySelectorAll('[data-local-recording]').forEach(form=>{
    const start=form.querySelector('[data-record-start]'), stop=form.querySelector('[data-record-stop]'), discard=form.querySelector('[data-record-discard]'), input=form.querySelector('[data-record-file]'), preview=form.querySelector('[data-record-preview]'), status=form.querySelector('[data-record-status]');
    let recorder,stream,parts=[],url,timer,ticker,started;
    const clean=()=>{clearTimeout(timer);clearInterval(ticker);stream?.getTracks().forEach(t=>t.stop());stream=null;};
    start.addEventListener('click',async()=>{
      if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){status.textContent='Браузер не поддерживает запись здесь. Можно выбрать готовый аудиофайл.';return;}
      try{
        stream=await navigator.mediaDevices.getUserMedia({audio:true});parts=[];
        const mime=['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(t=>MediaRecorder.isTypeSupported(t));
        recorder=new MediaRecorder(stream,mime?{mimeType:mime}:undefined);
        recorder.ondataavailable=e=>{if(e.data.size)parts.push(e.data);};
        recorder.onstop=()=>{
          clean();const type=recorder.mimeType||'audio/webm';const ext=type.includes('mp4')?'m4a':type.includes('ogg')?'ogg':'webm';
          const blob=new Blob(parts,{type});const transfer=new DataTransfer();[...input.files].filter(file=>!file.name.startsWith('voice-note.')).forEach(file=>transfer.items.add(file));transfer.items.add(new File([blob],'voice-note.'+ext,{type}));input.files=transfer.files;
          if(url)URL.revokeObjectURL(url);url=URL.createObjectURL(blob);preview.src=url;preview.hidden=false;start.hidden=false;stop.hidden=true;discard.hidden=false;status.textContent='Запись готова. Прослушайте её и нажмите «Сохранить выбранные вложения». Пока она не отправлена.';
        };
        recorder.start();started=Date.now();start.hidden=true;stop.hidden=false;discard.hidden=true;
        ticker=setInterval(()=>status.textContent=`Запись: ${Math.floor((Date.now()-started)/1000)} с · максимум 3 минуты.`,1000);
        timer=setTimeout(()=>recorder.state==='recording'&&recorder.stop(),180000);
      }catch(_){clean();status.textContent='Доступ к микрофону не получен. Можно прикрепить готовую заметку.';}
    });
    stop.addEventListener('click',()=>{if(recorder?.state==='recording')recorder.stop();});
    discard.addEventListener('click',()=>{const transfer=new DataTransfer();[...input.files].filter(file=>!file.name.startsWith('voice-note.')).forEach(file=>transfer.items.add(file));input.files=transfer.files;preview.removeAttribute('src');preview.hidden=true;discard.hidden=true;if(url)URL.revokeObjectURL(url);status.textContent='Несохранённая запись убрана.';});
    form.addEventListener('submit',event=>{if(recorder?.state==='recording'){event.preventDefault();status.textContent='Сначала остановите запись и прослушайте её.';}});
    window.addEventListener('pagehide',()=>{if(recorder?.state==='recording')recorder.stop();clean();if(url)URL.revokeObjectURL(url);});
  });
})();
