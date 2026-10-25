"use strict";
// A scrollable row, without autoplay; usable by touch, keyboard and buttons.
const track = document.getElementById("resource-track");
if (track) {
  const controls = document.querySelectorAll("[data-scroll]");
  function move(direction) {
    track.scrollBy({
      left: direction * Math.min(track.clientWidth * 0.85, 630),
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  }
  for (const button of controls)
    button.addEventListener("click", () => move(Number(button.dataset.scroll)));
  track.addEventListener("keydown", (event) => {
    if (event.target !== track) return;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      move(event.key === "ArrowRight" ? 1 : -1);
    }
  });
}
// Open the FAQ fragment without requiring the entire FAQ to be read.
if (location.hash) {
  const target = document.getElementById(
    decodeURIComponent(location.hash.slice(1)),
  );
  if (target && target.tagName === "DETAILS") target.open = true;
}
const assessmentForm = document.getElementById("assessment-form");
if (assessmentForm) {
  const state = document.getElementById("save-state");
  let changed = false;
  assessmentForm.addEventListener("input", (event) => {
    if (event.target.form !== assessmentForm) return;
    changed = true;
    state.textContent =
      "Есть несохранённые изменения. Специалист их пока не видит.";
  });
  assessmentForm.addEventListener("submit", () => {
    changed = false;
    state.textContent =
      "Отправляем… Успех будет показан только после ответа сервера.";
  });
  window.addEventListener("beforeunload", (event) => {
    if (changed) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  document.getElementById("audio-form")?.addEventListener("submit", (event) => {
    if (changed) {
      event.preventDefault();
      document.getElementById("audio-status").textContent =
        "Сначала сохраните ответ кнопкой «Сохранить и сделать паузу», затем отправьте запись. Она останется в выбранном файле до ухода со страницы.";
    }
  });
  const behavior = assessmentForm.querySelector("[data-safety-recent]");
  if (behavior) behavior.addEventListener("change", (event) => {
    const button = assessmentForm.querySelector('button[name="intent"][value="submit"],button[name="intent"][value="next"]');
    if (button) {
      const continues = event.target.value === "1";
      button.value = continues ? "next" : "submit";
      button.textContent = continues ? "Сохранить и продолжить" : "Завершить и отправить";
    }
  });
  const safety = assessmentForm.querySelector("[data-safety-answer]");
  if (safety)
    safety.addEventListener("change", (event) => {
      if (Number(event.target.value) > 0)
        state.textContent =
          "Этот ответ требует отдельного уточнения. Сохраните анкету, чтобы передать его специалисту. При опасности — «Экстренная помощь»; не ждите ответа в приложении.";
    });
}
const workflowAlerts = document.getElementById("workflow-alerts");
if (workflowAlerts) {
  async function refreshWorkflow() {
    try {
      const response = await fetch("/api/workflow-alerts", {
        cache: "no-store",
      });
      if (!response.ok) throw Error();
      const data = await response.json();
      workflowAlerts.replaceChildren();
      const pending = data.events.filter((e) => !e.action_at);
      if (!pending.length)
        workflowAlerts.textContent =
          "Нет незавершённых событий из анкет.";
      for (const item of pending) {
        const box = document.createElement("article");
        box.className = "critical";
        const link = document.createElement("a");
        link.href = "/patients/" + item.patient_id + "/workspace/safety";
        link.textContent = item.name + (item.kind.startsWith("safety6_") ? " · новые ответы о безопасности" : " · нужно уточнение по пункту 9 PHQ-9");
        const status = document.createElement("p");
        status.textContent = item.acknowledged_at
          ? "Получение подтверждено; действие ещё не записано."
          : "Ожидает подтверждения специалистом: " +
            item.awaiting_seconds +
            " с. Резервная служба не настроена.";
        box.append(link, status);
        workflowAlerts.append(box);
        if (!item.delivered_at) {
          const form = new URLSearchParams({
            csrf: workflowAlerts.dataset.csrf,
            intent: "delivered",
          });
          const delivery = await fetch("/events/" + item.id + "/status", {
            method: "POST",
            body: form,
          });
          if (!delivery.ok) {
            const warning = document.createElement("p");
            warning.textContent =
              "Не удалось записать получение кабинетом. Событие не подтверждено.";
            box.append(warning);
          }
        }
      }
    } catch {
      workflowAlerts.textContent =
        "Связь потеряна или сессия завершена. Новые события могут не отображаться. Повторите вход; при опасности используйте экстренный маршрут.";
    }
  }
  refreshWorkflow();
  setInterval(refreshWorkflow, 10000);
}
// Optional local recording; nothing is uploaded before explicit form submission.
const recordStart = document.getElementById("record-start");
if (recordStart) {
  let recorder,
    stream,
    chunks = [],
    started,
    timer,
    previewUrl;
  const stop = document.getElementById("record-stop"),
    status = document.getElementById("audio-status");
  const fileInput = document.getElementById("recording-file"),
    preview = document.getElementById("audio-preview");
  recordStart.addEventListener("click", async () => {
    if (!document.getElementById("audio-consent").checked) {
      status.textContent = "Сначала подтвердите отдельное согласие на запись.";
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      status.textContent =
        "Запись не поддерживается. Можно выбрать готовый синтетический аудиофайл или оставить текст.";
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunks = [];
      const mime = ["audio/webm", "audio/ogg", "audio/mp4"].find((type) =>
        MediaRecorder.isTypeSupported(type),
      );
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onstop = () => {
        clearTimeout(timer);
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: recorder.mimeType });
        const extension = blob.type.includes("ogg")
          ? "ogg"
          : blob.type.includes("mp4")
            ? "mp4"
            : "webm";
        const file = new File([blob], "synthetic-note." + extension, {
          type: blob.type,
        });
        const transfer = new DataTransfer();
        transfer.items.add(file);
        fileInput.files = transfer.files;
        document.getElementById("recording-duration").value = Math.min(
          60,
          (performance.now() - started) / 1000,
        ).toFixed(1);
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(blob);
        preview.src = previewUrl;
        preview.hidden = false;
        recordStart.disabled = false;
        stop.disabled = true;
        status.textContent =
          "Запись готова к прослушиванию. Она ещё не отправлена и не распознана. Можно заменить её или сохранить форму.";
      };
      recorder.start();
      started = performance.now();
      recordStart.disabled = true;
      stop.disabled = false;
      status.textContent =
        "Идёт локальная запись. Автоматическая остановка через 60 секунд.";
      timer = setTimeout(() => {
        if (recorder.state === "recording") recorder.stop();
      }, 60000);
    } catch {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      recordStart.disabled = false;
      stop.disabled = true;
      status.textContent =
        "Микрофон недоступен. Текстовые ответы продолжают работать.";
    }
  });
  stop.addEventListener("click", () => {
    if (recorder?.state === "recording") recorder.stop();
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file) {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      preview.src = previewUrl;
      preview.hidden = false;
    }
  });
  document.getElementById("audio-form").addEventListener("submit", (event) => {
    if (
      document.getElementById("recording-file").files[0]?.size >
      3 * 1024 * 1024
    ) {
      event.preventDefault();
      status.textContent = "Файл больше 3 МБ. Выберите более короткую запись.";
    }
  });
}
