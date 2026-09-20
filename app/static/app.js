const state = {
  groups: [],
  activeGroupId: null,
  conversations: [],
  activeConversationId: null,
  activeView: "channel",
  agents: [],
  members: [],
  lastMessageId: 0,
  pollTimer: null,
  pollGeneration: 0,
  pollInFlight: false,
  editingAgentId: null,
  groupSearchTerm: "",
  editingGroupId: null,
};

const AGENT_HUES = [200, 280, 340, 130, 20, 245, 165, 305];

const GROUP_ICON_OPTIONS = ["💬", "📊", "📁", "🧑", "📚", "🎨", "💰", "⚙️", "🧪", "🚀", "📈", "🗑️"];

function renderGroupIconGrid(selectedIcon) {
  const grid = document.getElementById("group-icon-grid");
  grid.innerHTML = "";
  for (const icon of GROUP_ICON_OPTIONS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "icon-grid-option" + (icon === selectedIcon ? " selected" : "");
    btn.textContent = icon;
    btn.setAttribute("aria-label", `Usar ícone ${icon}`);
    btn.onclick = () => {
      grid.dataset.selected = icon;
      for (const other of grid.querySelectorAll(".icon-grid-option")) {
        other.classList.toggle("selected", other === btn);
      }
    };
    grid.appendChild(btn);
  }
  grid.dataset.selected = selectedIcon;
}

function openGroupForm({ groupId = null, name = "", icon = GROUP_ICON_OPTIONS[0] } = {}) {
  state.editingGroupId = groupId;
  document.getElementById("group-form-title").textContent = groupId ? "Renomear grupo" : "Novo grupo";
  document.getElementById("group-form-name").value = name;
  renderGroupIconGrid(icon);
  document.getElementById("group-form-backdrop").classList.remove("hidden");
  document.getElementById("group-form-name").focus();
}

function closeGroupForm() {
  document.getElementById("group-form-backdrop").classList.add("hidden");
  document.getElementById("group-form").reset();
  state.editingGroupId = null;
}

function hashHue(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return AGENT_HUES[hash % AGENT_HUES.length];
}

function agentColor(name) {
  return `oklch(0.72 0.13 ${hashHue(name)})`;
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  if (resp.status === 204) return null;
  return resp.json();
}

function showView(name) {
  state.activeView = name;
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("hidden", view.id !== `view-${name}`);
  }
  document.getElementById("nav-agents").classList.toggle("active", name === "agents");
  document.getElementById("nav-settings").classList.toggle("active", name === "settings");
  if (name !== "channel") {
    for (const li of document.querySelectorAll("#group-list li")) {
      li.classList.remove("active");
    }
  }
  closeSidebarOnMobile();
}

function closeSidebarOnMobile() {
  document.getElementById("groups-col").classList.remove("open");
  document.getElementById("sidebar-toggle").setAttribute("aria-expanded", "false");
}

async function loadGroups() {
  state.groups = await api("/api/groups");
  renderGroupList();
}

function renderGroupList() {
  const list = document.getElementById("group-list");
  list.innerHTML = "";

  const term = state.groupSearchTerm.trim().toLowerCase();
  const visible = term
    ? state.groups.filter((g) => g.name.toLowerCase().includes(term))
    : state.groups;

  if (state.groups.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.textContent = "Nenhum grupo ainda";
    list.appendChild(li);
    return;
  }

  if (visible.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.textContent = "Nenhum grupo encontrado";
    list.appendChild(li);
    return;
  }

  for (const group of visible) {
    const li = document.createElement("li");
    li.className = state.activeView === "channel" && group.id === state.activeGroupId ? "active" : "";
    li.onclick = () => selectGroup(group.id);

    const icon = document.createElement("span");
    icon.className = "group-icon";
    icon.textContent = group.icon;
    li.appendChild(icon);

    const name = document.createElement("span");
    name.textContent = group.name;
    li.appendChild(name);

    list.appendChild(li);
  }
}

async function selectGroup(groupId) {
  state.activeGroupId = groupId;
  const group = state.groups.find((g) => g.id === groupId);
  document.getElementById("channel-header-name").textContent = group ? `# ${group.name}` : "";
  document.getElementById("channel-header-icon").textContent = group ? group.icon : "";
  document.getElementById("channel-empty").classList.add("hidden");
  document.getElementById("channel-content").classList.remove("hidden");
  showView("channel");
  await loadGroups();
  await loadMembers(groupId);
  await loadConversations(groupId);
}

async function loadConversations(groupId) {
  state.conversations = await api(`/api/groups/${groupId}/conversations`);
  const stillActive = state.conversations.some((c) => c.id === state.activeConversationId);
  if (stillActive) {
    renderConversationList();
    renderConversationContext();
  } else {
    await selectConversation(state.conversations[0].id);
  }
}

function renderConversationContext() {
  const el = document.getElementById("context-conversation-info");
  el.innerHTML = "";
  const conversation = state.conversations.find((c) => c.id === state.activeConversationId);
  if (!conversation) return;

  const created = new Date(conversation.created_at.replace(" ", "T") + "Z");
  const formatted = created.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });

  const card = document.createElement("div");
  card.className = "context-card";

  const icon = document.createElement("span");
  icon.className = "context-card-icon";
  icon.textContent = "📄";
  card.appendChild(icon);

  const text = document.createElement("span");
  text.className = "context-card-text";
  const name = document.createElement("span");
  name.className = "context-card-name";
  name.textContent = conversation.name;
  const date = document.createElement("span");
  date.className = "context-card-date";
  date.textContent = `criada em ${formatted}`;
  text.appendChild(name);
  text.appendChild(date);
  card.appendChild(text);

  const chevron = document.createElement("span");
  chevron.className = "context-card-chevron";
  chevron.textContent = "›";
  card.appendChild(chevron);

  el.appendChild(card);
}

function renderConversationList() {
  const list = document.getElementById("conversation-list");
  list.innerHTML = "";

  for (const conversation of state.conversations) {
    const card = document.createElement("li");
    card.className = "conversation-card" + (conversation.id === state.activeConversationId ? " active" : "");
    card.onclick = () => selectConversation(conversation.id);

    const icon = document.createElement("span");
    icon.className = "conversation-card-icon";
    icon.textContent = "💬";
    card.appendChild(icon);

    const name = document.createElement("span");
    name.className = "conversation-card-name";
    name.textContent = conversation.name;
    card.appendChild(name);

    const actions = document.createElement("span");
    actions.className = "conversation-card-actions";

    const renameBtn = document.createElement("span");
    renameBtn.className = "conversation-card-rename";
    renameBtn.textContent = "✎";
    renameBtn.tabIndex = 0;
    renameBtn.setAttribute("role", "button");
    renameBtn.setAttribute("aria-label", `Renomear conversa ${conversation.name}`);
    const renameConversation = async (e) => {
      e.stopPropagation();
      const newName = prompt("Novo nome da conversa:", conversation.name);
      if (!newName || !newName.trim() || newName.trim() === conversation.name) return;
      const updated = await api(
        `/api/groups/${state.activeGroupId}/conversations/${conversation.id}`,
        { method: "PUT", body: JSON.stringify({ name: newName.trim() }) }
      );
      const target = state.conversations.find((c) => c.id === conversation.id);
      if (target) target.name = updated.name;
      renderConversationList();
    };
    renameBtn.onclick = renameConversation;
    renameBtn.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        renameConversation(e);
      }
    };
    actions.appendChild(renameBtn);

    const closeBtn = document.createElement("span");
    closeBtn.className = "conversation-card-delete";
    closeBtn.textContent = "×";
    closeBtn.tabIndex = 0;
    closeBtn.setAttribute("role", "button");
    closeBtn.setAttribute("aria-label", `Apagar conversa ${conversation.name}`);
    const deleteConversation = async (e) => {
      e.stopPropagation();
      if (!confirm(`Apagar a conversa "${conversation.name}"? As mensagens dela serão perdidas permanentemente.`)) return;
      const remaining = await api(
        `/api/groups/${state.activeGroupId}/conversations/${conversation.id}`,
        { method: "DELETE" }
      );
      state.conversations = remaining;
      if (state.activeConversationId === conversation.id) {
        await selectConversation(remaining[0].id);
      } else {
        renderConversationList();
      }
    };
    closeBtn.onclick = deleteConversation;
    closeBtn.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        deleteConversation(e);
      }
    };
    actions.appendChild(closeBtn);

    card.appendChild(actions);
    list.appendChild(card);
  }
}

async function selectConversation(conversationId) {
  state.activeConversationId = conversationId;
  state.lastMessageId = 0;
  // Bump the generation and release any in-flight-poll lock held by the previous
  // conversation, so a stale response from a poll started before the switch can't
  // land on top of this one (see pollMessages' generation check) and a fresh poll
  // for the new conversation isn't blocked by that stale in-flight request.
  state.pollGeneration += 1;
  state.pollInFlight = false;
  document.getElementById("message-list").innerHTML = "";
  document.getElementById("queue-indicator").textContent = "";
  document.getElementById("stop-queue-btn").classList.add("hidden");
  renderConversationList();
  renderConversationContext();
  await refreshHiddenMessages();
  await pollMessages();
  await pollPendingStatus();
}

async function loadMembers(groupId) {
  if (state.agents.length === 0) {
    await loadAgents();
  }
  state.members = await api(`/api/groups/${groupId}/members`);
  renderMembers(groupId);
}

function renderMembers(groupId) {
  const list = document.getElementById("member-list");
  list.innerHTML = "";
  for (const member of state.members) {
    const badge = document.createElement("span");
    badge.className = "member-badge";
    const dot = document.createElement("span");
    dot.className = "member-dot";
    dot.style.background = agentColor(member.name);
    badge.appendChild(dot);
    const name = document.createElement("span");
    name.textContent = member.name;
    badge.appendChild(name);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", `Remover ${member.name} do grupo`);
    removeBtn.onclick = async () => {
      await api(`/api/groups/${groupId}/members/${member.id}`, { method: "DELETE" });
      await loadMembers(groupId);
    };
    badge.appendChild(removeBtn);
    list.appendChild(badge);
  }

  const select = document.getElementById("add-member-select");
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "+ adicionar agente";
  select.appendChild(placeholder);
  const memberIds = new Set(state.members.map((m) => m.id));
  for (const agent of state.agents) {
    if (memberIds.has(agent.id)) continue;
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = agent.name;
    select.appendChild(option);
  }
  select.value = "";
  select.onchange = async () => {
    if (!select.value) return;
    await api(`/api/groups/${groupId}/members`, {
      method: "POST",
      body: JSON.stringify({ agent_id: Number(select.value) }),
    });
    await loadMembers(groupId);
  };
}

async function refreshHiddenMessages() {
  if (!state.activeConversationId) return;
  const messages = await api(
    `/api/conversations/${state.activeConversationId}/messages?include_hidden=true`
  );
  const container = document.getElementById("hidden-messages-list");
  container.innerHTML = "";
  const hidden = messages.filter((m) => m.hidden);
  if (hidden.length === 0) {
    container.textContent = "Nenhuma.";
    return;
  }
  for (const message of hidden) {
    const item = document.createElement("div");
    item.className = "hidden-message-item";

    const preview = document.createElement("span");
    preview.className = "hidden-message-preview";
    const label = message.hidden_kind || "oculta";
    const text = message.content.length > 80 ? message.content.slice(0, 80) + "…" : message.content;
    preview.textContent = `[${label}] ${text}`;
    preview.title = message.content;
    item.appendChild(preview);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "message-delete-btn";
    deleteBtn.textContent = "🗑";
    deleteBtn.title = "Apagar mensagem";
    deleteBtn.onclick = async () => {
      if (!confirm("Apagar esta mensagem oculta? Ela some do contexto dos agentes permanentemente.")) return;
      await api(`/api/conversations/${state.activeConversationId}/messages/${message.id}`, { method: "DELETE" });
      refreshHiddenMessages();
    };
    item.appendChild(deleteBtn);

    container.appendChild(item);
  }
}

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderInlineMarkdown(escapedText) {
  // Runs on already-HTML-escaped text, so these substitutions only ever wrap the
  // escaped text in tags we control — no way for message content to inject markup.
  return escapedText
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
}

// Minimal, dependency-free markdown: headings, bold/italic/code, and lists — enough to make
// agent replies (which lean on **bold**, "## heading" and "- item" lists) readable without
// pulling in a full markdown library for a static, no-build-step frontend.
function formatMessageContent(content) {
  const lines = escapeHtml(content).split("\n");
  let html = "";
  let listType = null;
  const closeList = () => {
    if (listType) {
      html += `</${listType}>`;
      listType = null;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    // Deliberately don't close an open list on a blank line: LLM-generated markdown
    // commonly puts a blank line between numbered/bulleted items for readability, and
    // closing (then reopening) the list there would reset <ol> numbering back to 1.
    if (line === "") continue;

    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (heading) {
      closeList();
      html += `<p class="msg-heading">${renderInlineMarkdown(heading[1])}</p>`;
      continue;
    }

    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      if (listType !== "ul") {
        closeList();
        html += "<ul>";
        listType = "ul";
      }
      html += `<li>${renderInlineMarkdown(bullet[1])}</li>`;
      continue;
    }

    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    if (numbered) {
      if (listType !== "ol") {
        closeList();
        html += "<ol>";
        listType = "ol";
      }
      html += `<li>${renderInlineMarkdown(numbered[1])}</li>`;
      continue;
    }

    closeList();
    html += `<p>${renderInlineMarkdown(line)}</p>`;
  }
  closeList();
  return html;
}

function renderSearchChip(message) {
  const row = document.createElement("div");
  row.className = "message-row search-chip-row";

  const chip = document.createElement("div");
  chip.className = "search-chip";
  const firstLine = message.content.split("\n")[0] || "Pesquisou na internet";
  chip.textContent = `🔍 ${firstLine}`;
  chip.title = message.content;
  row.appendChild(chip);

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "message-delete-btn";
  deleteBtn.textContent = "🗑";
  deleteBtn.title = "Apagar mensagem";
  deleteBtn.onclick = async () => {
    if (!confirm("Apagar esta mensagem? Ela some da conversa e do contexto dos agentes permanentemente.")) return;
    await api(`/api/conversations/${message.conversation_id}/messages/${message.id}`, { method: "DELETE" });
    row.remove();
  };
  row.appendChild(deleteBtn);

  document.getElementById("message-list").appendChild(row);
}

function renderMessage(message) {
  if (message.hidden_kind === "search_result") {
    renderSearchChip(message);
    return;
  }

  const row = document.createElement("div");
  row.className = `message-row ${message.sender_type}`;

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.innerHTML = formatMessageContent(message.content);

  if (message.sender_type === "agent") {
    const agent = state.agents.find((a) => a.id === message.sender_id);
    const name = agent ? agent.name : "agente";
    const color = agentColor(name);

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.style.background = color;
    avatar.textContent = name.slice(0, 1).toUpperCase();
    row.appendChild(avatar);

    const col = document.createElement("div");
    col.className = "message-col";
    const label = document.createElement("div");
    label.className = "message-sender";
    label.textContent = name;
    label.style.color = color;
    col.appendChild(label);
    col.appendChild(bubble);
    row.appendChild(col);
  } else {
    row.appendChild(bubble);
  }

  if (message.image_path) {
    const img = document.createElement("img");
    img.src = `/api/conversations/${message.conversation_id}/messages/${message.id}/image`;
    img.alt = "Imagem enviada no chat";
    bubble.appendChild(img);
  }

  if (message.pdf_path) {
    const link = document.createElement("a");
    link.href = `/api/conversations/${message.conversation_id}/messages/${message.id}/pdf`;
    link.textContent = "📄 PDF anexado";
    link.target = "_blank";
    bubble.appendChild(link);
  }

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "message-delete-btn";
  deleteBtn.textContent = "🗑";
  deleteBtn.title = "Apagar mensagem";
  deleteBtn.onclick = async () => {
    if (!confirm("Apagar esta mensagem? Ela some da conversa e do contexto dos agentes permanentemente.")) return;
    await api(`/api/conversations/${message.conversation_id}/messages/${message.id}`, { method: "DELETE" });
    row.remove();
  };
  bubble.appendChild(deleteBtn);

  document.getElementById("message-list").appendChild(row);
}

function updateMessageListEmptyState() {
  const list = document.getElementById("message-list");
  const hasMessages = list.querySelector(".message-row") !== null;
  let placeholder = document.getElementById("message-list-empty");
  if (!hasMessages) {
    if (!placeholder) {
      placeholder = document.createElement("div");
      placeholder.id = "message-list-empty";
      placeholder.className = "empty-state empty-state-inline";
      placeholder.textContent = "Nenhuma mensagem ainda. Escreva algo e mencione @nome-do-agente pra começar.";
      list.appendChild(placeholder);
    }
  } else if (placeholder) {
    placeholder.remove();
  }
}

async function pollMessages() {
  if (!state.activeConversationId) return;
  // Without this guard, the 2s polling timer and a conversation switch (or two
  // overlapping timer ticks, if a fetch is slow) can both be in flight for the
  // same conversation at once; both would fetch the same since_id and both would
  // append the same messages, duplicating them in the DOM.
  if (state.pollInFlight) return;
  state.pollInFlight = true;
  const generation = state.pollGeneration;
  const conversationId = state.activeConversationId;
  try {
    const messages = await api(
      `/api/conversations/${conversationId}/messages?since_id=${state.lastMessageId}`
    );
    // A newer selectConversation happened while this fetch was in flight — discard
    // this stale response instead of rendering another conversation's messages here.
    if (generation !== state.pollGeneration) return;
    for (const message of messages) {
      renderMessage(message);
      state.lastMessageId = message.id;
    }
    updateMessageListEmptyState();
    if (messages.length > 0) {
      document.getElementById("message-list").scrollTop = 1e9;
    }
  } finally {
    if (generation === state.pollGeneration) state.pollInFlight = false;
  }
}

async function pollPendingStatus() {
  if (!state.activeConversationId) return;
  const conversationId = state.activeConversationId;
  let pending;
  try {
    pending = await api(`/api/conversations/${conversationId}/messages/pending`);
  } catch (err) {
    console.error("Failed to load pending status:", err);
    return;
  }
  // The active conversation changed while this request was in flight — the indicator
  // for it has already been reset by selectConversation, so don't overwrite it here.
  if (conversationId !== state.activeConversationId) return;
  renderPendingIndicator(pending);
}

function renderPendingIndicator(pending) {
  const el = document.getElementById("queue-indicator");
  const stopBtn = document.getElementById("stop-queue-btn");
  if (!pending || pending.length === 0) {
    el.textContent = "";
    stopBtn.classList.add("hidden");
    return;
  }
  const labels = pending.map((job) =>
    job.job_type === "describe_image"
      ? "Analisando a imagem enviada…"
      : job.job_type === "extract_pdf"
        ? "Lendo o PDF enviado…"
        : `${job.agent_name || "agente"} está respondendo…`
  );
  el.textContent = [...new Set(labels)].join(" · ");
  stopBtn.classList.remove("hidden");
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(() => {
    pollMessages();
    pollPendingStatus();
  }, 2000);
}

async function loadAgents() {
  state.agents = await api("/api/agents");
  const list = document.getElementById("agent-list");
  list.innerHTML = "";

  if (state.agents.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.textContent = "Nenhum agente ainda — crie o primeiro abaixo.";
    list.appendChild(li);
    return;
  }

  for (const agent of state.agents) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "agent-avatar-dot";
    dot.style.background = agentColor(agent.name);
    li.appendChild(dot);
    const label = document.createElement("span");
    label.textContent = `${agent.name} (${agent.model_name}${agent.vision_capable ? ", visão" : ""})`;
    li.appendChild(label);
    li.onclick = () => startEditingAgent(agent);
    list.appendChild(li);
  }
}

function startEditingAgent(agent) {
  document.getElementById("agent-name").value = agent.name;
  document.getElementById("agent-persona").value = agent.persona_prompt;
  document.getElementById("agent-vision").checked = agent.vision_capable;
  loadModels(agent.model_name);
  state.editingAgentId = agent.id;
  document.getElementById("agent-submit-btn").textContent = "Atualizar agente";
  document.getElementById("cancel-edit-agent-btn").classList.remove("hidden");
  document.getElementById("delete-agent-btn").classList.remove("hidden");
}

function stopEditingAgent() {
  document.getElementById("agent-form").reset();
  state.editingAgentId = null;
  document.getElementById("agent-submit-btn").textContent = "Salvar agente";
  document.getElementById("cancel-edit-agent-btn").classList.add("hidden");
  document.getElementById("delete-agent-btn").classList.add("hidden");
  loadModels();
}

async function populateModelSelect(selectEl, currentValue, { allowEmpty, emptyLabel, onError } = {}) {
  try {
    const data = await api("/api/models");
    selectEl.innerHTML = "";
    selectEl.disabled = false;

    if (allowEmpty) {
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = emptyLabel;
      selectEl.appendChild(emptyOption);
    }

    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      selectEl.appendChild(option);
    }

    if (currentValue && !data.models.includes(currentValue)) {
      const orphanOption = document.createElement("option");
      orphanOption.value = currentValue;
      orphanOption.textContent = `${currentValue} (não encontrado no llama-swap)`;
      selectEl.appendChild(orphanOption);
    }

    selectEl.value = currentValue || "";
    return true;
  } catch (err) {
    console.error("Failed to load models:", err);
    selectEl.innerHTML = "";
    const errorOption = document.createElement("option");
    errorOption.value = currentValue || "";
    errorOption.textContent = "Erro ao carregar modelos (verifique o llama-swap)";
    errorOption.disabled = true;
    errorOption.selected = true;
    selectEl.appendChild(errorOption);
    selectEl.disabled = true;
    if (onError) onError(err);
    return false;
  }
}

async function loadModels(currentValue = "") {
  const modelSelect = document.getElementById("agent-model");
  const submitButton = document.querySelector("#agent-form button[type=submit]");
  const ok = await populateModelSelect(modelSelect, currentValue, {
    allowEmpty: true,
    emptyLabel: "selecione um modelo",
    onError: () => {
      submitButton.disabled = true;
    },
  });
  if (ok) submitButton.disabled = false;
}

async function loadSettings() {
  const settings = await api("/api/settings");
  document.getElementById("setting-base-url").value = settings.llama_swap_base_url;
  document.getElementById("setting-max-pending").value = settings.max_pending_per_group;

  // Sem onError: ao contrário do formulário de agente, falha ao buscar modelos
  // aqui não deve travar o botão "Salvar" de Configurações (o usuário pode estar
  // justamente tentando corrigir a URL do llama-swap neste mesmo formulário).
  await populateModelSelect(
    document.getElementById("setting-vision-model"),
    settings.default_vision_model,
    { allowEmpty: true, emptyLabel: "nenhum (não configurado)" }
  );
  await populateModelSelect(
    document.getElementById("setting-assistant-model"),
    settings.assistant_model,
    { allowEmpty: true, emptyLabel: "nenhum (não configurado)" }
  );
}

function wireColumnToggle(collapseBtnId, mobileToggleBtnId, colId) {
  const col = document.getElementById(colId);
  const collapseBtn = document.getElementById(collapseBtnId);
  if (collapseBtn) {
    collapseBtn.onclick = () => {
      const collapsed = !col.classList.contains("collapsed");
      col.classList.toggle("collapsed", collapsed);
      collapseBtn.setAttribute("aria-expanded", String(!collapsed));
    };
  }
  if (mobileToggleBtnId) {
    const mobileBtn = document.getElementById(mobileToggleBtnId);
    mobileBtn.onclick = () => {
      const opening = !col.classList.contains("open");
      col.classList.toggle("open", opening);
      mobileBtn.setAttribute("aria-expanded", String(opening));
    };
  }
}

wireColumnToggle("groups-collapse-btn", null, "groups-col");
wireColumnToggle("conversations-collapse-btn", "conversations-toggle-btn", "conversations-col");
wireColumnToggle("context-collapse-btn", "context-toggle-btn", "context-panel");

document.getElementById("sidebar-toggle").onclick = () => {
  const sidebar = document.getElementById("groups-col");
  const opening = !sidebar.classList.contains("open");
  sidebar.classList.toggle("open", opening);
  document.getElementById("sidebar-toggle").setAttribute("aria-expanded", String(opening));
};

document.getElementById("group-search").addEventListener("input", (e) => {
  state.groupSearchTerm = e.target.value;
  renderGroupList();
});

document.getElementById("nav-agents").onclick = async () => {
  showView("agents");
  await loadAgents();
  await loadModels();
};

document.getElementById("nav-settings").onclick = async () => {
  showView("settings");
  await loadSettings();
};

document.getElementById("new-group-btn").onclick = () => openGroupForm();

document.getElementById("group-form-cancel").onclick = () => closeGroupForm();

document.getElementById("group-form-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "group-form-backdrop") closeGroupForm();
});

document.getElementById("group-form").onsubmit = async (e) => {
  e.preventDefault();
  const name = document.getElementById("group-form-name").value.trim();
  if (!name) return;
  const icon = document.getElementById("group-icon-grid").dataset.selected || GROUP_ICON_OPTIONS[0];

  if (state.editingGroupId) {
    await api(`/api/groups/${state.editingGroupId}`, {
      method: "PUT",
      body: JSON.stringify({ name, icon }),
    });
  } else {
    await api("/api/groups", { method: "POST", body: JSON.stringify({ name, icon }) });
  }
  closeGroupForm();
  await loadGroups();
  if (state.activeGroupId) {
    const updated = state.groups.find((g) => g.id === state.activeGroupId);
    document.getElementById("channel-header-name").textContent = updated ? `# ${updated.name}` : "";
    document.getElementById("channel-header-icon").textContent = updated ? updated.icon : "";
  }
};

document.getElementById("agent-form").onsubmit = async (e) => {
  e.preventDefault();
  const payload = {
    name: document.getElementById("agent-name").value,
    persona_prompt: document.getElementById("agent-persona").value,
    model_name: document.getElementById("agent-model").value,
    vision_capable: document.getElementById("agent-vision").checked,
  };
  if (state.editingAgentId) {
    await api(`/api/agents/${state.editingAgentId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  } else {
    await api("/api/agents", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }
  stopEditingAgent();
  await loadAgents();
};

document.getElementById("cancel-edit-agent-btn").onclick = () => {
  stopEditingAgent();
};

document.getElementById("delete-agent-btn").onclick = async () => {
  if (!state.editingAgentId) return;
  const name = document.getElementById("agent-name").value;
  if (!confirm(`Apagar o agente "${name}"?`)) return;
  await api(`/api/agents/${state.editingAgentId}`, { method: "DELETE" });
  stopEditingAgent();
  await loadAgents();
};

document.getElementById("rename-group-btn").onclick = () => {
  if (!state.activeGroupId) return;
  const group = state.groups.find((g) => g.id === state.activeGroupId);
  if (!group) return;
  openGroupForm({ groupId: group.id, name: group.name, icon: group.icon });
};

document.getElementById("new-conversation-btn").onclick = async () => {
  if (!state.activeGroupId) return;
  const name = prompt("Nome da nova conversa:");
  if (!name || !name.trim()) return;
  const conversation = await api(`/api/groups/${state.activeGroupId}/conversations`, {
    method: "POST",
    body: JSON.stringify({ name: name.trim() }),
  });
  state.conversations.push(conversation);
  await selectConversation(conversation.id);
};

document.getElementById("stop-queue-btn").onclick = async () => {
  if (!state.activeGroupId || !state.activeConversationId) return;
  const btn = document.getElementById("stop-queue-btn");
  btn.disabled = true;
  try {
    await api(
      `/api/groups/${state.activeGroupId}/conversations/${state.activeConversationId}/stop`,
      { method: "POST" }
    );
    await pollMessages();
    await pollPendingStatus();
  } finally {
    btn.disabled = false;
  }
};

document.getElementById("delete-group-btn").onclick = async () => {
  if (!state.activeGroupId) return;
  const group = state.groups.find((g) => g.id === state.activeGroupId);
  const name = group ? group.name : "";
  if (!confirm(`Apagar o grupo "${name}"? Todo o histórico de mensagens será perdido permanentemente.`)) return;
  await api(`/api/groups/${state.activeGroupId}`, { method: "DELETE" });
  state.activeGroupId = null;
  state.activeConversationId = null;
  state.conversations = [];
  document.getElementById("queue-indicator").textContent = "";
  document.getElementById("stop-queue-btn").classList.add("hidden");
  document.getElementById("channel-content").classList.add("hidden");
  document.getElementById("channel-empty").classList.remove("hidden");
  await loadGroups();
};

const personaTextarea = document.getElementById("agent-persona");
const generateBtn = document.getElementById("generate-persona-btn");
const generateError = document.getElementById("generate-persona-error");

personaTextarea.addEventListener("input", () => {
  generateBtn.disabled = personaTextarea.value.trim().length === 0;
});

generateBtn.onclick = async () => {
  generateError.textContent = "";
  generateBtn.disabled = true;
  const originalLabel = generateBtn.textContent;
  generateBtn.textContent = "Gerando...";
  personaTextarea.readOnly = true;
  try {
    const data = await api("/api/agents/generate-persona", {
      method: "POST",
      body: JSON.stringify({
        draft: personaTextarea.value,
        agent_name: document.getElementById("agent-name").value,
      }),
    });
    personaTextarea.value = data.persona_prompt;
  } catch (err) {
    generateError.textContent = err.message;
  } finally {
    personaTextarea.readOnly = false;
    generateBtn.textContent = originalLabel;
    generateBtn.disabled = personaTextarea.value.trim().length === 0;
  }
};

document.getElementById("settings-form").onsubmit = async (e) => {
  e.preventDefault();
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      llama_swap_base_url: document.getElementById("setting-base-url").value,
      default_vision_model: document.getElementById("setting-vision-model").value,
      max_pending_per_group: document.getElementById("setting-max-pending").value,
      assistant_model: document.getElementById("setting-assistant-model").value,
    }),
  });
};

document.getElementById("image-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  document.getElementById("image-filename").textContent = file ? file.name : "";
});

document.getElementById("pdf-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  document.getElementById("pdf-filename").textContent = file ? file.name : "";
});

document.getElementById("message-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!state.activeConversationId) return;
  const textInput = document.getElementById("message-input");
  const imageInput = document.getElementById("image-input");
  const pdfInput = document.getElementById("pdf-input");

  if (imageInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("image", imageInput.files[0]);
    await api(`/api/conversations/${state.activeConversationId}/messages/image`, { method: "POST", body: form });
    imageInput.value = "";
    document.getElementById("image-filename").textContent = "";
  } else if (pdfInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("pdf", pdfInput.files[0]);
    await api(`/api/conversations/${state.activeConversationId}/messages/pdf`, { method: "POST", body: form });
    pdfInput.value = "";
    document.getElementById("pdf-filename").textContent = "";
  } else {
    await api(`/api/conversations/${state.activeConversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: textInput.value }),
    });
  }
  textInput.value = "";
  await pollMessages();
  await pollPendingStatus();
};

(async function init() {
  await loadGroups();
  startPolling();
})();
