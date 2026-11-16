// 日志尾：定时拉取追加，可暂停滚动
const POLL_MS = 2500;

function timeOf(iso) {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return iso || "";
  return moment.toLocaleTimeString("zh-CN", { hour12: false });
}

export function initLogs() {
  const panel = document.querySelector("#log");
  if (!panel || !panel.dataset.runId) return;
  const runId = panel.dataset.runId;
  const pause = document.querySelector("#log-pause");
  const state = {
    paused: false,
    timer: null,
    lastSeq: Number(panel.dataset.lastSeq || 0),
  };

  function append(lines) {
    for (const line of lines) {
      const row = document.createElement("div");
      row.className = "log-line";
      if (line.level === "WARN") row.classList.add("is-warn");
      if (line.level === "ERROR") row.classList.add("is-error");
      const ts = document.createElement("span");
      ts.className = "ts";
      ts.textContent = timeOf(line.ts);
      const lvl = document.createElement("span");
      lvl.className = "lvl";
      lvl.textContent = line.level;
      const msg = document.createElement("span");
      msg.className = "msg";
      msg.textContent = line.message;
      row.append(ts, lvl, msg);
      panel.append(row);
      state.lastSeq = Math.max(state.lastSeq, line.seq);
    }
    if (!state.paused) panel.scrollTop = panel.scrollHeight;
  }

  if (pause) {
    pause.addEventListener("click", () => {
      state.paused = !state.paused;
      pause.setAttribute("aria-pressed", String(state.paused));
      pause.textContent = state.paused ? "继续" : "暂停";
      if (!state.paused) panel.scrollTop = panel.scrollHeight;
    });
  }

  async function tick() {
    if (!state.paused) {
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/log?after=${state.lastSeq}`);
        if (response.ok) {
          const payload = await response.json();
          append(payload.lines || []);
        }
      } catch (error) {
        // 下一轮继续；页面上已有服务端渲染的日志
      }
    }
    state.timer = setTimeout(tick, POLL_MS);
  }

  panel.scrollTop = panel.scrollHeight;
  tick();
}
