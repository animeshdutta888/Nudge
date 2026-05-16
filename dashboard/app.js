const summaryCards = document.getElementById("summaryCards");
const runtimeRibbon = document.getElementById("runtimeRibbon");
const runtimeSummary = document.getElementById("runtimeSummary");
const runtimeStatusTag = document.getElementById("runtimeStatusTag");
const focusChips = document.getElementById("focusChips");
const chatFeed = document.getElementById("chatFeed");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const quickPrompts = document.getElementById("quickPrompts");
const conversationSearchInput = document.getElementById("conversationSearchInput");
const contextSearchInput = document.getElementById("contextSearchInput");
const contextTabs = document.getElementById("contextTabs");
const memoryList = document.getElementById("memoryList");
const memoryMoreBtn = document.getElementById("memoryMoreBtn");
const projectsList = document.getElementById("projectsList");
const projectsMoreBtn = document.getElementById("projectsMoreBtn");
const remindersList = document.getElementById("remindersList");
const remindersMoreBtn = document.getElementById("remindersMoreBtn");
const reviewText = document.getElementById("reviewText");
const newChatBtn = document.getElementById("newChatBtn");
const refreshBtn = document.getElementById("refreshBtn");
const reviewBtn = document.getElementById("reviewBtn");
const inlineReviewBtn = document.getElementById("inlineReviewBtn");
const themeToggleBtn = document.getElementById("themeToggleBtn");
const openProjectModalBtn = document.getElementById("openProjectModalBtn");
const openProjectPanelBtn = document.getElementById("openProjectPanelBtn");
const projectModal = document.getElementById("projectModal");
const closeProjectModalBtn = document.getElementById("closeProjectModalBtn");
const projectForm = document.getElementById("projectForm");
const projectNameField = document.getElementById("projectNameField");
const projectGoalField = document.getElementById("projectGoalField");
const projectNameInput = document.getElementById("projectNameInput");
const projectGoalInput = document.getElementById("projectGoalInput");
const projectSubmitBtn = document.getElementById("projectSubmitBtn");
const projectModalTitle = document.getElementById("projectModalTitle");
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

const MAX_VISIBLE_CHATS = 24;
const PROMPTS = [
  "What do you know about me?",
  "What did I learn recently?",
  "What should I focus on next?",
  "Summarize my current priorities",
];

let overviewCache = null;
let visibleChats = [];
let activeContextTab = "memory";
let pendingActionActive = false;
let pendingActionSubmitting = false;
let activeChatStartTs = "";
let activeChatAnchorCount = 0;
let contextExpanded = {
  memory: false,
  projects: false,
  reminders: false,
};
let projectModalState = { mode: "add-project", project: "", goalIndex: 0 };

function init() {
  hydrateTheme();
  renderQuickPrompts();
  wireEvents();
  loadOverview();
}

async function loadOverview() {
  try {
    const res = await fetch("/api/overview");
    if (!res.ok) throw new Error(`Overview request failed (${res.status})`);
    const data = await res.json();
    overviewCache = data;
    renderOverview(data);
  } catch (error) {
    renderRuntimeError(`Could not load overview. ${error.message || error}`);
  }
}

function renderOverview(data) {
  renderSummary(data.summary || {});
  renderRuntime(data.runtime || {});
  renderFocus(data.focus || []);
  visibleChats = sortChatsChronologically((data.recent_conversations || []).slice(0, MAX_VISIBLE_CHATS).map(cleanChatItem));
  renderChat(filterConversation(visibleChats, conversationSearchInput.value.trim()));
  renderCurrentContext(data);
  pendingActionActive = Boolean(data.pending_action);
  syncDailyCheckin(data.daily_checkin || {});
}

function renderSummary(summary) {
  const items = [
    ["Chats", summary.conversations_total ?? 0],
    ["Memories", (summary.logs_total ?? 0) + (summary.notes_total ?? 0)],
    ["Reminders", summary.open_reminders ?? 0],
    ["Projects", summary.projects_total ?? 0],
  ];
  summaryCards.innerHTML = items
    .map(
      ([label, value]) => `
        <div class="summary-card">
          <span class="value">${escapeHtml(String(value))}</span>
          <span class="label">${escapeHtml(label)}</span>
        </div>
      `
    )
    .join("");
}

function renderRuntime(runtime) {
  const status = String(runtime.status || "IDLE").toUpperCase();
  const degraded = Boolean(runtime.degraded_mode);
  runtimeStatusTag.textContent = degraded ? `${status} / DEGRADED` : status;
  runtimeRibbon.innerHTML = [
    runtime.mode || "LOCAL-FIRST",
    runtime.network || "DISCONNECTED",
    degraded ? "FALLBACK ACTIVE" : "HEALTHY",
  ]
    .map((item) => `<span class="pill">${escapeHtml(item)}</span>`)
    .join("");

  runtimeSummary.innerHTML = `
    <div><strong>Latest query</strong><br />${escapeHtml(runtime.query || "No recent run yet.")}</div>
    <div><strong>Source</strong><br />${escapeHtml(runtime.source || "dashboard")}</div>
    <div><strong>Retrieved</strong><br />${escapeHtml(String(runtime.retrieved_chunks ?? 0))} items</div>
    <div><strong>Memory used</strong><br />${escapeHtml(String(runtime.memory_records ?? 0))} items</div>
  `;
}

function renderFocus(items) {
  focusChips.innerHTML = "";
  if (!items.length) {
    focusChips.innerHTML = `<div class="info-card-body">No current focus saved yet.</div>`;
    return;
  }
  items.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item;
    focusChips.appendChild(chip);
  });
}

function renderQuickPrompts() {
  quickPrompts.innerHTML = PROMPTS.map(
    (prompt) => `<button class="ghost mini quick-prompt" type="button" data-prompt="${escapeAttr(prompt)}">${escapeHtml(prompt)}</button>`
  ).join("");
}

function renderChat(items) {
  const shouldStick = chatFeed.scrollHeight - chatFeed.scrollTop - chatFeed.clientHeight < 100;
  if (!items.length) {
    chatFeed.innerHTML = `<div class="empty-state">No matching conversation yet.</div>`;
    return;
  }
  chatFeed.innerHTML = items
    .map((item, index) => {
      const actionBar = item.pendingAction && index === 0
        ? `
          <div class="chat-actions">
            <button class="ghost mini" data-pending-action="approve" type="button"${pendingActionSubmitting ? " disabled" : ""}>Approve</button>
            <button class="ghost mini" data-pending-action="skip" type="button"${pendingActionSubmitting ? " disabled" : ""}>Skip</button>
          </div>
        `
        : "";
      return `
        <div class="chat-row">
          <div class="chat-meta user">You</div>
          <div class="bubble user">${escapeHtml(item.user || "")}</div>
          <div class="chat-meta">${escapeHtml(item.source || "nudge")}</div>
          <div class="bubble assistant${item.pending ? " thinking" : ""}">${escapeHtml(item.assistant || "")}</div>
          ${actionBar}
        </div>
      `;
    })
    .join("");
  if (shouldStick) {
    chatFeed.scrollTop = chatFeed.scrollHeight;
  }
}

function renderCurrentContext(data) {
  if (activeContextTab === "memory") {
    renderMemoryPanel(data.recent_logs || [], data.recent_notes || []);
    return;
  }
  if (activeContextTab === "projects") {
    renderProjects(data.projects || []);
    return;
  }
  if (activeContextTab === "reminders") {
    renderReminders(data.reminders || []);
    return;
  }
  renderReviewPanel();
}

function renderMemoryPanel(logs, notes) {
  const mergedAll = [
    ...logs.map((item) => ({ ...item, kind: "log" })),
    ...notes.map((item) => ({ ...item, kind: "note" })),
  ]
    .sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")));

  const merged = contextExpanded.memory ? mergedAll : mergedAll.slice(0, 5);

  if (!mergedAll.length) {
    memoryList.innerHTML = `<div class="empty-state">No saved notes or logs yet.</div>`;
    memoryMoreBtn.classList.add("hidden");
    return;
  }

  memoryList.innerHTML = merged
    .map(
      (item) => `
        <div class="info-card">
          <div class="info-card-head">
            <div>
              <p class="info-card-title">${escapeHtml(item.kind === "note" ? "Note" : "Log")}</p>
              <div class="info-card-meta">${escapeHtml(item.ts || "")}</div>
            </div>
            <button class="ghost mini danger" type="button" data-action="delete" data-kind="${escapeAttr(item.kind)}" data-index="${escapeAttr(String(item.recent_index || 0))}">Remove</button>
          </div>
          <p class="info-card-body">${escapeHtml(item.text || "")}</p>
        </div>
      `
    )
    .join("");
  memoryMoreBtn.classList.toggle("hidden", mergedAll.length <= 5);
  memoryMoreBtn.textContent = contextExpanded.memory ? "Show Less" : "See More";
}

function renderProjects(items) {
  const allItems = items || [];
  const visibleItems = contextExpanded.projects ? allItems : allItems.slice(0, 5);
  if (!allItems.length) {
    projectsList.innerHTML = `<div class="empty-state">No active projects yet.</div>`;
    projectsMoreBtn.classList.add("hidden");
    return;
  }

  projectsList.innerHTML = visibleItems
    .map((project) => {
      const goals = Array.isArray(project.goals) ? project.goals.slice(0, 3) : [];
      const goalLines = goals.length
        ? goals
            .map(
              (goal, index) => `
                <div class="info-card-meta">${goal.done ? "Done" : "Open"} · ${escapeHtml(goal.text || "")}
                  <button class="ghost mini" type="button" data-project-action="${goal.done ? "reopen_goal" : "done_goal"}" data-project="${escapeAttr(project.name || "")}" data-goal-index="${index + 1}">${goal.done ? "Reopen" : "Done"}</button>
                </div>
              `
            )
            .join("")
        : `<div class="info-card-meta">No goals yet.</div>`;

      return `
        <div class="info-card">
          <div class="info-card-head">
            <div>
              <p class="info-card-title">${escapeHtml(project.name || "")}</p>
              <div class="info-card-meta">${escapeHtml(project.status || "active")}</div>
            </div>
            <button class="ghost mini danger" type="button" data-project-action="delete_project" data-project="${escapeAttr(project.name || "")}">Remove</button>
          </div>
          <div class="card-actions">
            <button class="ghost mini" type="button" data-project-action="open_add_goal" data-project="${escapeAttr(project.name || "")}">Add Goal</button>
            <button class="ghost mini" type="button" data-project-action="archive_project" data-project="${escapeAttr(project.name || "")}">Archive</button>
          </div>
          <div class="card-stack">${goalLines}</div>
        </div>
      `;
    })
    .join("");
  projectsMoreBtn.classList.toggle("hidden", allItems.length <= 5);
  projectsMoreBtn.textContent = contextExpanded.projects ? "Show Less" : "See More";
}

function renderReminders(items) {
  const allItems = items || [];
  const visibleItems = contextExpanded.reminders ? allItems : allItems.slice(0, 5);
  if (!allItems.length) {
    remindersList.innerHTML = `<div class="empty-state">No reminders due soon.</div>`;
    remindersMoreBtn.classList.add("hidden");
    return;
  }
  remindersList.innerHTML = visibleItems
    .map(
      (item) => `
        <div class="info-card">
          <div class="info-card-head">
            <div>
              <p class="info-card-title">${escapeHtml(item.text || "")}</p>
              <div class="info-card-meta">${escapeHtml(item.due_ts || "No due time")}</div>
            </div>
          </div>
        </div>
      `
    )
    .join("");
  remindersMoreBtn.classList.toggle("hidden", allItems.length <= 5);
  remindersMoreBtn.textContent = contextExpanded.reminders ? "Show Less" : "See More";
}

async function renderReviewPanel(forceRefresh = false) {
  if (!forceRefresh && reviewText.dataset.loaded === "true") {
    return;
  }
  reviewText.textContent = "Generating review...";
  try {
    const res = await fetch("/api/review-week");
    if (!res.ok) throw new Error(`Review request failed (${res.status})`);
    const data = await res.json();
    reviewText.textContent = data.review || "Review unavailable.";
    reviewText.dataset.loaded = "true";
  } catch (error) {
    reviewText.textContent = `Review failed.\n${error.message || error}`;
  }
}

async function sendChat(text) {
  const pending = cleanChatItem({ user: text, assistant: "Nudge is thinking...", source: "nudge", pending: true });
  const baseChats = filterConversation(visibleChats, "");
  visibleChats = [...baseChats, pending].slice(-MAX_VISIBLE_CHATS);
  renderChat(filterConversation(visibleChats, conversationSearchInput.value.trim()));

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error(`Chat request failed (${res.status})`);
    const data = await res.json();
    overviewCache = data.overview || overviewCache;
    visibleChats = sortChatsChronologically(
      ((data.overview && data.overview.recent_conversations) || []).slice(0, MAX_VISIBLE_CHATS).map(cleanChatItem)
    );
    if (visibleChats.length) {
      visibleChats[visibleChats.length - 1].pendingAction = Boolean(data.pending_action);
    }
    renderOverview({ ...(overviewCache || {}), recent_conversations: visibleChats });
  } catch (error) {
    visibleChats[visibleChats.length - 1] = cleanChatItem({
      user: text,
      assistant: `Nudge hit an error while responding.\n${error.message || error}`,
      source: "nudge",
    });
    renderChat(filterConversation(visibleChats, conversationSearchInput.value.trim()));
    renderRuntimeError(`Chat failed. ${error.message || error}`);
  }
}

async function handlePendingAction(action) {
  if (pendingActionSubmitting || !pendingActionActive) return;
  pendingActionSubmitting = true;
  try {
    const res = await fetch("/api/pending-action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    if (!res.ok) throw new Error(`Pending action failed (${res.status})`);
    const data = await res.json();
    overviewCache = data.overview || overviewCache;
    visibleChats = sortChatsChronologically(
      ((data.overview && data.overview.recent_conversations) || []).slice(0, MAX_VISIBLE_CHATS).map(cleanChatItem)
    );
    if (data.reply_display) {
      visibleChats = [...visibleChats, cleanChatItem({ user: "Action", assistant: data.reply_display, source: "nudge" })].slice(-MAX_VISIBLE_CHATS);
    }
    renderOverview({ ...(overviewCache || {}), recent_conversations: visibleChats });
  } catch (error) {
    renderRuntimeError(`Could not complete pending action. ${error.message || error}`);
  } finally {
    pendingActionSubmitting = false;
  }
}

async function repairItem(action, kind, recentIndex, text = "") {
  const res = await fetch("/api/repair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, kind, recent_index: recentIndex, text }),
  });
  const data = await res.json();
  overviewCache = data.overview || overviewCache;
  renderOverview(overviewCache || {});
}

async function mutateProject(action, payload) {
  const res = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  });
  const data = await res.json();
  overviewCache = data.overview || overviewCache;
  renderOverview(overviewCache || {});
  return data;
}

async function searchCurrentContext() {
  const query = contextSearchInput.value.trim();
  if (!query) {
    renderCurrentContext(overviewCache || {});
    return;
  }

  const card = activeContextTab === "review" ? "memory" : activeContextTab;
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card, query }),
  });
  const data = await res.json();

  if (activeContextTab === "memory") {
    renderMemoryPanel(data.recent_logs || [], data.recent_notes || []);
    return;
  }
  if (activeContextTab === "projects") {
    renderProjects(data.projects || []);
    return;
  }
  renderReminders(data.reminders || []);
}

async function submitDailyCheckin(action, payload = {}) {
  const res = await fetch("/api/daily-checkin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  });
  return await res.json();
}

function cleanChatItem(item) {
  return {
    ...item,
    assistant: normalizeAssistantText(item.assistant || ""),
    pendingAction: Boolean(item.pendingAction),
  };
}

function filterConversation(items, query) {
  const filteredBySession = applyChatSessionFilter(items);
  const q = (query || "").trim().toLowerCase();
  if (!q) return filteredBySession;
  return filteredBySession.filter((item) => {
    const hay = `${item.user || ""} ${item.assistant || ""} ${item.source || ""}`.toLowerCase();
    return hay.includes(q);
  });
}

function applyChatSessionFilter(items) {
  if (!activeChatStartTs && !activeChatAnchorCount) return items;
  return items.filter((item, index) => {
    if (item.pending) return true;
    if (activeChatStartTs && item.ts && String(item.ts) > activeChatStartTs) return true;
    return index >= activeChatAnchorCount;
  });
}

function sortChatsChronologically(items) {
  return [...items].sort((a, b) => {
    if (a.pending && !b.pending) return 1;
    if (!a.pending && b.pending) return -1;
    return String(a.ts || "").localeCompare(String(b.ts || ""));
  });
}

function switchContextTab(tab) {
  activeContextTab = tab;
  document.querySelectorAll(".tab-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `${tab}Panel`);
  });
  contextSearchInput.value = "";
  if (tab in contextExpanded) {
    contextExpanded[tab] = false;
  }
  renderCurrentContext(overviewCache || {});
  if (tab === "review") {
    renderReviewPanel();
  }
}

function hydrateTheme() {
  const theme = window.localStorage.getItem("nudge-theme") || "light";
  document.body.dataset.theme = theme;
  themeToggleBtn.textContent = theme === "dark" ? "Light Mode" : "Dark Mode";
}

function toggleTheme() {
  const next = document.body.dataset.theme === "dark" ? "light" : "dark";
  document.body.dataset.theme = next;
  window.localStorage.setItem("nudge-theme", next);
  themeToggleBtn.textContent = next === "dark" ? "Light Mode" : "Dark Mode";
}

function syncDailyCheckin(dailyCheckin) {
  const shouldPrompt = Boolean(dailyCheckin.should_prompt);
  dailyCheckinModal.classList.toggle("hidden", !shouldPrompt);
  dailyCheckinModal.setAttribute("aria-hidden", shouldPrompt ? "false" : "true");
}

function closeDailyCheckinModal() {
  dailyCheckinModal.classList.add("hidden");
  dailyCheckinModal.setAttribute("aria-hidden", "true");
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

  if (mode === "add-project") {
    projectModalTitle.textContent = "Add Project";
    projectNameField.classList.remove("hidden");
    projectGoalField.classList.remove("hidden");
    projectNameInput.readOnly = false;
    projectNameInput.value = "";
    projectGoalInput.value = "";
    projectSubmitBtn.textContent = "Create Project";
    projectNameInput.focus();
    return;
  }

  if (mode === "add-goal") {
    projectModalTitle.textContent = `Add Goal to ${options.project || ""}`;
    projectNameField.classList.add("hidden");
    projectGoalField.classList.remove("hidden");
    projectGoalInput.value = "";
    projectSubmitBtn.textContent = "Add Goal";
    projectGoalInput.focus();
    return;
  }

  projectModalTitle.textContent = `Edit Goal in ${options.project || ""}`;
  projectNameField.classList.add("hidden");
  projectGoalField.classList.remove("hidden");
  projectGoalInput.value = options.goalText || "";
  projectSubmitBtn.textContent = "Save Goal";
  projectGoalInput.focus();
}

function closeProjectModal() {
  projectModal.classList.add("hidden");
  projectModal.setAttribute("aria-hidden", "true");
}

function setProjectModalMessage(message) {
  if (!message) {
    projectModalMessage.textContent = "";
    projectModalMessage.classList.add("hidden");
    return;
  }
  projectModalMessage.textContent = message;
  projectModalMessage.classList.remove("hidden");
}

function setDailyCheckinMessage(message) {
  if (!message) {
    dailyCheckinMessage.textContent = "";
    dailyCheckinMessage.classList.add("hidden");
    return;
  }
  dailyCheckinMessage.textContent = message;
  dailyCheckinMessage.classList.remove("hidden");
}

function setDailyCheckinSubmitting(isSubmitting) {
  dailySubmitBtn.disabled = isSubmitting;
  dailyLaterBtn.disabled = isSubmitting;
  dailySkipBtn.disabled = isSubmitting;
}

function renderRuntimeError(message) {
  runtimeStatusTag.textContent = "ERROR";
  runtimeRibbon.innerHTML = `<span class="pill">${escapeHtml("attention needed")}</span>`;
  runtimeSummary.innerHTML = `<div>${escapeHtml(message)}</div>`;
}

function normalizeAssistantText(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.startsWith("```")) {
    const lines = text.split("\n");
    if (lines.length >= 3 && lines[lines.length - 1].trim() === "```") {
      return normalizeAssistantText(lines.slice(1, -1).join("\n"));
    }
  }
  if (text.startsWith("{") && text.endsWith("}") && text.includes('"answer"')) {
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && typeof parsed.answer === "string") {
        return parsed.answer.trim() || text;
      }
    } catch (error) {
      return text;
    }
  }
  return text;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('"', "&quot;");
}

function wireEvents() {
  refreshBtn.addEventListener("click", loadOverview);
  reviewBtn.addEventListener("click", () => {
    switchContextTab("review");
    renderReviewPanel(true);
  });
  newChatBtn.addEventListener("click", () => {
    const last = visibleChats.length ? visibleChats[visibleChats.length - 1] : null;
    activeChatStartTs = last && last.ts ? String(last.ts) : "";
    activeChatAnchorCount = visibleChats.length;
    conversationSearchInput.value = "";
    visibleChats = [];
    renderChat([]);
    chatInput.value = "";
    chatInput.focus();
  });
  inlineReviewBtn.addEventListener("click", () => renderReviewPanel(true));
  themeToggleBtn.addEventListener("click", toggleTheme);
  openProjectModalBtn.addEventListener("click", () => openProjectModal("add-project"));
  openProjectPanelBtn.addEventListener("click", () => {
    switchContextTab("projects");
    openProjectModal("add-project");
  });
  closeProjectModalBtn.addEventListener("click", closeProjectModal);

  quickPrompts.addEventListener("click", (event) => {
    const button = event.target.closest("[data-prompt]");
    if (!button) return;
    chatInput.value = button.dataset.prompt || "";
    chatInput.focus();
  });

  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    await sendChat(text);
  });

  conversationSearchInput.addEventListener("input", () => {
    renderChat(filterConversation(visibleChats, conversationSearchInput.value.trim()));
  });

  contextSearchInput.addEventListener("input", debounce(searchCurrentContext, 220));

  contextTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (!button) return;
    switchContextTab(button.dataset.tab);
  });

  memoryMoreBtn.addEventListener("click", () => {
    contextExpanded.memory = !contextExpanded.memory;
    renderCurrentContext(overviewCache || {});
  });
  projectsMoreBtn.addEventListener("click", () => {
    contextExpanded.projects = !contextExpanded.projects;
    renderCurrentContext(overviewCache || {});
  });
  remindersMoreBtn.addEventListener("click", () => {
    contextExpanded.reminders = !contextExpanded.reminders;
    renderCurrentContext(overviewCache || {});
  });

  projectForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setProjectModalMessage("");

    if (projectModalState.mode === "add-project") {
      const name = projectNameInput.value.trim();
      const goal = projectGoalInput.value.trim();
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
        await mutateProject("add_goal", { project: name, text: goal });
      }
      closeProjectModal();
      switchContextTab("projects");
      return;
    }

    const goalText = projectGoalInput.value.trim();
    if (!goalText) {
      setProjectModalMessage("Please enter a goal.");
      return;
    }

    if (projectModalState.mode === "add-goal") {
      const result = await mutateProject("add_goal", { project: projectModalState.project, text: goalText });
      if (!result.ok) {
        setProjectModalMessage(result.message || "Could not add goal.");
        return;
      }
      closeProjectModal();
      return;
    }

    const result = await mutateProject("edit_goal", {
      project: projectModalState.project,
      goal_index: projectModalState.goalIndex,
      text: goalText,
    });
    if (!result.ok) {
      setProjectModalMessage(result.message || "Could not update goal.");
      return;
    }
    closeProjectModal();
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
      setDailyCheckinMessage("Could not save your check-in right now.");
      return;
    }
    closeDailyCheckinModal();
    overviewCache = data.overview || overviewCache;
    renderOverview(overviewCache || {});
  });

  dailyLaterBtn.addEventListener("click", closeDailyCheckinModal);
  dailySkipBtn.addEventListener("click", async () => {
    await submitDailyCheckin("dismiss");
    closeDailyCheckinModal();
    await loadOverview();
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

    const pendingButton = event.target.closest("[data-pending-action]");
    if (pendingButton) {
      await handlePendingAction(pendingButton.dataset.pendingAction || "");
      return;
    }

    const memoryButton = event.target.closest("[data-action]");
    if (memoryButton) {
      const action = memoryButton.dataset.action || "";
      const kind = memoryButton.dataset.kind || "";
      const index = Number(memoryButton.dataset.index || 0);
      if (action && kind && index) {
        await repairItem(action, kind, index);
      }
      return;
    }

    const projectButton = event.target.closest("[data-project-action]");
    if (!projectButton) return;
    const projectAction = projectButton.dataset.projectAction || "";
    const project = projectButton.dataset.project || "";
    const goalIndex = Number(projectButton.dataset.goalIndex || 0);
    const goalText = projectButton.dataset.goalText || "";

    if (projectAction === "open_add_goal") {
      openProjectModal("add-goal", { project });
      return;
    }
    if (projectAction === "open_edit_goal") {
      openProjectModal("edit-goal", { project, goalIndex, goalText });
      return;
    }
    if (projectAction === "delete_project") {
      if (window.confirm(`Remove project "${project}"?`)) {
        await mutateProject("delete_project", { project });
      }
      return;
    }
    if (projectAction === "archive_project") {
      await mutateProject("archive_project", { project });
      return;
    }
    if (projectAction === "done_goal" || projectAction === "reopen_goal") {
      await mutateProject(projectAction, { project, goal_index: goalIndex });
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeProjectModal();
      closeDailyCheckinModal();
    }
  });
}

function debounce(fn, waitMs) {
  let timeoutId = 0;
  return (...args) => {
    window.clearTimeout(timeoutId);
    timeoutId = window.setTimeout(() => fn(...args), waitMs);
  };
}

init();
