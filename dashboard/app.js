const summaryCards = document.getElementById("summaryCards");
const focusChips = document.getElementById("focusChips");
const interestChips = document.getElementById("interestChips");
const recentLogs = document.getElementById("recentLogs");
const recentNotes = document.getElementById("recentNotes");
const remindersList = document.getElementById("remindersList");
const trendBars = document.getElementById("trendBars");
const reviewText = document.getElementById("reviewText");
const projectsList = document.getElementById("projectsList");
const timelineList = document.getElementById("timelineList");
const timelineToggleBtn = document.getElementById("timelineToggleBtn");
const conversationSearchInput = document.getElementById("conversationSearchInput");
const memorySearchInput = document.getElementById("memorySearchInput");
const projectsSearchInput = document.getElementById("projectsSearchInput");
const timelineSearchInput = document.getElementById("timelineSearchInput");
const remindersSearchInput = document.getElementById("remindersSearchInput");
const chatFeed = document.getElementById("chatFeed");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const refreshBtn = document.getElementById("refreshBtn");
const reviewBtn = document.getElementById("reviewBtn");
const openProjectModalBtn = document.getElementById("openProjectModalBtn");
const openProjectPanelBtn = document.getElementById("openProjectPanelBtn");
const closeProjectModalBtn = document.getElementById("closeProjectModalBtn");
const projectModal = document.getElementById("projectModal");
const projectForm = document.getElementById("projectForm");
const projectNameInput = document.getElementById("projectNameInput");
const projectGoalInput = document.getElementById("projectGoalInput");
const projectNameField = document.getElementById("projectNameField");
const projectGoalField = document.getElementById("projectGoalField");
const projectSubmitBtn = document.getElementById("projectSubmitBtn");
const projectModalMessage = document.getElementById("projectModalMessage");
const dailyCheckinModal = document.getElementById("dailyCheckinModal");
const dailyCheckinForm = document.getElementById("dailyCheckinForm");
const dailyEnergyInput = document.getElementById("dailyEnergyInput");
const dailyFocusInput = document.getElementById("dailyFocusInput");
const dailyWinInput = document.getElementById("dailyWinInput");
const dailyLaterBtn = document.getElementById("dailyLaterBtn");
const dailySkipBtn = document.getElementById("dailySkipBtn");
const dailySubmitBtn = document.getElementById("dailySubmitBtn");
const dailyCheckinMessage = document.getElementById("dailyCheckinMessage");
const MAX_VISIBLE_CHATS = 15;
const MAX_VISIBLE_TIMELINE = 6;

let visibleChats = [];
let projectModalState = { mode: "add-project", project: "", goalIndex: 0 };
let timelineExpanded = false;
let timelineItems = [];
let pendingActionActive = false;
let pendingActionSubmitting = false;
let dailyCheckinActive = false;
const searchState = {
  conversation: "",
  memory: "",
  projects: "",
  timeline: "",
  reminders: "",
};

const summarySpec = [
  ["logs_total", "Logs"],
  ["notes_total", "Notes"],
  ["conversations_total", "Chats"],
  ["logs_today", "Today"],
  ["notes_this_week", "7d Notes"],
  ["open_reminders", "Reminders"],
  ["projects_total", "Projects"],
];

async function loadOverview() {
  const res = await fetch("/api/overview");
  const data = await res.json();
  renderOverview(data);
}

function renderOverview(data) {
  renderCards(data.summary || {});
  renderChips(focusChips, data.focus || [], "No focus captured yet.");
  renderChips(interestChips, data.interests || [], "No interests learned yet.");
  if (!searchState.memory) {
    renderRepairStack(recentLogs, data.recent_logs || [], "log", "No logs yet.");
    renderRepairStack(recentNotes, data.recent_notes || [], "note", "No notes yet.");
  }
  if (!searchState.reminders) {
    renderStack(remindersList, data.reminders || [], "No open reminders.", (x) => x.text, (x) => x.due_ts || "No due time");
  }
  if (!searchState.projects) {
    renderProjects(data.projects || []);
  }
  timelineItems = Array.isArray(data.timeline) ? data.timeline : [];
  if (!searchState.timeline) {
    renderTimeline(timelineItems);
  }
  renderTrend(data.activity_by_day || []);
  pendingActionActive = Boolean(data.pending_action);
  if (!searchState.conversation) {
    visibleChats = (data.recent_conversations || []).slice(0, MAX_VISIBLE_CHATS);
    renderChat(visibleChats);
  }
  syncDailyCheckin(data.daily_checkin || {});
}

function renderCards(summary) {
  summaryCards.innerHTML = "";
  summarySpec.forEach(([key, label]) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<span class="value">${summary[key] ?? 0}</span><span class="label">${label}</span>`;
    summaryCards.appendChild(card);
  });
}

function renderChips(root, items, emptyText) {
  root.innerHTML = "";
  if (!items.length) {
    root.innerHTML = `<div class="stack-item">${emptyText}</div>`;
    return;
  }
  items.forEach((item) => {
    const el = document.createElement("span");
    el.className = "chip";
    el.textContent = item;
    root.appendChild(el);
  });
}

function renderStack(root, items, emptyText, bodyFn, metaFn) {
  root.innerHTML = "";
  if (!items.length) {
    root.innerHTML = `<div class="stack-item">${emptyText}</div>`;
    return;
  }
  items.forEach((item) => {
    const el = document.createElement("div");
    el.className = "stack-item";
    el.innerHTML = `<div>${escapeHtml(bodyFn(item) || "")}</div><small>${escapeHtml(metaFn(item) || "")}</small>`;
    root.appendChild(el);
  });
}

function renderTimeline(items) {
  const visible = timelineExpanded ? items : items.slice(0, MAX_VISIBLE_TIMELINE);
  renderStack(timelineList, visible, "No timeline yet.", (x) => `[${x.kind}] ${x.text}`, (x) => x.ts);

  if (searchState.timeline) {
    timelineToggleBtn.classList.add("hidden");
    timelineToggleBtn.textContent = "Show More";
    return;
  }

  if (!items.length || items.length <= MAX_VISIBLE_TIMELINE) {
    timelineToggleBtn.classList.add("hidden");
    timelineToggleBtn.textContent = "Show More";
    return;
  }

  timelineToggleBtn.classList.remove("hidden");
  timelineToggleBtn.textContent = timelineExpanded ? "Show Less" : `Show More (${items.length - MAX_VISIBLE_TIMELINE})`;
}

function renderRepairStack(root, items, kind, emptyText) {
  root.innerHTML = "";
  if (!items.length) {
    root.innerHTML = `<div class="stack-item">${emptyText}</div>`;
    return;
  }
  items.forEach((item) => {
    const pinned = item.pinned ? " pinned" : "";
    const pinLabel = item.pinned ? "Unpin" : "Pin";
    const el = document.createElement("div");
    el.className = `stack-item${pinned}`;
    el.innerHTML = `
      <div>${escapeHtml(item.text || "")}</div>
      <small>${escapeHtml(item.ts || "")} · ${kind} ${item.recent_index}</small>
      <div class="row-actions">
        <button class="ghost mini" data-action="edit" data-kind="${kind}" data-index="${item.recent_index}">Edit</button>
        <button class="ghost mini" data-action="${item.pinned ? "unpin" : "pin"}" data-kind="${kind}" data-index="${item.recent_index}">${pinLabel}</button>
        <button class="ghost mini danger" data-action="delete" data-kind="${kind}" data-index="${item.recent_index}">Delete</button>
      </div>
    `;
    root.appendChild(el);
  });
}

function renderProjects(items) {
  projectsList.innerHTML = "";
  if (!items.length) {
    projectsList.innerHTML = `<div class="stack-item">No projects yet.</div>`;
    return;
  }
  items.forEach((project) => {
    const goals = Array.isArray(project.goals) ? project.goals : [];
    const projectName = project.name || "";
    const status = project.status || "active";
    const goalsHtml = goals.length
      ? goals
          .map(
            (goal, index) => `
              <div class="goal-row">
                <div class="goal-line${goal.done ? " done" : ""}">${escapeHtml(goal.text || "")}</div>
                <div class="row-actions compact">
                  ${
                    goal.done
                      ? `<button class="ghost mini" data-project-action="reopen_goal" data-project="${escapeAttr(projectName)}" data-goal-index="${
                          index + 1
                        }">Reopen</button>`
                      : `<button class="ghost mini" data-project-action="done_goal" data-project="${escapeAttr(projectName)}" data-goal-index="${
                          index + 1
                        }">Done</button>`
                  }
                  <button class="ghost mini" data-project-action="open_edit_goal" data-project="${escapeAttr(projectName)}" data-goal-index="${
                    index + 1
                  }" data-goal-text="${escapeAttr(goal.text || "")}">Edit</button>
                  <button class="ghost mini danger" data-project-action="delete_goal" data-project="${escapeAttr(projectName)}" data-goal-index="${
                    index + 1
                  }">Delete</button>
                </div>
              </div>
            `
          )
          .join("")
      : `<div class="goal-line">No goals yet.</div>`;
    const projectActions = [];
    projectActions.push(`<button class="ghost mini" data-project-action="open_add_goal" data-project="${escapeAttr(projectName)}">Add Goal</button>`);
    if (status === "active") {
      projectActions.push(`<button class="ghost mini" data-project-action="complete_project" data-project="${escapeAttr(projectName)}">Done</button>`);
      projectActions.push(`<button class="ghost mini" data-project-action="archive_project" data-project="${escapeAttr(projectName)}">Archive</button>`);
    } else if (status === "archived") {
      projectActions.push(`<button class="ghost mini" data-project-action="activate_project" data-project="${escapeAttr(projectName)}">Reopen</button>`);
      projectActions.push(`<button class="ghost mini" data-project-action="complete_project" data-project="${escapeAttr(projectName)}">Mark Done</button>`);
    } else {
      projectActions.push(`<button class="ghost mini" data-project-action="activate_project" data-project="${escapeAttr(projectName)}">Move Active</button>`);
      projectActions.push(`<button class="ghost mini" data-project-action="archive_project" data-project="${escapeAttr(projectName)}">Archive</button>`);
    }
    const el = document.createElement("div");
    el.className = "stack-item";
    el.innerHTML = `
      <div class="project-head">
        <strong>${escapeHtml(projectName)}</strong>
        <span class="project-status status-${escapeHtml(status)}">${escapeHtml(status)}</span>
      </div>
      <div class="row-actions project-actions">${projectActions.join("")}</div>
      <div class="goal-list">${goalsHtml}</div>
    `;
    projectsList.appendChild(el);
  });
}

function renderTrend(items) {
  trendBars.innerHTML = "";
  if (!items.length) {
    trendBars.innerHTML = `<div class="stack-item">No trend data yet.</div>`;
    return;
  }
  const max = Math.max(...items.map((x) => x.count), 1);
  items.forEach((item) => {
    const wrap = document.createElement("div");
    wrap.className = "bar-wrap";
    const h = Math.max(14, Math.round((item.count / max) * 140));
    wrap.innerHTML = `
      <div class="bar-value">${item.count}</div>
      <div class="bar" style="height:${h}px"></div>
      <div class="bar-label">${item.day.slice(5)}</div>
    `;
    trendBars.appendChild(wrap);
  });
}

function renderChat(items) {
  const previousTop = chatFeed.scrollTop;
  chatFeed.innerHTML = "";
  if (!items.length) {
    chatFeed.innerHTML = `<div class="stack-item">No conversation history yet.</div>`;
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "chat-row";
    const assistantClass = item.pending ? "bubble assistant thinking" : "bubble assistant";
    const actionBar = item.pendingAction && item.isLatest
      ? `
        <div class="chat-actions">
          <button class="ghost mini icon-btn" data-pending-action="approve" title="Approve"${pendingActionSubmitting ? " disabled" : ""}>👍</button>
          <button class="ghost mini icon-btn" data-pending-action="skip" title="Skip"${pendingActionSubmitting ? " disabled" : ""}>👎</button>
        </div>
      `
      : "";
    row.innerHTML = `
      <div class="bubble user">${escapeHtml(item.user || "")}</div>
      <div class="${assistantClass}">${escapeHtml(item.assistant || "")}</div>
      ${actionBar}
    `;
    chatFeed.appendChild(row);
  });
  chatFeed.scrollTop = previousTop;
}

async function sendChat(text) {
  const pending = { user: text, assistant: "Nudge is thinking...", source: "dashboard", pending: true };
  visibleChats = [pending, ...visibleChats].slice(0, MAX_VISIBLE_CHATS);
  renderChat(visibleChats);

  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const data = await res.json();
  visibleChats = (data.overview?.recent_conversations || []).slice(0, MAX_VISIBLE_CHATS);
  visibleChats = visibleChats.map((item, index) => ({
    ...item,
    isLatest: index === 0,
    pendingAction: index === 0 ? Boolean(data.pending_action) : false,
  }));
  if (visibleChats.length) {
    visibleChats[0].assistant = data.reply_display || data.reply || visibleChats[0].assistant;
  }
  renderOverview({
    ...(data.overview || {}),
    recent_conversations: visibleChats,
    graph_enabled: data.graph_enabled,
  });
}

async function handlePendingAction(action) {
  if (pendingActionSubmitting || !pendingActionActive) {
    return;
  }
  pendingActionSubmitting = true;
  if (visibleChats.length) {
    visibleChats[0] = {
      ...visibleChats[0],
      assistant: action === "approve" ? "Saving..." : "Skipping...",
      pendingAction: false,
      isLatest: true,
    };
    renderChat(visibleChats);
  }
  const res = await fetch("/api/pending-save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const data = await res.json();
  if (visibleChats.length) {
    visibleChats[0] = {
      ...visibleChats[0],
      assistant: data.reply_display || data.reply || "",
      pendingAction: false,
      isLatest: true,
    };
  }
  pendingActionSubmitting = false;
  renderOverview({
    ...(data.overview || {}),
    recent_conversations: visibleChats,
    graph_enabled: data.graph_enabled,
  });
}

async function repairItem(action, kind, recentIndex, text = "") {
  const res = await fetch("/api/repair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, kind, recent_index: recentIndex, text }),
  });
  const data = await res.json();
  renderOverview(data.overview || {});
}

async function mutateProject(action, payload) {
  const res = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  });
  const data = await res.json();
  renderOverview(data.overview || {});
  return data;
}

async function searchCard(card, query) {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card, query }),
  });
  return await res.json();
}

async function submitDailyCheckin(action, payload = {}) {
  const res = await fetch("/api/daily-checkin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  });
  return await res.json();
}

async function loadReview() {
  reviewText.textContent = "Generating weekly review...";
  const res = await fetch("/api/review-week");
  const data = await res.json();
  reviewText.textContent = data.review || "Review unavailable.";
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  await sendChat(text);
});

refreshBtn.addEventListener("click", loadOverview);
reviewBtn.addEventListener("click", loadReview);
timelineToggleBtn.addEventListener("click", () => {
  timelineExpanded = !timelineExpanded;
  renderTimeline(timelineItems);
});
openProjectModalBtn.addEventListener("click", () => openProjectModal("add-project"));
openProjectPanelBtn.addEventListener("click", () => openProjectModal("add-project"));
closeProjectModalBtn.addEventListener("click", closeProjectModal);
dailyLaterBtn.addEventListener("click", closeDailyCheckinModal);
dailySkipBtn.addEventListener("click", async () => {
  await submitDailyCheckin("dismiss");
  closeDailyCheckinModal();
  await loadOverview();
});

projectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setProjectModalMessage("");
  const name = projectNameInput.value.trim();
  const goal = projectGoalInput.value.trim();
  if (projectModalState.mode === "add-project") {
    if (!name) {
      setProjectModalMessage("Please enter a project name.");
      return;
    }
    const result = await mutateProject("add_project", { name });
    if (!result.ok) {
      setProjectModalMessage(result.message || "Could not create project.");
      return;
    }
    if (goal) {
      const goalResult = await mutateProject("add_goal", { project: name, text: goal });
      if (!goalResult.ok) {
        setProjectModalMessage(goalResult.message || "Project created, but the first goal could not be added.");
        return;
      }
    }
    closeProjectModal();
    return;
  }

  if (!projectModalState.project) {
    setProjectModalMessage("Project details are missing.");
    return;
  }
  if (!goal) {
    setProjectModalMessage("Please enter a goal.");
    return;
  }
  if (projectModalState.mode === "add-goal") {
    const result = await mutateProject("add_goal", { project: projectModalState.project, text: goal });
    if (!result.ok) {
      setProjectModalMessage(result.message || "Could not add goal.");
      return;
    }
    closeProjectModal();
    return;
  }
  if (projectModalState.mode === "edit-goal") {
    const result = await mutateProject("edit_goal", {
      project: projectModalState.project,
      goal_index: projectModalState.goalIndex,
      text: goal,
    });
    if (!result.ok) {
      setProjectModalMessage(result.message || "Could not update goal.");
      return;
    }
    closeProjectModal();
  }
});

dailyCheckinForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setDailyCheckinMessage("");
  setDailyCheckinSubmitting(true);
  const data = await submitDailyCheckin("submit", {
    energy: dailyEnergyInput.value.trim(),
    focus: dailyFocusInput.value.trim(),
    win: dailyWinInput.value.trim(),
  });
  setDailyCheckinSubmitting(false);
  if (!data.ok) {
    setDailyCheckinMessage("Could not save your check-in just now.");
    return;
  }
  closeDailyCheckinModal();
  renderOverview(data.overview || {});
});

document.addEventListener("click", async (event) => {
  if (event.target.closest("[data-close-modal='true']")) {
    closeProjectModal();
    return;
  }
  if (event.target.closest("[data-close-checkin='dismiss']")) {
    closeDailyCheckinModal();
    return;
  }

  const btn = event.target.closest("button[data-action]");
  if (btn) {
    const action = btn.dataset.action;
    const kind = btn.dataset.kind;
    const recentIndex = Number(btn.dataset.index || 0);
    if (!action || !kind || !recentIndex) return;

    if (action === "edit") {
      const text = window.prompt(`Edit ${kind} ${recentIndex}`);
      if (!text || !text.trim()) return;
      await repairItem("edit", kind, recentIndex, text.trim());
      return;
    }

    await repairItem(action, kind, recentIndex);
    return;
  }

  const projectBtn = event.target.closest("button[data-project-action]");
  const pendingBtn = event.target.closest("button[data-pending-action]");
  if (pendingBtn) {
    pendingBtn.disabled = true;
    await handlePendingAction(pendingBtn.dataset.pendingAction || "");
    return;
  }
  if (!projectBtn) return;
  const projectAction = projectBtn.dataset.projectAction;
  const project = projectBtn.dataset.project;
  const goalIndex = Number(projectBtn.dataset.goalIndex || 0);
  const goalText = projectBtn.dataset.goalText || "";

  if (projectAction === "open_add_goal" && project) {
    openProjectModal("add-goal", { project });
    return;
  }
  if (projectAction === "open_edit_goal" && project && goalIndex) {
    openProjectModal("edit-goal", { project, goalIndex, goalText });
    return;
  }
  if (projectAction === "delete_goal" && project && goalIndex) {
    if (window.confirm("Delete this goal?")) {
      await mutateProject("delete_goal", { project, goal_index: goalIndex });
    }
    return;
  }
  if ((projectAction === "done_goal" || projectAction === "reopen_goal") && project && goalIndex) {
    await mutateProject(projectAction, { project, goal_index: goalIndex });
    return;
  }
  if (
    (projectAction === "archive_project" || projectAction === "complete_project" || projectAction === "activate_project") &&
    project
  ) {
    await mutateProject(projectAction, { project });
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !projectModal.classList.contains("hidden")) {
    closeProjectModal();
    return;
  }
  if (event.key === "Escape" && !dailyCheckinModal.classList.contains("hidden")) {
    closeDailyCheckinModal();
  }
});

wireSearch(conversationSearchInput, "conversation", async (data) => {
  visibleChats = (data.recent_conversations || []).slice(0, 40);
  renderChat(visibleChats);
});

wireSearch(memorySearchInput, "memory", async (data) => {
  renderRepairStack(recentLogs, data.recent_logs || [], "log", "No matching logs.");
  renderRepairStack(recentNotes, data.recent_notes || [], "note", "No matching notes.");
});

wireSearch(projectsSearchInput, "projects", async (data) => {
  renderProjects(data.projects || []);
});

wireSearch(timelineSearchInput, "timeline", async (data) => {
  timelineExpanded = true;
  timelineItems = Array.isArray(data.timeline) ? data.timeline : [];
  renderTimeline(timelineItems);
});

wireSearch(remindersSearchInput, "reminders", async (data) => {
  renderStack(remindersList, data.reminders || [], "No matching reminders.", (x) => x.text, (x) => x.due_ts || "No due time");
});

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('"', "&quot;");
}

function openProjectModal(mode, options = {}) {
  projectModalState = {
    mode,
    project: options.project || "",
    goalIndex: options.goalIndex || 0,
  };
  setProjectModalMessage("");
  projectModal.classList.remove("hidden");
  projectModal.setAttribute("aria-hidden", "false");
  projectNameField.classList.remove("hidden");
  projectGoalField.classList.remove("hidden");

  const eyebrow = document.querySelector(".modal-eyebrow");
  const title = document.querySelector(".modal-head h2");
  if (mode === "add-project") {
    eyebrow.textContent = "Project Setup";
    title.textContent = "Add A Project";
    projectNameInput.readOnly = false;
    projectNameInput.value = "";
    projectGoalInput.value = "";
    projectGoalInput.placeholder = "Play badminton twice this week";
    projectSubmitBtn.textContent = "Create Project";
    projectNameInput.focus();
    return;
  }

  projectNameInput.readOnly = true;
  projectNameInput.value = options.project || "";

  if (mode === "add-goal") {
    eyebrow.textContent = "Project Goal";
    title.textContent = "Add Goal";
    projectGoalInput.value = "";
    projectGoalInput.placeholder = "What do you want to achieve next?";
    projectSubmitBtn.textContent = "Add Goal";
    projectGoalInput.focus();
    return;
  }

  eyebrow.textContent = "Goal Update";
  title.textContent = "Edit Goal";
  projectGoalInput.value = options.goalText || "";
  projectGoalInput.placeholder = "Refine this goal";
  projectSubmitBtn.textContent = "Save Goal";
  projectGoalInput.focus();
  projectGoalInput.select();
}

function setProjectModalMessage(text) {
  if (!text) {
    projectModalMessage.textContent = "";
    projectModalMessage.classList.add("hidden");
    return;
  }
  projectModalMessage.textContent = text;
  projectModalMessage.classList.remove("hidden");
}

function wireSearch(input, card, applyResults) {
  let requestId = 0;
  input.addEventListener("input", async () => {
    const query = input.value.trim();
    searchState[card] = query;
    requestId += 1;
    const currentRequest = requestId;
    if (!query) {
      await loadOverview();
      return;
    }
    const data = await searchCard(card, query);
    if (currentRequest !== requestId) {
      return;
    }
    await applyResults(data);
  });
}

function closeProjectModal() {
  projectModal.classList.add("hidden");
  projectModal.setAttribute("aria-hidden", "true");
  setProjectModalMessage("");
  projectModalState = { mode: "add-project", project: "", goalIndex: 0 };
  projectNameInput.readOnly = false;
  projectForm.reset();
}

function syncDailyCheckin(state) {
  dailyCheckinActive = Boolean(state.should_prompt);
  if (dailyCheckinActive) {
    openDailyCheckinModal();
    return;
  }
  closeDailyCheckinModal();
}

function openDailyCheckinModal() {
  dailyCheckinModal.classList.remove("hidden");
  dailyCheckinModal.setAttribute("aria-hidden", "false");
  dailyEnergyInput.focus();
}

function closeDailyCheckinModal() {
  dailyCheckinActive = false;
  dailyCheckinModal.classList.add("hidden");
  dailyCheckinModal.setAttribute("aria-hidden", "true");
  setDailyCheckinMessage("");
  setDailyCheckinSubmitting(false);
}

function setDailyCheckinMessage(text) {
  if (!text) {
    dailyCheckinMessage.textContent = "";
    dailyCheckinMessage.classList.add("hidden");
    return;
  }
  dailyCheckinMessage.textContent = text;
  dailyCheckinMessage.classList.remove("hidden");
}

function setDailyCheckinSubmitting(isSubmitting) {
  dailyEnergyInput.disabled = isSubmitting;
  dailyFocusInput.disabled = isSubmitting;
  dailyWinInput.disabled = isSubmitting;
  dailyLaterBtn.disabled = isSubmitting;
  dailySkipBtn.disabled = isSubmitting;
  dailySubmitBtn.disabled = isSubmitting;
  dailySubmitBtn.textContent = isSubmitting ? "Saving..." : "Start Check-in";
}

loadOverview();
