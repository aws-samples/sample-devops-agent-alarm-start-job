// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
let activeRegion = "";

function promptsFor(region) {
  return {
    health: `请检查 ${region} 最近 3 小时的运行健康状况。按高优先级告警、异常服务、关键指标和建议下一步四部分回答；每个结论都要列出资源标识与证据。如果没有异常请明确说明。只读分析，不执行任何资源变更。`,
    cost: `请检查 ${region} 中可能闲置、低利用率或配置明显高于近期峰值的资源。按资源、证据、潜在优化动作、实施前核查项输出。不要修改资源，也不要在没有价格证据时估算节省金额。`,
    topology: `请梳理 ${region} 中主要应用资源的服务拓扑和调用依赖，标出入口、计算层、数据层、外部依赖、跨可用区情况与潜在单点。请说明每个判断依据；只读分析，不执行变更。`
  };
}

const output = document.querySelector("#output");
const preflightButton = document.querySelector("#preflightButton");
const preflightState = document.querySelector("#preflightState");
const submitInvestigation = document.querySelector("#submitInvestigation");
const getSummary = document.querySelector("#getSummary");
let activeTaskId = null;
let pollTimer = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`服务返回了不可解析的响应（HTTP ${response.status}）`);
  }
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

const AUTO_REFRESH_MS = 15000;
const knownTaskIds = new Set();
let firstFeedLoad = true;
let feedTimer = null;
let feedBusy = false;

function renderAlarm(alarm) {
  const node = document.querySelector("#alarmState");
  if (!node) return;
  node.className = "alarm-state";
  if (!alarm.deployed) {
    node.classList.add("unknown");
    node.textContent = `告警 ${alarm.alarmName}：尚未部署`;
    return;
  }
  const label = { OK: "正常", ALARM: "告警中", INSUFFICIENT_DATA: "数据不足" }[alarm.state] || alarm.state;
  node.classList.add((alarm.state || "unknown").toLowerCase());
  node.textContent = `告警 ${alarm.alarmName}：${label}（${alarm.state}）｜状态变化于 ${alarm.updatedAt || "-"}`;
}

function hhmmss(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString("zh-CN", { hour12: false });
}

function setStage(id, cls, value, note) {
  const node = document.querySelector(`#st-${id}`);
  if (!node) return;
  node.className = `stage-card ${cls}`;
  node.querySelector("b").textContent = value;
  if (note !== undefined) node.querySelector("small").textContent = note;
}

function renderPipeline(p) {
  const f = p.fault || {};
  if (f.value === "error") setStage("fault", "alert", "error · 已注入故障", `修改于 ${hhmmss(f.updatedAt)}`);
  else if (f.value) setStage("fault", "ok", f.value, `修改于 ${hhmmss(f.updatedAt)}`);
  else setStage("fault", "idle", "未部署", "SSM 参数");

  const w = p.workload || {};
  if (w.level === "ERROR") setStage("workload", "alert", "报错 · 下游超时", hhmmss(w.time));
  else if (w.level === "INFO") setStage("workload", "ok", "正常 · 同步 120 条", hhmmss(w.time));
  else setStage("workload", "idle", "近 5 分钟无日志", "最新日志");

  const a = p.alarm || {};
  if (!a.deployed) setStage("alarm", "idle", "未部署", "P2-DEMO-…Errors");
  else if (a.state === "ALARM") setStage("alarm", "alert", "ALARM 告警中", `变化于 ${hhmmss(a.updatedAt)}`);
  else setStage("alarm", a.state === "OK" ? "ok" : "idle", a.state, `变化于 ${hhmmss(a.updatedAt)}`);

  const t = p.trigger || {}, inv = p.investigation;
  const recent = t.time && Date.now() - new Date(t.time) < 30 * 60 * 1000;
  if (t.taskId && recent) setStage("trigger", "active", `已创建 ${t.taskId.slice(0, 8)}`, hhmmss(t.time));
  else if (t.taskId) setStage("trigger", "ok", `上次 ${t.taskId.slice(0, 8)}`, hhmmss(t.time));
  else setStage("trigger", "idle", "等待告警", "Trigger Lambda");

  if (!inv) setStage("investigation", "idle", "暂无调查", "Investigation");
  else if (["PENDING_START", "IN_PROGRESS", "PENDING_TRIAGE", "LINKED"].includes(inv.status))
    setStage("investigation", "active", `调查中 · ${inv.status}`, inv.taskId.slice(0, 8));
  else if (inv.status === "COMPLETED") setStage("investigation", "ok", "已完成", `${inv.taskId.slice(0, 8)} · ${hhmmss(inv.createdAt)} 创建`);
  else setStage("investigation", "alert", inv.status, inv.taskId.slice(0, 8));

  const q = p.queue;
  if (!q) setStage("queue", "idle", "未部署", "等待本机拉取");
  else if (q.visible + q.inFlight > 0) setStage("queue", "active", `${q.visible + q.inFlight} 条结果待拉取`, "KiroCrew 出站拉取");
  else setStage("queue", "idle", "0 条", "等待本机拉取");
}

async function refreshDiscoveryFeed() {
  const node = document.querySelector("#discoveryOutput");
  const stateNode = document.querySelector("#autoRefreshState");
  if (!node || feedBusy) return;
  feedBusy = true;
  try {
    const [alarm, tasks] = await Promise.all([
      api("/api/alarm"),
      api("/api/investigations?limit=5")
    ]);
    api("/api/pipeline").then(r => renderPipeline(r.data)).catch(err => console.warn("pipeline", err));
    renderAlarm(alarm.data);
    const fresh = tasks.data.filter(task => !knownTaskIds.has(task.taskId));
    tasks.data.forEach(task => knownTaskIds.add(task.taskId));
    const newIds = firstFeedLoad ? new Set() : new Set(fresh.map(task => task.taskId));
    firstFeedLoad = false;
    node.textContent = tasks.data.length
      ? tasks.data.map((task, index) => [
          `${newIds.has(task.taskId) ? "🆕 NEW " : ""}${index + 1}. ${task.title || "未命名调查"}`,
          `   Task ID：${task.taskId}`,
          `   状态：${task.status}｜类型：${task.taskType || "-"}｜优先级：${task.priority || "-"}`,
          `   创建时间：${task.createdAt || "-"}`
        ].join("\n")).join("\n\n")
      : "当前没有 Investigation。等待 P2-DEMO- 告警进入 ALARM。";
    node.classList.toggle("has-new", newIds.size > 0);
    if (stateNode) {
      stateNode.textContent = `自动刷新 · 每 15 秒 · 上次 ${now()}`;
      stateNode.className = "state ok";
    }
  } catch (error) {
    if (stateNode) {
      stateNode.textContent = `自动刷新失败 · ${now()}`;
      stateNode.className = "state error";
    }
    node.textContent = `[${now()}] 主动调查状态读取失败\n\n${conciseError(error)}`;
  } finally {
    feedBusy = false;
  }
}

function startAutoRefresh() {
  if (feedTimer) return;
  refreshDiscoveryFeed();
  feedTimer = setInterval(refreshDiscoveryFeed, AUTO_REFRESH_MS);
}

function stopAutoRefresh() {
  clearInterval(feedTimer);
  feedTimer = null;
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopAutoRefresh();
  else startAutoRefresh();
});

function now() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(new Date());
}

function showOutput(text, kind = "normal") {
  output.textContent = text;
  output.classList.toggle("loading", kind === "loading");
  output.classList.toggle("error", kind === "error");
  output.scrollTop = 0;
}

function setButtonBusy(button, busy, busyLabel = "处理中…") {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.label;
}

function conciseError(error) {
  const raw = error instanceof Error ? error.message : String(error);
  if (raw.includes("sts:TagSession")) {
    return `${raw}\n\n预检结论：当前身份缺少 sts:TagSession，或 Operator App Role 的信任策略未同时允许 sts:AssumeRole 与 sts:TagSession。请切换到目标 Profile 后重启 Demo。`;
  }
  if (raw.includes("Unable to locate credentials") || raw.includes("SSO session")) {
    return `${raw}\n\n预检结论：本机凭证不可用。请先完成 aws configure 或 aws sso login，再用 --profile 启动 Demo。`;
  }
  return raw;
}

async function loadConfig() {
  try {
    const payload = await api("/api/config");
    document.querySelector("#accountId").textContent = payload.accountId;
    document.querySelector("#region").textContent = payload.region;
    document.querySelector("#spaceId").textContent = payload.agentSpaceName
      ? `${payload.agentSpaceName} · ${payload.agentSpaceId}`
      : payload.agentSpaceId;
    document.querySelector("#profile").textContent = payload.profile;
    activeRegion = payload.region;
    document.querySelector("#modeBadge").textContent = "本机配置已发现";
    document.querySelector("#modeBadge").classList.add("live");
    document.querySelector("#customQuestion").placeholder =
      `也可以输入自定义问题，例如：检查 ${activeRegion} 最近一小时是否存在 5xx 或延迟异常`;
    document.querySelector("#investigationDescription").value =
      `调查 ${activeRegion} 过去 24 小时内是否发生告警、5xx、延迟突增或资源瓶颈。请构建时间线，关联 CloudWatch 指标、日志、调用依赖和近期变更，给出有证据支持的根因判断与只读处置参考；不要修改任何资源。`;
  } catch (error) {
    showOutput(`配置加载失败：\n${conciseError(error)}`, "error");
  }
}

preflightButton.addEventListener("click", async () => {
  setButtonBusy(preflightButton, true, "正在验证链路…");
  preflightState.textContent = "连接中";
  preflightState.className = "state muted";
  showOutput(`[${now()}] 正在验证本机凭证 → Operator Role → Agent Space → 只读任务查询…`, "loading");
  try {
    const payload = await api("/api/preflight", { method: "POST", body: "{}" });
    preflightState.textContent = "预检通过";
    preflightState.className = "state ok";
    document.querySelector("#modeBadge").textContent = "主动调查链路已连接";
    showOutput(`[${now()}] 预检通过\n\n${payload.message}`);
  } catch (error) {
    preflightState.textContent = "预检未通过";
    preflightState.className = "state error";
    showOutput(`[${now()}] 预检未通过\n\n${conciseError(error)}`, "error");
  } finally {
    setButtonBusy(preflightButton, false);
  }
});

async function runChat(question, button) {
  setButtonBusy(button, true, "Agent 分析中…");
  showOutput(`[${now()}] 已发送问题\n\n${question}\n\n正在等待 Agent 返回证据链…`, "loading");
  try {
    const payload = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ question })
    });
    showOutput(`[${now()}] Agent 返回\n\n${payload.answer}`);
  } catch (error) {
    showOutput(`[${now()}] 查询未完成\n\n${conciseError(error)}`, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

document.querySelectorAll("button[data-scenario]").forEach(button => {
  button.addEventListener("click", () => {
    if (!activeRegion) {
      showOutput("本机 Profile 配置尚未加载完成。", "error");
      return;
    }
    runChat(promptsFor(activeRegion)[button.dataset.scenario], button);
  });
});

document.querySelector("#customSend").addEventListener("click", event => {
  const question = document.querySelector("#customQuestion").value.trim();
  if (!question) {
    showOutput("请输入自定义问题。", "error");
    return;
  }
  runChat(question, event.currentTarget);
});

document.querySelector("#clearOutput").addEventListener("click", () => {
  showOutput("输出已清空。请选择下一个演示场景。");
});

submitInvestigation.addEventListener("click", async () => {
  const title = document.querySelector("#investigationTitle").value.trim();
  const description = document.querySelector("#investigationDescription").value.trim();
  if (!title || !description) {
    showOutput("调查标题和目标不能为空。", "error");
    return;
  }
  setButtonBusy(submitInvestigation, true, "正在提交…");
  showOutput(`[${now()}] 正在提交异步 Investigation…`, "loading");
  try {
    const payload = await api("/api/investigation", {
      method: "POST",
      body: JSON.stringify({ title, description })
    });
    activeTaskId = payload.data.taskId;
    document.querySelector("#taskId").textContent = activeTaskId;
    updateTaskStatus(payload.data.status);
    showOutput(`[${now()}] 深度调查已提交\n\nTask ID：${activeTaskId}\n状态：${payload.data.status}\n\n页面将每 5 秒自动查询状态。实时调查通常需要 5–8 分钟。`);
    startPolling();
  } catch (error) {
    updateTaskStatus("FAILED");
    showOutput(`[${now()}] 调查提交失败\n\n${conciseError(error)}`, "error");
  } finally {
    setButtonBusy(submitInvestigation, false);
  }
});

function updateTaskStatus(status) {
  const node = document.querySelector("#taskStatus");
  node.textContent = status;
  node.className = "task-status";
  if (status === "COMPLETED") node.classList.add("complete");
  else if (["FAILED", "TIMED_OUT", "CANCELED", "SKIPPED"].includes(status)) node.classList.add("failed");
  else if (status === "尚未提交") node.classList.add("idle");
  else node.classList.add("running");
  getSummary.classList.toggle("hidden", status !== "COMPLETED");
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollInvestigation();
  pollTimer = setInterval(pollInvestigation, 5000);
}

async function pollInvestigation() {
  if (!activeTaskId) return;
  try {
    const payload = await api(`/api/investigation?task_id=${encodeURIComponent(activeTaskId)}`);
    const status = payload.data.status;
    updateTaskStatus(status);
    document.querySelector("#taskUpdated").textContent = payload.data.updatedAt || now();
    if (["COMPLETED", "FAILED", "TIMED_OUT", "CANCELED", "SKIPPED"].includes(status)) {
      clearInterval(pollTimer);
      pollTimer = null;
      if (status === "COMPLETED") {
        showOutput(`[${now()}] Investigation 已完成\n\nTask ID：${activeTaskId}\n点击“提取调查结论”读取正式 investigation_summary_md。`);
      }
    }
  } catch (error) {
    document.querySelector("#taskUpdated").textContent = "查询失败";
    console.error(error);
  }
}

getSummary.addEventListener("click", async () => {
  if (!activeTaskId) return;
  setButtonBusy(getSummary, true, "正在整理结论…");
  showOutput(`[${now()}] 正在提取调查任务 ${activeTaskId} 的完整结论…`, "loading");
  try {
    const payload = await api("/api/investigation/summary", {
      method: "POST",
      body: JSON.stringify({ taskId: activeTaskId })
    });
    showOutput(`[${now()}] Investigation 结论\n\n${payload.answer}`);
    output.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showOutput(`[${now()}] 结论提取失败\n\n${conciseError(error)}`, "error");
  } finally {
    setButtonBusy(getSummary, false);
  }
});

loadConfig().then(startAutoRefresh);
