// AnonChat - Simple Clean Cards & Host Moderation Options

let ws = null;
let reconnectTimer = null;
let isHost = false;
let myIdentity = { name: "Animal", avatar: "/api/animal/snow_leopard", id: "", sci_name: "" };
let pendingImage = null; // { path, thumb, name, size, dims }

// Reply state
let activeReply = null; // { post_num, author, snippet }
let allMessages = {}; // post_num -> msg object

// Session token handling: Fresh session on each page reload/rejoin gives user a new animal and ID
let storedSession = "sess_" + Math.random().toString(36).substring(2, 12) + Math.random().toString(36).substring(2, 12);
const sessionCookieMatch = document.cookie.match(/session_id=([^;]+)/);
if (sessionCookieMatch && sessionCookieMatch[1]) {
  storedSession = sessionCookieMatch[1];
}
localStorage.removeItem("anon_session_token");
document.cookie = `session_id=${storedSession}; path=/; max-age=86400; SameSite=Lax`;

// Public Streams & Official Department Tabs
const PUBLIC_STREAMS = {
  "main": { id: "main", name: "Public Board", title: "Pesitm Anonymous Board", subtitle: "100% Anonymous // No registration // Speak freely", icon: "globe" },
  "cse": { id: "cse", name: "CSE Stream", title: "Computer Science & Engineering", subtitle: "Anonymous Stream for CSE // Code, projects & campus banter", icon: "terminal" },
  "ece": { id: "ece", name: "Electronics Stream", title: "Electronics & Communication", subtitle: "Anonymous Stream for ECE & EEE // Signals, silicon & chatter", icon: "zap" },
  "mech": { id: "mech", name: "Mechanical Stream", title: "Mechanical Engineering", subtitle: "Anonymous Stream for Mech & Auto // Gears, CAD & campus talk", icon: "tool" },
  "civil": { id: "civil", name: "Civil Stream", title: "Civil Engineering", subtitle: "Anonymous Stream for Civil & Infra // Structures & college life", icon: "home" },
  "faculty": { id: "faculty", name: "Faculty", title: "Faculty & Academics", subtitle: "Anonymous Discussion on Academics, Exams & Professors", icon: "book-open" },
  "hostel": { id: "hostel", name: "Hostel", title: "Hostel & Campus Life", subtitle: "Anonymous Discussion on Hostels, Mess & Late Night Hangouts", icon: "moon" }
};

// Rooms state
let currentRoom = "main";
let activeRooms = {}; // room_id -> room_data
let unreadRooms = {}; // room_id -> count
let currentInviteId = null;
let currentProfileTarget = null;

function renderIcon(name, extraClass = '', extraAttrs = '') {
  return `<svg class="ui-icon ${extraClass}" aria-hidden="true" ${extraAttrs}><use href="#icon-${name}" xlink:href="#icon-${name}"></use></svg>`;
}

function getUserColorIndex(str) {
  if (!str) return 0;
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash) + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash) % 16;
}

function formatCleanTime(isoStr) {
  const d = isoStr ? new Date(isoStr) : new Date();
  let hours = d.getHours();
  const minutes = String(d.getMinutes()).padStart(2, '0');
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12;
  hours = hours ? hours : 12;
  return `${hours}:${minutes} ${ampm}`;
}

function extractMediaUrlFromText(text) {
  if (!text || typeof text !== "string") return null;
  const trimmed = text.trim();
  const pattern = /(https?:\/\/[^\s<>]+?\.(?:gif|webp|png|jpe?g|svg)(?:\?[^\s<>]*)?|https?:\/\/(?:[a-zA-Z0-9.-]+\.)?(?:tenor\.com|giphy\.com|klipy\.com)\/[^\s<>]+)/i;
  const match = trimmed.match(pattern);
  return match ? match[1] : null;
}

function formatMessageText(raw) {
  if (!raw) return '';
  const lines = raw.split('\n');
  const parsed = lines.map(line => {
    let esc = escapeHtml(line);
    // Greentext
    if (esc.startsWith('&gt;')) {
      return `<span class="greentext">${esc}</span>`;
    }
    // Auto-embed standalone GIF / Sticker / Image / Tenor / Giphy URL
    const trimmed = esc.trim();
    const mediaUrl = extractMediaUrlFromText(trimmed);
    if (mediaUrl && (trimmed === mediaUrl || trimmed.startsWith("http"))) {
      const lower = mediaUrl.toLowerCase();
      const isSticker = lower.includes("sticker") || lower.endsWith(".svg");
      const isGif = lower.includes(".gif") || lower.includes("tenor.com") || lower.includes("giphy.com");
      const wrapClass = isSticker ? "chat-sticker-wrap" : (isGif ? "chat-gif-wrap" : "chat-image-wrap");
      const imgClass = isSticker ? "chat-sticker-img" : (isGif ? "chat-gif-img" : "chat-attached-image");
      return `<div class="${wrapClass}"><img class="${imgClass}" src="${mediaUrl}" data-full="${mediaUrl}" alt="Media" loading="lazy" onclick="toggleExpandImage(this)"></div>`;
    }
    // Spoiler [spoiler]secret[/spoiler]
    esc = esc.replace(/\[spoiler\]([\s\S]*?)\[\/spoiler\]/gi, '<span style="background:#222; color:#222; padding:0 3px; border-radius:2px; cursor:pointer;" onmouseenter="this.style.color=\'#fff\'" onmouseleave="this.style.color=\'#222\'">$1</span>');
    return esc;
  });
  return parsed.join('<br>');
}

// WebSocket Connection
function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws?session_id=${encodeURIComponent(storedSession)}`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("Connected to /anon/");
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleWSEvent(data);
    } catch (e) {
      console.error("WS error", e);
    }
  };

  ws.onclose = () => {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(initWebSocket, 2000);
  };
}

function handleWSEvent(data) {
  if (data.type === "online_count") {
    const onlineEl = document.getElementById("online-count");
    if (onlineEl) onlineEl.innerHTML = `<span class="online-pulse-dot"></span> ${data.count} Online`;
  } else if (data.type === "my_identity") {
    myIdentity = {
      name: data.anon_name,
      avatar: data.avatar_url,
      id: data.public_id,
      sci_name: data.sci_name || ""
    };
    isHost = data.is_host;
    updateMyIdentityUI();
  } else if (data.type === "new_message") {
    const msgRoom = data.message.room_id || "main";
    if (msgRoom === currentRoom) {
      renderPost(data.message, false);
      scrollToBottom(true);
    } else {
      unreadRooms[msgRoom] = (unreadRooms[msgRoom] || 0) + 1;
      renderRoomChips();
    }
  } else if (data.type === "private_chat_invite") {
    currentInviteId = data.invite_id;
    const titleEl = document.getElementById("invite-header-title");
    const textEl = document.getElementById("invite-text");
    const acceptBtn = document.getElementById("btn-invite-accept");
    const iconEl = document.getElementById("invite-icon");

    if (data.is_random_match) {
      if (titleEl) titleEl.innerText = "Random Match Request 🎲";
      if (textEl) textEl.innerText = "wants to chat privately with you! (Matched via Find Someone)";
      if (acceptBtn) acceptBtn.innerText = "Accept & Chat";
      if (iconEl) iconEl.innerHTML = renderIcon('shuffle', 'ui-icon-md');
    } else if (data.is_group) {
      if (titleEl) titleEl.innerText = "Group Chat Invitation";
      if (textEl) textEl.innerHTML = `invited you to join <strong>${escapeHtml(data.room_name || 'the private group')}</strong>.`;
      if (acceptBtn) acceptBtn.innerText = "Accept & Join";
      if (iconEl) iconEl.innerHTML = renderIcon('users', 'ui-icon-md');
    } else {
      if (titleEl) titleEl.innerText = "Private Chat Request";
      if (textEl) textEl.innerText = "wants to start a 1-on-1 private chat with you.";
      if (acceptBtn) acceptBtn.innerText = "Accept & Chat";
      if (iconEl) iconEl.innerHTML = renderIcon('message-square', 'ui-icon-md');
    }

    const invNameEl = document.getElementById("invite-name");
    if (invNameEl) {
      invNameEl.innerText = data.from_name;
      const invColor = getUserColorIndex(data.from_public_id || data.from_name);
      invNameEl.className = `user-color-${invColor}`;
    }
    document.getElementById("invite-avatar").src = data.from_avatar;
    document.getElementById("modal-invite").style.display = "flex";
  } else if (data.type === "private_chat_sent") {
    if (data.is_group) {
      showToast(`Invitation sent to ${data.target_name}! They will join once they accept.`, "info");
    } else {
      showToast(`Private chat request sent to ${data.target_name}! Waiting for them to accept...`, "info");
    }
  } else if (data.type === "private_chat_declined") {
    showToast(`${data.declined_by} declined the invitation.`, "warning");
  } else if (data.type === "find_someone_result") {
    showToast(data.message, data.success ? "success" : "warning", 4500);
  } else if (data.type === "room_deleted") {
    delete activeRooms[data.room_id];
    delete unreadRooms[data.room_id];
    if (localStorage.getItem("active_room_id") === data.room_id) {
      localStorage.removeItem("active_room_id");
    }
    renderRoomChips();
    if (currentRoom === data.room_id) {
      switchRoom("main");
      showToast(data.reason || `Group '${data.room_name || data.room_id}' was deleted by the host admin.`, "error", 5000);
    }
    const modalHost = document.getElementById("modal-host");
    if (modalHost && modalHost.style.display === "flex") {
      loadHostGroups();
    }
  } else if (data.type === "room_left") {
    delete activeRooms[data.room_id];
    delete unreadRooms[data.room_id];
    if (localStorage.getItem("active_room_id") === data.room_id) {
      localStorage.removeItem("active_room_id");
    }
    renderRoomChips();
    if (currentRoom === data.room_id) {
      switchRoom("main");
      showToast("You left the group chat.", "info");
    }
    const modalHost = document.getElementById("modal-host");
    if (modalHost && modalHost.style.display === "flex") {
      loadHostGroups();
    }
  } else if (data.type === "room_joined") {
    activeRooms[data.room.room_id] = data.room;
    renderRoomChips();
    switchRoom(data.room.room_id);
    if (data.system_message) {
      renderPost(data.system_message, false);
      scrollToBottom(true);
    }
  } else if (data.type === "room_created") {
    activeRooms[data.room.room_id] = data.room;
    renderRoomChips();
    if (isHost) {
      showToast(`👁️ Admin: New private group created "${data.room.name}"`, "info", 5000);
      const modalHost = document.getElementById("modal-host");
      if (modalHost && modalHost.style.display === "flex") {
        loadHostGroups();
      }
    }
  } else if (data.type === "room_updated") {
    activeRooms[data.room.room_id] = data.room;
    renderRoomChips();
    if (currentRoom === data.room.room_id) {
      updatePrivateRoomBanner();
      if (data.system_message) {
        renderPost(data.system_message, false);
        scrollToBottom(true);
      }
    }
    if (isHost) {
      const modalHost = document.getElementById("modal-host");
      if (modalHost && modalHost.style.display === "flex") {
        loadHostGroups();
      }
    }
  } else if (data.type === "delete_message" || data.type === "hide_message") {
    const el = document.getElementById(`post-${data.post_num}`);
    if (el) el.remove();
  } else if (data.type === "posts_deleted") {
    if (Array.isArray(data.post_nums)) {
      data.post_nums.forEach(pn => {
        const el = document.getElementById(`post-${pn}`);
        if (el) el.remove();
      });
    }
  } else if (data.type === "ai_moderation_event") {
    const ev = data.event || {};
    if (isHost) {
      const actLabel = ev.action === "delete_and_ban" ? "Deleted & Banned" : "Deleted";
      showToast(`🛡️ AI Auto-Mod: ${actLabel} Post #${ev.post_num} (${escapeHtml(ev.anon_name || 'Author')}) - ${escapeHtml(ev.reason || '')}`, "error");
      incrementAiModBadge();
      appendAiLogToDOM(ev);
    }
  } else if (data.type === "purge_ip") {
    document.querySelectorAll(`.chat-box[data-ip="${data.ip}"]`).forEach(el => el.remove());
  } else if (data.type === "announcement") {
    showAnnouncement(data.text);
  } else if (data.type === "clear_chat") {
    document.getElementById("posts-stream").innerHTML = '<div style="text-align:center; padding: 40px; color: var(--text-date);">No messages yet.</div>';
  } else if (data.type === "update_identity") {
    updateHostIdentityInDOM(data.ip, data.real_name);
  } else if (data.type === "error") {
    alert(data.message);
  }
}

function playBroadcastChime() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    if (ctx.state === 'suspended') {
      ctx.resume();
    }
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = 'sine';
    osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
    osc.frequency.exponentialRampToValueAtTime(880.00, ctx.currentTime + 0.14); // A5

    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.42);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start();
    osc.stop(ctx.currentTime + 0.45);
  } catch (e) {
    // AudioContext blocked or not supported on device; safe to ignore
  }
}

function showAnnouncement(text) {
  if (!text) return;

  // 1. Update persistent in-feed announcement banner
  const banner = document.getElementById("announcement-banner");
  const bannerText = document.getElementById("announcement-text");
  if (banner && bannerText) {
    bannerText.innerText = text;
    banner.style.display = "flex";
  }

  // 2. Open prominent Broadcast Pop-up Notification Modal on all users' screens
  const modal = document.getElementById("modal-broadcast-popup");
  const msgEl = document.getElementById("broadcast-popup-message");
  const timeEl = document.getElementById("broadcast-popup-time");

  if (modal && msgEl) {
    msgEl.innerText = text;
    if (timeEl) {
      timeEl.innerText = `Received at ${formatCleanTime(new Date().toISOString())}`;
    }
    modal.style.display = "flex";
  }

  // 3. Audio alert chime
  playBroadcastChime();
}

function closeBroadcastModal() {
  const modal = document.getElementById("modal-broadcast-popup");
  if (modal) modal.style.display = "none";
}

function showToast(message, type = "info", duration = 3500) {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast-item toast-${type}`;

  let iconName = "message-square";
  if (type === "success") iconName = "check-circle";
  else if (type === "warning") iconName = "shuffle";
  else if (type === "error") iconName = "trash";

  toast.innerHTML = `
    <span style="display:inline-flex; align-items:center; flex-shrink:0;">${renderIcon(iconName, 'ui-icon-sm')}</span>
    <span style="flex:1; line-height:1.4;">${escapeHtml(message)}</span>
  `;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(16px) scale(0.96)";
    setTimeout(() => toast.remove(), 260);
  }, duration);
}

let isFindingSomeone = false;
function findSomeoneToChat() {
  if (isFindingSomeone) {
    showToast("Looking for a match... please wait a moment.", "info");
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    showToast("Connecting to chat server, please retry in a second...", "warning");
    return;
  }

  isFindingSomeone = true;
  showToast("Finding someone online to chat with...", "info");

  const findChip = document.getElementById("chip-find-someone");
  const topFindBtn = document.getElementById("btn-top-find-someone");
  if (findChip) findChip.style.opacity = "0.6";
  if (topFindBtn) topFindBtn.style.opacity = "0.6";

  ws.send(JSON.stringify({ action: "find_someone" }));

  setTimeout(() => {
    isFindingSomeone = false;
    if (findChip) findChip.style.opacity = "1";
    if (topFindBtn) topFindBtn.style.opacity = "1";
  }, 2500);
}

function updateMyIdentityUI() {
  const nameEl = document.getElementById("my-animal-name");
  const avatarEl = document.getElementById("my-animal-avatar");
  if (nameEl) {
    nameEl.innerText = myIdentity.name;
    const myColor = getUserColorIndex(myIdentity.id || myIdentity.name);
    nameEl.className = `user-color-${myColor}`;
  }
  if (avatarEl) avatarEl.src = myIdentity.avatar;
  const hostBtn = document.getElementById("btn-host-modal");
  if (hostBtn) hostBtn.style.display = isHost ? "inline-flex" : "none";
}

// Render Simple Chat Box: DP, Animal Name, Time, Text (+ Host Toolbar if on laptop)
function renderPost(msg, prepend = false) {
  const container = document.getElementById("posts-stream");
  const existing = document.getElementById(`post-${msg.post_num}`);
  if (existing) return;

  allMessages[msg.post_num] = msg;

  let avatarUrl = msg.anon_avatar;
  let displayName = msg.anon_name || "Anonymous";

  if (!avatarUrl || !avatarUrl.startsWith("/api/animal/")) {
    const fallbackAnimals = ["snow_leopard", "capybara", "red_fox", "barn_owl", "giant_panda", "arctic_fox", "koala", "meerkat"];
    let code = 0;
    const pid = msg.public_id || "anon";
    for (let i = 0; i < pid.length; i++) code += pid.charCodeAt(i);
    const chosen = fallbackAnimals[code % fallbackAnimals.length];
    avatarUrl = `/api/animal/${chosen}`;
    if (!msg.anon_name) displayName = `${chosen.replace("_", " ").toUpperCase()} #${pid.slice(0, 4)}`;
  }

  const card = document.createElement("div");
  card.className = "chat-box";
  card.id = `post-${msg.post_num}`;
  card.setAttribute("data-post-num", msg.post_num);
  card.setAttribute("data-ip", msg.ip || "");

  // Host Intel Ribbon (Only rendered on Host Laptop)
  let hostToolbar = '';
  if (isHost && (msg.ip || msg.real_name)) {
    const realNameDisplay = msg.real_name 
      ? `${renderIcon('crown', 'ui-icon-xs', 'style="color:#f59e0b;"')} <strong>${escapeHtml(msg.real_name)}</strong>` 
      : `<span>${renderIcon('user', 'ui-icon-xs')} No Name</span>`;

    hostToolbar = `
      <div class="host-ribbon-tag">
        <div class="host-intel-info">
          ${realNameDisplay}
          <span>${renderIcon('globe', 'ui-icon-xs')} ${msg.ip}</span>
        </div>
        <div class="host-action-btns">
          <button class="btn-host-mini" onclick="hostPinMessage(${msg.post_num}, ${!msg.is_pinned})">${renderIcon('pin', 'ui-icon-xs')} ${msg.is_pinned ? 'Unpin' : 'Pin'}</button>
          <button class="btn-host-mini" onclick="promptAssignName('${msg.ip}', '${escapeHtml(msg.real_name || '')}')">${renderIcon('edit', 'ui-icon-xs')} Name</button>
          <button class="btn-host-mini" onclick="hostHideMessage(${msg.post_num})">${renderIcon('eye-off', 'ui-icon-xs')} Hide</button>
          <button class="btn-host-mini" onclick="hostMuteUser('${msg.ip}', true)">${renderIcon('volume-x', 'ui-icon-xs')} Mute</button>
          <button class="btn-host-mini btn-danger" onclick="hostPurgeIP('${msg.ip}')">${renderIcon('trash', 'ui-icon-xs')} Purge</button>
          <button class="btn-host-mini btn-danger" onclick="hostToggleBan('${msg.ip}', ${!msg.is_banned})">${msg.is_banned ? renderIcon('check-circle', 'ui-icon-xs') + ' Unban' : renderIcon('ban', 'ui-icon-xs') + ' Ban'}</button>
        </div>
      </div>
    `;
  }

  const userColorIdx = getUserColorIndex(msg.public_id || msg.anon_name || "anon");

  // Pinned post badge
  const pinnedBadge = msg.is_pinned ? `<span class="pinned-badge">${renderIcon('pin', 'ui-icon-xs')} Pinned</span>` : '';

  // Quoted Reply (WhatsApp Style)
  let quoteHTML = '';
  if (msg.reply_to) {
    const replyMsg = allMessages[msg.reply_to];
    const replyAuthor = msg.reply_name || (replyMsg ? replyMsg.anon_name : `Post #${msg.reply_to}`);
    const replySnippet = msg.reply_snippet || (replyMsg ? (replyMsg.content || '[Attachment]') : '');
    const replyColorIdx = replyMsg ? getUserColorIndex(replyMsg.public_id || replyMsg.anon_name) : userColorIdx;
    quoteHTML = `
      <div class="chat-reply-quote" onclick="jumpToPost(${msg.reply_to})" title="Jump to original message">
        <div class="chat-reply-quote-header">
          <span class="chat-reply-quote-arrow">${renderIcon('reply', 'ui-icon-xs')}</span>
          <span class="chat-reply-quote-author user-color-${replyColorIdx}">${escapeHtml(replyAuthor)}</span>
        </div>
        <div class="chat-reply-quote-text">${escapeHtml(replySnippet)}</div>
      </div>
    `;
  }

  // Optional Image / Sticker / GIF Attachment
  let imageHTML = '';
  if (msg.image_path) {
    const full = msg.image_path;
    const thumb = msg.image_thumb || full;
    const lower = full.toLowerCase();
    const isSticker = lower.includes('/static/stickers/') || lower.endsWith('.svg') || (msg.image_name && msg.image_name.toLowerCase().startsWith('sticker'));
    const isGif = lower.endsWith('.gif') || lower.includes('/static/gifs/') || lower.includes('tenor.com') || lower.includes('giphy.com') || (msg.image_name && msg.image_name.toLowerCase().startsWith('gif'));

    if (isSticker) {
      imageHTML = `
        <div class="chat-sticker-wrap">
          <img class="chat-sticker-img" src="${full}" data-full="${full}" alt="${escapeHtml(msg.image_name || 'Sticker')}" loading="lazy" onclick="toggleExpandImage(this)">
        </div>
      `;
    } else if (isGif) {
      imageHTML = `
        <div class="chat-gif-wrap">
          <img class="chat-gif-img" src="${full}" data-full="${full}" alt="${escapeHtml(msg.image_name || 'GIF')}" loading="lazy" onclick="toggleExpandImage(this)">
        </div>
      `;
    } else {
      imageHTML = `
        <div class="chat-image-wrap">
          <img class="chat-attached-image" src="${thumb}" data-full="${full}" alt="Image" onclick="toggleExpandImage(this)" loading="lazy">
        </div>
      `;
    }
  }

  const timeFormatted = formatCleanTime(msg.created_at);

  card.innerHTML = `
    <!-- 1. DP (Animal Avatar Photo) - Click to zoom like WhatsApp -->
    <img class="chat-dp clickable-dp" src="${avatarUrl}" alt="DP" loading="lazy" onclick="showUserProfileModal('${msg.public_id || ''}', '${escapeHtml(displayName)}', '${avatarUrl}', '', '${msg.ip || ''}', '${escapeHtml(msg.real_name || '')}')" title="Tap to view profile & zoom">

    <div class="chat-main">
      ${hostToolbar}

      <!-- 2. Header: Animal Name, Time and Reply Button -->
      <div class="chat-header">
        <div>
          ${pinnedBadge}
          <span class="chat-animal-name clickable-name user-color-${userColorIdx}" onclick="showUserProfileModal('${msg.public_id || ''}', '${escapeHtml(displayName)}', '${avatarUrl}', '', '${msg.ip || ''}', '${escapeHtml(msg.real_name || '')}')" title="Tap to chat privately">${escapeHtml(displayName)}</span>
        </div>
        <div class="chat-header-actions">
          <span class="chat-time">${timeFormatted}</span>
          <button class="btn-chat-reply" onclick="startReply(${msg.post_num})" title="Reply to this message" aria-label="Reply">
            ${renderIcon('reply', 'ui-icon-xs')}
            <span>Reply</span>
          </button>
        </div>
      </div>

      ${quoteHTML}

      <!-- 3. Text (Message Content) -->
      <div class="chat-text">${formatMessageText(msg.content)}</div>

      ${imageHTML}
    </div>
  `;

  if (prepend) {
    container.prepend(card);
  } else {
    container.appendChild(card);
  }
}

// Start Replying to a post
function startReply(postNum) {
  const msg = allMessages[postNum];
  if (!msg) return;

  const isSticker = msg.image_path && (msg.image_path.includes('/static/stickers/') || msg.image_path.endsWith('.svg'));
  const isGif = msg.image_path && (msg.image_path.toLowerCase().endsWith('.gif') || msg.image_path.includes('/static/gifs/'));
  let fallbackType = isSticker ? "Sticker" : (isGif ? "GIF" : "Photo");
  activeReply = {
    post_num: postNum,
    author: msg.anon_name || "Anonymous",
    public_id: msg.public_id || "",
    snippet: msg.content || (msg.image_name ? `${fallbackType}: ${msg.image_name}` : (msg.image_path ? fallbackType : "Message"))
  };

  const bar = document.getElementById("reply-preview-bar");
  const authorEl = document.getElementById("reply-preview-name");
  const snippetEl = document.getElementById("reply-preview-snippet");

  if (bar && authorEl && snippetEl) {
    authorEl.innerText = activeReply.author;
    const repColorIdx = getUserColorIndex(activeReply.public_id || activeReply.author);
    authorEl.className = `user-color-${repColorIdx}`;
    snippetEl.innerText = activeReply.snippet.slice(0, 80);
    bar.style.display = "flex";
  }

  const textarea = document.getElementById("chat-textarea");
  if (textarea) {
    textarea.focus();
  }
}

// Cancel current reply
function cancelReply() {
  activeReply = null;
  const bar = document.getElementById("reply-preview-bar");
  if (bar) {
    bar.style.display = "none";
  }
}

// Jump to a replied-to message with smooth scroll & highlight pulse
function jumpToPost(postNum) {
  const target = document.getElementById(`post-${postNum}`);
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.remove("post-jump-pulse");
    void target.offsetWidth; // trigger reflow
    target.classList.add("post-jump-pulse");
    setTimeout(() => {
      target.classList.remove("post-jump-pulse");
    }, 1900);
  } else {
    alert(`Replied to Post #${postNum}`);
  }
}

// Inline image expansion toggle
function toggleExpandImage(imgEl) {
  const fullSrc = imgEl.getAttribute("data-full");
  if (imgEl.classList.contains("expanded")) {
    imgEl.classList.remove("expanded");
  } else {
    imgEl.classList.add("expanded");
    if (imgEl.src !== fullSrc) {
      imgEl.src = fullSrc;
    }
  }
}

// Submit Message
async function sendMessage() {
  const textarea = document.getElementById("chat-textarea");
  let content = textarea ? textarea.value.trim() : "";

  // Check if content itself is or contains a GIF / sticker / Tenor URL from Gboard
  let mediaPath = pendingImage ? pendingImage.path : null;
  let mediaThumb = pendingImage ? pendingImage.thumb : null;
  let mediaName = pendingImage ? pendingImage.name : null;
  let mediaSize = pendingImage ? pendingImage.size : null;
  let mediaDims = pendingImage ? pendingImage.dims : null;

  if (!mediaPath && content) {
    const extracted = extractMediaUrlFromText(content);
    if (extracted) {
      mediaPath = extracted;
      mediaThumb = extracted;
      const lower = extracted.toLowerCase();
      const isSticker = lower.includes("sticker") || lower.endsWith(".svg");
      mediaName = isSticker ? "Sticker" : "GIF";
      mediaSize = 0;
      mediaDims = isSticker ? "120x120" : "240x240";
      content = content.replace(extracted, "").trim();
    }
  }

  if (!content && !mediaPath) {
    alert("Please write a message, attach an image, or send a sticker/GIF.");
    return;
  }

  const payload = {
    action: "post_message",
    room_id: currentRoom,
    content: content,
    reply_to: activeReply ? activeReply.post_num : null,
    image_path: mediaPath,
    image_thumb: mediaThumb,
    image_name: mediaName,
    image_size: mediaSize,
    image_dims: mediaDims
  };

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  } else {
    initWebSocket();
    return;
  }

  if (textarea) {
    textarea.value = "";
    textarea.style.height = "38px";
  }
  removePendingImage();
  cancelReply();
  setTimeout(() => scrollToBottom(true), 50);
}

// Image upload handling
async function handleImageUpload(file) {
  if (!file) return;

  const validTypes = ["image/jpeg", "image/png", "image/gif", "image/webp"];
  if (!validTypes.includes(file.type)) {
    alert("Please choose an image (JPG, PNG, GIF, WebP).");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  const chip = document.getElementById("preview-chip");
  chip.style.display = "inline-flex";
  document.getElementById("preview-filename").innerText = "Uploading...";

  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Upload failed");
    }

    const data = await res.json();
    pendingImage = {
      path: data.image_path,
      thumb: data.image_thumb,
      name: data.image_name,
      size: data.image_size,
      dims: data.image_dims
    };

    document.getElementById("preview-thumb-img").src = data.image_thumb;
    document.getElementById("preview-filename").innerText = data.image_name;
  } catch (err) {
    alert("Upload error: " + err.message);
    removePendingImage();
  }
}

function removePendingImage() {
  pendingImage = null;
  document.getElementById("file-input").value = "";
  document.getElementById("preview-chip").style.display = "none";
}

// Theme handling with graceful migration of legacy themes
function changeTheme(themeName) {
  const legacyMap = {
    'slate-dark': 'mono-noir',
    'nordic-light': 'mono-paper',
    'dracula': 'nordic-dusk',
    'warm-paper': 'terracotta'
  };
  const activeTheme = legacyMap[themeName] || themeName || 'mono-noir';
  document.documentElement.setAttribute("data-theme", activeTheme);
  localStorage.setItem("anon_theme", activeTheme);
  const sel = document.getElementById("theme-select");
  if (sel) sel.value = activeTheme;
}

// Host Console Actions (All available directly to Host Laptop)
function openHostModal(defaultTab = "devices") {
  const modal = document.getElementById("modal-host");
  if (!modal) return;
  modal.style.display = "flex";
  if (defaultTab === "ai") {
    switchHostTab("ai");
  } else if (defaultTab === "groups") {
    switchHostTab("groups");
  } else {
    switchHostTab("devices");
  }
}

function openHostAiModal() {
  openHostModal("ai");
}

let aiChatHistory = [];
let currentAiModel = "qwen2.5:1.5b";

function switchHostTab(tab) {
  const tabDevicesBtn = document.getElementById("tab-host-devices");
  const tabGroupsBtn = document.getElementById("tab-host-groups");
  const tabAiBtn = document.getElementById("tab-host-ai");
  const tabAiLogsBtn = document.getElementById("tab-host-ailogs");
  const tabFeedbacksBtn = document.getElementById("tab-host-feedbacks");
  const paneDevices = document.getElementById("host-pane-devices");
  const paneGroups = document.getElementById("host-pane-groups");
  const paneAi = document.getElementById("host-pane-ai");
  const paneAiLogs = document.getElementById("host-pane-ailogs");
  const paneFeedbacks = document.getElementById("host-pane-feedbacks");

  if (tabDevicesBtn) tabDevicesBtn.classList.remove("active");
  if (tabGroupsBtn) tabGroupsBtn.classList.remove("active");
  if (tabAiBtn) tabAiBtn.classList.remove("active");
  if (tabAiLogsBtn) tabAiLogsBtn.classList.remove("active");
  if (tabFeedbacksBtn) tabFeedbacksBtn.classList.remove("active");
  if (paneDevices) paneDevices.style.display = "none";
  if (paneGroups) paneGroups.style.display = "none";
  if (paneAi) paneAi.style.display = "none";
  if (paneAiLogs) paneAiLogs.style.display = "none";
  if (paneFeedbacks) paneFeedbacks.style.display = "none";

  if (tab === "groups") {
    if (tabGroupsBtn) tabGroupsBtn.classList.add("active");
    if (paneGroups) paneGroups.style.display = "block";
    loadHostGroups();
  } else if (tab === "ai") {
    if (tabAiBtn) tabAiBtn.classList.add("active");
    if (paneAi) paneAi.style.display = "block";
    checkAiBackendStatus();
    loadAiAutomodConfig();
    const chatInput = document.getElementById("ai-chat-input");
    if (chatInput) chatInput.focus();
  } else if (tab === "ailogs") {
    if (tabAiLogsBtn) tabAiLogsBtn.classList.add("active");
    if (paneAiLogs) paneAiLogs.style.display = "block";
    loadHostAiLogs();
  } else if (tab === "feedbacks") {
    if (tabFeedbacksBtn) tabFeedbacksBtn.classList.add("active");
    if (paneFeedbacks) paneFeedbacks.style.display = "block";
    loadHostFeedbacks();
  } else {
    if (tabDevicesBtn) tabDevicesBtn.classList.add("active");
    if (paneDevices) paneDevices.style.display = "block";
    loadHostDevices();
  }
}

async function loadAiAutomodConfig() {
  try {
    const res = await fetch("/api/host/ai/config");
    const data = await res.json();
    if (data.status === "ok" && data.config) {
      const cfg = data.config;
      const toggle = document.getElementById("ai-automod-toggle");
      const badge = document.getElementById("ai-automod-status-badge");
      const modeSel = document.getElementById("ai-automod-mode-select");
      const purgeCb = document.getElementById("ai-automod-purge-cb");
      const modelSel = document.getElementById("ai-automod-model-select");

      if (toggle) toggle.checked = cfg.enabled;
      if (badge) {
        badge.textContent = cfg.enabled ? "ACTIVE" : "OFF";
        badge.style.background = cfg.enabled ? "#22c55e" : "var(--text-date)";
      }
      if (modeSel && cfg.mode) modeSel.value = cfg.mode;
      if (purgeCb) purgeCb.checked = cfg.purge;
      if (modelSel && cfg.model) modelSel.value = cfg.model;
    }
  } catch (e) {}
}

async function toggleAiAutomod(enabled) {
  const badge = document.getElementById("ai-automod-status-badge");
  if (badge) {
    badge.textContent = enabled ? "ACTIVE" : "OFF";
    badge.style.background = enabled ? "#22c55e" : "var(--text-date)";
  }
  try {
    const res = await fetch("/api/host/ai/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled })
    });
    const data = await res.json();
    if (data.status === "ok") {
      showToast(`Real-Time AI Auto-Mod ${enabled ? 'Activated' : 'Paused'}`, enabled ? "success" : "info");
    }
  } catch (e) {
    showToast("Failed to update Auto-Mod setting", "error");
  }
}

async function updateAiAutomodMode(mode) {
  try {
    await fetch("/api/host/ai/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    });
    const label = mode === "delete_and_ban" ? "Delete & Ban Author" : "Delete Only";
    showToast(`AI Auto-Mod Mode: ${label}`, "info");
  } catch (e) {}
}

async function updateAiAutomodPurge(purge) {
  try {
    await fetch("/api/host/ai/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ purge })
    });
    showToast(`Purge on Ban: ${purge ? 'Enabled' : 'Disabled'}`, "info");
  } catch (e) {}
}

async function updateAiAutomodModel(model) {
  try {
    await fetch("/api/host/ai/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model })
    });
    showToast(`Fast AI Model: ${model}`, "info");
  } catch (e) {}
}

let aiModCount = 0;
function incrementAiModBadge() {
  aiModCount++;
  const b1 = document.getElementById("ai-mod-count");
  const b2 = document.getElementById("host-ailog-count");
  if (b1) b1.textContent = aiModCount;
  if (b2) b2.textContent = aiModCount;
}

function appendAiLogToDOM(ev) {
  const tbody = document.getElementById("host-ailogs-tbody");
  if (!tbody) return;
  const emptyRow = tbody.querySelector(".no-logs-row");
  if (emptyRow) emptyRow.remove();

  const tr = document.createElement("tr");
  const isBan = ev.action === "delete_and_ban" || (ev.action && ev.action.includes("ban"));
  const badgeColor = isBan ? "#ef4444" : "#f59e0b";
  const badgeLabel = isBan ? "Deleted & Banned" : "Deleted Post";
  const timeStr = ev.timestamp || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  tr.innerHTML = `
    <td style="font-size:11px; color:var(--text-date);">${timeStr}</td>
    <td style="font-weight:600;">#${ev.post_num || '-'}</td>
    <td>${escapeHtml(ev.anon_name || 'Anonymous')}</td>
    <td><code style="font-size:10.5px;">${escapeHtml(ev.ip || 'Unknown')}</code></td>
    <td><span style="font-size:10.5px; padding:2px 6px; border-radius:4px; font-weight:600; color:#fff; background:${badgeColor};">${badgeLabel}</span></td>
    <td style="font-size:11.5px; color:var(--text-main);">${escapeHtml(ev.reason || 'Safety policy')}</td>
  `;
  tbody.insertBefore(tr, tbody.firstChild);
}

async function loadHostAiLogs() {
  const tbody = document.getElementById("host-ailogs-tbody");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-date); padding:20px;">Loading audit logs...</td></tr>`;
  try {
    const res = await fetch("/api/host/ai/logs");
    const data = await res.json();
    if (data.status === "ok" && data.logs) {
      aiModCount = data.logs.length;
      const b1 = document.getElementById("ai-mod-count");
      const b2 = document.getElementById("host-ailog-count");
      if (b1) b1.textContent = aiModCount;
      if (b2) b2.textContent = aiModCount;

      if (data.logs.length === 0) {
        tbody.innerHTML = `<tr class="no-logs-row"><td colspan="6" style="text-align:center; color:var(--text-date); padding:20px;">No moderation events recorded yet. AI Guardian is actively monitoring chat in real time.</td></tr>`;
        return;
      }

      tbody.innerHTML = data.logs.map(log => {
        const isBan = log.action && (log.action.includes("ban") || log.action.includes("Ban"));
        const badgeColor = isBan ? "#ef4444" : "#f59e0b";
        const dateStr = log.created_at ? new Date(log.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-';
        return `
          <tr>
            <td style="font-size:11px; color:var(--text-date);">${dateStr}</td>
            <td style="font-weight:600;">#${log.post_num || '-'}</td>
            <td>${escapeHtml(log.anon_name || 'Anonymous')}</td>
            <td><code style="font-size:10.5px;">${escapeHtml(log.ip || 'Unknown')}</code></td>
            <td><span style="font-size:10.5px; padding:2px 6px; border-radius:4px; font-weight:600; color:#fff; background:${badgeColor};">${escapeHtml(log.action || 'Moderated')}</span></td>
            <td style="font-size:11.5px; color:var(--text-main);">${escapeHtml(log.reason || 'Safety violation')}</td>
          </tr>
        `;
      }).join("");
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#ef4444; padding:20px;">Failed to load audit logs.</td></tr>`;
  }
}

async function scanAndAutoClean() {
  const box = document.getElementById("ai-chat-messages");
  if (box) {
    const notice = document.createElement("div");
    notice.className = "ai-msg-bubble ai-msg-user";
    notice.innerHTML = `
      <div class="ai-msg-meta"><strong>Admin (You)</strong><span class="ai-msg-time">Now</span></div>
      <div class="ai-msg-content">⚡ <em>Executing Autonomous Scan &amp; Auto-Clean on live messages...</em></div>
    `;
    box.appendChild(notice);

    const loading = document.createElement("div");
    loading.className = "ai-msg-bubble ai-msg-bot";
    loading.innerHTML = `
      <div class="ai-msg-meta"><strong>Qwen AI Guardian</strong><span class="ai-msg-time">Scanning...</span></div>
      <div class="ai-msg-content">Scanning live chat for violations and executing real-time deletions &amp; bans...</div>
    `;
    box.appendChild(loading);
    box.scrollTop = box.scrollHeight;

    try {
      const res = await fetch("/api/host/ai/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          room_id: currentRoom || "main",
          auto_enforce: true
        })
      });
      const data = await res.json();
      loading.remove();

      const resultBubble = document.createElement("div");
      resultBubble.className = "ai-msg-bubble ai-msg-bot";
      resultBubble.innerHTML = `
        <div class="ai-msg-meta"><strong>Qwen AI Guardian</strong><span class="ai-msg-time">${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span></div>
        <div class="ai-msg-content">${formatAiResponse(data.reply || 'Scan complete.')}</div>
      `;
      box.appendChild(resultBubble);
      box.scrollTop = box.scrollHeight;

      if (data.actions_taken && data.actions_taken.length > 0) {
        data.actions_taken.forEach(act => {
          if (act.action === "delete_post" && act.post_num) {
            const el = document.getElementById(`post-${act.post_num}`);
            if (el) el.remove();
          } else if (act.ip) {
            document.querySelectorAll(`.chat-box[data-ip="${act.ip}"]`).forEach(el => el.remove());
          }
        });
        loadHostDevices();
        showToast(`⚡ AI Cleaned ${data.actions_taken.length} violation(s) in real-time!`, "success", 4000);
      } else {
        showToast("Scan completed: Board is clean.", "info");
      }
      loadHostAiLogs();
    } catch (err) {
      loading.remove();
      showToast("Scan & Auto-Clean failed: " + err.message, "error");
    }
  }
}

async function checkAiBackendStatus() {
  const pulseDot = document.getElementById("ai-pulse-dot");
  const statusText = document.getElementById("ai-status-text");
  const modelSelect = document.getElementById("ai-model-select");

  if (pulseDot) pulseDot.style.background = "#f59e0b";
  if (statusText) statusText.textContent = "Checking Ollama...";

  try {
    const res = await fetch("/api/host/ai/status");
    const data = await res.json();
    if (data.status === "ok" && data.ollama_online) {
      if (pulseDot) pulseDot.style.background = "#22c55e";
      if (statusText) statusText.textContent = "Qwen / Ollama Online";

      if (modelSelect && data.models && data.models.length > 0) {
        modelSelect.innerHTML = data.models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
        if (data.models.includes(currentAiModel)) {
          modelSelect.value = currentAiModel;
        } else {
          currentAiModel = data.models[0];
          modelSelect.value = currentAiModel;
        }
      }
      loadAiAutomodConfig();
    } else {
      if (pulseDot) pulseDot.style.background = "#ef4444";
      if (statusText) statusText.textContent = "Ollama Offline";
    }
  } catch (err) {
    if (pulseDot) pulseDot.style.background = "#ef4444";
    if (statusText) statusText.textContent = "Connection Error";
  }
}


function updateSelectedAiModel(modelName) {
  currentAiModel = modelName;
  showToast(`Active AI Model: ${modelName}`, "info");
}

function clearAiChat() {
  aiChatHistory = [];
  const box = document.getElementById("ai-chat-messages");
  if (!box) return;
  box.innerHTML = `
    <div class="ai-msg-bubble ai-msg-bot">
      <div class="ai-msg-meta">
        <strong>Qwen AI Co-Pilot</strong>
        <span class="ai-msg-time">Ready</span>
      </div>
      <div class="ai-msg-content">
        Conversation history cleared. Ready for your next request!
      </div>
    </div>
  `;
}

function sendAiQuickAction(type) {
  const input = document.getElementById("ai-chat-input");
  if (!input) return;

  if (type === "summarize") {
    input.value = "Summarize the key topics and discussions happening in the chat right now.";
  } else if (type === "audit") {
    input.value = "Audit recent messages for toxicity, hate speech, spam, or leaked private numbers/data.";
  } else if (type === "announcement") {
    input.value = "Draft a clean, friendly admin announcement reminding users about board rules and staying respectful.";
  }
  submitAiChat(new Event("submit"));
}

async function submitAiChat(e) {
  if (e && e.preventDefault) e.preventDefault();

  const input = document.getElementById("ai-chat-input");
  const box = document.getElementById("ai-chat-messages");
  const sendBtn = document.getElementById("btn-ai-send");
  const includeContextCb = document.getElementById("ai-include-context-cb");
  const modelSelect = document.getElementById("ai-model-select");

  if (!input || !box) return;
  const prompt = input.value.trim();
  if (!prompt) return;

  input.value = "";
  const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  // Append user bubble
  const userBubble = document.createElement("div");
  userBubble.className = "ai-msg-bubble ai-msg-user";
  userBubble.innerHTML = `
    <div class="ai-msg-meta">
      <strong>Admin (You)</strong>
      <span class="ai-msg-time">${timeStr}</span>
    </div>
    <div class="ai-msg-content">${escapeHtml(prompt)}</div>
  `;
  box.appendChild(userBubble);

  // Append loading bubble
  const loadingBubble = document.createElement("div");
  loadingBubble.className = "ai-msg-bubble ai-msg-bot";
  loadingBubble.id = "ai-loading-bubble";
  loadingBubble.innerHTML = `
    <div class="ai-msg-meta">
      <strong>Qwen AI</strong>
      <span class="ai-msg-time">Thinking...</span>
    </div>
    <div class="ai-msg-content" style="color:var(--text-date); font-style:italic;">
      <span class="online-pulse-dot" style="background:var(--btn-submit);"></span> Analyzing &amp; generating response...
    </div>
  `;
  box.appendChild(loadingBubble);
  box.scrollTop = box.scrollHeight;

  if (sendBtn) sendBtn.disabled = true;

  const targetModel = modelSelect ? modelSelect.value : currentAiModel;
  const includeContext = includeContextCb ? includeContextCb.checked : true;

  try {
    const res = await fetch("/api/host/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: prompt,
        history: aiChatHistory,
        model: targetModel,
        include_context: includeContext,
        room_id: currentRoom || "main"
      })
    });

    const data = await res.json();
    loadingBubble.remove();

    const botBubble = document.createElement("div");
    botBubble.className = "ai-msg-bubble ai-msg-bot";

    const replyText = data.reply || "No response received.";
    const latencyBadge = data.latency_ms ? `${(data.latency_ms / 1000).toFixed(1)}s` : "";

    // Process real-time administrative actions executed by AI
    const actionsTaken = data.actions_taken || [];
    const executedPostNums = new Set();
    let hasBans = false;

    actionsTaken.forEach(act => {
      if (act.post_num) executedPostNums.add(parseInt(act.post_num, 10));
      if (act.action === "delete_post" && act.post_num) {
        const el = document.getElementById(`post-${act.post_num}`);
        if (el) el.remove();
      } else if (act.action === "ban_author" || act.action === "purge_author" || act.action === "ban_user" || act.action === "ban_author_by_name") {
        hasBans = true;
        if (act.ip) {
          document.querySelectorAll(`.chat-box[data-ip="${act.ip}"]`).forEach(el => el.remove());
        }
      } else if (act.action === "clear_chat") {
        const stream = document.getElementById("posts-stream");
        if (stream) stream.innerHTML = '<div style="text-align:center; padding: 40px; color: var(--text-date);">No messages yet.</div>';
      }
    });

    if (actionsTaken.length > 0) {
      showToast(`⚡ AI Executed: ${actionsTaken.length} administrative action${actionsTaken.length > 1 ? 's' : ''} taken!`, "success", 4000);
      if (hasBans) {
        loadHostDevices();
      }
      if (typeof loadHostAiLogs === "function") {
        loadHostAiLogs();
      }
    }

    // Extract mentioned post numbers ONLY for posts that were NOT executed and still exist in DOM
    const postMatches = [...replyText.matchAll(/(?:post|#)\s*#?(\d{1,7})\b/gi)];
    const mentionedPosts = [...new Set(postMatches.map(m => parseInt(m[1], 10)))]
      .filter(pNum => !executedPostNums.has(pNum) && document.getElementById(`post-${pNum}`));

    let postActionButtons = '';
    if (mentionedPosts.length > 0) {
      mentionedPosts.slice(0, 3).forEach(pNum => {
        postActionButtons += `
          <button type="button" class="btn-top btn-ai-action-post" onclick="hostDeletePost(${pNum})" title="Permanently delete post #${pNum}">
            ${renderIcon('trash', 'ui-icon-xs')} Delete #${pNum}
          </button>
          <button type="button" class="btn-top btn-ai-action-ban" onclick="hostBanUserByTag(${pNum}, true)" title="Ban author of post #${pNum}">
            ${renderIcon('shield', 'ui-icon-xs')} Ban Author #${pNum}
          </button>
        `;
      });
    }

    botBubble.innerHTML = `
      <div class="ai-msg-meta">
        <strong>Qwen (${escapeHtml(data.model || targetModel)})</strong>
        <span class="ai-msg-time">${latencyBadge}</span>
      </div>
      <div class="ai-msg-content">${formatAiResponse(replyText)}</div>
      <div class="ai-msg-actions" style="margin-top:8px; display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end;">
        ${postActionButtons}
        <button type="button" class="btn-top" style="font-size:11px; padding:2px 8px;" onclick="copyAiReply(this)" title="Copy text to clipboard">
          ${renderIcon('copy', 'ui-icon-xs')} Copy
        </button>
        <button type="button" class="btn-top" style="font-size:11px; padding:2px 8px; color:var(--host-banner-text);" onclick="useAiAsAnnouncement(this)" title="Insert into Broadcast Announcement box">
          ${renderIcon('megaphone', 'ui-icon-xs')} Use as Broadcast
        </button>
      </div>
    `;
    box.appendChild(botBubble);

    aiChatHistory.push({ role: "user", content: prompt });
    aiChatHistory.push({ role: "assistant", content: replyText });

  } catch (err) {
    loadingBubble.remove();
    const errBubble = document.createElement("div");
    errBubble.className = "ai-msg-bubble ai-msg-bot";
    errBubble.innerHTML = `
      <div class="ai-msg-meta" style="color:#ef4444;">
        <strong>Error</strong>
        <span class="ai-msg-time">${timeStr}</span>
      </div>
      <div class="ai-msg-content" style="color:#ef4444;">
        Failed to communicate with Qwen AI: ${escapeHtml(err.message)}
      </div>
    `;
    box.appendChild(errBubble);
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    box.scrollTop = box.scrollHeight;
    input.focus();
  }
}

function formatAiResponse(text) {
  if (!text) return "";
  let formatted = escapeHtml(text);
  formatted = formatted.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
  formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');
  formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Format Action Executed & Failed badges
  formatted = formatted.replace(/(?:[🧹🗑️🚫🛡️🔥⚠️]\s*)?<strong>\[Action\s*(Executed|Failed):\s*([^\]]+)\]<\/strong>/gi, (match, status, content) => {
    const isBan = content.toLowerCase().includes('ban');
    const isPurge = content.toLowerCase().includes('purge');
    const isFail = status.toLowerCase() === 'failed';
    const cls = isFail ? 'ai-executed-badge badge-failed' : (isBan || isPurge ? 'ai-executed-badge badge-banned' : 'ai-executed-badge');
    const icon = isFail ? renderIcon('alert-triangle', 'ui-icon-xs') : (isBan ? renderIcon('shield', 'ui-icon-xs') : renderIcon('check-circle', 'ui-icon-xs'));
    return `<span class="${cls}">${icon} [Action ${status}: ${content}]</span>`;
  });
  return formatted;
}

function copyAiReply(btn) {
  const bubble = btn.closest(".ai-msg-bot");
  if (!bubble) return;
  const content = bubble.querySelector(".ai-msg-content");
  if (!content) return;
  navigator.clipboard.writeText(content.innerText).then(() => {
    showToast("AI reply copied to clipboard!", "info");
  }).catch(() => {
    showToast("Failed to copy", "error");
  });
}

function useAiAsAnnouncement(btn) {
  const bubble = btn.closest(".ai-msg-bot");
  if (!bubble) return;
  const content = bubble.querySelector(".ai-msg-content");
  if (!content) return;
  const text = content.innerText.trim();
  const announceInput = document.getElementById("announce-input");
  if (announceInput) {
    announceInput.value = text;
    announceInput.focus();
    showToast("Pasted into Broadcast Announcement box above!", "info");
  }
}

function loadHostDevices() {
  const tbody = document.getElementById("host-table-tbody");
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:15px;">Loading devices...</td></tr>';

  fetch("/api/host/identities")
    .then(r => r.json())
    .then(data => {
      const countEl = document.getElementById("host-device-count");
      if (countEl) countEl.innerText = (data.identities || []).length;
      if (!data.identities || data.identities.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:15px;">No devices recorded yet.</td></tr>';
        return;
      }
      tbody.innerHTML = data.identities.map(client => {
        const realNameDisplay = client.real_name 
          ? `<strong style="color:var(--text-animal);">${escapeHtml(client.real_name)}</strong>` 
          : '<em style="color:var(--text-date);">(None)</em>';

        const isBanned = client.is_banned === 1;
        const isMuted = client.is_muted === 1;

        let statusBadge = '<span style="color:#22c55e; font-weight:bold; display:inline-flex; align-items:center; gap:4px;"><span class="online-pulse-dot"></span> Active</span>';
        if (isBanned) statusBadge = `<span style="color:#ef4444; font-weight:bold; display:inline-flex; align-items:center; gap:4px;">${renderIcon('ban', 'ui-icon-xs')} Banned</span>`;
        else if (isMuted) statusBadge = `<span style="color:#eab308; font-weight:bold; display:inline-flex; align-items:center; gap:4px;">${renderIcon('volume-x', 'ui-icon-xs')} Mute</span>`;

        return `
          <tr>
            <td>
              <strong>${client.ip}</strong><br>
              <small style="color:var(--text-date);">${escapeHtml(client.hostname || 'No Hostname')}</small>
            </td>
            <td>
              ${realNameDisplay}<br>
              <button class="btn-host-mini" style="margin-top:3px;" onclick="promptAssignName('${client.ip}', '${escapeHtml(client.real_name || '')}')">${renderIcon('edit', 'ui-icon-xs')} Edit Name</button>
            </td>
            <td>
              <small>${escapeHtml(client.device_summary || 'Unknown Device')}</small><br>
              <small style="color:var(--text-date); font-family:monospace;">${escapeHtml(client.mac || '')}</small>
            </td>
            <td>${client.message_count || 0}</td>
            <td>${statusBadge}</td>
            <td>
              <div style="display:flex; gap:4px; flex-wrap:wrap;">
                <button class="btn-host-mini" onclick="hostMuteUser('${client.ip}', ${!isMuted})">${isMuted ? renderIcon('volume-2', 'ui-icon-xs') + ' Unmute' : renderIcon('volume-x', 'ui-icon-xs') + ' Mute'}</button>
                <button class="btn-host-mini btn-danger" onclick="hostToggleBan('${client.ip}', ${!isBanned})">${isBanned ? renderIcon('check-circle', 'ui-icon-xs') + ' Unban' : renderIcon('ban', 'ui-icon-xs') + ' Ban'}</button>
                <button class="btn-host-mini btn-danger" onclick="hostPurgeIP('${client.ip}')">${renderIcon('trash', 'ui-icon-xs')} Purge</button>
              </div>
            </td>
          </tr>
        `;
      }).join('');
    })
    .catch(e => {
      tbody.innerHTML = `<tr><td colspan="6" style="color:red; padding:15px;">Error: ${e.message}</td></tr>`;
    });
}

let allHostGroups = [];

function loadHostGroups() {
  const tbody = document.getElementById("host-groups-tbody");
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px;">Loading active groups...</td></tr>';

  fetch("/api/host/rooms")
    .then(r => r.json())
    .then(data => {
      allHostGroups = data.rooms || [];
      const countEl = document.getElementById("host-group-count");
      if (countEl) countEl.innerText = allHostGroups.length;
      const searchInput = document.getElementById("host-groups-search-input");
      filterHostGroups(searchInput ? searchInput.value : "");
    })
    .catch(e => {
      tbody.innerHTML = `<tr><td colspan="5" style="color:red; padding:15px;">Error: ${e.message}</td></tr>`;
    });
}

function filterHostGroups(query) {
  const tbody = document.getElementById("host-groups-tbody");
  if (!tbody) return;

  const q = (query || "").trim().toLowerCase();
  const filtered = allHostGroups.filter(room => {
    if (!q) return true;
    const nameMatch = (room.name || "").toLowerCase().includes(q);
    const idMatch = (room.room_id || "").toLowerCase().includes(q);
    const memberMatch = (room.members || []).some(m => 
      (m.anon_name || "").toLowerCase().includes(q) || 
      (m.public_id || "").toLowerCase().includes(q)
    );
    return nameMatch || idMatch || memberMatch;
  });

  if (filtered.length === 0) {
    if (allHostGroups.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px; color:var(--text-date);">No active groups or private rooms currently created.</td></tr>';
    } else {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:15px; color:var(--text-date);">No groups matching "${escapeHtml(query)}"</td></tr>`;
    }
    return;
  }

  tbody.innerHTML = filtered.map(room => {
    const membersList = (room.members || []).map(m => {
      const cIdx = getUserColorIndex(m.public_id || m.anon_name);
      return `<span class="user-color-${cIdx}" style="font-weight:600;">${escapeHtml(m.anon_name || 'User')}</span>`;
    }).join(', ');

    return `
      <tr>
        <td>
          <strong style="color:var(--text-main);">${escapeHtml(room.name || 'Private Chat')}</strong>
          <div style="font-size:11px; color:var(--text-date);">${room.created_at ? formatCleanTime(room.created_at) : ''}</div>
        </td>
        <td><code style="font-size:11px; background:var(--bg-post); padding:2px 5px; border-radius:3px;">${escapeHtml(room.room_id)}</code></td>
        <td>
          <div style="font-size:12px; margin-bottom:2px;"><strong>${(room.members || []).length}</strong> members</div>
          <div style="font-size:11.5px; max-width:240px; line-height:1.3;">${membersList || 'None'}</div>
        </td>
        <td>${room.message_count || 0}</td>
        <td>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            <button class="btn-host-mini" onclick="closeModals(); switchRoom('${room.room_id}');">${renderIcon('message-square', 'ui-icon-xs')} Enter</button>
            <button class="btn-host-mini btn-danger" onclick="hostDeleteGroup('${room.room_id}', '${escapeHtml(room.name)}')">${renderIcon('trash', 'ui-icon-xs')} Delete Group</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

async function hostDeleteGroup(roomId, roomName) {
  if (!confirm(`Are you sure you want to permanently delete group "${roomName || roomId}"? All messages and members will be removed.`)) return;

  try {
    const res = await fetch(`/api/host/delete_room/${encodeURIComponent(roomId)}`, {
      method: "POST"
    });
    const data = await res.json();
    if (data.status === "ok") {
      showToast(`Group "${roomName || roomId}" deleted successfully.`, "success");
      loadHostGroups();
    } else {
      showToast(`Error: ${data.detail || 'Could not delete group'}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

function adminDeleteCurrentRoom() {
  if (!isHost || currentRoom === "main") return;
  const room = activeRooms[currentRoom];
  const roomName = room ? room.name : currentRoom;
  hostDeleteGroup(currentRoom, roomName);
}

// 1. Assign Real Name
function promptAssignName(ip, currentName = "") {
  const newName = prompt(`Assign Real Name for ${ip} (visible ONLY on this laptop):`, currentName);
  if (newName === null) return;

  fetch("/api/host/assign_name", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip: ip, real_name: newName.trim() })
  })
  .then(r => r.json())
  .then(() => {
    if (document.getElementById("modal-host").style.display === "flex") {
      openHostModal();
    }
  })
  .catch(e => alert("Error: " + e.message));
}

function updateHostIdentityInDOM(ip, realName) {
  document.querySelectorAll(`.chat-box[data-ip="${ip}"]`).forEach(card => {
    const tag = card.querySelector(".host-ribbon-tag strong");
    if (tag) tag.innerText = realName;
  });
}

// 2. Hide Message
function hostHideMessage(postNum) {
  if (!confirm(`Hide message #${postNum}? It will be removed for all users.`)) return;
  fetch(`/api/host/delete/${postNum}`, { method: "POST" });
}

// 3. Ban / Unban
function hostToggleBan(ip, ban) {
  const action = ban ? "BAN" : "UNBAN";
  if (!confirm(`${action} user with IP ${ip}?`)) return;

  fetch("/api/host/ban", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip: ip, banned: ban })
  }).then(() => {
    if (document.getElementById("modal-host").style.display === "flex") {
      openHostModal();
    }
  });
}

// 4. Mute / Unmute
function hostMuteUser(ip, mute) {
  let minutes = 15;
  if (mute) {
    const m = prompt(`Mute ${ip} for how many minutes? (Enter 0 for permanent mute)`, "15");
    if (m === null) return;
    minutes = parseInt(m) || 0;
  }

  fetch("/api/host/mute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip: ip, muted: mute, minutes: minutes })
  }).then(() => {
    alert(`User ${ip} is now ${mute ? 'MUTED' : 'UNMUTED'}.`);
    if (document.getElementById("modal-host").style.display === "flex") {
      openHostModal();
    }
  });
}

// 5. Purge All Messages from an IP
function hostPurgeIP(ip) {
  if (!confirm(`Are you sure? This will delete ALL messages ever posted by ${ip}!`)) return;

  fetch("/api/host/purge_ip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip: ip })
  })
  .then(r => r.json())
  .then(data => {
    alert(`Purged ${data.deleted_count || 0} messages from ${ip}.`);
    if (document.getElementById("modal-host").style.display === "flex") {
      openHostModal();
    }
  });
}

// 6. Pin / Unpin Message
function hostPinMessage(postNum, pin) {
  fetch("/api/host/pin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ post_num: postNum, pinned: pin })
  }).then(() => {
    loadMessages();
  });
}

// 7. Broadcast Announcement
function hostBroadcastAnnouncement() {
  const input = document.getElementById("announce-input");
  const msg = (input ? input.value : "").trim();
  if (!msg) return;

  fetch("/api/host/announce", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: msg })
  }).then(() => {
    alert("Announcement broadcasted!");
    if (input) input.value = "";
  });
}

// Clear all messages
function hostClearAll() {
  if (!confirm("Are you sure you want to clear ALL messages for everyone?")) return;
  fetch("/api/host/clear", { method: "POST" });
}

function closeModals() {
  document.querySelectorAll(".modal-overlay").forEach(m => m.style.display = "none");
}

// WhatsApp-Style Profile & DP Zoom Modal
function showUserProfileModal(publicId, anonName, avatarUrl, sciName = "", ip = "", realName = "") {
  currentProfileTarget = publicId;
  const imgEl = document.getElementById("profile-modal-img");
  if (imgEl) {
    imgEl.src = avatarUrl;
    imgEl.classList.remove("zoomed");
  }

  const nameEl = document.getElementById("profile-modal-name");
  const sciEl = document.getElementById("profile-modal-sci");
  if (nameEl) {
    nameEl.innerText = anonName || "Anonymous Member";
    const profileColor = getUserColorIndex(publicId || anonName || "anon");
    nameEl.className = `profile-modal-name user-color-${profileColor}`;
  }
  if (sciEl) sciEl.innerText = sciName || "Anonymous Member";

  const hostIntel = document.getElementById("profile-modal-host-intel");
  if (hostIntel) {
    if (isHost && (ip || realName)) {
      hostIntel.style.display = "block";
      hostIntel.innerHTML = `<strong>Host Intel:</strong> ${realName ? `Name: <strong>${escapeHtml(realName)}</strong> | ` : ''}IP: <code>${escapeHtml(ip)}</code>`;
    } else {
      hostIntel.style.display = "none";
    }
  }

  const actionsEl = document.getElementById("profile-modal-actions");
  if (actionsEl) {
    if (publicId && publicId === myIdentity.id) {
      actionsEl.innerHTML = '<div style="color:var(--text-date); font-size:12px; padding:6px;">This is your assigned anonymous identity.</div>';
    } else if (publicId) {
      let extraGroupBtn = '';
      if (currentRoom !== 'main' && activeRooms[currentRoom]) {
        const room = activeRooms[currentRoom];
        const isAlreadyMember = room.members && room.members.some(m => m.public_id === publicId);
        if (!isAlreadyMember) {
          extraGroupBtn = `
            <button class="btn-start-dm" style="background:#10b981; margin-top:8px;" onclick="addUserToRoom('${publicId}')">
              ${renderIcon('users', 'ui-icon-sm')}
              <span>Invite to Current Group</span>
            </button>
          `;
        }
      }
      actionsEl.innerHTML = `
        <button class="btn-start-dm" onclick="sendPrivateChatRequest('${publicId}')">
          ${renderIcon('message-square', 'ui-icon-sm')}
          <span>Request 1-on-1 Private Chat</span>
        </button>
        ${extraGroupBtn}
      `;
    } else {
      actionsEl.innerHTML = '';
    }
  }

  const modal = document.getElementById("modal-profile");
  if (modal) modal.style.display = "flex";
}

function showMyProfileModal() {
  showUserProfileModal(myIdentity.id, myIdentity.name, myIdentity.avatar, myIdentity.sci_name);
}

function toggleProfileZoom() {
  const img = document.getElementById("profile-modal-img");
  if (img) {
    img.classList.toggle("zoomed");
  }
}

function sendPrivateChatRequest(targetPublicId) {
  closeModals();
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    alert("Connecting to chat server...");
    return;
  }
  ws.send(JSON.stringify({
    action: "request_private_chat",
    target_public_id: targetPublicId
  }));
}

function respondPrivateChat(accept) {
  closeModals();
  if (currentInviteId && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      action: "respond_private_chat",
      invite_id: currentInviteId,
      accepted: accept
    }));
  }
  currentInviteId = null;
}

// Room Switching, Groups & Navigation
function renderRoomChips() {
  // 1. Calculate active private groups count
  const privateRooms = Object.values(activeRooms).filter(r => {
    if (PUBLIC_STREAMS[r.room_id]) return false;
    if (!isHost) {
      return r.members && r.members.some(m => m.public_id === myIdentity.id);
    }
    return true;
  });

  const groupCount = privateRooms.length;

  // 2. Update Topbar & Hamburger badges
  const btnMyGroups = document.getElementById("btn-my-groups");
  const badgeTop = document.getElementById("user-groups-badge");
  const badgeMenu = document.getElementById("menu-groups-count");

  if (badgeTop) badgeTop.textContent = groupCount;
  if (badgeMenu) badgeMenu.textContent = groupCount;
  if (btnMyGroups) {
    btnMyGroups.style.display = groupCount > 0 ? "inline-flex" : "none";
  }

  // 3. Highlight current stream/room in hamburger dropdown
  document.querySelectorAll(".hamburger-item").forEach(el => {
    const onclickAttr = el.getAttribute("onclick") || "";
    if (onclickAttr.includes(`switchRoom('${currentRoom}')`)) {
      el.classList.add("hamburger-item-active");
    } else {
      el.classList.remove("hamburger-item-active");
    }
  });

  // 4. Update My Groups modal list if open
  const modalGroups = document.getElementById("modal-user-groups");
  if (modalGroups && modalGroups.style.display === "flex") {
    const searchVal = document.getElementById("user-groups-search")?.value || "";
    renderUserGroupsList(searchVal);
  }
}

function openUserGroupsModal() {
  const modal = document.getElementById("modal-user-groups");
  if (!modal) return;
  modal.style.display = "flex";
  const searchInput = document.getElementById("user-groups-search");
  if (searchInput) searchInput.value = "";
  renderUserGroupsList("");
  if (searchInput) searchInput.focus();
}

function filterUserGroups(query) {
  renderUserGroupsList(query);
}

function renderUserGroupsList(filterQuery = "") {
  const container = document.getElementById("user-groups-list");
  if (!container) return;

  const q = (filterQuery || "").trim().toLowerCase();

  // Get all active private groups (excluding public streams)
  const groups = Object.values(activeRooms).filter(r => {
    if (PUBLIC_STREAMS[r.room_id]) return false;
    if (!isHost) {
      const isParticipant = r.members && r.members.some(m => m.public_id === myIdentity.id);
      if (!isParticipant) return false;
    }
    return true;
  });

  // Filter if query is provided
  const filtered = groups.filter(room => {
    if (!q) return true;
    const nameMatch = (room.name || "Private Chat").toLowerCase().includes(q);
    const idMatch = (room.room_id || "").toLowerCase().includes(q);
    const memberMatch = (room.members || []).some(m => 
      (m.anon_name || "").toLowerCase().includes(q) || 
      (m.public_id || "").toLowerCase().includes(q)
    );
    return nameMatch || idMatch || memberMatch;
  });

  if (filtered.length === 0) {
    if (q) {
      container.innerHTML = `
        <div style="text-align:center; padding:24px 12px; color:var(--text-date);">
          <div style="font-size:13px; font-weight:500; margin-bottom:4px;">No groups match "${escapeHtml(filterQuery)}"</div>
          <div style="font-size:11.5px;">Try a different name or member tag.</div>
        </div>
      `;
    } else {
      container.innerHTML = `
        <div style="text-align:center; padding:32px 16px; color:var(--text-date);">
          <div style="margin-bottom:8px; opacity:0.6;">
            ${renderIcon('users', 'ui-icon-lg')}
          </div>
          <div style="font-size:13.5px; font-weight:600; color:var(--text-main); margin-bottom:4px;">No Active Private Groups</div>
          <div style="font-size:12px; max-width:280px; margin:0 auto 16px; line-height:1.4;">You aren't in any private group chats right now. Match with someone or invite users to chat privately!</div>
          <button class="btn-top" onclick="closeModals(); findSomeoneToChat();" style="font-size:12px; padding:6px 14px; background:var(--btn-submit); color:var(--btn-submit-text, #fff);">
            ${renderIcon('shuffle', 'ui-icon-xs')} Match in Find Someone
          </button>
        </div>
      `;
    }
    return;
  }

  let html = '';
  filtered.forEach(room => {
    const isCurrent = (currentRoom === room.room_id);
    const unread = unreadRooms[room.room_id] || 0;
    const isParticipant = room.members && room.members.some(m => m.public_id === myIdentity.id);
    const isSpectator = isHost && !isParticipant;

    const membersSummary = (room.members || []).map(m => {
      const isMe = (m.public_id === myIdentity.id);
      const cIdx = getUserColorIndex(m.public_id || m.anon_name);
      return `<span class="user-color-${cIdx}" style="font-weight:600;">${escapeHtml(m.anon_name || 'User')}${isMe ? ' (You)' : ''}</span>`;
    }).join(', ');

    html += `
      <div class="user-group-card ${isCurrent ? 'active-group-card' : ''}">
        <div class="user-group-card-info">
          <div class="user-group-card-header">
            <strong class="user-group-card-name">${escapeHtml(room.name || 'Private Chat')}</strong>
            <div style="display:inline-flex; align-items:center; gap:6px;">
              ${isSpectator ? `<span class="group-admin-monitor-badge">ADMIN MONITOR</span>` : ''}
              ${isCurrent ? `<span class="group-current-badge">ACTIVE NOW</span>` : ''}
              ${unread > 0 ? `<span class="room-unread-badge">${unread}</span>` : ''}
            </div>
          </div>
          <div class="user-group-card-members">
            <span style="color:var(--text-date); font-size:11px;">Members (${(room.members || []).length}):</span> ${membersSummary || '<span style="color:var(--text-date);">Empty</span>'}
          </div>
        </div>
        <div class="user-group-card-actions">
          <button class="btn-top btn-group-open" onclick="closeModals(); switchRoom('${room.room_id}');" title="Open this group chat">
            ${renderIcon('message-square', 'ui-icon-xs')} Open
          </button>
          ${isSpectator ? `
            <button class="btn-top" style="color:#ef4444; font-size:11.5px; padding:3px 8px;" onclick="hostDeleteGroup('${room.room_id}', '${escapeHtml(room.name)}')" title="Admin: Permanently delete group">
              ${renderIcon('trash', 'ui-icon-xs')} Delete
            </button>
          ` : `
            <button class="btn-top btn-group-leave" onclick="leaveSpecificRoom('${room.room_id}')" title="Leave this group">
              ${renderIcon('log-out', 'ui-icon-xs')} Leave
            </button>
          `}
        </div>
      </div>
    `;
  });

  container.innerHTML = html;
}

function leaveSpecificRoom(roomId) {
  if (!roomId || PUBLIC_STREAMS[roomId]) return;
  const room = activeRooms[roomId];
  const roomName = room ? room.name : "this chat";
  if (!confirm(`Are you sure you want to leave "${roomName}"?`)) return;

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      action: "leave_room",
      room_id: roomId
    }));
  }

  delete activeRooms[roomId];
  delete unreadRooms[roomId];
  if (localStorage.getItem("active_room_id") === roomId) {
    localStorage.removeItem("active_room_id");
  }
  renderRoomChips();
  if (currentRoom === roomId) {
    switchRoom("main");
  }
  renderUserGroupsList(document.getElementById("user-groups-search")?.value || "");
}

function switchRoom(roomId) {
  currentRoom = roomId;
  unreadRooms[roomId] = 0;
  if (PUBLIC_STREAMS[roomId]) {
    localStorage.setItem("active_room_id", roomId);
  } else {
    localStorage.removeItem("active_room_id");
  }
  renderRoomChips();

  const banner = document.getElementById("private-room-banner");
  const textarea = document.getElementById("chat-textarea");
  const boardTitle = document.getElementById("board-room-title");
  const boardSubtitle = document.getElementById("board-room-subtitle");

  const streamInfo = PUBLIC_STREAMS[roomId];
  if (streamInfo) {
    if (banner) banner.style.display = "none";
    if (boardTitle) boardTitle.textContent = streamInfo.title;
    if (boardSubtitle) boardSubtitle.textContent = streamInfo.subtitle;
    if (textarea) textarea.placeholder = "";
  } else {
    if (banner) banner.style.display = "flex";
    updatePrivateRoomBanner();
    const room = activeRooms[roomId];
    if (boardTitle) boardTitle.textContent = room ? room.name : 'Private Chat';
    if (boardSubtitle) boardSubtitle.textContent = "Encrypted Anonymous Private Room";
    if (textarea) textarea.placeholder = "";
  }

  loadMessages(roomId);
}

function updatePrivateRoomBanner() {
  const room = activeRooms[currentRoom];
  const titleEl = document.getElementById("private-room-title");
  const membersEl = document.getElementById("private-room-members");
  const adminDeleteBtn = document.getElementById("btn-admin-delete-room");

  if (adminDeleteBtn) {
    adminDeleteBtn.style.display = (isHost && !PUBLIC_STREAMS[currentRoom]) ? "inline-flex" : "none";
  }

  if (room) {
    const isParticipant = room.members && room.members.some(m => m.public_id === myIdentity.id);
    const isSpectator = isHost && !isParticipant;

    if (titleEl) {
      if (isSpectator) {
        titleEl.innerHTML = `<span style="display:inline-flex; align-items:center; gap:6px;">${escapeHtml(room.name || "Private Chat")} <span style="font-size:10px; padding:2px 6px; border-radius:10px; background:rgba(245, 158, 11, 0.2); color:#f59e0b; border:1px solid rgba(245, 158, 11, 0.4); font-weight:700;">ADMIN MONITOR</span></span>`;
      } else {
        titleEl.innerText = room.name || "Private Chat";
      }
    }
    if (membersEl && room.members) {
      const namesHTML = room.members.map(m => {
        const cIdx = getUserColorIndex(m.public_id || m.anon_name);
        return `<span class="user-color-${cIdx}" style="font-weight:600;">${escapeHtml(m.anon_name || "Anonymous")}</span>`;
      }).join(", ");
      membersEl.innerHTML = `Participants (${room.members.length}): ${namesHTML}`;
    }
  }
}

async function loadRooms() {
  try {
    const res = await fetch(`/api/rooms?session_id=${encodeURIComponent(storedSession)}`);
    const data = await res.json();
    if (data.rooms) {
      activeRooms = {};
      data.rooms.forEach(r => {
        activeRooms[r.room_id] = r;
      });
      renderRoomChips();

      const savedRoom = localStorage.getItem("active_room_id");
      if (savedRoom && PUBLIC_STREAMS[savedRoom]) {
        switchRoom(savedRoom);
      } else if (savedRoom && activeRooms[savedRoom]) {
        switchRoom(savedRoom);
      } else {
        switchRoom("main");
      }
    }
  } catch (e) {
    console.error("Failed to load rooms", e);
  }
}

// Add User to Room modal (Available for group/private chats)
async function openAddUserModal() {
  if (currentRoom === "main") {
    alert("Switch to or start a private chat first to add users.");
    return;
  }
  const modal = document.getElementById("modal-add-user");
  const list = document.getElementById("online-users-list");
  if (!modal || !list) return;

  modal.style.display = "flex";
  list.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-date);">Loading active users...</div>';

  try {
    const res = await fetch("/api/online_users");
    const data = await res.json();
    const users = data.users || [];

    const room = activeRooms[currentRoom];
    const existingMemberIds = room && room.members ? room.members.map(m => m.public_id) : [];

    const filtered = users.filter(u => u.public_id !== myIdentity.id && !existingMemberIds.includes(u.public_id));

    if (filtered.length === 0) {
      list.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-date);">No other online users available to add right now.</div>';
      return;
    }

    list.innerHTML = filtered.map(u => `
      <div class="online-user-item">
        <div class="online-user-meta">
          <img src="${u.anon_avatar}" class="online-user-avatar" alt="Avatar">
          <div>
            <strong class="user-color-${getUserColorIndex(u.public_id || u.anon_name)}">${escapeHtml(u.anon_name)}</strong>
            ${u.sci_name ? `<div style="font-size:11px; color:var(--text-date);">${escapeHtml(u.sci_name)}</div>` : ''}
          </div>
        </div>
        <button class="btn-top" style="background:var(--btn-submit); color:#fff;" onclick="addUserToRoom('${u.public_id}')">
          ${renderIcon('mail', 'ui-icon-xs')}
          <span>Send Invite</span>
        </button>
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = '<div style="text-align:center; padding:20px; color:#ef4444;">Failed to load online users.</div>';
  }
}

function addUserToRoom(targetPublicId) {
  closeModals();
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      action: "add_user_to_room",
      room_id: currentRoom,
      target_public_id: targetPublicId
    }));
  }
}

function adminAddUserToRoom(targetPublicId) {
  addUserToRoom(targetPublicId);
}

// Leave Current Group / Private Chat
function leaveCurrentRoom() {
  if (currentRoom === "main") return;

  const room = activeRooms[currentRoom];
  const roomName = room ? room.name : "this chat";
  if (!confirm(`Are you sure you want to leave "${roomName}"? You will no longer receive messages in this group.`)) {
    return;
  }

  const leavingRoomId = currentRoom;

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      action: "leave_room",
      room_id: leavingRoomId
    }));
  }

  // Remove room locally and return to public board
  delete activeRooms[leavingRoomId];
  if (localStorage.getItem("active_room_id") === leavingRoomId) {
    localStorage.removeItem("active_room_id");
  }
  renderRoomChips();
  switchRoom("main");
}

async function loadMessages(roomId = "main") {
  try {
    const res = await fetch(`/api/messages?room_id=${encodeURIComponent(roomId)}&limit=150&session_id=${encodeURIComponent(storedSession)}`);
    const data = await res.json();
    isHost = data.is_host;
    updateMyIdentityUI();

    const stream = document.getElementById("posts-stream");
    stream.innerHTML = "";
    if (data.messages && data.messages.length > 0) {
      data.messages.forEach(msg => renderPost(msg, false));
      setTimeout(() => scrollToBottom(false), 80);
    } else {
      const streamInfo = PUBLIC_STREAMS[roomId];
      const isPrivate = !streamInfo;
      stream.innerHTML = `<div style="text-align:center; padding: 40px; color: var(--text-date);">
        ${isPrivate ? `<div style="margin-bottom:8px;">${renderIcon('lock', 'ui-icon-lg', 'style="color:var(--text-date); opacity:0.7;"')}</div>This is the start of your private group chat. Messages here are only visible to members.` : `No messages yet in ${streamInfo ? streamInfo.name : 'this stream'}. Drop the first message below!`}
      </div>`;
    }
  } catch (e) {
    console.error("Load messages error", e);
  }
}

function scrollToBottom(smooth = true) {
  window.scrollTo({
    top: document.documentElement.scrollHeight,
    behavior: smooth ? "smooth" : "auto"
  });
}

window.addEventListener("beforeunload", () => {
  if (navigator.sendBeacon) {
    navigator.sendBeacon(`/api/leave_all_rooms?session_id=${encodeURIComponent(storedSession)}`);
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const savedTheme = localStorage.getItem("anon_theme") || "mono-noir";
  changeTheme(savedTheme);

  // On page reload/refresh, remove user from private groups (per refresh-to-leave requirement)
  fetch(`/api/leave_all_rooms?session_id=${encodeURIComponent(storedSession)}`, { method: "POST" }).catch(() => {});

  let savedRoom = localStorage.getItem("active_room_id") || "main";
  if (!PUBLIC_STREAMS[savedRoom]) {
    savedRoom = "main";
    localStorage.setItem("active_room_id", "main");
  }
  switchRoom(savedRoom);
  loadRooms();
  initWebSocket();
  loadStickersAndGifs();

  // Auto-open Host Console if on localhost and visiting /admin or /host
  if (window.location.pathname.startsWith('/admin') || window.location.pathname.startsWith('/host') || window.location.search.includes('admin')) {
    setTimeout(() => {
      const hostBtn = document.getElementById("btn-host-modal");
      if (hostBtn) {
        openHostModal();
      }
    }, 350);
  }

  // Enter to send (Shift+Enter for new line), Escape to cancel reply
  const textarea = document.getElementById("chat-textarea");
  if (textarea) {
    textarea.placeholder = "";
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      } else if (e.key === "Escape") {
        cancelReply();
        closeStickersTray();
      }
    });
  }

  // Global Escape handler to close modals & menus
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeHamburgerMenu();
      closeStickersTray();
      const broadcastModal = document.getElementById("modal-broadcast-popup");
      if (broadcastModal && broadcastModal.style.display === "flex") {
        closeBroadcastModal();
        return;
      }
      const inviteModal = document.getElementById("modal-invite");
      if (inviteModal && inviteModal.style.display === "flex") {
        respondPrivateChat(false);
        return;
      }
      let anyModalClosed = false;
      document.querySelectorAll(".modal-overlay").forEach(m => {
        if (m.style.display === "flex") {
          m.style.display = "none";
          anyModalClosed = true;
        }
      });
      if (anyModalClosed) return;

      if (replyingTo) {
        cancelReply();
      }
    }
  });

  // Auto-grow textarea like WhatsApp & handle Gboard GIF/Sticker URL interception
  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";

    // Detect if Gboard or user inserted a GIF/sticker URL into the text
    const currentVal = textarea.value;
    const mediaUrl = extractMediaUrlFromText(currentVal);
    if (mediaUrl) {
      // Remove URL immediately so it never shows as raw text in the input box
      textarea.value = currentVal.replace(mediaUrl, "").trim();
      textarea.style.height = "auto";
      textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
      attachMediaUrl(mediaUrl);
    }
  });

  // Intercept beforeinput (Gboard text replacement fallback)
  textarea.addEventListener("beforeinput", (e) => {
    let text = e.data;
    if (!text && e.dataTransfer) {
      try { text = e.dataTransfer.getData("text/plain"); } catch (err) {}
    }
    if (text) {
      const mediaUrl = extractMediaUrlFromText(text.trim());
      if (mediaUrl) {
        e.preventDefault();
        attachMediaUrl(mediaUrl);
      }
    }
  });

  // File input
  const fileInput = document.getElementById("file-input");
  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      if (e.target.files.length > 0) handleImageUpload(e.target.files[0]);
    });
  }

  // Intercept paste on textarea and window for Gboard GIFs/stickers and clipboard images
  function handlePasteEvent(e) {
    const cd = e.clipboardData || (e.originalEvent && e.originalEvent.clipboardData);
    if (!cd) return;

    // 1. Check if Gboard or clipboard provided an image / GIF file
    if (cd.items && cd.items.length > 0) {
      for (let i = 0; i < cd.items.length; i++) {
        const item = cd.items[i];
        if (item.kind === 'file' && item.type && item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) {
            e.preventDefault();
            e.stopPropagation();
            handleImageUpload(file);
            return;
          }
        }
      }
    }

    if (cd.files && cd.files.length > 0) {
      for (let i = 0; i < cd.files.length; i++) {
        const file = cd.files[i];
        if (file.type && file.type.startsWith('image/')) {
          e.preventDefault();
          e.stopPropagation();
          handleImageUpload(file);
          return;
        }
      }
    }

    // 2. Check if Gboard pasted a GIF / Sticker / Media URL
    let text = "";
    try { text = cd.getData('text/plain'); } catch (err) {}
    if (text) {
      const mediaUrl = extractMediaUrlFromText(text.trim());
      if (mediaUrl) {
        e.preventDefault();
        e.stopPropagation();
        attachMediaUrl(mediaUrl);
        return;
      }
    }
  }

  textarea.addEventListener("paste", handlePasteEvent);
  window.addEventListener("paste", handlePasteEvent);
});

async function attachMediaUrl(url) {
  if (!url) return;
  const raw = url.trim();
  const chip = document.getElementById("preview-chip");
  const thumbImg = document.getElementById("preview-thumb-img");
  const filenameEl = document.getElementById("preview-filename");

  const lower = raw.toLowerCase();
  const isSticker = lower.includes("sticker") || lower.endsWith(".svg");
  const isGif = lower.includes(".gif") || lower.includes("tenor.com") || lower.includes("giphy.com");
  const mediaType = isSticker ? "Sticker" : (isGif ? "GIF" : "Image");

  if (chip && thumbImg && filenameEl) {
    thumbImg.src = raw;
    filenameEl.innerText = `${mediaType} attached`;
    chip.style.display = "inline-flex";
  }

  pendingImage = {
    path: raw,
    thumb: raw,
    name: mediaType,
    size: 0,
    dims: isSticker ? "120x120" : "240x240"
  };

  // If it is a Tenor or Giphy web page, resolve it to the direct animated GIF URL
  if (raw.includes("tenor.com/view/") || raw.includes("giphy.com/gifs/")) {
    try {
      const res = await fetch("/api/resolve_media", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: raw })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.status === "ok" && data.resolved) {
          pendingImage.path = data.resolved;
          pendingImage.thumb = data.resolved;
          if (thumbImg) thumbImg.src = data.resolved;
        }
      }
    } catch (e) {
      console.warn("Could not resolve media url:", e);
    }
  }
}

function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Share & Invite Modal Functions
async function openShareModal() {
  const modal = document.getElementById("modal-share");
  if (!modal) return;
  modal.style.display = "flex";

  try {
    const res = await fetch("/api/share_info");
    if (res.ok) {
      const data = await res.json();
      const pubInput = document.getElementById("share-public-url-input");
      const lanInput = document.getElementById("share-lan-url-input");
      const qrImg = document.getElementById("share-qr-img");
      const badge = document.getElementById("share-badge-status");

      if (data.public_url) {
        if (pubInput) pubInput.value = data.public_url;
        if (badge) {
          badge.textContent = "Active 24/7";
          badge.style.background = "rgba(52,211,153,0.15)";
          badge.style.color = "#34d399";
        }
      } else {
        if (pubInput) pubInput.value = window.location.origin;
        if (badge) {
          badge.textContent = "Local Host";
          badge.style.background = "rgba(251,191,36,0.15)";
          badge.style.color = "#fbbf24";
        }
      }

      if (data.lan_url && lanInput) {
        lanInput.value = data.lan_url;
      }

      if (qrImg) {
        const activeTarget = data.public_url || data.lan_url || window.location.origin;
        qrImg.src = `/api/qrcode?custom_url=${encodeURIComponent(activeTarget)}&t=${Date.now()}`;
      }
    }
  } catch (e) {
    console.error("Could not fetch share info", e);
  }
}

function copyShareUrl(inputId) {
  const input = document.getElementById(inputId);
  if (!input || !input.value) return;
  navigator.clipboard.writeText(input.value).then(() => {
    showToast("Link copied to clipboard! Send it to anyone.");
  }).catch(() => {
    input.select();
    document.execCommand("copy");
    showToast("Link copied to clipboard!");
  });
}

function downloadQrCode() {
  const qrImg = document.getElementById("share-qr-img");
  if (!qrImg) return;
  const a = document.createElement("a");
  a.href = qrImg.src;
  a.download = "anonchat-qr.png";
  a.target = "_blank";
  a.click();
}

async function nativeShareLink() {
  const pubInput = document.getElementById("share-public-url-input");
  const shareUrl = (pubInput && pubInput.value) ? pubInput.value : window.location.origin;
  if (navigator.share) {
    try {
      await navigator.share({
        title: "/anon/ Anonymous Chat",
        text: "Join me on /anon/ - 100% anonymous real-time chat with no signup!",
        url: shareUrl
      });
    } catch (err) {
      if (err.name !== "AbortError") {
        copyShareUrl("share-public-url-input");
      }
    }
  } else {
    copyShareUrl("share-public-url-input");
  }
}


// Hamburger Menu Dropdown Controls
function toggleHamburgerMenu(e) {
  if (e) {
    e.stopPropagation();
  }
  const dropdown = document.getElementById("hamburger-dropdown");
  const btn = document.getElementById("btn-hamburger");
  if (!dropdown) return;
  const isHidden = (dropdown.style.display === "none" || !dropdown.classList.contains("open"));
  if (isHidden) {
    dropdown.style.display = "block";
    dropdown.classList.add("open");
    if (btn) btn.classList.add("active");
  } else {
    dropdown.style.display = "none";
    dropdown.classList.remove("open");
    if (btn) btn.classList.remove("active");
  }
}

function closeHamburgerMenu() {
  const dropdown = document.getElementById("hamburger-dropdown");
  const btn = document.getElementById("btn-hamburger");
  if (dropdown) {
    dropdown.style.display = "none";
    dropdown.classList.remove("open");
  }
  if (btn) btn.classList.remove("active");
}

// Close hamburger menu when clicking outside
document.addEventListener("click", (e) => {
  const wrapper = document.getElementById("hamburger-menu-wrapper");
  if (wrapper && !wrapper.contains(e.target)) {
    closeHamburgerMenu();
  }
});

// --- Anonymous Feedback Form & Host Moderation Actions ---

function openFeedbackModal() {
  const modal = document.getElementById("modal-feedback");
  if (modal) {
    modal.style.display = "flex";
    const msg = document.getElementById("feedback-message");
    if (msg) msg.focus();
  }
}

function setFeedbackRating(stars) {
  const valInput = document.getElementById("feedback-rating-val");
  const label = document.getElementById("feedback-rating-label");
  if (valInput) valInput.value = stars;
  if (label) label.textContent = `${stars} / 5 Stars`;

  const starBtns = document.querySelectorAll("#feedback-star-rating .star-btn");
  starBtns.forEach(btn => {
    const r = parseInt(btn.getAttribute("data-rating"), 10);
    if (r <= stars) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });
}

async function submitAnonymousFeedback(e) {
  if (e && e.preventDefault) e.preventDefault();
  const catEl = document.getElementById("feedback-category");
  const ratingEl = document.getElementById("feedback-rating-val");
  const msgEl = document.getElementById("feedback-message");
  const submitBtn = document.getElementById("btn-submit-feedback");

  if (!msgEl || !msgEl.value.trim()) {
    showToast("Please write a message for your feedback.", "error");
    return;
  }

  const category = catEl ? catEl.value : "General Suggestion";
  const rating = ratingEl ? parseInt(ratingEl.value, 10) : 5;
  const message = msgEl.value.trim();

  if (submitBtn) submitBtn.disabled = true;

  try {
    const res = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category, rating, message })
    });

    const data = await res.json();
    if (res.ok && data.status === "ok") {
      showToast("Thank you! Anonymous feedback submitted successfully.", "info");
      msgEl.value = "";
      closeModals();
    } else {
      showToast(data.detail || "Failed to submit feedback", "error");
    }
  } catch (err) {
    showToast("Error submitting feedback: " + err.message, "error");
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

async function loadHostFeedbacks() {
  const tbody = document.getElementById("host-feedbacks-tbody");
  const countEl = document.getElementById("host-feedback-count");
  if (!tbody) return;

  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px;">Loading feedbacks...</td></tr>';

  try {
    const res = await fetch("/api/host/feedbacks");
    const data = await res.json();
    const feedbacks = data.feedbacks || [];
    if (countEl) countEl.textContent = feedbacks.length;

    if (feedbacks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px; color:var(--text-date);">No feedbacks received yet.</td></tr>';
      return;
    }

    let html = '';
    feedbacks.forEach(f => {
      const stars = "★".repeat(f.rating || 5) + "☆".repeat(5 - (f.rating || 5));
      html += `
        <tr>
          <td style="white-space:nowrap; font-size:11px; color:var(--text-date);">${escapeHtml(f.created_at || '')}</td>
          <td><span class="feedback-cat-tag">${escapeHtml(f.category || 'General')}</span></td>
          <td style="color:#f59e0b; white-space:nowrap;">${stars}</td>
          <td style="max-width:320px; word-break:break-word; font-size:12.5px;">${escapeHtml(f.message || '')}</td>
          <td>
            <button class="btn-top" style="color:#ef4444; font-size:11px; padding:2px 8px;" onclick="deleteHostFeedback(${f.id})" title="Delete this feedback">
              ${renderIcon('trash', 'ui-icon-xs')} Delete
            </button>
          </td>
        </tr>
      `;
    });
    tbody.innerHTML = html;
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:15px; color:#ef4444;">Failed to load feedbacks: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function deleteHostFeedback(id) {
  if (!confirm(`Delete feedback #${id}?`)) return;
  try {
    const res = await fetch(`/api/host/feedbacks/delete/${id}`, { method: "POST" });
    if (res.ok) {
      showToast("Feedback deleted", "info");
      loadHostFeedbacks();
    }
  } catch (err) {
    showToast("Failed to delete feedback", "error");
  }
}

async function hostDeletePost(postNum) {
  if (!confirm(`Permanently delete post #${postNum}?`)) return;
  try {
    const res = await fetch(`/api/host/delete/${postNum}`, { method: "POST" });
    if (res.ok) {
      showToast(`Post #${postNum} deleted!`, "info");
    } else {
      showToast(`Failed to delete post #${postNum}`, "error");
    }
  } catch (err) {
    showToast("Error deleting post: " + err.message, "error");
  }
}

async function hostBanUserByTag(userOrPost, isPostNum = false) {
  const label = isPostNum ? `author of post #${userOrPost}` : `user #${userOrPost}`;
  if (!confirm(`Ban ${label} and terminate their connection?`)) return;

  try {
    const body = isPostNum ? { post_num: parseInt(userOrPost, 10), banned: true } : { user_id: userOrPost, banned: true };
    const res = await fetch("/api/host/ban_by_user", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (res.ok && data.status === "ok") {
      showToast(`Banned IP ${data.ip} successfully!`, "info");
    } else {
      showToast(data.detail || "Failed to ban user", "error");
    }
  } catch (err) {
    showToast("Error banning user: " + err.message, "error");
  }
}

// ==========================================
// Stickers and Animated GIFs System
// ==========================================
let loadedStickers = [];
let loadedGifs = [];
let stickersLoaded = false;
let activeStickerTab = "stickers";
let activeStickerCategory = "all";
let stickerSearchQuery = "";

async function loadStickersAndGifs() {
  if (stickersLoaded) return;
  try {
    const res = await fetch("/api/stickers_and_gifs");
    if (!res.ok) return;
    const data = await res.json();
    if (data.status === "ok") {
      loadedStickers = data.stickers || [];
      loadedGifs = data.gifs || [];
      stickersLoaded = true;
      renderStickersGrid();
      renderGifsGrid();
    }
  } catch (err) {
    console.error("Failed to load stickers and gifs:", err);
  }
}

function toggleStickersTray() {
  const tray = document.getElementById("stickers-tray");
  if (!tray) return;

  if (tray.style.display === "none" || !tray.style.display) {
    tray.style.display = "flex";
    if (!stickersLoaded) {
      loadStickersAndGifs();
    } else {
      renderStickersGrid();
      renderGifsGrid();
    }
  } else {
    tray.style.display = "none";
  }
}

function closeStickersTray() {
  const tray = document.getElementById("stickers-tray");
  if (tray) tray.style.display = "none";
}

function switchStickerTab(tab) {
  activeStickerTab = tab;
  const stickersView = document.getElementById("stickers-view");
  const gifsView = document.getElementById("gifs-view");
  const tabStickers = document.getElementById("tab-btn-stickers");
  const tabGifs = document.getElementById("tab-btn-gifs");

  if (tab === "stickers") {
    if (stickersView) stickersView.style.display = "flex";
    if (gifsView) gifsView.style.display = "none";
    if (tabStickers) tabStickers.classList.add("active");
    if (tabGifs) tabGifs.classList.remove("active");
  } else {
    if (stickersView) stickersView.style.display = "none";
    if (gifsView) gifsView.style.display = "flex";
    if (tabStickers) tabStickers.classList.remove("active");
    if (tabGifs) tabGifs.classList.add("active");
  }
  filterStickersTray(stickerSearchQuery);
}

function filterStickerCategory(category, chipBtn) {
  activeStickerCategory = category;
  document.querySelectorAll(".sticker-cat-chip").forEach(btn => btn.classList.remove("active"));
  if (chipBtn) chipBtn.classList.add("active");
  renderStickersGrid();
}

function filterStickersTray(query) {
  stickerSearchQuery = (query || "").trim().toLowerCase();
  if (activeStickerTab === "stickers") {
    renderStickersGrid();
  } else {
    renderGifsGrid();
  }
}

function renderStickersGrid() {
  const grid = document.getElementById("stickers-grid");
  if (!grid) return;

  let filtered = loadedStickers.filter(s => {
    const matchesCategory = activeStickerCategory === "all" || s.category === activeStickerCategory;
    const matchesSearch = !stickerSearchQuery || s.name.toLowerCase().includes(stickerSearchQuery);
    return matchesCategory && matchesSearch;
  });

  if (filtered.length === 0) {
    grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; padding: 25px 10px; color: var(--text-date); font-size: 12px;">No stickers found</div>`;
    return;
  }

  grid.innerHTML = filtered.map(s => `
    <div class="sticker-grid-item" onclick="sendStickerOrGif('${escapeHtml(s.path)}', '${escapeHtml(s.name)}', 'sticker')" title="${escapeHtml(s.name)}">
      <img class="sticker-grid-img" src="${s.path}" alt="${escapeHtml(s.name)}" loading="lazy">
      <span class="sticker-grid-name">${escapeHtml(s.name)}</span>
    </div>
  `).join("");
}

function renderGifsGrid() {
  const grid = document.getElementById("gifs-grid");
  if (!grid) return;

  let filtered = loadedGifs.filter(g => {
    return !stickerSearchQuery || g.name.toLowerCase().includes(stickerSearchQuery);
  });

  if (filtered.length === 0) {
    grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; padding: 25px 10px; color: var(--text-date); font-size: 12px;">No GIFs found</div>`;
    return;
  }

  grid.innerHTML = filtered.map(g => `
    <div class="gif-grid-item" onclick="sendStickerOrGif('${escapeHtml(g.path)}', '${escapeHtml(g.name)}', 'gif')" title="${escapeHtml(g.name)}">
      <img class="gif-grid-img" src="${g.path}" alt="${escapeHtml(g.name)}" loading="lazy">
      <span class="gif-grid-name">${escapeHtml(g.name)}</span>
    </div>
  `).join("");
}

async function sendStickerOrGif(path, name, type) {
  if (!path) return;

  const textarea = document.getElementById("chat-textarea");
  const content = textarea ? textarea.value.trim() : "";

  const payload = {
    action: "post_message",
    room_id: currentRoom,
    content: content,
    reply_to: activeReply ? activeReply.post_num : null,
    image_path: path,
    image_thumb: path,
    image_name: name || (type === "sticker" ? "Sticker" : "GIF"),
    image_size: 0,
    image_dims: type === "sticker" ? "120x120" : "240x240"
  };

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  } else {
    initWebSocket();
    return;
  }

  if (textarea) {
    textarea.value = "";
    textarea.style.height = "38px";
  }
  removePendingImage();
  cancelReply();
  closeStickersTray();
  setTimeout(() => scrollToBottom(true), 50);
}

function sendCustomGifUrl() {
  const input = document.getElementById("custom-gif-input");
  if (!input) return;
  const url = input.value.trim();
  if (!url) {
    alert("Please enter a valid GIF image URL.");
    return;
  }

  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    alert("Please enter a full URL starting with https:// or http://");
    return;
  }

  sendStickerOrGif(url, "GIF", "gif");
  input.value = "";
}

// Click outside tray to close
document.addEventListener("click", (e) => {
  const tray = document.getElementById("stickers-tray");
  const toggleBtn = document.getElementById("btn-stickers-toggle");
  if (!tray || tray.style.display === "none") return;

  if (!tray.contains(e.target) && !toggleBtn.contains(e.target)) {
    tray.style.display = "none";
  }
});

