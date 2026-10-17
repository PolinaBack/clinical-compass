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
const family = document.getElementById("genogram");
if (family) {
  const people = JSON.parse(family.dataset.family);
  if (people.length) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    const positions = new Map(),
      counts = new Map();
    for (const p of people) {
      const level =
        p.parent_id && positions.has(p.parent_id)
          ? positions.get(p.parent_id).level + 1
          : 0;
      const col = counts.get(level) || 0;
      counts.set(level, col + 1);
      positions.set(p.id, { x: 90 + col * 180, y: 45 + level * 100, level });
    }
    const width = Math.max(360, ...Array.from(counts.values(), (n) => n * 180));
    const height = Math.max(...Array.from(positions.values(), (p) => p.y)) + 50;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.style.maxHeight = "none";
    svg.setAttribute("role", "img");
    svg.setAttribute(
      "aria-label",
      "Семейная схема, стрелки от родителей к детям",
    );
    const defs = document.createElementNS(ns, "defs");
    const marker = document.createElementNS(ns, "marker");
    for (const [k, v] of Object.entries({
      id: "arrow",
      viewBox: "0 0 10 10",
      refX: "9",
      refY: "5",
      markerWidth: "6",
      markerHeight: "6",
      orient: "auto-start-reverse",
    }))
      marker.setAttribute(k, v);
    const arrow = document.createElementNS(ns, "path");
    arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    arrow.setAttribute("fill", "#6a8984");
    marker.append(arrow);
    defs.append(marker);
    svg.append(defs);
    for (const p of people) {
      const pos = positions.get(p.id),
        parent = positions.get(p.parent_id);
      if (parent) {
        const line = document.createElementNS(ns, "line");
        for (const [k, v] of Object.entries({
          x1: parent.x,
          y1: parent.y + 22,
          x2: pos.x,
          y2: pos.y - 22,
          stroke: "#6a8984",
          "marker-end": "url(#arrow)",
        }))
          line.setAttribute(k, v);
        svg.append(line);
      }
    }
    for (const p of people) {
      const pos = positions.get(p.id);
      const rect = document.createElementNS(ns, "rect");
      for (const [k, v] of Object.entries({
        x: pos.x - 75,
        y: pos.y - 20,
        width: 150,
        height: 40,
        rx: 8,
        fill: "#e1eeeb",
        stroke: "#23766a",
      }))
        rect.setAttribute(k, v);
      svg.append(rect);
      const text = document.createElementNS(ns, "text");
      text.setAttribute("x", pos.x);
      text.setAttribute("y", pos.y + 5);
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("font-size", "12");
      text.textContent =
        p.name.length > 19 ? p.name.slice(0, 18) + "…" : p.name;
      svg.append(text);
    }
    family.append(svg);
  }
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
