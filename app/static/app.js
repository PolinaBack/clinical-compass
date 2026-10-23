"use strict";
// Speech recognition is optional and may use the browser vendor's remote service.
for (const button of document.querySelectorAll(".voice")) {
  button.addEventListener("click", () => {
    const status = document.getElementById("voice-status");
    const Recognition =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      status.textContent =
        "Голосовой ввод недоступен. Введите текст вручную (Chrome/Edge могут поддерживать распознавание).";
      return;
    }
    if (
      !window.confirm(
        "Распознавание может отправлять аудио провайдеру браузера. Используйте только вымышленные данные. Продолжить?",
      )
    )
      return;
    const speech = new Recognition();
    speech.lang = "ru-RU";
    speech.interimResults = false;
    speech.onstart = () => {
      button.disabled = true;
      status.textContent = "Слушаю… Для завершения перестаньте говорить.";
    };
    speech.onresult = (event) => {
      const target = document.getElementById(button.dataset.target);
      target.value +=
        (target.value ? " " : "") + event.results[0][0].transcript;
      status.textContent =
        "Текст добавлен. Проверьте его и сохраните форму вручную.";
    };
    speech.onerror = (event) => {
      status.textContent =
        "Распознавание не выполнено (" +
        event.error +
        "). Используйте клавиатуру.";
    };
    speech.onend = () => {
      button.disabled = false;
      if (status.textContent.startsWith("Слушаю"))
        status.textContent = "Запись завершена. Можно повторить ввод.";
    };
    try {
      speech.start();
    } catch {
      status.textContent =
        "Не удалось включить микрофон. Используйте клавиатуру.";
      button.disabled = false;
    }
  });
}
const alerts = document.getElementById("live-alerts");
if (alerts) {
  async function refresh() {
    try {
      const response = await fetch("/api/alerts");
      if (!response.ok) throw Error();
      const data = await response.json();
      alerts.replaceChildren();
      const pending = data.alerts.filter((a) => !a.acknowledged);
      if (!pending.length) alerts.textContent = "Новых сообщений о рисках нет.";
      for (const a of pending) {
        const box = document.createElement("article");
        box.className = a.urgent ? "critical" : "warning";
        const link = document.createElement("a");
        link.href = "/patients/" + a.patient_id;
        link.textContent = (a.urgent ? "СРОЧНЫЙ ДЕМОСИГНАЛ · " : "") + a.name;
        const text = document.createElement("p");
        text.textContent = a.body;
        box.append(link, text);
        alerts.append(box);
      }
    } catch {
      alerts.textContent =
        "Нет соединения. Уведомления не обновляются. Обновите страницу после восстановления.";
    }
  }
  refresh();
  setInterval(refresh, 10000);
}

// A link to an older entry opens its history before moving focus to the entry.
function revealHistoryAnchor() {
  let target;
  try { target = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch { return; }
  if (!target) return;
  let parent = target.parentElement;
  while (parent) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; }
  if (location.hash) target.scrollIntoView({block: 'start'});
}
window.addEventListener('hashchange', revealHistoryAnchor);
window.addEventListener('DOMContentLoaded', revealHistoryAnchor);
