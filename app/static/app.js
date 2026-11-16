import { initLogs } from "/static/logs.js";
import { initTables } from "/static/tables.js";

// 页面动作：触发运行、提交预算、失败提示
const state = { busy: false };

function banner(text) {
  const node = document.querySelector("#banner");
  if (!node) return;
  node.textContent = text;
  node.classList.add("is-on");
}

function clearBanner() {
  const node = document.querySelector("#banner");
  if (!node) return;
  node.textContent = "";
  node.classList.remove("is-on");
}

async function request(url, options) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }
  if (!response.ok) {
    const message = payload && payload.message ? payload.message : `请求失败 ${response.status}`;
    const code = payload && payload.error ? `${payload.error}：` : "";
    throw new Error(code + message);
  }
  return payload;
}

async function triggerRun(dataset, button) {
  if (state.busy) return;
  state.busy = true;
  clearBanner();
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "运行中";
  try {
    const result = await request("/api/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset }),
    });
    window.location.href = `/?run_id=${encodeURIComponent(result.run_id)}`;
  } catch (error) {
    banner(error.message);
    button.disabled = false;
    button.textContent = label;
  } finally {
    state.busy = false;
  }
}

async function submitBudget(form) {
  const submit = form.querySelector("button[type=submit]");
  clearBanner();
  submit.disabled = true;
  const body = {
    plan_version: Number(form.elements.plan_version.value),
    total_yuan: Number(form.elements.total_yuan.value),
    model: form.elements.model.value,
    use_refund_net: form.elements.use_refund_net
      ? form.elements.use_refund_net.checked
      : false,
  };
  try {
    await request("/api/budgets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    window.location.reload();
  } catch (error) {
    banner(error.message);
  } finally {
    submit.disabled = false;
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-run]");
  if (!button) return;
  event.preventDefault();
  triggerRun(button.dataset.run, button);
});

async function submitRefund(form) {
  const submit = form.querySelector("button[type=submit]");
  clearBanner();
  submit.disabled = true;
  const body = {
    batch_id: form.elements.batch_id.value.trim(),
    source: form.elements.source.value,
  };
  try {
    await request("/api/refunds/batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    window.location.reload();
  } catch (error) {
    banner(error.message);
  } finally {
    submit.disabled = false;
  }
}

const form = document.querySelector("#budget-form");
if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitBudget(form);
  });
}

const refundForm = document.querySelector("#refund-form");
if (refundForm) {
  refundForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitRefund(refundForm);
  });
}

initTables();
initLogs();
