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
const reminderModal = document.getElementById("reminderModal");
const reminderModalTitle = document.getElementById("reminderModalTitle");
const reminderModalText = document.getElementById("reminderModalText");
const reminderModalDue = document.getElementById("reminderModalDue");
const reminderModalMessage = document.getElementById("reminderModalMessage");
const closeReminderModalBtn = document.getElementById("closeReminderModalBtn");
const reminderDoneBtn = document.getElementById("reminderDoneBtn");
const reminderSnoozeBtn = document.getElementById("reminderSnoozeBtn");
const reminderNotifyBtn = document.getElementById("reminderNotifyBtn");

const MAX_VISIBLE_CHATS = 24;
const OVERVIEW_POLL_MS = 10000;
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
let activeReminder = null;
let reminderActionSubmitting = false;
const seenReminderKeys = new Set();
const reminderTimeouts = new Map();
const REMINDER_AUTO_OPEN_GRACE_MS = 15000;

function init() {
  hydrateTheme();
  renderQuickPrompts();
  wireEvents();
  loadOverview();
  window.setInterval(() => {
    loadOverview();
  }, OVERVIEW_POLL_MS);
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
  syncReminderTimers(data.reminders || []);
  syncDueReminder(data.due_reminder || null);
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
          <div class="bubble assistant${item.pending ? " thinking" : ""}">
            ${renderAssistantContent(item)}
          </div>
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
            <div class="card-actions">
              <button class="ghost mini" type="button" data-reminder-action="snooze" data-reminder-id="${escapeAttr(String(item.id || 0))}" data-reminder-minutes="1">Snooze 1m</button>
              <button class="ghost mini" type="button" data-reminder-action="done" data-reminder-id="${escapeAttr(String(item.id || 0))}">Dismiss</button>
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
    toolResult: item.tool_result || item.toolResult || null,
    pendingAction: Boolean(item.pendingAction),
  };
}

function renderAssistantContent(item) {
  const text = escapeHtml(item.assistant || "");
  const card = renderToolResultCard(item.toolResult);
  const cardOnlyKinds = new Set(["filesystem_list", "filesystem_read", "shell_run", "notes_search", "notes_list"]);
  const kind = item.toolResult && typeof item.toolResult === "object" ? String(item.toolResult.kind || "") : "";
  if (card && cardOnlyKinds.has(kind)) {
    return card;
  }
  if (text && card) {
    return `<div class="assistant-text">${text}</div>${card}`;
  }
  if (card) {
    return card;
  }
  return text;
}

function renderToolResultCard(result) {
  if (!result || typeof result !== "object") return "";
  const kind = String(result.kind || "");
  const title = escapeHtml(String(result.title || "Tool result"));
  if (kind === "notes_create") {
    return `
      <div class="tool-card">
        <div class="tool-card-head"><span class="tool-kind">Notes</span><strong>${title}</strong></div>
        <div class="tool-card-body">${escapeHtml(String(result.text || ""))}</div>
      </div>
    `;
  }
  if (kind === "notes_search" || kind === "notes_list") {
    const items = Array.isArray(result.items) ? result.items : [];
    const rows = items.length
      ? items.map((entry) => `<div class="tool-row">${escapeHtml(String(entry.text || ""))}</div>`).join("")
      : `<div class="tool-empty">No notes to show.</div>`;
    return `<div class="tool-card"><div class="tool-card-head"><span class="tool-kind">Notes</span><strong>${title}</strong></div>${rows}</div>`;
  }
  if (kind === "filesystem_list") {
    const items = Array.isArray(result.items) ? result.items : [];
    const rows = items.length
      ? items.map((entry) => `<div class="tool-row"><span class="tool-badge">${escapeHtml(String(entry.entry_type || ""))}</span>${escapeHtml(String(entry.name || ""))}</div>`).join("")
      : `<div class="tool-empty">No entries.</div>`;
    return `
      <div class="tool-card">
        <div class="tool-card-head"><span class="tool-kind">Filesystem</span><strong>${title}</strong></div>
        <div class="tool-card-meta">${escapeHtml(String(result.path || ""))}</div>
        ${rows}
      </div>
    `;
  }
  if (kind === "filesystem_read") {
    return `
      <div class="tool-card">
        <div class="tool-card-head"><span class="tool-kind">File</span><strong>${title}</strong></div>
        <div class="tool-card-meta">${escapeHtml(String(result.path || ""))}</div>
        <pre class="tool-pre">${escapeHtml(String(result.content || ""))}</pre>
      </div>
    `;
  }
  if (kind === "shell_run") {
    const stdout = String(result.stdout || "");
    const stderr = String(result.stderr || "");
    return `
      <div class="tool-card">
        <div class="tool-card-head"><span class="tool-kind">Shell</span><strong>${title}</strong></div>
        <div class="tool-card-meta">cwd ${escapeHtml(String(result.cwd || ""))} · exit ${escapeHtml(String(result.exit_code ?? ""))}</div>
        ${stdout ? `<pre class="tool-pre">${escapeHtml(stdout)}</pre>` : ""}
        ${stderr ? `<pre class="tool-pre error">${escapeHtml(stderr)}</pre>` : ""}
      </div>
    `;
  }
  return "";
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

function syncDueReminder(reminder) {
  if (!reminder || !reminder.id) {
    activeReminder = null;
    closeReminderModal();
    return;
  }
  activeReminder = reminder;
  const reminderKey = `${reminder.id}:${reminder.due_ts || ""}`;
  if (!seenReminderKeys.has(reminderKey) && shouldAutoOpenReminder(reminder)) {
    seenReminderKeys.add(reminderKey);
    openReminderModal(reminder);
    maybeSendDesktopReminder(reminder);
    return;
  }
  if (!reminderModal.classList.contains("hidden")) {
    openReminderModal(reminder);
  }
}

function shouldAutoOpenReminder(reminder) {
  const dueAtMs = Date.parse(String(reminder.due_ts || ""));
  if (!Number.isFinite(dueAtMs)) {
    return false;
  }
  const deltaMs = Date.now() - dueAtMs;
  return deltaMs >= 0 && deltaMs <= REMINDER_AUTO_OPEN_GRACE_MS;
}

function syncReminderTimers(reminders) {
  const activeKeys = new Set();
  reminders.forEach((reminder) => {
    if (!reminder || !reminder.id || !reminder.due_ts) return;
    const reminderId = Number(reminder.id);
    if (!reminderId) return;
    activeKeys.add(reminderId);
    if (reminderTimeouts.has(reminderId)) return;
    const dueAtMs = Date.parse(String(reminder.due_ts));
    if (!Number.isFinite(dueAtMs)) return;
    const delayMs = dueAtMs - Date.now();
    if (delayMs <= 0) return;
    const timeoutId = window.setTimeout(async () => {
      reminderTimeouts.delete(reminderId);
      seenReminderKeys.add(`${reminder.id}:${reminder.due_ts || ""}`);
      openReminderModal(reminder);
      maybeSendDesktopReminder(reminder);
      await loadOverview();
    }, delayMs);
    reminderTimeouts.set(reminderId, timeoutId);
  });

  for (const [reminderId, timeoutId] of reminderTimeouts.entries()) {
    if (activeKeys.has(reminderId)) continue;
    window.clearTimeout(timeoutId);
    reminderTimeouts.delete(reminderId);
  }
}

function openReminderModal(reminder) {
  activeReminder = reminder;
  reminderModalTitle.textContent = "Reminder due now";
  reminderModalText.textContent = String(reminder.text || "You have a reminder due.");
  reminderModalDue.textContent = reminder.due_ts ? `Scheduled for ${reminder.due_ts}` : "No due time saved.";
  setReminderModalMessage("");
  syncNotificationButton();
  reminderModal.classList.remove("hidden");
  reminderModal.setAttribute("aria-hidden", "false");
}

function closeReminderModal() {
  reminderModal.classList.add("hidden");
  reminderModal.setAttribute("aria-hidden", "true");
}

function setReminderModalMessage(message) {
  if (!message) {
    reminderModalMessage.textContent = "";
    reminderModalMessage.classList.add("hidden");
    return;
  }
  reminderModalMessage.textContent = message;
  reminderModalMessage.classList.remove("hidden");
}

function syncNotificationButton() {
  const supported = typeof window.Notification !== "undefined";
  const canRequest = supported && window.Notification.permission === "default";
  reminderNotifyBtn.classList.toggle("hidden", !canRequest);
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

function setReminderSubmitting(isSubmitting) {
  reminderActionSubmitting = isSubmitting;
  reminderDoneBtn.disabled = isSubmitting;
  reminderSnoozeBtn.disabled = isSubmitting;
  reminderNotifyBtn.disabled = isSubmitting;
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

async function mutateReminder(action, reminderId, extra = {}) {
  const res = await fetch("/api/reminders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, reminder_id: reminderId, ...extra }),
  });
  const data = await res.json();
  overviewCache = data.overview || overviewCache;
  renderOverview(overviewCache || {});
  return data;
}

async function maybeEnableDesktopNotifications() {
  if (typeof window.Notification === "undefined") {
    setReminderModalMessage("Desktop notifications are not supported in this browser.");
    return;
  }
  if (window.Notification.permission === "granted") {
    setReminderModalMessage("Desktop alerts are already enabled.");
    syncNotificationButton();
    return;
  }
  const permission = await window.Notification.requestPermission();
  syncNotificationButton();
  if (permission === "granted") {
    setReminderModalMessage("Desktop alerts enabled for due reminders.");
    if (activeReminder) {
      maybeSendDesktopReminder(activeReminder, true);
    }
    return;
  }
  setReminderModalMessage("Desktop alerts stayed disabled. In-app reminder popups will still work.");
}

function maybeSendDesktopReminder(reminder, force = false) {
  if (typeof window.Notification === "undefined") return;
  if (window.Notification.permission !== "granted") return;
  const notification = new window.Notification("Nudge reminder", {
    body: String(reminder.text || "You have a reminder due."),
    tag: `nudge-reminder-${reminder.id}`,
    renotify: Boolean(force),
  });
  notification.onclick = () => {
    window.focus();
    openReminderModal(reminder);
  };
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
  closeReminderModalBtn.addEventListener("click", closeReminderModal);

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

  reminderDoneBtn.addEventListener("click", async () => {
    if (!activeReminder || reminderActionSubmitting) return;
    setReminderSubmitting(true);
    const data = await mutateReminder("done", activeReminder.id);
    setReminderSubmitting(false);
    if (!data.ok) {
      setReminderModalMessage(data.message || "Could not complete reminder.");
      return;
    }
    closeReminderModal();
  });

  reminderSnoozeBtn.addEventListener("click", async () => {
    if (!activeReminder || reminderActionSubmitting) return;
    setReminderSubmitting(true);
    const data = await mutateReminder("snooze", activeReminder.id, { minutes: 1 });
    setReminderSubmitting(false);
    if (!data.ok) {
      setReminderModalMessage(data.message || "Could not snooze reminder.");
      return;
    }
    closeReminderModal();
  });

  reminderNotifyBtn.addEventListener("click", maybeEnableDesktopNotifications);

  document.addEventListener("click", async (event) => {
    if (event.target.closest("[data-close-modal='true']")) {
      closeProjectModal();
      return;
    }
    if (event.target.closest("[data-close-checkin='dismiss']")) {
      closeDailyCheckinModal();
      return;
    }
    if (event.target.closest("[data-close-reminder='dismiss']")) {
      closeReminderModal();
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
    if (projectButton) {
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
      return;
    }

    const reminderButton = event.target.closest("[data-reminder-action]");
    if (!reminderButton) return;
    const reminderAction = reminderButton.dataset.reminderAction || "";
    const reminderId = Number(reminderButton.dataset.reminderId || 0);
    const minutes = Number(reminderButton.dataset.reminderMinutes || 10);
    if (!reminderId) return;
    await mutateReminder(reminderAction, reminderId, { minutes });
    if (activeReminder && Number(activeReminder.id || 0) === reminderId && reminderAction === "done") {
      closeReminderModal();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeProjectModal();
      closeDailyCheckinModal();
      closeReminderModal();
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
