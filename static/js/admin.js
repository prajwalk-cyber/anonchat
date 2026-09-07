// AnonChat - Admin Controller & Identity Intel

function renderIcon(name, extraClass = '', extraAttrs = '') {
  return `<svg class="ui-icon ${extraClass}" aria-hidden="true" ${extraAttrs}><use href="#icon-${name}" xlink:href="#icon-${name}"></use></svg>`;
}

// Check and update Admin UI state
function updateAdminUI() {
  const adminBtn = document.getElementById("btn-admin-modal");
  const adminTools = document.getElementById("admin-exclusive-tools");

  if (isAdmin) {
    adminBtn.innerHTML = `${renderIcon('crown', 'ui-icon-xs')} Admin: Active`;
    adminBtn.classList.add("btn-admin-active");
    if (adminTools) adminTools.style.display = "flex";
  } else {
    adminBtn.innerHTML = `${renderIcon('lock', 'ui-icon-xs')} Admin Login`;
    adminBtn.classList.remove("btn-admin-active");
    if (adminTools) adminTools.style.display = "none";
  }
}

// Open Admin Modal
function openAdminModal() {
  if (isAdmin) {
    // Already admin: open dashboard
    openClientsDashboard();
  } else {
    document.getElementById("modal-admin-login").style.display = "flex";
    document.getElementById("admin-password-input").focus();
  }
}

function closeModals() {
  document.querySelectorAll(".modal-overlay").forEach(m => m.style.display = "none");
}

// Admin Login
async function performAdminLogin(isLocalhostAuto = false) {
  const password = document.getElementById("admin-password-input").value;

  try {
    const res = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        password: password,
        localhost_auto: isLocalhostAuto
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Authentication failed");
    }

    const data = await res.json();
    localStorage.setItem("anon_admin_token", data.token);
    isAdmin = true;
    closeModals();
    updateAdminUI();

    // Authenticate WS
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "authenticate_admin", token: data.token }));
    }

    // Reload messages to get full IP & real name metadata
    loadInitialMessages();
  } catch (e) {
    alert(e.message);
  }
}

// Admin Logout
async function performAdminLogout() {
  try {
    await fetch("/api/admin/logout", { method: "POST" });
    localStorage.removeItem("anon_admin_token");
    isAdmin = false;
    updateAdminUI();
    closeModals();
    loadInitialMessages();
  } catch (e) {
    console.error("Logout error", e);
  }
}

// Assign Real Name to an IP
async function promptAssignRealName(ip, currentName = "") {
  const newName = prompt(`Assign Real Name for ${ip} (visible ONLY to you):`, currentName);
  if (newName === null) return; // Cancelled

  try {
    const res = await fetch("/api/admin/assign_name", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ip: ip, real_name: newName.trim() })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to set real name");
    }

    // Refresh identities if dashboard open
    if (document.getElementById("modal-clients-dashboard").style.display === "flex") {
      openClientsDashboard();
    }
  } catch (e) {
    alert("Error: " + e.message);
  }
}

// Delete a post
async function adminDeletePost(postNum) {
  if (!confirm(`Delete Post No. ${postNum}?`)) return;

  try {
    const res = await fetch(`/api/admin/delete/${postNum}`, { method: "POST" });
    if (!res.ok) throw new Error("Delete failed");
  } catch (e) {
    alert("Error deleting post: " + e.message);
  }
}

// Toggle Ban
async function adminToggleBan(ip, ban) {
  const action = ban ? "BAN" : "UNBAN";
  if (!confirm(`${action} IP ${ip}?`)) return;

  try {
    const res = await fetch("/api/admin/ban", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ip: ip, banned: ban })
    });
    if (!res.ok) throw new Error("Ban update failed");
    alert(`IP ${ip} is now ${ban ? 'BANNED' : 'UNBANNED'}.`);
    if (document.getElementById("modal-clients-dashboard").style.display === "flex") {
      openClientsDashboard();
    }
  } catch (e) {
    alert("Error updating ban: " + e.message);
  }
}

// Clear all messages
async function adminClearChat() {
  if (!confirm("ARE YOU SURE? This will permanently delete ALL messages in the chat!")) return;

  try {
    const res = await fetch("/api/admin/clear", { method: "POST" });
    if (!res.ok) throw new Error("Clear failed");
  } catch (e) {
    alert("Error: " + e.message);
  }
}

// Open Clients Intel Dashboard
async function openClientsDashboard() {
  const modal = document.getElementById("modal-clients-dashboard");
  const tableBody = document.getElementById("clients-table-body");
  modal.style.display = "flex";
  tableBody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:15px;">Loading connected devices...</td></tr>';

  try {
    const res = await fetch("/api/admin/identities");
    if (!res.ok) throw new Error("Failed to fetch identities");
    const data = await res.json();

    if (!data.identities || data.identities.length === 0) {
      tableBody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:15px;">No devices recorded yet.</td></tr>';
      return;
    }

    tableBody.innerHTML = data.identities.map(client => {
      const realNameDisplay = client.real_name ? `<strong>${escapeHtml(client.real_name)}</strong>` : '<em style="color:#888;">(None)</em>';
      const banStatus = client.is_banned 
        ? '<span style="color:#c00; font-weight:bold;">BANNED</span>' 
        : '<span style="color:#27ae60;">Active</span>';

      return `
        <tr>
          <td>
            <strong>${client.ip}</strong><br>
            <small style="color:#777;">${escapeHtml(client.hostname || 'No Hostname')}</small>
          </td>
          <td>
            ${realNameDisplay}<br>
            <button class="btn-admin-mini" style="margin-top:3px;" onclick="promptAssignRealName('${client.ip}', '${escapeHtml(client.real_name || '')}')">${renderIcon('edit', 'ui-icon-xs')} Edit Name</button>
          </td>
          <td>
            <small>${escapeHtml(client.device_summary || 'Unknown Device')}</small><br>
            <small style="color:#888; font-family:monospace;">${escapeHtml(client.mac || '')}</small>
          </td>
          <td>${client.message_count || 0}</td>
          <td>${banStatus}</td>
          <td>
            <button class="btn-admin-mini" onclick="adminToggleBan('${client.ip}', ${!client.is_banned})">
              ${client.is_banned ? renderIcon('check-circle', 'ui-icon-xs') + ' Unban' : renderIcon('ban', 'ui-icon-xs') + ' Ban'}
            </button>
          </td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    tableBody.innerHTML = `<tr><td colspan="6" style="color:red; padding:15px;">Error: ${e.message}</td></tr>`;
  }
}

// Open Wi-Fi QR Code Modal
function openWifiModal() {
  document.getElementById("modal-wifi").style.display = "flex";
}

// Copy URL to clipboard
function copyLanUrl() {
  const url = document.getElementById("lan-url-display").innerText;
  navigator.clipboard.writeText(url).then(() => {
    alert("Copied to clipboard: " + url);
  });
}
