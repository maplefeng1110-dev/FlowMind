const state = {
    conversations: [],
    currentConversationId: null,
    commonTasks: [],
    schedules: [],
    scheduleRuns: [],
    scheduleEditingId: null,
    uploadedFiles: [],
    machines: [],
    machinesLoadedAt: 0,
    machinePage: 1,
    tools: [],
    toolsLoadedAt: 0,
    tasks: [],
    taskFilterConversationId: null,
    taskPage: 1,
    auth: {
        user: null,
        checked: false
    },
    adminUsers: [],
    adminPlugins: [],
    providers: [],
    currentProvider: null
};

const activeTaskPollers = new Map();
const TASK_RENDER_LIMIT = 16;
const TASK_PREVIEW_LIMIT = 180;
const TASKS_PER_PAGE = 6;
const MACHINE_RENDER_LIMIT = 18;
const MACHINES_PER_PAGE = 5;
const MACHINES_CACHE_TTL_MS = 10000;
const TOOLS_CACHE_TTL_MS = 30000;
const TOOL_RENDER_CHUNK = 8;
const TOOL_TAG_PREVIEW_COUNT = 3;
const TOOL_MACHINE_PREVIEW_COUNT = 2;
const MACHINE_RPA_PREVIEW_COUNT = 3;
let toolRenderToken = 0;
let visibleToolCount = TOOL_RENDER_CHUNK;
let taskScrollIdleTimer = 0;
let taskScrollSettledAt = 0;
const pendingTaskCardUpdates = new Map();

const elements = {
    messagesContainer: document.getElementById("messagesContainer"),
    messages: document.getElementById("messages"),
    messageInput: document.getElementById("messageInput"),
    sendBtn: document.getElementById("sendBtn"),
    newChatBtn: document.getElementById("newChatBtn"),
    historyList: document.getElementById("historyList"),
    commonTaskList: document.getElementById("commonTaskList"),
    machinesBtn: document.getElementById("machinesBtn"),
    machinesModal: document.getElementById("machinesModal"),
    refreshMachinesBtn: document.getElementById("refreshMachinesBtn"),
    closeMachinesModal: document.getElementById("closeMachinesModal"),
    machineList: document.getElementById("machineList"),
    tasksBtn: document.getElementById("tasksBtn"),
    tasksBadge: document.getElementById("tasksBadge"),
    tasksModal: document.getElementById("tasksModal"),
    refreshTasksBtn: document.getElementById("refreshTasksBtn"),
    closeTasksModal: document.getElementById("closeTasksModal"),
    taskList: document.getElementById("taskList"),
    scheduleBtn: document.getElementById("scheduleBtn"),
    schedulesModal: document.getElementById("schedulesModal"),
    refreshSchedulesBtn: document.getElementById("refreshSchedulesBtn"),
    closeSchedulesModal: document.getElementById("closeSchedulesModal"),
    scheduleList: document.getElementById("scheduleList"),
    scheduleRunList: document.getElementById("scheduleRunList"),
    scheduleForm: document.getElementById("scheduleForm"),
    scheduleTitle: document.getElementById("scheduleTitle"),
    scheduleCommonTask: document.getElementById("scheduleCommonTask"),
    schedulePrompt: document.getElementById("schedulePrompt"),
    scheduleProvider: document.getElementById("scheduleProvider"),
    scheduleType: document.getElementById("scheduleType"),
    scheduleDailyRow: document.getElementById("scheduleDailyRow"),
    scheduleIntervalRow: document.getElementById("scheduleIntervalRow"),
    scheduleOnceRow: document.getElementById("scheduleOnceRow"),
    scheduleDailyTime: document.getElementById("scheduleDailyTime"),
    scheduleIntervalMinutes: document.getElementById("scheduleIntervalMinutes"),
    scheduleRunAt: document.getElementById("scheduleRunAt"),
    scheduleTimezone: document.getElementById("scheduleTimezone"),
    scheduleIsActive: document.getElementById("scheduleIsActive"),
    scheduleFormHint: document.getElementById("scheduleFormHint"),
    resetScheduleFormBtn: document.getElementById("resetScheduleFormBtn"),
    saveScheduleBtn: document.getElementById("saveScheduleBtn"),
    toolsBtn: document.getElementById("toolsBtn"),
    toolsModal: document.getElementById("toolsModal"),
    refreshToolsBtn: document.getElementById("refreshToolsBtn"),
    closeToolsModal: document.getElementById("closeToolsModal"),
    toolList: document.getElementById("toolList"),
    exportBtn: document.getElementById("exportBtn"),
    exportsModal: document.getElementById("exportsModal"),
    refreshExportsBtn: document.getElementById("refreshExportsBtn"),
    closeExportsModal: document.getElementById("closeExportsModal"),
    exportList: document.getElementById("exportList"),
    exportHint: document.getElementById("exportHint"),
    adminBtn: document.getElementById("adminBtn"),
    adminModal: document.getElementById("adminModal"),
    refreshAdminBtn: document.getElementById("refreshAdminBtn"),
    closeAdminModal: document.getElementById("closeAdminModal"),
    adminUserList: document.getElementById("adminUserList"),
    createUserForm: document.getElementById("createUserForm"),
    newUsername: document.getElementById("newUsername"),
    newDisplayName: document.getElementById("newDisplayName"),
    newPassword: document.getElementById("newPassword"),
    adminFormHint: document.getElementById("adminFormHint"),
    pluginScaffoldForm: document.getElementById("pluginScaffoldForm"),
    pluginId: document.getElementById("pluginId"),
    pluginDescription: document.getElementById("pluginDescription"),
    pluginSourceFile: document.getElementById("pluginSourceFile"),
    pluginTargetMachine: document.getElementById("pluginTargetMachine"),
    pluginParams: document.getElementById("pluginParams"),
    pluginRequiredParams: document.getElementById("pluginRequiredParams"),
    pluginKeywords: document.getElementById("pluginKeywords"),
    pluginTags: document.getElementById("pluginTags"),
    pluginCapabilities: document.getElementById("pluginCapabilities"),
    pluginTimeoutSec: document.getElementById("pluginTimeoutSec"),
    pluginForceOverwrite: document.getElementById("pluginForceOverwrite"),
    pluginScaffoldHint: document.getElementById("pluginScaffoldHint"),
    attachBtn: document.getElementById("attachBtn"),
    fileInput: document.getElementById("fileInput"),
    filePreviewContainer: document.getElementById("filePreviewContainer"),
    statusBadge: document.getElementById("statusBadge"),
    themeToggleBtn: document.getElementById("themeToggleBtn"),
    sunIcon: document.querySelector(".sun-icon"),
    moonIcon: document.querySelector(".moon-icon"),
    authScreen: document.getElementById("authScreen"),
    loginForm: document.getElementById("loginForm"),
    loginUsername: document.getElementById("loginUsername"),
    loginPassword: document.getElementById("loginPassword"),
    authError: document.getElementById("authError"),
    loginBtn: document.getElementById("loginBtn"),
    currentUserName: document.getElementById("currentUserName"),
    currentUserRole: document.getElementById("currentUserRole"),
    logoutBtn: document.getElementById("logoutBtn")
};

const icons = {
    chat: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
    document: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>`,
    email: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>`,
    user: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
    assistant: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>`,
    trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>`,
    spark: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z"/></svg>`
};

function generateId() {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

function getTimestamp() {
    return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function getCurrentUser() {
    return state.auth.user;
}

function isAdminUser() {
    const user = getCurrentUser();
    return Boolean(user && user.role === "admin");
}

function formatUserRole(role) {
    if (role === "admin") {
        return "管理员账号";
    }
    if (role === "business") {
        return "业务账号";
    }
    return "未登录";
}

function setAuthError(message = "") {
    if (!elements.authError) {
        return;
    }
    elements.authError.textContent = message || "";
    elements.authError.style.display = message ? "block" : "none";
}

function updateUserPanel() {
    const user = getCurrentUser();
    if (!user) {
        elements.currentUserName.textContent = "未登录";
        elements.currentUserRole.textContent = "请先登录继续使用";
        elements.logoutBtn.style.display = "none";
        elements.adminBtn.style.display = "none";
        return;
    }

    elements.currentUserName.textContent = user.display_name || user.username;
    elements.currentUserRole.textContent = `${formatUserRole(user.role)} · ${user.username}`;
    elements.logoutBtn.style.display = "inline-flex";
    elements.adminBtn.style.display = isAdminUser() ? "inline-flex" : "none";
}

function showAuthScreen(message = "") {
    state.auth.user = null;
    state.auth.checked = true;
    updateUserPanel();
    setAuthError(message);
    elements.authScreen.classList.add("active");
}

function hideAuthScreen() {
    setAuthError("");
    elements.authScreen.classList.remove("active");
}

function applyAuthenticatedUser(user) {
    state.auth.user = user;
    state.auth.checked = true;
    updateUserPanel();
    hideAuthScreen();
}

function getConversationTag(conversationId) {
    const raw = String(conversationId || "").replace(/[^a-zA-Z0-9]/g, "").toUpperCase();
    const suffix = raw.slice(-6) || "CHAT";
    return `#${suffix}`;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function truncateText(text, maxLength = TASK_PREVIEW_LIMIT) {
    const value = String(text || "");
    if (value.length <= maxLength) {
        return value;
    }
    return `${value.slice(0, maxLength)}...`;
}

function formatMarkdown(text) {
    if (!text) {
        return "";
    }

    let formatted = escapeHtml(text);
    formatted = formatted.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    formatted = formatted.replace(/`(.*?)`/g, "<code>$1</code>");
    formatted = formatted.replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="markdown-link">$1</a>');
    formatted = formatted.replace(/\n/g, "<br>");
    return formatted;
}

function stringifyValue(value) {
    if (value === null || value === undefined) {
        return "";
    }
    if (typeof value === "string") {
        return value;
    }
    try {
        return JSON.stringify(value, null, 2);
    } catch (error) {
        return String(value);
    }
}

function normalizeToolTrace(message) {
    if (Array.isArray(message.toolTrace)) {
        return message.toolTrace;
    }
    if (message.meta && Array.isArray(message.meta.tool_trace)) {
        return message.meta.tool_trace;
    }
    return [];
}

function normalizeRuntimePlan(message) {
    if (message && message.runtimePlan && typeof message.runtimePlan === "object") {
        return message.runtimePlan;
    }
    if (message && message.meta && message.meta.runtime_plan && typeof message.meta.runtime_plan === "object") {
        return message.meta.runtime_plan;
    }
    return null;
}

function normalizeReusableTaskSuggestion(message) {
    if (message && message.reusableTaskSuggestion && typeof message.reusableTaskSuggestion === "object") {
        return message.reusableTaskSuggestion;
    }
    if (
        message &&
        message.meta &&
        message.meta.reusable_task_suggestion &&
        typeof message.meta.reusable_task_suggestion === "object"
    ) {
        return message.meta.reusable_task_suggestion;
    }
    return null;
}

function getPlanStatusLabel(status) {
    if (status === "completed") {
        return "已完成";
    }
    if (status === "running") {
        return "执行中";
    }
    if (status === "needs_attention") {
        return "待处理";
    }
    if (status === "draft") {
        return "待判断";
    }
    return "已规划";
}

function getPlanStepStatusLabel(status) {
    if (status === "planned") {
        return "待执行";
    }
    if (status === "running") {
        return "执行中";
    }
    if (status === "pending") {
        return "后台中";
    }
    if (status === "cached") {
        return "已复用";
    }
    if (status === "success") {
        return "成功";
    }
    if (status === "blocked") {
        return "已阻断";
    }
    if (status === "timeout") {
        return "超时";
    }
    if (status === "error") {
        return "失败";
    }
    return "待执行";
}

function getToolStatusLabel(status) {
    if (status === "pending") {
        return "后台中";
    }
    if (status === "success") {
        return "成功";
    }
    if (status === "cached") {
        return "复用";
    }
    if (status === "timeout") {
        return "超时";
    }
    if (status === "error") {
        return "失败";
    }
    return "执行中";
}

function formatTaskStatus(status) {
    if (status === "running") {
        return "执行中";
    }
    return getToolStatusLabel(status);
}

function formatMachineStatus(status) {
    if (status === "online") {
        return "在线";
    }
    if (status === "offline") {
        return "离线";
    }
    return "未知";
}

function formatScheduleType(type) {
    if (type === "once") {
        return "一次性";
    }
    if (type === "interval") {
        return "固定间隔";
    }
    if (type === "daily") {
        return "每日定时";
    }
    return "未分类";
}

function formatScheduleRunStatus(status) {
    if (status === "queued") {
        return "待执行";
    }
    if (status === "running") {
        return "执行中";
    }
    if (status === "success") {
        return "成功";
    }
    if (status === "needs_attention") {
        return "待处理";
    }
    if (status === "error") {
        return "失败";
    }
    return "未知";
}

function formatScheduleTime(value) {
    if (!value) {
        return "-";
    }
    return String(value).replace("T", " ").replace("Z", "");
}

function formatLastHeartbeat(value) {
    if (!value) {
        return "-";
    }
    return String(value).replace("T", " ").replace("Z", "");
}

function getMachineStatusClass(status) {
    if (status === "online" || status === "offline") {
        return status;
    }
    return "unknown";
}

function getScheduleStatusClass(status) {
    if (["queued", "running", "success", "needs_attention", "error"].includes(status)) {
        return status;
    }
    return "unknown";
}

function getToolTraceSummary(toolTrace) {
    const items = Array.isArray(toolTrace) ? toolTrace : [];
    const runningCount = items.filter((tool) => tool.status === "running").length;
    const pendingCount = items.filter((tool) => tool.status === "pending").length;
    const cachedCount = items.filter((tool) => tool.status === "cached").length;
    const timeoutCount = items.filter((tool) => tool.status === "timeout").length;
    const errorCount = items.filter((tool) => tool.status === "error").length;
    const names = items.map((tool) => tool.name).filter(Boolean);

    if (runningCount > 0) {
        return {
            statusClass: "running",
            title: `正在调用 ${items.length} 个 RPA 工具`,
            detail: names.join(" · ")
        };
    }

    if (pendingCount > 0) {
        return {
            statusClass: "pending",
            title: `后台执行中 ${items.length} 个 RPA 工具`,
            detail: names.join(" · ")
        };
    }

    if (errorCount > 0) {
        return {
            statusClass: "error",
            title: `已调用 ${items.length} 个 RPA 工具（含失败）`,
            detail: names.join(" · ")
        };
    }

    if (timeoutCount > 0) {
        return {
            statusClass: "timeout",
            title: `已调用 ${items.length} 个 RPA 工具（含超时）`,
            detail: names.join(" · ")
        };
    }

    if (cachedCount > 0) {
        return {
            statusClass: "cached",
            title: `已调用 ${items.length} 个 RPA 工具（含复用）`,
            detail: names.join(" · ")
        };
    }

    return {
        statusClass: "success",
        title: `已调用 ${items.length} 个 RPA 工具`,
        detail: names.join(" · ")
    };
}

function renderToolTraceBanner(toolTrace) {
    if (!Array.isArray(toolTrace) || toolTrace.length === 0) {
        return "";
    }

    const summary = getToolTraceSummary(toolTrace);
    return `
        <div class="message-tool-banner ${summary.statusClass}">
            <div class="message-tool-banner-badge">RPA</div>
            <div class="message-tool-banner-body">
                <div class="message-tool-banner-title">${escapeHtml(summary.title)}</div>
                <div class="message-tool-banner-detail">${escapeHtml(summary.detail)}</div>
            </div>
        </div>
    `;
}

// 为每条工具轨迹生成稳定键，用来在局部刷新时保留用户手动展开的状态。
function getToolTraceDetailsKey(tool, index) {
    if (tool && tool.id) {
        return `tool-${tool.id}`;
    }
    if (tool && tool.task_id) {
        return `task-${tool.task_id}`;
    }
    return `fallback-${tool && tool.name ? tool.name : "unknown"}-${index}`;
}

function renderToolTrace(toolTrace) {
    if (!Array.isArray(toolTrace) || toolTrace.length === 0) {
        return "";
    }

    return `
        <div class="tool-trace-panel">
            <div class="tool-trace-title">工具调用轨迹</div>
            ${toolTrace.map((tool, index) => `
                <div class="tool-trace-item ${tool.status || "running"}">
                    <div class="tool-trace-summary">
                        <span class="tool-trace-name">${escapeHtml(tool.name || "unknown_tool")}</span>
                        <span class="tool-trace-status ${tool.status || "running"}">${getToolStatusLabel(tool.status)}</span>
                    </div>
                    <details class="tool-trace-details" data-trace-key="${escapeHtml(getToolTraceDetailsKey(tool, index))}">
                        <summary>查看参数和返回摘要</summary>
                        <div class="tool-trace-block">
                            <div class="tool-trace-label">参数</div>
                            <pre>${escapeHtml(stringifyValue(tool.arguments || {}))}</pre>
                        </div>
                        ${tool.task_id ? `
                            <div class="tool-trace-block">
                                <div class="tool-trace-label">任务 ID</div>
                                <pre>${escapeHtml(tool.task_id)}</pre>
                            </div>
                        ` : ""}
                        <div class="tool-trace-block">
                            <div class="tool-trace-label">返回摘要</div>
                            <pre>${escapeHtml(stringifyValue(tool.result_preview || ((tool.status === "running" || tool.status === "pending") ? "等待工具返回..." : "")))}</pre>
                        </div>
                    </details>
                </div>
            `).join("")}
        </div>
    `;
}

function renderRuntimePlan(plan) {
    if (!plan || typeof plan !== "object") {
        return "";
    }

    const steps = Array.isArray(plan.steps) ? plan.steps : [];
    const constraints = Array.isArray(plan.constraints) ? plan.constraints : [];
    const recovery = plan.recovery && typeof plan.recovery === "object" ? plan.recovery : null;

    return `
        <div class="runtime-plan-panel">
            <div class="runtime-plan-header">
                <div>
                    <div class="runtime-plan-kicker">运行时计划</div>
                    <div class="runtime-plan-goal">${escapeHtml(plan.goal || "根据当前对话动态规划执行步骤")}</div>
                </div>
                <span class="runtime-plan-status ${escapeHtml(plan.status || "planned")}">${escapeHtml(getPlanStatusLabel(plan.status))}</span>
            </div>
            ${plan.summary ? `<div class="runtime-plan-summary">${escapeHtml(plan.summary)}</div>` : ""}
            ${steps.length > 0 ? `
                <div class="runtime-plan-steps">
                    ${steps.map((step) => `
                        <div class="runtime-plan-step ${escapeHtml(step.status || "planned")}">
                            <div class="runtime-plan-step-header">
                                <span class="runtime-plan-step-title">${escapeHtml(step.title || step.tool_name || "步骤")}</span>
                                <span class="runtime-plan-step-status ${escapeHtml(step.status || "planned")}">${escapeHtml(getPlanStepStatusLabel(step.status))}</span>
                            </div>
                            <div class="runtime-plan-step-meta">
                                <span>${escapeHtml(step.tool_name || "推理步骤")}</span>
                                ${step.task_id ? `<span>任务 ${escapeHtml(step.task_id)}</span>` : ""}
                                ${step.attempts ? `<span>尝试 ${escapeHtml(String(step.attempts))} 次</span>` : ""}
                            </div>
                            ${step.result_preview ? `<div class="runtime-plan-step-preview">${escapeHtml(step.result_preview)}</div>` : ""}
                        </div>
                    `).join("")}
                </div>
            ` : ""}
            ${constraints.length > 0 ? `
                <div class="runtime-plan-constraints">
                    ${constraints.slice(-3).map((item) => `
                        <div class="runtime-plan-constraint ${escapeHtml(item.status || "warning")}">
                            <strong>${escapeHtml(item.tool_name || item.type || "约束")}</strong>
                            <span>${escapeHtml(item.message || "")}</span>
                        </div>
                    `).join("")}
                </div>
            ` : ""}
            ${recovery && recovery.suggested_action ? `
                <div class="runtime-plan-recovery">
                    <div class="runtime-plan-recovery-label">恢复策略</div>
                    <div class="runtime-plan-recovery-text">${escapeHtml(recovery.suggested_action)}</div>
                </div>
            ` : ""}
        </div>
    `;
}

function updateRuntimePlanElement(element, plan) {
    if (!element) {
        return;
    }
    const normalized = plan && typeof plan === "object" ? plan : null;
    element.innerHTML = normalized ? renderRuntimePlan(normalized) : "";
    element.style.display = normalized ? "block" : "none";
}

function renderReusableTaskSuggestion(message) {
    const suggestion = normalizeReusableTaskSuggestion(message);
    if (!suggestion) {
        return "";
    }
    const saved = suggestion.saved_task_id ? "已保存" : "保存为常用任务";
    return `
        <div class="reusable-task-card">
            <div class="reusable-task-card-header">
                <div class="reusable-task-card-title">${icons.spark}<span>${escapeHtml(suggestion.title || "常用任务建议")}</span></div>
                <button class="reusable-task-save-btn" onclick="saveReusableTaskSuggestion('${escapeHtml(message.id || "")}')" ${suggestion.saved_task_id ? "disabled" : ""}>${escapeHtml(saved)}</button>
            </div>
            ${suggestion.summary ? `<div class="reusable-task-card-summary">${escapeHtml(suggestion.summary)}</div>` : ""}
        </div>
    `;
}

function updateReusableTaskElement(element, message) {
    if (!element) {
        return;
    }
    const suggestion = normalizeReusableTaskSuggestion(message);
    element.innerHTML = suggestion ? renderReusableTaskSuggestion(message) : "";
    element.style.display = suggestion ? "block" : "none";
}

function updateToolTraceElement(element, toolTrace) {
    if (!element) {
        return;
    }
    const normalized = Array.isArray(toolTrace) ? toolTrace : [];
    const openKeys = new Set(
        Array.from(element.querySelectorAll(".tool-trace-details[open]"))
            .map((details) => details.dataset.traceKey)
            .filter(Boolean)
    );
    element.innerHTML = renderToolTrace(normalized);
    if (openKeys.size > 0) {
        element.querySelectorAll(".tool-trace-details").forEach((details) => {
            if (openKeys.has(details.dataset.traceKey)) {
                details.open = true;
            }
        });
    }
    element.style.display = normalized.length > 0 ? "block" : "none";
}

function updateToolTraceBannerElement(element, toolTrace) {
    if (!element) {
        return;
    }
    const normalized = Array.isArray(toolTrace) ? toolTrace : [];
    element.innerHTML = renderToolTraceBanner(normalized);
    element.style.display = normalized.length > 0 ? "block" : "none";
}

function buildMessageMeta(message) {
    const toolTrace = normalizeToolTrace(message);
    const runtimePlan = normalizeRuntimePlan(message);
    const reusableTaskSuggestion = normalizeReusableTaskSuggestion(message);
    if (toolTrace.length === 0 && !runtimePlan && !reusableTaskSuggestion) {
        return null;
    }
    return {
        tool_trace: toolTrace,
        runtime_plan: runtimePlan,
        reusable_task_suggestion: reusableTaskSuggestion,
    };
}

function formatTaskTime(value) {
    if (!value) {
        return "-";
    }
    return String(value).replace("T", " ").replace("Z", "");
}

function getTaskConversationInfo(task) {
    const conversationId = task && task.conversation_id ? task.conversation_id : null;
    const conversationTitle = task && task.conversation_title ? task.conversation_title : "未关联对话";
    const conversationTag = conversationId ? getConversationTag(conversationId) : "未关联";
    return { conversationId, conversationTitle, conversationTag };
}

function normalizeMessage(message) {
    return {
        ...message,
        toolTrace: normalizeToolTrace(message),
        runtimePlan: normalizeRuntimePlan(message),
        reusableTaskSuggestion: normalizeReusableTaskSuggestion(message),
    };
}

function enrichSummaryMessageToolTrace(messages) {
    const items = Array.isArray(messages) ? messages : [];
    items.forEach((message, index) => {
        if (Array.isArray(message.toolTrace) && message.toolTrace.length > 0) {
            return;
        }

        const match = /^task-summary-(.+)$/.exec(String(message.id || ""));
        if (!match) {
            return;
        }

        const taskId = match[1];
        for (let pointer = index - 1; pointer >= 0; pointer -= 1) {
            const candidate = items[pointer];
            const candidateTrace = Array.isArray(candidate.toolTrace) ? candidate.toolTrace : [];
            const matchedTool = candidateTrace.find((tool) => tool.task_id === taskId);
            if (!matchedTool) {
                continue;
            }

            message.toolTrace = [{
                ...matchedTool,
                arguments: matchedTool.arguments || {},
                result_preview: matchedTool.result_preview || ""
            }];
            return;
        }
    });
}

function upsertToolTrace(toolTrace, nextTool) {
    const items = Array.isArray(toolTrace) ? [...toolTrace] : [];
    const index = items.findIndex((item) => item.id === nextTool.id);
    if (index >= 0) {
        items[index] = { ...items[index], ...nextTool };
        return items;
    }
    return [...items, nextTool];
}

function isTerminalToolStatus(status) {
    return status === "success" || status === "error" || status === "timeout";
}

function canDeleteTask(task) {
    return isTerminalToolStatus(normalizeTaskStatus(task));
}

function canDeleteMachine(machine) {
    if (!isAdminUser()) {
        return false;
    }
    const runningTaskCount = Number.isFinite(machine && machine.running_task_count) ? machine.running_task_count : 0;
    return machine && machine.status !== "online" && runningTaskCount <= 0;
}

async function readResponseError(response, fallbackMessage) {
    try {
        const data = await response.json();
        if (data && typeof data.detail === "string" && data.detail.trim()) {
            return data.detail.trim();
        }
        if (data && typeof data.error === "string" && data.error.trim()) {
            return data.error.trim();
        }
        if (data && typeof data.message === "string" && data.message.trim()) {
            return data.message.trim();
        }
    } catch (error) {
        console.warn("Failed to read error response:", error);
    }
    return fallbackMessage;
}

function normalizeTaskStatus(task) {
    const result = task && typeof task.result === "object" ? task.result : null;
    const rawStatus = (result && result.status) || (task && task.status) || "pending";

    if (rawStatus === "success") {
        return "success";
    }
    if (rawStatus === "timeout") {
        return "timeout";
    }
    if (rawStatus === "error" || rawStatus === "failed") {
        return "error";
    }
    return "pending";
}

function buildTaskResultPreview(task) {
    const status = normalizeTaskStatus(task);
    if (status === "pending") {
        return "任务后台执行中...";
    }

    if (!task) {
        return "";
    }

    if (task.result !== undefined && task.result !== null) {
        if (typeof task.result === "object" && status === "success" && Object.prototype.hasOwnProperty.call(task.result, "data")) {
            return stringifyValue(task.result.data);
        }
        return stringifyValue(task.result);
    }

    return stringifyValue(task.status || "");
}

function summarizeTaskResultText(task) {
    const preview = buildTaskResultPreview(task);
    if (!preview) {
        return "暂无返回内容。";
    }
    if (preview.length > 600) {
        return `${preview.slice(0, 600)}...`;
    }
    return preview;
}

function buildTaskSummaryFallback(tool, task) {
    const toolName = tool && tool.name ? tool.name : "RPA 工具";
    const status = normalizeTaskStatus(task);
    const summaryText = summarizeTaskResultText(task);

    if (status === "success") {
        return `后台任务 \`${toolName}\` 已完成。\n\n结果摘要：\n${summaryText}`;
    }

    if (status === "timeout") {
        return `后台任务 \`${toolName}\` 已超时。\n\n详情：\n${summaryText}`;
    }

    return `后台任务 \`${toolName}\` 执行失败。\n\n详情：\n${summaryText}`;
}

async function fetchTaskSummary(tool, task) {
    const toolName = tool && tool.name ? tool.name : "RPA 工具";
    try {
        const response = await fetch("/api/task-summary", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                tool_name: toolName,
                arguments: tool && tool.arguments ? tool.arguments : {},
                task
            })
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        if (data && typeof data.summary === "string" && data.summary.trim()) {
            return data.summary.trim();
        }
    } catch (error) {
        console.warn("Failed to fetch AI task summary:", error);
    }
    return buildTaskSummaryFallback(tool, task);
}

function findConversationMessage(conversationId, messageId) {
    const conversation = getConversationById(conversationId);
    if (!conversation || !Array.isArray(conversation.messages)) {
        return null;
    }
    return conversation.messages.find((message) => message.id === messageId) || null;
}

function findMessageElement(messageId) {
    return elements.messages.querySelector(`[data-message-id="${messageId}"]`);
}

function updateMessageToolTraceFromTask(message, taskId, task) {
    if (!message || !Array.isArray(message.toolTrace)) {
        return { changed: false, terminal: true };
    }

    const nextStatus = normalizeTaskStatus(task);
    const nextPreview = buildTaskResultPreview(task);
    let changed = false;

    message.toolTrace = message.toolTrace.map((tool) => {
        if (tool.task_id !== taskId) {
            return tool;
        }

        const updatedTool = {
            ...tool,
            status: nextStatus,
            result_preview: nextPreview
        };
        if (tool.status !== updatedTool.status || tool.result_preview !== updatedTool.result_preview) {
            changed = true;
        }
        return updatedTool;
    });

    return { changed, terminal: isTerminalToolStatus(nextStatus) };
}

function updateMessageRuntimePlanFromTask(message, taskId, task) {
    if (!message || !message.runtimePlan || !Array.isArray(message.runtimePlan.steps)) {
        return false;
    }

    const nextStatus = normalizeTaskStatus(task);
    const nextPreview = buildTaskResultPreview(task);
    let changed = false;

    message.runtimePlan = {
        ...message.runtimePlan,
        steps: message.runtimePlan.steps.map((step) => {
            if (step.task_id !== taskId) {
                return step;
            }
            const updatedStep = {
                ...step,
                status: nextStatus,
                result_preview: nextPreview
            };
            if (step.status !== updatedStep.status || step.result_preview !== updatedStep.result_preview) {
                changed = true;
            }
            return updatedStep;
        })
    };

    const steps = message.runtimePlan.steps;
    const failedStep = steps.find((step) => ["error", "timeout", "blocked"].includes(step.status));
    const pendingStep = steps.find((step) => ["pending", "running", "planned"].includes(step.status));
    if (failedStep) {
        message.runtimePlan.status = "needs_attention";
        message.runtimePlan.recovery = {
            resume_supported: true,
            resume_from_step_id: failedStep.id || null,
            failed_step_id: failedStep.id || null,
            suggested_action: "失败步骤可在补齐参数、修正范围或完成确认后继续。"
        };
    } else if (pendingStep) {
        message.runtimePlan.status = "running";
        message.runtimePlan.recovery = {
            resume_supported: true,
            resume_from_step_id: pendingStep.id || null,
            failed_step_id: null,
            suggested_action: "当前仍有后台步骤在执行，系统会继续更新结果。"
        };
    } else if (steps.length > 0) {
        message.runtimePlan.status = "completed";
        message.runtimePlan.recovery = {
            resume_supported: true,
            resume_from_step_id: null,
            failed_step_id: null,
            suggested_action: "所有步骤已完成，可复用为常用任务。"
        };
    }

    return changed;
}

function refreshMessageToolTraceUI(message) {
    const messageElement = findMessageElement(message.id);
    if (!messageElement) {
        return;
    }

    const toolTrace = normalizeToolTrace(message);
    const planElement = messageElement.querySelector(".message-runtime-plan");
    const bannerElement = messageElement.querySelector(".message-tool-banner-container");
    const traceElement = messageElement.querySelector(".message-tool-trace");
    const reusableTaskElement = messageElement.querySelector(".message-reusable-task");
    messageElement.classList.toggle("has-tool-trace", toolTrace.length > 0);
    updateRuntimePlanElement(planElement, normalizeRuntimePlan(message));
    updateToolTraceBannerElement(bannerElement, toolTrace);
    updateToolTraceElement(traceElement, toolTrace);
    updateReusableTaskElement(reusableTaskElement, message);
}

async function ensureTaskSummaryMessage(conversationId, sourceMessageId, taskId, task) {
    const conversation = getConversationById(conversationId);
    if (!conversation || !Array.isArray(conversation.messages)) {
        return;
    }

    const sourceMessage = findConversationMessage(conversationId, sourceMessageId);
    if (!sourceMessage || !Array.isArray(sourceMessage.toolTrace)) {
        return;
    }

    const tool = sourceMessage.toolTrace.find((item) => item.task_id === taskId);
    if (!tool) {
        return;
    }

    const summaryMessageId = `task-summary-${taskId}`;
    const existing = findConversationMessage(conversationId, summaryMessageId);
    if (existing) {
        return;
    }

    const nextContent = await fetchTaskSummary(tool, task);

    const summaryMessage = {
        id: summaryMessageId,
        role: "assistant",
        content: nextContent,
        timestamp: getTimestamp(),
        toolTrace: [{
            ...tool,
            status: normalizeTaskStatus(task),
            result_preview: buildTaskResultPreview(task)
        }]
    };
    conversation.messages.push(summaryMessage);
    await def_saveMessage(conversationId, summaryMessage);

    if (state.currentConversationId === conversationId) {
        appendMessage(summaryMessage);
    }
}

async function pollTaskStatus(conversationId, messageId, taskId) {
    const pollerKey = `${conversationId}:${messageId}:${taskId}`;
    if (!activeTaskPollers.has(pollerKey)) {
        return;
    }

    try {
        const response = await fetch(`/api/task/${encodeURIComponent(taskId)}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const task = await response.json();
        upsertTaskRecord(task);
        updateTaskCardInView(task);
        const message = findConversationMessage(conversationId, messageId);
        if (!message) {
            activeTaskPollers.delete(pollerKey);
            return;
        }

        const { changed, terminal } = updateMessageToolTraceFromTask(message, taskId, task);
        const planChanged = updateMessageRuntimePlanFromTask(message, taskId, task);
        if (changed || planChanged) {
            refreshMessageToolTraceUI(message);
            await def_saveMessage(conversationId, message);
            scrollToBottom();
        }

        if (terminal) {
            await ensureTaskSummaryMessage(conversationId, messageId, taskId, task);
            updateTaskCardInView(task);
            activeTaskPollers.delete(pollerKey);
            return;
        }
    } catch (error) {
        console.warn("Failed to poll task status:", error);
    }

    const timeoutId = window.setTimeout(() => {
        pollTaskStatus(conversationId, messageId, taskId);
    }, 3000);
    activeTaskPollers.set(pollerKey, timeoutId);
}

function startTaskPolling(conversationId, messageId, taskId) {
    if (!conversationId || !messageId || !taskId) {
        return;
    }

    const pollerKey = `${conversationId}:${messageId}:${taskId}`;
    if (activeTaskPollers.has(pollerKey)) {
        return;
    }

    activeTaskPollers.set(pollerKey, 0);
    pollTaskStatus(conversationId, messageId, taskId);
}

function restorePendingTaskPollers(conversationId, messages) {
    const items = Array.isArray(messages) ? messages : [];
    items.forEach((message) => {
        const toolTrace = normalizeToolTrace(message);
        toolTrace.forEach((tool) => {
            if (tool.task_id && !isTerminalToolStatus(tool.status)) {
                startTaskPolling(conversationId, message.id, tool.task_id);
            }
        });
    });
}

function getCurrentConversation() {
    return state.conversations.find((conversation) => conversation.id === state.currentConversationId) || null;
}

function getConversationById(conversationId) {
    return state.conversations.find((conversation) => conversation.id === conversationId) || null;
}

function createMessageElement(message) {
    const div = document.createElement("div");
    const toolTrace = normalizeToolTrace(message);
    const runtimePlan = normalizeRuntimePlan(message);
    div.className = `message ${message.role}${toolTrace.length > 0 ? " has-tool-trace" : ""}`;
    div.dataset.messageId = message.id || "";
    div.innerHTML = `
        <div class="message-avatar">${message.role === "user" ? icons.user : icons.assistant}</div>
        <div class="message-content">
            <div class="message-header">
                <span class="message-name">${message.role === "user" ? "你" : "AI 助手"}</span>
                <span class="message-time">${message.timestamp || ""}</span>
            </div>
            ${message.role === "assistant" ? `<div class="message-runtime-plan" style="display:${runtimePlan ? "block" : "none"};">${runtimePlan ? renderRuntimePlan(runtimePlan) : ""}</div>` : ""}
            ${message.role === "assistant" ? `<div class="message-tool-banner-container" style="display:${toolTrace.length > 0 ? "block" : "none"};">${renderToolTraceBanner(toolTrace)}</div>` : ""}
            <div class="message-text">${formatMarkdown(message.content)}</div>
            ${message.role === "assistant" ? `<div class="message-tool-trace" style="display:${toolTrace.length > 0 ? "block" : "none"};">${renderToolTrace(toolTrace)}</div>` : ""}
            ${message.role === "assistant" ? `<div class="message-reusable-task" style="display:${normalizeReusableTaskSuggestion(message) ? "block" : "none"};">${renderReusableTaskSuggestion(message)}</div>` : ""}
        </div>
    `;
    return div;
}

function appendMessage(message) {
    elements.messages.appendChild(createMessageElement(message));
    scrollToBottom();
}

function showTyping() {
    const div = document.createElement("div");
    div.className = "message assistant typing-message";
    div.id = "typingIndicator";
    div.innerHTML = `
        <div class="message-avatar">${icons.assistant}</div>
        <div class="message-content">
            <div class="message-text">
                <div class="typing-indicator"><span></span><span></span><span></span></div>
            </div>
        </div>
    `;
    elements.messages.appendChild(div);
    scrollToBottom();
}

function hideTyping() {
    const typing = document.getElementById("typingIndicator");
    if (typing) {
        typing.remove();
    }
}

function scrollToBottom() {
    elements.messagesContainer.scrollTop = elements.messagesContainer.scrollHeight;
}

async function restoreAuthSession() {
    try {
        const response = await fetch("/api/auth/me");
        if (!response.ok) {
            return false;
        }
        const data = await response.json();
        if (!data || !data.user) {
            return false;
        }
        applyAuthenticatedUser(data.user);
        return true;
    } catch (error) {
        console.warn("Failed to restore auth session:", error);
        return false;
    }
}

async function handleLogin(event) {
    event.preventDefault();
    const username = elements.loginUsername.value.trim();
    const password = elements.loginPassword.value;
    if (!username || !password) {
        setAuthError("请输入账号和密码");
        return;
    }

    elements.loginBtn.disabled = true;
    setAuthError("");
    try {
        const response = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.user) {
            throw new Error(data.detail || "登录失败，请检查账号或密码");
        }

        applyAuthenticatedUser(data.user);
        elements.loginPassword.value = "";
        await initializeWorkspace();
    } catch (error) {
        showAuthScreen(error.message || "登录失败，请稍后重试");
    } finally {
        elements.loginBtn.disabled = false;
    }
}

async function handleLogout() {
    try {
        await fetch("/api/auth/logout", { method: "POST" });
    } catch (error) {
        console.warn("Failed to logout cleanly:", error);
    }
    window.location.reload();
}

function setAdminFormHint(message, isError = false) {
    if (!elements.adminFormHint) {
        return;
    }
    elements.adminFormHint.textContent = message;
    elements.adminFormHint.classList.toggle("error", Boolean(isError));
}

function setPluginScaffoldHint(message, isError = false) {
    if (!elements.pluginScaffoldHint) {
        return;
    }
    elements.pluginScaffoldHint.textContent = message;
    elements.pluginScaffoldHint.classList.toggle("error", Boolean(isError));
}

function setScheduleFormHint(message, isError = false) {
    if (!elements.scheduleFormHint) {
        return;
    }
    elements.scheduleFormHint.textContent = message;
    elements.scheduleFormHint.classList.toggle("error", Boolean(isError));
}

function setExportHint(message, isError = false) {
    if (!elements.exportHint) {
        return;
    }
    elements.exportHint.textContent = message;
    elements.exportHint.classList.toggle("error", Boolean(isError));
}

function parseCommaOrLineList(value) {
    return String(value || "")
        .split(/[\n,]+/)
        .map((item) => item.trim())
        .filter(Boolean);
}

async function loadAdminData() {
    if (!isAdminUser()) {
        state.adminUsers = [];
        state.adminPlugins = [];
        return;
    }

    try {
        const [usersResponse, pluginsResponse] = await Promise.all([
            fetch("/api/admin/users"),
            fetch("/api/admin/plugins")
        ]);

        const usersPayload = await usersResponse.json().catch(() => ({}));
        const pluginsPayload = await pluginsResponse.json().catch(() => ({}));

        if (!usersResponse.ok) {
            throw new Error(usersPayload.detail || "加载账号列表失败");
        }
        if (!pluginsResponse.ok) {
            throw new Error(pluginsPayload.detail || "加载插件列表失败");
        }

        state.adminUsers = Array.isArray(usersPayload.users) ? usersPayload.users : [];
        state.adminPlugins = Array.isArray(pluginsPayload.plugins) ? pluginsPayload.plugins : [];
        setAdminFormHint("管理员可以为不同业务账号勾选不同的插件权限。");
    } catch (error) {
        console.error("Failed to load admin data:", error);
        state.adminUsers = [];
        state.adminPlugins = [];
        setAdminFormHint(error.message || "加载管理员数据失败", true);
    }
}

function getInstallableMachines() {
    return state.machines
        .filter((machine) => machine.status === "online")
        .filter((machine) => {
            const systemInfo = machine.system_info;
            const agentAdmin = systemInfo && typeof systemInfo === "object" ? systemInfo.agent_admin : null;
            return Boolean(agentAdmin && agentAdmin.api_url);
        });
}

function renderPluginTargetMachineOptions() {
    if (!elements.pluginTargetMachine) {
        return;
    }

    const currentValue = elements.pluginTargetMachine.value;
    const installableMachines = getInstallableMachines()
        .sort((left, right) => String(left.id || "").localeCompare(String(right.id || ""), "zh-CN"));
    const options = [
        `<option value="">默认路径（本地 Agent 或全局 Agent API）</option>`,
        ...installableMachines.map((machine) => {
            const supportedRpas = Array.isArray(machine.rpas) ? machine.rpas.length : 0;
            return `<option value="${escapeHtml(machine.id || "")}">${escapeHtml(machine.id || "unknown-machine")} · 在线 · ${supportedRpas} 个插件</option>`;
        })
    ];

    if (installableMachines.length === 0) {
        options.push(`<option value="" disabled>暂无可远端安装的在线机器</option>`);
    }

    elements.pluginTargetMachine.innerHTML = options.join("");
    if (currentValue && installableMachines.some((machine) => machine.id === currentValue)) {
        elements.pluginTargetMachine.value = currentValue;
    } else {
        elements.pluginTargetMachine.value = "";
    }
}

function getAdminPluginSummary(plugin) {
    const onlineCount = Number.isFinite(plugin.online_machine_count) ? plugin.online_machine_count : 0;
    if (onlineCount > 0) {
        return `${onlineCount} 台机器在线`;
    }
    return "已注册，当前无在线机器";
}

function renderAdminUserCard(user) {
    const assignedIds = new Set(Array.isArray(user.assigned_rpa_ids) ? user.assigned_rpa_ids : []);
    const isAdmin = user.role === "admin";
    const pluginOptions = state.adminPlugins.length > 0
        ? state.adminPlugins.map((plugin) => `
            <label class="admin-plugin-option">
                <input
                    type="checkbox"
                    data-plugin-id="${escapeHtml(plugin.id || "")}"
                    ${assignedIds.has(plugin.id) ? "checked" : ""}
                    ${isAdmin ? "disabled" : ""}
                >
                <div class="admin-plugin-option-body">
                    <span class="admin-plugin-option-name">${escapeHtml(plugin.id || "unknown_plugin")}</span>
                    <span class="admin-plugin-option-meta">${escapeHtml(plugin.description || getAdminPluginSummary(plugin))}</span>
                </div>
            </label>
        `).join("")
        : `<div class="admin-empty-inline">当前还没有可分配的插件，请先让 Agent 完成注册。</div>`;

    return `
        <div class="admin-user-card" data-user-id="${escapeHtml(user.id || "")}">
            <div class="admin-user-card-header">
                <div>
                    <div class="admin-user-card-title">${escapeHtml(user.display_name || user.username || "未命名账号")}</div>
                    <div class="admin-user-card-subtitle">${escapeHtml(formatUserRole(user.role))} · ${escapeHtml(user.username || "-")}</div>
                </div>
                <span class="admin-role-chip ${isAdmin ? "admin" : "business"}">${escapeHtml(isAdmin ? "全部权限" : `已分配 ${assignedIds.size} 个插件`)}</span>
            </div>
            ${isAdmin
                ? `<div class="admin-admin-note">管理员账号默认拥有全部插件权限，无需单独勾选。</div>`
                : `
                    <div class="admin-plugin-grid">
                        ${pluginOptions}
                    </div>
                    <div class="admin-user-actions">
                        <button class="secondary-action-btn" onclick="saveAdminPermissions('${escapeHtml(user.id || "")}')">保存插件授权</button>
                    </div>
                `}
        </div>
    `;
}

function renderAdminUsers() {
    if (!elements.adminUserList) {
        return;
    }

    if (!isAdminUser()) {
        elements.adminUserList.innerHTML = `
            <div class="tool-loading-state">
                <div class="tool-loading-title">仅管理员可查看</div>
                <div class="tool-loading-description">请使用管理员账号登录后再管理业务账号和插件权限。</div>
            </div>
        `;
        return;
    }

    if (state.adminUsers.length === 0) {
        elements.adminUserList.innerHTML = `
            <div class="tool-loading-state">
                <div class="tool-loading-title">暂无账号数据</div>
                <div class="tool-loading-description">创建业务账号后，它们会显示在这里。</div>
            </div>
        `;
        return;
    }

    const users = [...state.adminUsers].sort((left, right) => {
        if (left.role === right.role) {
            return String(left.username || "").localeCompare(String(right.username || ""), "zh-CN");
        }
        return left.role === "admin" ? -1 : 1;
    });
    elements.adminUserList.innerHTML = users.map((user) => renderAdminUserCard(user)).join("");
}

async function openAdminModal() {
    if (!isAdminUser()) {
        return;
    }
    elements.adminModal.classList.add("active");
    elements.adminUserList.innerHTML = `
        <div class="tool-loading-state">
            <div class="tool-loading-title">正在加载账号与权限...</div>
            <div class="tool-loading-description">马上就好，正在同步业务账号和插件清单。</div>
        </div>
    `;
    await Promise.all([loadAdminData(), loadMachines()]);
    renderPluginTargetMachineOptions();
    renderAdminUsers();
}

async function refreshAdminData() {
    elements.adminUserList.innerHTML = `
        <div class="tool-loading-state">
            <div class="tool-loading-title">正在刷新账号与插件权限...</div>
            <div class="tool-loading-description">请稍候，新的授权状态会立即显示。</div>
        </div>
    `;
    await Promise.all([loadAdminData(), loadMachines()]);
    renderPluginTargetMachineOptions();
    renderAdminUsers();
}

async function createBusinessUser(event) {
    event.preventDefault();
    const username = elements.newUsername.value.trim();
    const displayName = elements.newDisplayName.value.trim();
    const password = elements.newPassword.value;
    if (!username || !password) {
        setAdminFormHint("请至少填写登录账号和初始密码。", true);
        return;
    }

    const createButton = document.getElementById("createUserBtn");
    if (createButton) {
        createButton.disabled = true;
    }

    try {
        const response = await fetch("/api/admin/users", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username,
                display_name: displayName || null,
                password,
                role: "business",
                allowed_rpa_ids: []
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.user) {
            throw new Error(data.detail || "创建业务账号失败");
        }

        elements.newUsername.value = "";
        elements.newDisplayName.value = "";
        elements.newPassword.value = "";
        setAdminFormHint(`账号 ${data.user.username} 创建成功，现在可以给它分配插件。`);
        await refreshAdminData();
    } catch (error) {
        setAdminFormHint(error.message || "创建业务账号失败", true);
    } finally {
        if (createButton) {
            createButton.disabled = false;
        }
    }
}

async function scaffoldPluginFromAdmin(event) {
    event.preventDefault();
    if (!isAdminUser()) {
        setPluginScaffoldHint("仅管理员可创建插件脚手架。", true);
        return;
    }

    const pluginId = elements.pluginId.value.trim();
    const targetMachineId = elements.pluginTargetMachine ? elements.pluginTargetMachine.value.trim() : "";
    const description = elements.pluginDescription.value.trim();
    const sourceFile = elements.pluginSourceFile.value.trim();
    const params = parseCommaOrLineList(elements.pluginParams.value);
    const requiredParams = parseCommaOrLineList(elements.pluginRequiredParams.value);
    const keywords = parseCommaOrLineList(elements.pluginKeywords.value);
    const tags = parseCommaOrLineList(elements.pluginTags.value);
    const capabilities = parseCommaOrLineList(elements.pluginCapabilities.value);
    const timeoutSec = Math.max(1, Number.parseInt(elements.pluginTimeoutSec.value, 10) || 60);
    const force = Boolean(elements.pluginForceOverwrite.checked);

    if (!pluginId) {
        setPluginScaffoldHint("请先填写插件 ID。", true);
        return;
    }

    const createButton = document.getElementById("createPluginBtn");
    if (createButton) {
        createButton.disabled = true;
    }
    setPluginScaffoldHint("正在生成插件骨架并写入 manifest 元数据...");

    try {
        const response = await fetch("/api/admin/plugins/scaffold", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                plugin_id: pluginId,
                machine_id: targetMachineId || null,
                description: description || null,
                source_file: sourceFile || null,
                params,
                required_params: requiredParams,
                keywords,
                tags,
                capabilities,
                timeout_sec: timeoutSec,
                force
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.plugin) {
            throw new Error(data.detail || "生成插件骨架失败");
        }

        const createdFiles = Array.isArray(data.files) ? data.files.join(" / ") : "manifest.yaml / __init__.py";
        elements.pluginScaffoldForm.reset();
        elements.pluginTimeoutSec.value = "60";
        renderPluginTargetMachineOptions();
        setPluginScaffoldHint(
            `插件 ${data.plugin.id || pluginId} 已生成到 ${data.plugin_dir || "agent/plugins"}，文件：${createdFiles}。${targetMachineId ? `目标机器：${targetMachineId}。` : ""}请重启 Agent 让它完成注册。`
        );
        await refreshAdminData();
    } catch (error) {
        setPluginScaffoldHint(error.message || "生成插件骨架失败", true);
    } finally {
        if (createButton) {
            createButton.disabled = false;
        }
    }
}

async function saveAdminPermissions(userId) {
    const card = elements.adminUserList.querySelector(`[data-user-id="${userId}"]`);
    if (!card) {
        return;
    }
    const assignedIds = Array.from(card.querySelectorAll("input[data-plugin-id]:checked"))
        .map((input) => input.dataset.pluginId)
        .filter(Boolean);

    try {
        const response = await fetch(`/api/admin/users/${encodeURIComponent(userId)}/plugins`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ allowed_rpa_ids: assignedIds })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.user) {
            throw new Error(data.detail || "保存插件授权失败");
        }

        const index = state.adminUsers.findIndex((item) => item.id === data.user.id);
        if (index >= 0) {
            state.adminUsers[index] = data.user;
        } else {
            state.adminUsers.push(data.user);
        }
        renderAdminUsers();
        setAdminFormHint(`已保存 ${data.user.username} 的插件授权。`);
    } catch (error) {
        setAdminFormHint(error.message || "保存插件授权失败", true);
    }
}

async function def_loadConversations() {
    try {
        const response = await fetch("/api/history/conversations");
        state.conversations = await response.json();
    } catch (error) {
        console.error("Failed to load conversations:", error);
        state.conversations = [];
    }
}

async function def_saveConversation(conversation) {
    try {
        await fetch("/api/history/conversations", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: conversation.id, title: conversation.title })
        });
    } catch (error) {
        console.error("Failed to save conversation:", error);
    }
}

async function def_saveMessage(conversationId, message) {
    try {
        await fetch("/api/history/messages", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                id: message.id,
                conversation_id: conversationId,
                role: message.role,
                content: message.content,
                timestamp: message.timestamp,
                meta: buildMessageMeta(message)
            })
        });
    } catch (error) {
        console.error("Failed to save message:", error);
    }
}

async function loadConversations() {
    await def_loadConversations();
    renderHistory();
}

async function loadConversation(id) {
    try {
        const response = await fetch(`/api/history/messages/${id}`);
        const messages = (await response.json()).map((message) => normalizeMessage(message));
        enrichSummaryMessageToolTrace(messages);

        state.currentConversationId = id;
        const conversation = state.conversations.find((item) => item.id === id);
        if (conversation) {
            conversation.messages = messages;
        }

        renderHistory();
        elements.messages.innerHTML = "";
        messages.forEach((message) => appendMessage(message));
        restorePendingTaskPollers(id, messages);
    } catch (error) {
        console.error("Failed to load messages:", error);
    }
}

async function createNewConversation() {
    const conversation = {
        id: generateId(),
        title: "新对话",
        messages: [],
        createdAt: new Date().toISOString()
    };

    state.conversations.unshift(conversation);
    state.currentConversationId = conversation.id;
    await def_saveConversation(conversation);

    renderHistory();
    elements.messages.innerHTML = "";

    const welcome = {
        id: generateId(),
        role: "assistant",
        content: "你好，我是 FlowMind 工作台助手。你可以把 OCR、文档处理、网页抓取和本地 RPA 调度放进同一段对话里。",
        timestamp: getTimestamp()
    };

    conversation.messages.push(welcome);
    appendMessage(welcome);
    await def_saveMessage(conversation.id, welcome);
}

async function deleteConversation(id) {
    if (!window.confirm("确定要删除这段对话吗？")) {
        return;
    }

    try {
        await fetch(`/api/history/conversations/${id}`, { method: "DELETE" });
        state.conversations = state.conversations.filter((conversation) => conversation.id !== id);

        if (state.currentConversationId === id) {
            if (state.conversations.length > 0) {
                state.currentConversationId = state.conversations[0].id;
                await loadConversation(state.currentConversationId);
            } else {
                await createNewConversation();
            }
        }

        renderHistory();
    } catch (error) {
        console.error("Failed to delete conversation:", error);
    }
}

function renderHistory() {
    elements.historyList.innerHTML = state.conversations.map((conversation) => `
        <div class="history-item ${conversation.id === state.currentConversationId ? "active" : ""}" onclick="loadConversation('${conversation.id}')">
            <div class="history-item-content">
                ${icons.chat}
                <span>${escapeHtml(conversation.title)}</span>
            </div>
            <div class="history-item-actions">
                <button class="history-task-tag" onclick="event.stopPropagation(); openTaskCenterForConversation('${conversation.id}')" title="查看该对话的任务">
                    ${escapeHtml(getConversationTag(conversation.id))}
                </button>
                <button class="delete-history-btn" onclick="event.stopPropagation(); deleteConversation('${conversation.id}')" title="删除">
                    ${icons.trash}
                </button>
            </div>
        </div>
    `).join("");
}

async function loadCommonTasks() {
    try {
        const response = await fetch("/api/common-tasks");
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const tasks = await response.json();
        state.commonTasks = Array.isArray(tasks) ? tasks : [];
    } catch (error) {
        console.error("Failed to load common tasks:", error);
        state.commonTasks = [];
    }
    renderCommonTasks();
    renderScheduleCommonTaskOptions();
}

function renderCommonTasks() {
    if (!elements.commonTaskList) {
        return;
    }
    if (!Array.isArray(state.commonTasks) || state.commonTasks.length === 0) {
        elements.commonTaskList.innerHTML = `
            <div class="common-task-empty">
                成功对话可保存为常用任务，下次一键复用。
            </div>
        `;
        return;
    }

    elements.commonTaskList.innerHTML = state.commonTasks.map((task) => `
        <div class="common-task-item">
            <button class="common-task-use-btn" onclick="useCommonTask('${escapeHtml(task.id || "")}')">
                <span class="common-task-title">${escapeHtml(task.title || "常用任务")}</span>
                <span class="common-task-summary">${escapeHtml(task.summary || task.prompt || "")}</span>
            </button>
            <button class="common-task-delete-btn" onclick="deleteCommonTask('${escapeHtml(task.id || "")}')" title="删除">
                ${icons.trash}
            </button>
        </div>
    `).join("");
}

async function saveReusableTaskSuggestion(messageId) {
    const conversation = getCurrentConversation();
    if (!conversation || !Array.isArray(conversation.messages)) {
        return;
    }
    const message = conversation.messages.find((item) => item.id === messageId);
    const suggestion = normalizeReusableTaskSuggestion(message);
    if (!message || !suggestion || suggestion.saved_task_id) {
        return;
    }

    const title = window.prompt("保存为常用任务：请输入名称", suggestion.title || "常用任务");
    if (!title || !title.trim()) {
        return;
    }

    try {
        const response = await fetch("/api/common-tasks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title: title.trim(),
                prompt: suggestion.prompt || "",
                summary: suggestion.summary || "",
                plan: normalizeRuntimePlan(message)
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.id) {
            throw new Error(data.detail || "保存常用任务失败");
        }

        state.commonTasks.unshift(data);
        renderCommonTasks();
        message.reusableTaskSuggestion = {
            ...suggestion,
            title: data.title || title.trim(),
            saved_task_id: data.id
        };
        refreshMessageToolTraceUI(message);
        await def_saveMessage(conversation.id, message);
    } catch (error) {
        window.alert(error.message || "保存常用任务失败");
    }
}

async function useCommonTask(taskId) {
    const task = state.commonTasks.find((item) => item.id === taskId);
    if (!task) {
        return;
    }
    try {
        await fetch(`/api/common-tasks/${encodeURIComponent(taskId)}/use`, { method: "POST" });
    } catch (error) {
        console.warn("Failed to mark common task as used:", error);
    }
    elements.messageInput.value = task.prompt || "";
    elements.messageInput.style.height = "auto";
    elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 200)}px`;
    elements.messageInput.focus();
}

async function deleteCommonTask(taskId) {
    if (!window.confirm("确定要删除这条常用任务吗？")) {
        return;
    }
    try {
        const response = await fetch(`/api/common-tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        state.commonTasks = state.commonTasks.filter((item) => item.id !== taskId);
        renderCommonTasks();
    } catch (error) {
        window.alert(error.message || "删除常用任务失败");
    }
}

function getBrowserTimezone() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
    } catch (error) {
        return "Asia/Shanghai";
    }
}

function renderScheduleCommonTaskOptions() {
    if (!elements.scheduleCommonTask) {
        return;
    }
    const currentValue = elements.scheduleCommonTask.value;
    const options = [
        `<option value="">不关联常用任务，直接使用下面的提示词</option>`,
        ...state.commonTasks.map((task) => `<option value="${escapeHtml(task.id || "")}">${escapeHtml(task.title || "常用任务")}</option>`)
    ];
    elements.scheduleCommonTask.innerHTML = options.join("");
    if (currentValue && state.commonTasks.some((task) => task.id === currentValue)) {
        elements.scheduleCommonTask.value = currentValue;
    } else {
        elements.scheduleCommonTask.value = "";
    }
}

function renderScheduleProviderOptions() {
    if (!elements.scheduleProvider) {
        return;
    }
    const providers = Array.isArray(state.providers) ? state.providers : [];
    const currentValue = elements.scheduleProvider.value || state.currentProvider || "";
    const options = [
        `<option value="">跟随当前默认模型</option>`,
        ...providers.map((item) => `<option value="${escapeHtml(item.id || "")}">${escapeHtml(item.name || item.id || "provider")}</option>`)
    ];
    elements.scheduleProvider.innerHTML = options.join("");
    elements.scheduleProvider.value = currentValue;
}

function syncScheduleTypeFields() {
    if (!elements.scheduleType) {
        return;
    }
    const type = elements.scheduleType.value;
    elements.scheduleDailyRow.style.display = type === "daily" ? "block" : "none";
    elements.scheduleIntervalRow.style.display = type === "interval" ? "block" : "none";
    elements.scheduleOnceRow.style.display = type === "once" ? "block" : "none";
}

function resetScheduleForm() {
    state.scheduleEditingId = null;
    if (!elements.scheduleForm) {
        return;
    }
    elements.scheduleForm.reset();
    elements.scheduleDailyTime.value = "09:00";
    elements.scheduleIntervalMinutes.value = "30";
    elements.scheduleTimezone.value = getBrowserTimezone();
    if (state.currentProvider) {
        elements.scheduleProvider.value = state.currentProvider;
    }
    elements.scheduleIsActive.checked = true;
    syncScheduleTypeFields();
    setScheduleFormHint("调度计划会以当前账号权限执行，并保留运行轨迹。");
    if (elements.saveScheduleBtn) {
        elements.saveScheduleBtn.textContent = "保存计划";
    }
}

function buildScheduleConfigFromForm() {
    const type = elements.scheduleType.value;
    if (type === "daily") {
        return { time: elements.scheduleDailyTime.value || "09:00" };
    }
    if (type === "interval") {
        return { interval_minutes: Math.max(5, Number.parseInt(elements.scheduleIntervalMinutes.value, 10) || 30) };
    }
    return { run_at: elements.scheduleRunAt.value || "" };
}

function populateScheduleForm(scheduleId) {
    const schedule = state.schedules.find((item) => item.id === scheduleId);
    if (!schedule) {
        return;
    }
    state.scheduleEditingId = schedule.id;
    elements.scheduleTitle.value = schedule.title || "";
    elements.scheduleCommonTask.value = schedule.common_task_id || "";
    elements.schedulePrompt.value = schedule.prompt || "";
    elements.scheduleProvider.value = schedule.provider || state.currentProvider || "";
    elements.scheduleType.value = schedule.schedule_type || "daily";
    elements.scheduleTimezone.value = schedule.timezone || getBrowserTimezone();
    elements.scheduleIsActive.checked = Boolean(schedule.is_active);
    const config = schedule.schedule_config && typeof schedule.schedule_config === "object" ? schedule.schedule_config : {};
    elements.scheduleDailyTime.value = config.time || "09:00";
    elements.scheduleIntervalMinutes.value = String(config.interval_minutes || 30);
    if (schedule.next_run_at) {
        elements.scheduleRunAt.value = String(schedule.next_run_at).replace(" ", "T").slice(0, 16);
    }
    syncScheduleTypeFields();
    setScheduleFormHint(`正在编辑计划：${schedule.title || schedule.id}`);
    if (elements.saveScheduleBtn) {
        elements.saveScheduleBtn.textContent = "更新计划";
    }
}

async function loadSchedules() {
    try {
        const response = await fetch("/api/schedules");
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        state.schedules = Array.isArray(data) ? data : [];
    } catch (error) {
        console.error("Failed to load schedules:", error);
        state.schedules = [];
    }
    renderScheduleList();
}

async function loadScheduleRuns(limit = 30) {
    try {
        const response = await fetch(`/api/schedule-runs?limit=${encodeURIComponent(limit)}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        state.scheduleRuns = Array.isArray(data) ? data : [];
    } catch (error) {
        console.error("Failed to load schedule runs:", error);
        state.scheduleRuns = [];
    }
    renderScheduleRunList();
}

function renderScheduleList() {
    if (!elements.scheduleList) {
        return;
    }
    if (!Array.isArray(state.schedules) || state.schedules.length === 0) {
        elements.scheduleList.innerHTML = `
            <div class="task-empty-state">
                <div class="task-empty-title">还没有调度计划</div>
                <div class="task-empty-description">先把一次成功对话保存为常用任务，再在这里安排一次性、间隔或每日执行。</div>
            </div>
        `;
        return;
    }

    elements.scheduleList.innerHTML = state.schedules.map((schedule) => {
        const statusClass = getScheduleStatusClass(schedule.last_status || (schedule.is_active ? "queued" : "unknown"));
        const scheduleConfig = schedule.schedule_config && typeof schedule.schedule_config === "object" ? schedule.schedule_config : {};
        let cadenceText = formatScheduleType(schedule.schedule_type);
        if (schedule.schedule_type === "daily") {
            cadenceText += ` · ${scheduleConfig.time || "09:00"}`;
        } else if (schedule.schedule_type === "interval") {
            cadenceText += ` · 每 ${scheduleConfig.interval_minutes || 30} 分钟`;
        } else if (schedule.schedule_type === "once") {
            cadenceText += ` · ${formatScheduleTime(scheduleConfig.run_at || schedule.next_run_at)}`;
        }
        return `
            <div class="schedule-card ${statusClass}">
                <div class="schedule-card-header">
                    <div>
                        <div class="task-card-title">${escapeHtml(schedule.title || "未命名计划")}</div>
                        <div class="task-card-subtitle">${escapeHtml(cadenceText)}</div>
                    </div>
                    <span class="schedule-status ${statusClass}">${escapeHtml(formatScheduleRunStatus(schedule.last_status || "queued"))}</span>
                </div>
                <div class="task-card-meta">
                    <div class="task-meta-row">
                        <span class="task-meta-text">下次执行: ${escapeHtml(formatScheduleTime(schedule.next_run_at))}</span>
                        <span class="task-meta-dot">·</span>
                        <span class="task-meta-text">${escapeHtml(schedule.is_active ? "已启用" : "已暂停")}</span>
                    </div>
                    <div class="task-meta-row">
                        <span class="task-meta-text">模型: ${escapeHtml(schedule.provider || state.currentProvider || "默认")}</span>
                        <span class="task-meta-dot">·</span>
                        <span class="task-meta-text">时区: ${escapeHtml(schedule.timezone || "-")}</span>
                    </div>
                </div>
                <div class="task-card-preview">${escapeHtml(schedule.summary || schedule.prompt || "暂无说明")}</div>
                ${schedule.last_error ? `<div class="schedule-error-text">${escapeHtml(schedule.last_error)}</div>` : ""}
                <div class="schedule-card-actions">
                    <button class="card-action-btn" type="button" onclick="populateScheduleForm('${escapeHtml(schedule.id || "")}')">编辑</button>
                    <button class="card-action-btn" type="button" onclick="toggleScheduleActive('${escapeHtml(schedule.id || "")}', ${schedule.is_active ? "false" : "true"})">${schedule.is_active ? "暂停" : "启用"}</button>
                    <button class="card-action-btn danger" type="button" onclick="deleteSchedule('${escapeHtml(schedule.id || "")}')">${icons.trash}<span>删除</span></button>
                </div>
            </div>
        `;
    }).join("");
}

function renderScheduleRunList() {
    if (!elements.scheduleRunList) {
        return;
    }
    if (!Array.isArray(state.scheduleRuns) || state.scheduleRuns.length === 0) {
        elements.scheduleRunList.innerHTML = `
            <div class="task-empty-state">
                <div class="task-empty-title">暂无执行记录</div>
                <div class="task-empty-description">计划第一次跑起来后，这里会显示状态、对话和恢复线索。</div>
            </div>
        `;
        return;
    }
    elements.scheduleRunList.innerHTML = state.scheduleRuns.map((run) => `
        <div class="schedule-run-card ${getScheduleStatusClass(run.status)}">
            <div class="schedule-run-header">
                <div>
                    <div class="task-card-title">${escapeHtml(run.conversation_title || run.schedule_id || "计划执行")}</div>
                    <div class="task-card-subtitle">${escapeHtml(formatScheduleRunStatus(run.status))}</div>
                </div>
                <span class="schedule-status ${getScheduleStatusClass(run.status)}">${escapeHtml(formatScheduleRunStatus(run.status))}</span>
            </div>
            <div class="task-card-meta">
                <div class="task-meta-row">
                    <span class="task-meta-text">计划时间: ${escapeHtml(formatScheduleTime(run.planned_for))}</span>
                    <span class="task-meta-dot">·</span>
                    <span class="task-meta-text">开始: ${escapeHtml(formatScheduleTime(run.started_at))}</span>
                </div>
                <div class="task-meta-row">
                    <span class="task-meta-text">结束: ${escapeHtml(formatScheduleTime(run.finished_at))}</span>
                    <span class="task-meta-dot">·</span>
                    <span class="task-meta-text">会话: ${escapeHtml(run.conversation_id || "-")}</span>
                </div>
            </div>
            <div class="task-card-preview">${escapeHtml(run.error_text || run.response_text || "暂无摘要")}</div>
        </div>
    `).join("");
}

async function openSchedulesModal() {
    elements.schedulesModal.classList.add("active");
    renderScheduleCommonTaskOptions();
    renderScheduleProviderOptions();
    resetScheduleForm();
    if (!elements.scheduleTimezone.value) {
        elements.scheduleTimezone.value = getBrowserTimezone();
    }
    syncScheduleTypeFields();
    await Promise.all([loadSchedules(), loadScheduleRuns()]);
}

async function refreshSchedules() {
    await Promise.all([loadSchedules(), loadScheduleRuns(), updateProviderInfo(), loadCommonTasks()]);
    renderScheduleCommonTaskOptions();
    renderScheduleProviderOptions();
}

async function saveSchedule(event) {
    event.preventDefault();
    const payload = {
        title: elements.scheduleTitle.value.trim() || null,
        common_task_id: elements.scheduleCommonTask.value.trim() || null,
        prompt: elements.schedulePrompt.value.trim() || null,
        provider: elements.scheduleProvider.value.trim() || null,
        schedule_type: elements.scheduleType.value,
        schedule_config: buildScheduleConfigFromForm(),
        timezone: elements.scheduleTimezone.value.trim() || getBrowserTimezone(),
        is_active: Boolean(elements.scheduleIsActive.checked)
    };
    if (payload.schedule_type === "once" && !payload.schedule_config.run_at) {
        setScheduleFormHint("一次性调度需要填写执行时间。", true);
        return;
    }

    if (elements.saveScheduleBtn) {
        elements.saveScheduleBtn.disabled = true;
    }
    setScheduleFormHint(state.scheduleEditingId ? "正在更新调度计划..." : "正在创建调度计划...");
    try {
        const response = await fetch(
            state.scheduleEditingId ? `/api/schedules/${encodeURIComponent(state.scheduleEditingId)}` : "/api/schedules",
            {
                method: state.scheduleEditingId ? "PUT" : "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }
        );
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.id) {
            throw new Error(data.detail || "保存调度计划失败");
        }

        setScheduleFormHint(`计划 ${data.title || "未命名计划"} 已保存。`);
        resetScheduleForm();
        await Promise.all([loadSchedules(), loadScheduleRuns()]);
    } catch (error) {
        setScheduleFormHint(error.message || "保存调度计划失败", true);
    } finally {
        if (elements.saveScheduleBtn) {
            elements.saveScheduleBtn.disabled = false;
        }
    }
}

async function toggleScheduleActive(scheduleId, isActive) {
    try {
        const response = await fetch(`/api/schedules/${encodeURIComponent(scheduleId)}/toggle`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_active: Boolean(isActive) })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.id) {
            throw new Error(data.detail || "更新调度状态失败");
        }
        await loadSchedules();
    } catch (error) {
        setScheduleFormHint(error.message || "更新调度状态失败", true);
    }
}

async function deleteSchedule(scheduleId) {
    if (!window.confirm("确定要删除这条调度计划吗？")) {
        return;
    }
    try {
        const response = await fetch(`/api/schedules/${encodeURIComponent(scheduleId)}`, { method: "DELETE" });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || "删除调度计划失败");
        }
        if (state.scheduleEditingId === scheduleId) {
            resetScheduleForm();
        }
        await Promise.all([loadSchedules(), loadScheduleRuns()]);
    } catch (error) {
        setScheduleFormHint(error.message || "删除调度计划失败", true);
    }
}

function handleScheduleCommonTaskChange() {
    const taskId = elements.scheduleCommonTask.value;
    if (!taskId) {
        return;
    }
    const task = state.commonTasks.find((item) => item.id === taskId);
    if (!task) {
        return;
    }
    if (!elements.scheduleTitle.value.trim()) {
        elements.scheduleTitle.value = task.title || "";
    }
    if (!elements.schedulePrompt.value.trim()) {
        elements.schedulePrompt.value = task.prompt || "";
    }
}

function getExportTargets() {
    const currentConversation = getCurrentConversation();
    return [
        {
            id: "conversation",
            title: "当前对话",
            description: currentConversation ? "导出当前会话的全文、计划和工具轨迹。" : "先选择一段对话，再导出会话内容。",
            disabled: !currentConversation,
            buttons: [
                { label: "JSON", format: "json" },
                { label: "Markdown", format: "markdown" }
            ]
        },
        {
            id: "tasks",
            title: "任务中心",
            description: "导出任务状态、所属对话和结果摘要，便于审计或二次分析。",
            disabled: false,
            buttons: [
                { label: "JSON", format: "json" },
                { label: "CSV", format: "csv" }
            ]
        },
        {
            id: "common_tasks",
            title: "常用任务",
            description: "导出沉淀下来的常用任务模板和运行时计划摘要。",
            disabled: false,
            buttons: [
                { label: "JSON", format: "json" },
                { label: "CSV", format: "csv" }
            ]
        },
        {
            id: "schedules",
            title: "调度计划",
            description: "导出调度计划、执行频率、下次执行时间和最近状态。",
            disabled: false,
            buttons: [
                { label: "JSON", format: "json" },
                { label: "CSV", format: "csv" }
            ]
        },
        {
            id: "schedule_runs",
            title: "执行记录",
            description: "导出计划每次执行的标准化记录，用于审计、归档和排错。",
            disabled: false,
            buttons: [
                { label: "JSON", format: "json" },
                { label: "CSV", format: "csv" }
            ]
        }
    ];
}

function renderExportCenter() {
    if (!elements.exportList) {
        return;
    }
    const targets = getExportTargets();
    elements.exportList.innerHTML = targets.map((target) => `
        <div class="export-card ${target.disabled ? "disabled" : ""}">
            <div class="export-card-title">${escapeHtml(target.title)}</div>
            <div class="export-card-description">${escapeHtml(target.description)}</div>
            <div class="export-card-actions">
                ${target.buttons.map((button) => `
                    <button
                        class="secondary-action-btn"
                        type="button"
                        ${target.disabled ? "disabled" : ""}
                        onclick="downloadExport('${escapeHtml(target.id)}', '${escapeHtml(button.format)}')"
                    >
                        下载 ${escapeHtml(button.label)}
                    </button>
                `).join("")}
            </div>
        </div>
    `).join("");
}

async function openExportsModal() {
    elements.exportsModal.classList.add("active");
    renderExportCenter();
    setExportHint("导出文件会按标准 JSON / CSV / Markdown 下载，不需要额外进入服务器目录。");
}

async function downloadExport(resource, format) {
    const currentConversation = getCurrentConversation();
    setExportHint(`正在准备 ${resource} 的 ${format.toUpperCase()} 导出...`);
    try {
        const response = await fetch("/api/exports/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                resource,
                format,
                conversation_id: resource === "conversation" ? (currentConversation && currentConversation.id) : null
            })
        });
        if (!response.ok) {
            const errorText = await readResponseError(response, "导出失败");
            throw new Error(errorText);
        }
        const blob = await response.blob();
        const contentDisposition = response.headers.get("Content-Disposition") || "";
        const matched = /filename=\"?([^"]+)\"?/.exec(contentDisposition);
        const filename = matched ? matched[1] : `flowmind-${resource}.${format === "markdown" ? "md" : format}`;
        const downloadUrl = window.URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = downloadUrl;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.URL.revokeObjectURL(downloadUrl);
        setExportHint(`${filename} 已开始下载。`);
    } catch (error) {
        setExportHint(error.message || "导出失败", true);
    }
}

async function sendMessage() {
    if (!getCurrentUser()) {
        showAuthScreen("请先登录后再发送消息。");
        return;
    }

    const text = elements.messageInput.value.trim();
    if (!text) {
        return;
    }

    let conversation = getCurrentConversation();
    if (!conversation) {
        await createNewConversation();
        conversation = getCurrentConversation();
    }

    let fullContent = text;
    if (state.uploadedFiles.length > 0) {
        const fileContext = state.uploadedFiles
            .map((file) => `[Attached File: ${file.filename} (Path: ${file.path})]`)
            .join("\n");
        fullContent += `\n\n${fileContext}`;
    }

    const userMessage = {
        id: generateId(),
        role: "user",
        content: fullContent,
        timestamp: getTimestamp()
    };

    conversation.messages = conversation.messages || [];
    conversation.messages.push(userMessage);
    appendMessage(userMessage);
    await def_saveMessage(conversation.id, userMessage);

    if (conversation.title === "新对话") {
        conversation.title = text.slice(0, 30) + (text.length > 30 ? "..." : "");
        await def_saveConversation(conversation);
        renderHistory();
    }

    state.uploadedFiles = [];
    renderFilePreviews();
    elements.messageInput.value = "";
    elements.messageInput.style.height = "auto";
    showTyping();

    try {
        const response = await fetch("/api/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                messages: conversation.messages.map((message) => ({ role: message.role, content: message.content })),
                conversation_id: conversation.id,
                conversation_title: conversation.title
            })
        });

        if (!response.ok || !response.body) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        hideTyping();

        const aiMessage = {
            id: generateId(),
            role: "assistant",
            content: "",
            timestamp: getTimestamp(),
            toolTrace: [],
            runtimePlan: null,
            reusableTaskSuggestion: null
        };
        conversation.messages.push(aiMessage);
        const aiMessageElement = createMessageElement(aiMessage);
        elements.messages.appendChild(aiMessageElement);
        const textElement = aiMessageElement.querySelector(".message-text");
        const runtimePlanElement = aiMessageElement.querySelector(".message-runtime-plan");
        const toolTraceBannerElement = aiMessageElement.querySelector(".message-tool-banner-container");
        const toolTraceElement = aiMessageElement.querySelector(".message-tool-trace");
        const reusableTaskElement = aiMessageElement.querySelector(".message-reusable-task");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finished = false;

        while (!finished) {
            const { value, done } = await reader.read();
            finished = done;
            if (!value) {
                continue;
            }

            buffer += decoder.decode(value, { stream: !done });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (!line.startsWith("data: ")) {
                    continue;
                }

                const dataStr = line.slice(6).trim();
                if (dataStr === "[DONE]") {
                    finished = true;
                    break;
                }

                try {
                    const data = JSON.parse(dataStr);
                    if (data.type === "runtime_plan" && data.plan) {
                        aiMessage.runtimePlan = data.plan;
                        updateRuntimePlanElement(runtimePlanElement, aiMessage.runtimePlan);
                        scrollToBottom();
                        continue;
                    }
                    if (data.type === "reusable_task_suggestion" && data.suggestion) {
                        aiMessage.reusableTaskSuggestion = data.suggestion;
                        updateReusableTaskElement(reusableTaskElement, aiMessage);
                        continue;
                    }
                    if (data.type === "tool_start" && data.tool) {
                        aiMessage.toolTrace = upsertToolTrace(aiMessage.toolTrace, {
                            id: data.tool.id,
                            name: data.tool.name,
                            arguments: data.tool.arguments || {},
                            status: "running",
                            result_preview: ""
                        });
                        aiMessageElement.classList.add("has-tool-trace");
                        updateToolTraceBannerElement(toolTraceBannerElement, aiMessage.toolTrace);
                        updateToolTraceElement(toolTraceElement, aiMessage.toolTrace);
                        scrollToBottom();
                        continue;
                    }
                    if (data.type === "tool_result" && data.tool) {
                        if (data.tool.task_id) {
                            const nowIso = new Date().toISOString();
                            const taskRecord = {
                                id: data.tool.task_id,
                                rpa_id: data.tool.name,
                                machine_id: "待分配",
                                status: data.tool.status || "pending",
                                params: data.tool.arguments || {},
                                conversation_id: conversation.id,
                                conversation_title: conversation.title,
                                result: null,
                                created_at: nowIso,
                                updated_at: nowIso
                            };
                            upsertTaskRecord(taskRecord);
                            if (elements.tasksModal.classList.contains("active") && isTaskVisibleInCurrentFilter(taskRecord)) {
                                renderTasks();
                            }
                        }
                        aiMessage.toolTrace = upsertToolTrace(aiMessage.toolTrace, {
                            id: data.tool.id,
                            name: data.tool.name,
                            arguments: data.tool.arguments || {},
                            status: data.tool.status || "success",
                            task_id: data.tool.task_id || null,
                            result_preview: data.tool.result_preview || ""
                        });
                        aiMessageElement.classList.add("has-tool-trace");
                        updateToolTraceBannerElement(toolTraceBannerElement, aiMessage.toolTrace);
                        updateToolTraceElement(toolTraceElement, aiMessage.toolTrace);
                        if (data.tool.task_id) {
                            startTaskPolling(conversation.id, aiMessage.id, data.tool.task_id);
                        }
                        scrollToBottom();
                        continue;
                    }
                    if (typeof data.content === "string") {
                        aiMessage.content += data.content;
                        textElement.innerHTML = formatMarkdown(aiMessage.content);
                        scrollToBottom();
                    }
                } catch (error) {
                    console.warn("Error parsing stream chunk:", error);
                }
            }
        }

        await def_saveMessage(conversation.id, aiMessage);
    } catch (error) {
        console.error("Chat error:", error);
        hideTyping();
        const fallback = {
            id: generateId(),
            role: "assistant",
            content: `发送失败：${error.message}`,
            timestamp: getTimestamp()
        };
        conversation.messages.push(fallback);
        appendMessage(fallback);
        await def_saveMessage(conversation.id, fallback);
    }
}

async function updateProviderInfo() {
    try {
        const response = await fetch("/api/provider");
        const data = await response.json();
        state.providers = Array.isArray(data.providers) ? data.providers : [];
        state.currentProvider = data.current || null;
        renderScheduleProviderOptions();
        if (data.current) {
            const providerName = data.providers.find((item) => item.id === data.current)?.name || data.current;
            elements.statusBadge.innerHTML = `<span class="status-dot"></span><span class="status-text">在线: ${providerName}</span>`;
            elements.statusBadge.classList.add("online");
            return;
        }
    } catch (error) {
        console.warn("Failed to load provider info:", error);
        state.providers = [];
        state.currentProvider = null;
        renderScheduleProviderOptions();
    }

    elements.statusBadge.innerHTML = `<span class="status-dot"></span><span class="status-text">未配置模型</span>`;
    elements.statusBadge.classList.remove("online");
}

async function handleFileUpload(event) {
    const files = event.target.files;
    if (!files || files.length === 0) {
        return;
    }

    for (const file of files) {
        const formData = new FormData();
        formData.append("file", file);
        try {
            const response = await fetch("/api/upload", { method: "POST", body: formData });
            const data = await response.json();
            if (data.path) {
                state.uploadedFiles.push(data);
            }
        } catch (error) {
            alert("文件上传失败");
        }
    }

    renderFilePreviews();
    elements.fileInput.value = "";
}

function renderFilePreviews() {
    if (state.uploadedFiles.length === 0) {
        elements.filePreviewContainer.style.display = "none";
        elements.filePreviewContainer.innerHTML = "";
        return;
    }

    elements.filePreviewContainer.style.display = "flex";
    elements.filePreviewContainer.innerHTML = state.uploadedFiles.map((file, index) => `
        <div class="file-chip">
            ${icons.document}
            <span>${escapeHtml(file.filename)}</span>
            <span class="remove-file" onclick="removeFile(${index})">&times;</span>
        </div>
    `).join("");
}

function removeFile(index) {
    state.uploadedFiles.splice(index, 1);
    renderFilePreviews();
}

function inferToolIcon(tool) {
    const id = (tool.id || "").toLowerCase();
    const tags = Array.isArray(tool.tags) ? tool.tags.join(" ").toLowerCase() : "";
    const text = `${id} ${tags} ${(tool.description || "").toLowerCase()}`;

    if (text.includes("mail") || text.includes("email") || text.includes("邮件")) {
        return "email";
    }
    if (
        text.includes("excel") ||
        text.includes("word") ||
        text.includes("ocr") ||
        text.includes("文档") ||
        text.includes("表格")
    ) {
        return "document";
    }
    return "chat";
}

async function loadTools() {
    const isCacheFresh = state.toolsLoadedAt && (Date.now() - state.toolsLoadedAt) < TOOLS_CACHE_TTL_MS;
    if (isCacheFresh && state.tools.length > 0) {
        return state.tools;
    }

    try {
        const response = await fetch("/api/tools");
        const data = await response.json();
        const tools = Array.isArray(data.tools) ? data.tools : [];
        state.tools = tools.map((tool) => ({
            id: tool.id,
            name: tool.id,
            description: tool.description || "暂无描述",
            icon: inferToolIcon(tool),
            tags: Array.isArray(tool.tags) ? tool.tags : [],
            onlineMachineIds: Array.isArray(tool.online_machine_ids) ? tool.online_machine_ids : [],
            onlineMachineCount: Number.isFinite(tool.online_machine_count) ? tool.online_machine_count : 0,
            toolProfile: tool.tool_profile && typeof tool.tool_profile === "object" ? tool.tool_profile : null
        }));
        state.toolsLoadedAt = Date.now();
    } catch (error) {
        console.error("Failed to load tools:", error);
        state.tools = [];
        state.toolsLoadedAt = 0;
    }
    return state.tools;
}

async function refreshTools() {
    state.toolsLoadedAt = 0;
    visibleToolCount = TOOL_RENDER_CHUNK;
    showToolsLoadingState();
    await loadTools();
    renderTools();
}

async function loadMachines() {
    const isCacheFresh = state.machinesLoadedAt && (Date.now() - state.machinesLoadedAt) < MACHINES_CACHE_TTL_MS;
    if (isCacheFresh && state.machines.length > 0) {
        return state.machines;
    }

    try {
        const response = await fetch("/api/machines");
        const data = await response.json();
        state.machines = Array.isArray(data)
            ? data.map((machine) => ({
                id: machine.id,
                status: machine.status,
                last_heartbeat: machine.last_heartbeat,
                rpas: Array.isArray(machine.rpas) ? machine.rpas : [],
                running_task_count: Number.isFinite(machine.running_task_count) ? machine.running_task_count : 0
            }))
            : [];
        state.machinesLoadedAt = Date.now();
    } catch (error) {
        console.error("Failed to load machines:", error);
        state.machines = [];
        state.machinesLoadedAt = 0;
    }
    return state.machines;
}

async function refreshMachines() {
    state.machinesLoadedAt = 0;
    await loadMachines();
    renderMachines();
}

async function loadTasks(limit = 30) {
    try {
        const response = await fetch(`/api/tasks?limit=${encodeURIComponent(limit)}`);
        const tasks = await response.json();
        state.tasks = Array.isArray(tasks) ? tasks : [];
    } catch (error) {
        console.error("Failed to load tasks:", error);
        state.tasks = [];
    }
    updateTasksBadge();
}

function upsertTaskRecord(task) {
    if (!task || !task.id) {
        return;
    }

    const index = state.tasks.findIndex((item) => item.id === task.id);
    if (index >= 0) {
        state.tasks[index] = { ...state.tasks[index], ...task };
    } else {
        state.tasks.unshift(task);
    }

    state.tasks.sort((left, right) => {
        const leftTime = Date.parse(left.updated_at || left.created_at || "") || 0;
        const rightTime = Date.parse(right.updated_at || right.created_at || "") || 0;
        return rightTime - leftTime;
    });
    updateTasksBadge();
}

function buildTaskPreview(task) {
    if (task.result !== undefined && task.result !== null) {
        return truncateText(stringifyValue(task.result));
    }
    if (task.params !== undefined && task.params !== null) {
        return truncateText(stringifyValue(task.params));
    }
    return "暂无详细信息";
}

function buildFullTaskPreview(task) {
    if (task.result !== undefined && task.result !== null) {
        return stringifyValue(task.result);
    }
    if (task.params !== undefined && task.params !== null) {
        return stringifyValue(task.params);
    }
    return "暂无详细信息";
}

function updateTasksBadge() {
    const pendingCount = state.tasks.filter((task) => {
        const status = normalizeTaskStatus(task);
        return status === "pending" || status === "running";
    }).length;

    if (!elements.tasksBadge) {
        return;
    }

    if (pendingCount > 0) {
        elements.tasksBadge.textContent = pendingCount > 99 ? "99+" : String(pendingCount);
        elements.tasksBadge.style.display = "inline-flex";
        return;
    }

    elements.tasksBadge.style.display = "none";
}

function getAllFilteredTasks() {
    return state.taskFilterConversationId
        ? state.tasks.filter((task) => task.conversation_id === state.taskFilterConversationId)
        : state.tasks;
}

function getVisibleTasks() {
    const tasks = getAllFilteredTasks().slice(0, TASK_RENDER_LIMIT);
    const totalPages = Math.max(1, Math.ceil(tasks.length / TASKS_PER_PAGE));
    if (state.taskPage > totalPages) {
        state.taskPage = totalPages;
    }
    if (state.taskPage < 1) {
        state.taskPage = 1;
    }
    const startIndex = (state.taskPage - 1) * TASKS_PER_PAGE;
    return tasks.slice(startIndex, startIndex + TASKS_PER_PAGE);
}

function isTaskVisibleInCurrentFilter(task) {
    if (!task || !task.id) {
        return false;
    }
    return getVisibleTasks().some((item) => item.id === task.id);
}

function renderTaskDetailContent(task) {
    return `
        <div class="task-detail-block">
            <div class="task-detail-label">参数</div>
            <pre>${escapeHtml(stringifyValue(task.params || {}))}</pre>
        </div>
        <div class="task-detail-block">
            <div class="task-detail-label">结果</div>
            <pre>${escapeHtml(buildFullTaskPreview(task))}</pre>
        </div>
    `;
}

function renderTaskCard(task) {
    const status = normalizeTaskStatus(task);
    const preview = buildTaskPreview(task);
    const conversationInfo = getTaskConversationInfo(task);
    const machineStatusClass = getMachineStatusClass(task.machine_status);
    const machineStatusLabel = formatMachineStatus(task.machine_status);
    const conversationSummary = `${conversationInfo.conversationTag} · ${conversationInfo.conversationTitle}`;
    const deleteDisabled = !canDeleteTask(task);
    const deleteTitle = deleteDisabled ? "执行中的任务暂不支持删除" : "删除任务";

    return `
        <div class="task-card ${status}" data-task-id="${escapeHtml(task.id || "")}">
            <div class="task-card-header">
                <div>
                    <div class="task-card-title">${escapeHtml(task.rpa_id || "unknown_task")}</div>
                    <div class="task-card-subtitle">
                        机器：${escapeHtml(task.machine_id || "-")}
                        <span class="task-machine-status ${machineStatusClass}">${escapeHtml(machineStatusLabel)}</span>
                    </div>
                </div>
                <div class="card-header-actions">
                    <span class="task-status ${status}">${formatTaskStatus(status)}</span>
                    <button
                        class="card-action-btn danger"
                        type="button"
                        onclick="deleteTask('${escapeHtml(task.id || "")}')"
                        ${deleteDisabled ? "disabled" : ""}
                        title="${escapeHtml(deleteTitle)}"
                    >
                        ${icons.trash}
                        <span>删除</span>
                    </button>
                </div>
            </div>
            <div class="task-card-meta">
                <div class="task-meta-row">
                    <span class="task-meta-text">任务 ID: ${escapeHtml(task.id || "-")}</span>
                    <span class="task-meta-dot">·</span>
                    <span class="task-meta-text">${escapeHtml(conversationSummary)}</span>
                </div>
                <div class="task-meta-row">
                    <span class="task-meta-text">创建: ${escapeHtml(formatTaskTime(task.created_at))}</span>
                    <span class="task-meta-dot">·</span>
                    <span class="task-meta-text">更新: ${escapeHtml(formatTaskTime(task.updated_at))}</span>
                </div>
            </div>
            <div class="task-card-preview">${escapeHtml(preview)}</div>
            <details class="task-details" data-task-id="${escapeHtml(task.id || "")}" ontoggle="handleTaskDetailsToggle(this)">
                <summary>查看参数和结果</summary>
                <div class="task-detail-content" data-loaded="false"></div>
            </details>
        </div>
    `;
}

function updateTaskCardInView(task) {
    if (!elements.tasksModal.classList.contains("active") || !elements.taskList) {
        return;
    }

    if (!isTaskVisibleInCurrentFilter(task)) {
        return;
    }

    if (Date.now() < taskScrollSettledAt) {
        pendingTaskCardUpdates.set(task.id, task);
        return;
    }

    const card = elements.taskList.querySelector(`.task-card[data-task-id="${task.id}"]`);
    if (!card) {
        renderTasks();
        return;
    }

    const wrapper = document.createElement("div");
    wrapper.innerHTML = renderTaskCard(task).trim();
    const nextCard = wrapper.firstElementChild;
    if (!nextCard) {
        return;
    }

    const existingDetails = card.querySelector(".task-details");
    const existingContent = card.querySelector(".task-detail-content");
    const nextDetails = nextCard.querySelector(".task-details");
    const nextContent = nextCard.querySelector(".task-detail-content");
    if (existingDetails && nextDetails) {
        nextDetails.open = existingDetails.open;
        if (existingDetails.open && existingContent && nextContent) {
            nextContent.dataset.loaded = "true";
            nextContent.innerHTML = existingContent.innerHTML;
        }
    }

    card.replaceWith(nextCard);
}

function flushPendingTaskCardUpdates() {
    if (pendingTaskCardUpdates.size === 0) {
        return;
    }
    const queuedTasks = Array.from(pendingTaskCardUpdates.values());
    pendingTaskCardUpdates.clear();
    queuedTasks.forEach((task) => updateTaskCardInView(task));
}

function markScrollingState(container, onIdle = null) {
    if (!container) {
        return;
    }
    container.classList.add("is-scrolling");
    if (container.__scrollPerfTimer) {
        window.clearTimeout(container.__scrollPerfTimer);
    }
    container.__scrollPerfTimer = window.setTimeout(() => {
        container.classList.remove("is-scrolling");
        container.__scrollPerfTimer = 0;
        if (onIdle) {
            onIdle();
        }
    }, 140);
}

function markTaskScrollActivity() {
    taskScrollSettledAt = Date.now() + 180;
    if (taskScrollIdleTimer) {
        window.clearTimeout(taskScrollIdleTimer);
    }
    taskScrollIdleTimer = window.setTimeout(() => {
        taskScrollSettledAt = 0;
        flushPendingTaskCardUpdates();
    }, 200);
    markScrollingState(taskModalContent);
}

function handleTaskDetailsToggle(detailsElement) {
    if (!detailsElement || !detailsElement.open) {
        return;
    }

    const taskId = detailsElement.dataset.taskId;
    if (!taskId) {
        return;
    }

    const task = state.tasks.find((item) => item.id === taskId);
    const content = detailsElement.querySelector(".task-detail-content");
    if (!task || !content || content.dataset.loaded === "true") {
        return;
    }

    content.innerHTML = renderTaskDetailContent(task);
    content.dataset.loaded = "true";
}

function renderTasks() {
    if (!elements.taskList) {
        return;
    }

    const allFilteredTasks = getAllFilteredTasks();
    const filteredTasks = getVisibleTasks();
    const currentConversation = state.taskFilterConversationId
        ? getConversationById(state.taskFilterConversationId)
        : null;
    const pageSourceTasks = allFilteredTasks.slice(0, TASK_RENDER_LIMIT);
    const totalPages = Math.max(1, Math.ceil(pageSourceTasks.length / TASKS_PER_PAGE));
    const filterBanner = state.taskFilterConversationId
        ? `
            <div class="task-filter-banner">
                <div>
                    <div class="task-filter-title">当前筛选：${escapeHtml(getConversationTag(state.taskFilterConversationId))}</div>
                    <div class="task-filter-detail">${escapeHtml(currentConversation ? currentConversation.title : "该对话")}</div>
                </div>
                <button class="task-filter-clear" onclick="clearTaskFilter()">查看全部</button>
            </div>
        `
        : "";
    const listNote = allFilteredTasks.length > TASK_RENDER_LIMIT
        ? `<div class="task-list-note">仅显示最近 ${TASK_RENDER_LIMIT} 条任务，减少滚动卡顿。</div>`
        : "";
    const pagination = pageSourceTasks.length > TASKS_PER_PAGE
        ? `
            <div class="task-pagination">
                <button class="task-page-btn" ${state.taskPage <= 1 ? "disabled" : ""} onclick="changeTaskPage(-1)">上一页</button>
                <span class="task-page-info">第 ${state.taskPage} / ${totalPages} 页，共 ${pageSourceTasks.length} 条</span>
                <button class="task-page-btn" ${state.taskPage >= totalPages ? "disabled" : ""} onclick="changeTaskPage(1)">下一页</button>
            </div>
        `
        : "";

    if (filteredTasks.length === 0) {
        elements.taskList.innerHTML = `
            ${filterBanner}
            ${listNote}
            ${pagination}
            <div class="task-empty-state">
                <div class="task-empty-title">暂无任务</div>
                <div class="task-empty-description">${state.taskFilterConversationId ? "这个对话暂时还没有后台任务。" : "后台任务提交后，会在这里显示执行状态和结果摘要。"}</div>
            </div>
        `;
        return;
    }

    elements.taskList.innerHTML = `${filterBanner}${listNote}${pagination}${filteredTasks.map((task) => renderTaskCard(task)).join("")}${pagination}`;
}

async function refreshTaskCenter() {
    await loadTasks();
    renderTasks();
}

async function openTaskCenter(conversationId = null) {
    state.taskFilterConversationId = conversationId;
    state.taskPage = 1;
    pendingTaskCardUpdates.clear();
    taskScrollSettledAt = 0;
    await refreshTaskCenter();
    elements.tasksModal.classList.add("active");
}

async function openTaskCenterForConversation(conversationId) {
    await loadConversation(conversationId);
    await openTaskCenter(conversationId);
}

function clearTaskFilter() {
    state.taskFilterConversationId = null;
    state.taskPage = 1;
    renderTasks();
}

function changeTaskPage(delta) {
    const allFilteredTasks = getAllFilteredTasks().slice(0, TASK_RENDER_LIMIT);
    const totalPages = Math.max(1, Math.ceil(allFilteredTasks.length / TASKS_PER_PAGE));
    const nextPage = Math.min(totalPages, Math.max(1, state.taskPage + delta));
    if (nextPage === state.taskPage) {
        return;
    }
    state.taskPage = nextPage;
    renderTasks();
}

function getVisibleMachines() {
    const machines = state.machines.slice(0, MACHINE_RENDER_LIMIT);
    const totalPages = Math.max(1, Math.ceil(machines.length / MACHINES_PER_PAGE));
    if (state.machinePage > totalPages) {
        state.machinePage = totalPages;
    }
    if (state.machinePage < 1) {
        state.machinePage = 1;
    }
    const startIndex = (state.machinePage - 1) * MACHINES_PER_PAGE;
    return machines.slice(startIndex, startIndex + MACHINES_PER_PAGE);
}

function changeMachinePage(delta) {
    const machines = state.machines.slice(0, MACHINE_RENDER_LIMIT);
    const totalPages = Math.max(1, Math.ceil(machines.length / MACHINES_PER_PAGE));
    const nextPage = Math.min(totalPages, Math.max(1, state.machinePage + delta));
    if (nextPage === state.machinePage) {
        return;
    }
    state.machinePage = nextPage;
    renderMachines();
}

async function deleteTask(taskId) {
    const task = state.tasks.find((item) => item.id === taskId);
    if (!task) {
        return;
    }
    if (!canDeleteTask(task)) {
        alert("执行中的任务暂不支持删除。");
        return;
    }
    if (!window.confirm("确定要删除这条后台任务吗？")) {
        return;
    }

    try {
        const response = await fetch(`/api/task/${encodeURIComponent(taskId)}`, { method: "DELETE" });
        if (!response.ok) {
            throw new Error(await readResponseError(response, "删除任务失败"));
        }
        state.tasks = state.tasks.filter((item) => item.id !== taskId);
        renderTasks();
        updateTasksBadge();
    } catch (error) {
        console.error("Failed to delete task:", error);
        alert(error instanceof Error ? error.message : "删除任务失败");
    }
}

async function deleteMachine(machineId) {
    const machine = state.machines.find((item) => item.id === machineId);
    if (!machine) {
        return;
    }
    if (!canDeleteMachine(machine)) {
        alert("在线机器或仍有运行中任务的机器暂不支持删除。");
        return;
    }
    if (!window.confirm("确定要删除这台离线机器记录吗？")) {
        return;
    }

    try {
        const response = await fetch(`/api/machines/${encodeURIComponent(machineId)}`, { method: "DELETE" });
        if (!response.ok) {
            throw new Error(await readResponseError(response, "删除机器失败"));
        }
        state.machines = state.machines.filter((item) => item.id !== machineId);
        state.machinesLoadedAt = Date.now();
        renderMachines();
    } catch (error) {
        console.error("Failed to delete machine:", error);
        alert(error instanceof Error ? error.message : "删除机器失败");
    }
}

function renderToolCard(tool) {
    const onlineMachineIds = Array.isArray(tool.onlineMachineIds) ? tool.onlineMachineIds : [];
    const onlineMachineCount = Number.isFinite(tool.onlineMachineCount) ? tool.onlineMachineCount : onlineMachineIds.length;
    const visibleTags = tool.tags.slice(0, TOOL_TAG_PREVIEW_COUNT);
    const extraTagCount = Math.max(0, tool.tags.length - visibleTags.length);
    const visibleMachineIds = onlineMachineIds.slice(0, TOOL_MACHINE_PREVIEW_COUNT);
    const hiddenMachineCount = Math.max(0, onlineMachineIds.length - visibleMachineIds.length);
    const tagSummary = visibleTags.length > 0
        ? `${visibleTags.join(" / ")}${extraTagCount > 0 ? ` / +${extraTagCount}` : ""}`
        : "暂无标签";
    const machineDetail = onlineMachineIds.length > 0
        ? `${visibleMachineIds.join(" / ")}${hiddenMachineCount > 0 ? ` / +${hiddenMachineCount}` : ""}`
        : "等待 Agent 上线";
    const machineSummary = onlineMachineCount > 0
        ? `在线机器 ${onlineMachineCount} 台`
        : "暂无在线机器";
    const profile = tool.toolProfile && typeof tool.toolProfile === "object" ? tool.toolProfile : {};
    const solves = Array.isArray(profile.solves) ? profile.solves.slice(0, 2) : [];
    const prerequisites = Array.isArray(profile.prerequisites) ? profile.prerequisites.slice(0, 2) : [];
    const failures = Array.isArray(profile.common_failure_reasons) ? profile.common_failure_reasons.slice(0, 2) : [];
    const riskLine = [
        profile.supports_batch ? "支持批量" : "单次优先",
        profile.has_side_effects ? "有副作用" : "无副作用",
        profile.requires_confirmation ? "需确认" : "无需额外确认"
    ].join(" · ");

    return `
        <div class="tool-card">
            <div class="tool-header">
                <div class="tool-icon">${icons[tool.icon] || icons.chat}</div>
                <div class="tool-info">
                    <div class="tool-name">${escapeHtml(tool.name)}</div>
                    <div class="tool-description">${escapeHtml(tool.description)}</div>
                </div>
            </div>
            <div class="tool-summary-line">插件 ID：${escapeHtml(tool.id)}</div>
            <div class="tool-summary-line">标签：${escapeHtml(tagSummary)}</div>
            <div class="tool-summary-line">${escapeHtml(machineSummary)} · ${escapeHtml(machineDetail)}</div>
            ${solves.length > 0 ? `<div class="tool-summary-line">解决问题：${escapeHtml(solves.join("；"))}</div>` : ""}
            ${prerequisites.length > 0 ? `<div class="tool-summary-line">前置条件：${escapeHtml(prerequisites.join("；"))}</div>` : ""}
            ${failures.length > 0 ? `<div class="tool-summary-line">常见失败：${escapeHtml(failures.join("；"))}</div>` : ""}
            <div class="tool-summary-line">${escapeHtml(riskLine)}</div>
        </div>
    `;
}

function renderMachineCard(machine) {
    const statusClass = getMachineStatusClass(machine.status);
    const statusLabel = formatMachineStatus(machine.status);
    const supportedRpas = Array.isArray(machine.rpas) ? machine.rpas : [];
    const visibleRpas = supportedRpas.slice(0, MACHINE_RPA_PREVIEW_COUNT);
    const hiddenRpaCount = Math.max(0, supportedRpas.length - visibleRpas.length);
    const runningTaskCount = Number.isFinite(machine.running_task_count) ? machine.running_task_count : 0;
    const rpaSummary = supportedRpas.length > 0
        ? `${visibleRpas.join(" / ")}${hiddenRpaCount > 0 ? ` / +${hiddenRpaCount}` : ""}`
        : "暂无已注册插件";
    const deleteDisabled = !canDeleteMachine(machine);
    const deleteTitle = deleteDisabled ? "在线机器或仍有运行中任务的机器暂不支持删除" : "删除机器";
    const deleteAction = isAdminUser()
        ? `
                    <button
                        class="card-action-btn danger"
                        type="button"
                        onclick="deleteMachine('${escapeHtml(machine.id || "")}')"
                        ${deleteDisabled ? "disabled" : ""}
                        title="${escapeHtml(deleteTitle)}"
                    >
                        ${icons.trash}
                        <span>删除</span>
                    </button>
                `
        : "";

    return `
        <div class="machine-card ${statusClass}">
            <div class="machine-card-header">
                <div>
                    <div class="machine-card-title">${escapeHtml(machine.id || "unknown-machine")}</div>
                    <div class="machine-card-subtitle">最后心跳：${escapeHtml(formatLastHeartbeat(machine.last_heartbeat))}</div>
                </div>
                <div class="card-header-actions">
                    <span class="machine-status ${statusClass}">${escapeHtml(statusLabel)}</span>
                    ${deleteAction}
                </div>
            </div>
            <div class="machine-card-meta">
                <span class="machine-meta-text">运行中任务 ${runningTaskCount} 个</span>
                <span class="machine-meta-dot">·</span>
                <span class="machine-meta-text">支持插件 ${supportedRpas.length} 个</span>
            </div>
            <div class="machine-rpa-summary">插件：${escapeHtml(rpaSummary)}</div>
        </div>
    `;
}

function renderMachines() {
    if (!elements.machineList) {
        return;
    }

    if (state.machines.length === 0) {
        const emptyDescription = isAdminUser()
            ? "请确认 Agent 已启动并成功连接到 Registry。"
            : "当前账号尚未分配可用插件，或暂无支持这些插件的在线机器。";
        elements.machineList.innerHTML = `
            <div class="tool-loading-state">
                <div class="tool-loading-title">暂无在线机器</div>
                <div class="tool-loading-description">${escapeHtml(emptyDescription)}</div>
            </div>
        `;
        return;
    }

    const onlineCount = state.machines.filter((machine) => machine.status === "online").length;
    const pageSourceMachines = state.machines.slice(0, MACHINE_RENDER_LIMIT);
    const visibleMachines = getVisibleMachines();
    const totalPages = Math.max(1, Math.ceil(pageSourceMachines.length / MACHINES_PER_PAGE));
    const listNote = state.machines.length > MACHINE_RENDER_LIMIT
        ? `<div class="task-list-note">仅显示最近 ${MACHINE_RENDER_LIMIT} 台机器，减少面板滚动卡顿。</div>`
        : "";
    const pagination = pageSourceMachines.length > MACHINES_PER_PAGE
        ? `
            <div class="task-pagination">
                <button class="task-page-btn" ${state.machinePage <= 1 ? "disabled" : ""} onclick="changeMachinePage(-1)">上一页</button>
                <span class="task-page-info">第 ${state.machinePage} / ${totalPages} 页，共 ${pageSourceMachines.length} 台</span>
                <button class="task-page-btn" ${state.machinePage >= totalPages ? "disabled" : ""} onclick="changeMachinePage(1)">下一页</button>
            </div>
        `
        : "";
    elements.machineList.innerHTML = `
        <div class="tool-list-summary">共 ${state.machines.length} 台机器，在线 ${onlineCount} 台</div>
        ${listNote}
        ${pagination}
        <div class="machine-list-body">
            ${visibleMachines.map((machine) => renderMachineCard(machine)).join("")}
        </div>
        ${pagination}
    `;
}

function showToolsLoadingState() {
    elements.toolList.innerHTML = `
        <div class="tool-loading-state">
            <div class="tool-loading-title">正在加载可用工具...</div>
            <div class="tool-loading-description">工具较多时会分批渲染，页面会先打开，不会整块卡住。</div>
        </div>
    `;
}

function renderTools() {
    toolRenderToken += 1;

    if (state.tools.length === 0) {
        elements.toolList.innerHTML = `
            <div class="tool-loading-state">
                <div class="tool-loading-title">暂无可用工具</div>
                <div class="tool-loading-description">请确认 Registry 和 Agent 已启动，并且新插件已完成注册。</div>
            </div>
        `;
        return;
    }

    const tools = [...state.tools].sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
    const visibleTools = tools.slice(0, visibleToolCount);
    const hasMore = visibleTools.length < tools.length;
    elements.toolList.innerHTML = `
        <div class="tool-list-summary">共 ${tools.length} 个可用工具</div>
        <div class="tool-list-body">${visibleTools.map((tool) => renderToolCard(tool)).join("")}</div>
        ${hasMore
            ? `<div class="tool-list-actions"><button class="secondary-action-btn tool-load-more-btn" onclick="loadMoreTools()">继续加载 ${Math.min(TOOL_RENDER_CHUNK, tools.length - visibleTools.length)} 个工具</button></div>`
            : ""}
    `;
}

function loadMoreTools() {
    visibleToolCount += TOOL_RENDER_CHUNK;
    renderTools();
}

function updateThemeUI(theme) {
    elements.sunIcon.style.display = theme === "dark" ? "none" : "block";
    elements.moonIcon.style.display = theme === "dark" ? "block" : "none";
    elements.themeToggleBtn.querySelector("span").textContent = theme === "dark" ? "切换浅色主题" : "切换深色主题";
}

function toggleTheme() {
    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    const nextTheme = isDark ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", nextTheme);
    localStorage.setItem("rpa_theme", nextTheme);
    updateThemeUI(nextTheme);
}

function initTheme() {
    const theme = localStorage.getItem("rpa_theme") || "light";
    document.documentElement.setAttribute("data-theme", theme);
    updateThemeUI(theme);
}

async function initializeWorkspace() {
    await loadConversations();
    await loadTasks();
    await loadCommonTasks();
    await updateProviderInfo();

    if (state.conversations.length === 0) {
        await createNewConversation();
    } else {
        state.currentConversationId = state.conversations[0].id;
        await loadConversation(state.currentConversationId);
    }

    loadTools().catch((error) => {
        console.warn("Failed to prefetch tools:", error);
    });
    loadMachines().catch((error) => {
        console.warn("Failed to prefetch machines:", error);
    });
    loadSchedules().catch((error) => {
        console.warn("Failed to prefetch schedules:", error);
    });
    loadScheduleRuns().catch((error) => {
        console.warn("Failed to prefetch schedule runs:", error);
    });
    if (isAdminUser()) {
        loadAdminData().catch((error) => {
            console.warn("Failed to prefetch admin data:", error);
        });
    }
}

async function init() {
    initTheme();
    updateUserPanel();
    if (elements.scheduleTimezone) {
        elements.scheduleTimezone.value = getBrowserTimezone();
    }
    const hasSession = await restoreAuthSession();
    if (!hasSession) {
        showAuthScreen();
        return;
    }
    await initializeWorkspace();
}

elements.sendBtn.onclick = sendMessage;
elements.messageInput.onkeydown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
};
elements.messageInput.oninput = () => {
    elements.messageInput.style.height = "auto";
    elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 200)}px`;
};
elements.newChatBtn.onclick = createNewConversation;
elements.machinesBtn.onclick = async () => {
    elements.machinesModal.classList.add("active");
    state.machinePage = 1;
    if (machineModalContent) {
        machineModalContent.classList.remove("is-scrolling");
    }
    renderMachines();
    await loadMachines();
    renderMachines();
};
elements.refreshMachinesBtn.onclick = refreshMachines;
elements.closeMachinesModal.onclick = () => {
    if (machineModalContent) {
        machineModalContent.classList.remove("is-scrolling");
    }
    elements.machinesModal.classList.remove("active");
};
elements.machinesModal.onclick = (event) => {
    if (event.target === elements.machinesModal) {
        if (machineModalContent) {
            machineModalContent.classList.remove("is-scrolling");
        }
        elements.machinesModal.classList.remove("active");
    }
};
elements.tasksBtn.onclick = async () => openTaskCenter(null);
elements.refreshTasksBtn.onclick = refreshTaskCenter;
elements.closeTasksModal.onclick = () => {
    elements.tasksModal.classList.remove("active");
    state.taskFilterConversationId = null;
    pendingTaskCardUpdates.clear();
    taskScrollSettledAt = 0;
    if (taskModalContent) {
        taskModalContent.classList.remove("is-scrolling");
    }
};
elements.tasksModal.onclick = (event) => {
    if (event.target === elements.tasksModal) {
        elements.tasksModal.classList.remove("active");
        state.taskFilterConversationId = null;
        pendingTaskCardUpdates.clear();
        taskScrollSettledAt = 0;
        if (taskModalContent) {
            taskModalContent.classList.remove("is-scrolling");
        }
    }
};
elements.scheduleBtn.onclick = openSchedulesModal;
elements.refreshSchedulesBtn.onclick = refreshSchedules;
elements.closeSchedulesModal.onclick = () => {
    elements.schedulesModal.classList.remove("active");
    resetScheduleForm();
};
elements.schedulesModal.onclick = (event) => {
    if (event.target === elements.schedulesModal) {
        elements.schedulesModal.classList.remove("active");
        resetScheduleForm();
    }
};
elements.toolsBtn.onclick = async () => {
    elements.toolsModal.classList.add("active");
    visibleToolCount = TOOL_RENDER_CHUNK;
    if (toolsModalContent) {
        toolsModalContent.classList.remove("is-scrolling");
    }
    if (state.tools.length > 0) {
        renderTools();
    } else {
        showToolsLoadingState();
    }
    await loadTools();
    renderTools();
};
elements.refreshToolsBtn.onclick = refreshTools;
elements.closeToolsModal.onclick = () => {
    toolRenderToken += 1;
    visibleToolCount = TOOL_RENDER_CHUNK;
    if (toolsModalContent) {
        toolsModalContent.classList.remove("is-scrolling");
    }
    elements.toolsModal.classList.remove("active");
};
elements.toolsModal.onclick = (event) => {
    if (event.target === elements.toolsModal) {
        toolRenderToken += 1;
        visibleToolCount = TOOL_RENDER_CHUNK;
        if (toolsModalContent) {
            toolsModalContent.classList.remove("is-scrolling");
        }
        elements.toolsModal.classList.remove("active");
    }
};
elements.exportBtn.onclick = openExportsModal;
elements.refreshExportsBtn.onclick = renderExportCenter;
elements.closeExportsModal.onclick = () => {
    elements.exportsModal.classList.remove("active");
};
elements.exportsModal.onclick = (event) => {
    if (event.target === elements.exportsModal) {
        elements.exportsModal.classList.remove("active");
    }
};
elements.attachBtn.onclick = () => elements.fileInput.click();
elements.fileInput.onchange = handleFileUpload;
elements.themeToggleBtn.onclick = toggleTheme;
elements.loginForm.onsubmit = handleLogin;
elements.logoutBtn.onclick = handleLogout;
elements.adminBtn.onclick = openAdminModal;
elements.refreshAdminBtn.onclick = refreshAdminData;
elements.closeAdminModal.onclick = () => {
    elements.adminModal.classList.remove("active");
};
elements.adminModal.onclick = (event) => {
    if (event.target === elements.adminModal) {
        elements.adminModal.classList.remove("active");
    }
};
elements.createUserForm.onsubmit = createBusinessUser;
elements.pluginScaffoldForm.onsubmit = scaffoldPluginFromAdmin;
elements.scheduleForm.onsubmit = saveSchedule;
elements.resetScheduleFormBtn.onclick = resetScheduleForm;
elements.scheduleType.onchange = syncScheduleTypeFields;
elements.scheduleCommonTask.onchange = handleScheduleCommonTaskChange;

window.loadConversation = loadConversation;
window.deleteConversation = deleteConversation;
window.removeFile = removeFile;
window.openTaskCenterForConversation = openTaskCenterForConversation;
window.clearTaskFilter = clearTaskFilter;
window.changeTaskPage = changeTaskPage;
window.changeMachinePage = changeMachinePage;
window.handleTaskDetailsToggle = handleTaskDetailsToggle;
window.deleteTask = deleteTask;
window.deleteMachine = deleteMachine;
window.saveAdminPermissions = saveAdminPermissions;
window.loadMoreTools = loadMoreTools;
window.saveReusableTaskSuggestion = saveReusableTaskSuggestion;
window.useCommonTask = useCommonTask;
window.deleteCommonTask = deleteCommonTask;
window.populateScheduleForm = populateScheduleForm;
window.toggleScheduleActive = toggleScheduleActive;
window.deleteSchedule = deleteSchedule;
window.downloadExport = downloadExport;

const machineModalContent = elements.machinesModal ? elements.machinesModal.querySelector(".modal-content") : null;
const taskModalContent = elements.tasksModal ? elements.tasksModal.querySelector(".modal-content") : null;
if (taskModalContent) {
    taskModalContent.addEventListener("scroll", markTaskScrollActivity, { passive: true });
}
const toolsModalContent = elements.toolsModal ? elements.toolsModal.querySelector(".modal-content") : null;

init();
