# 🐾 AnonChat Classic - Old-School Anonymous Laptop Board

A classic, solid, human-crafted real-time anonymous chat board hosted directly on your laptop. Built with **FastAPI**, **WebSockets**, and **SQLite**.

---

## 🌟 Key Features

1. **Clear, Real Animal Profile Avatars (160+ Species)**:
   - Covers animals from all over Earth: **Snow Leopard, Capybara, Red Panda, Orca, Barn Owl, Bald Eagle, Gray Wolf, Arctic Fox, Axolotl, Platypus, Meerkat, Komodo Dragon, Cheetah, Chameleon, Koala, Kangaroo, etc.**
   - Every user receives a **crystal clear, high-resolution photographic portrait** of their assigned animal.
   - Assigned deterministically per user session — completely anonymous, no sign-up or profile setup needed.

2. **Old-School, Classic Human-Crafted Interface**:
   - Designed to look grounded, authentic, and purposeful — **not like a generic AI-generated template**.
   - No flashy neon gradients or floating glass capsules.
   - Clean classic typography, solid structured borders, and crisp monospace details for timestamps and post numbers.
   - Real greentext (`>quote`), clickable reply links (`>>1004`), spoiler tags, and photo attachment handling.

3. **Zero-Login Host Control (For Laptop Owner Only)**:
   - **No login prompt or password**: Opening `http://localhost:8000` on your laptop automatically grants you Host access.
   - **Remote Wi-Fi visitors see only 100% anonymous animal handles**. No host options or admin links are visible to them.
   - **Real Name Tagging**: On your laptop, click `[Edit Name]` on any message to tag that device with their real name (e.g., *"Rahul"*). Once tagged, a golden `👤 Real Name: Rahul` label appears on all their messages **only for you**.
   - **Host Console**: Click `[ 👑 Host Console ]` at the top to see all connected devices, their real names, and one-click moderation.

4. **Worldwide Public Link (Access Outside Your Wi-Fi)**:
   - Built-in secure tunneling powered by **Cloudflare Tunnel (`cloudflared`)**.
   - **No router port forwarding or public IP required**.
   - Anyone with your public link (e.g. `https://xxxx.trycloudflare.com`) can connect from anywhere on 4G/5G mobile data, remote home Wi-Fi, or abroad.
   - Click **[ Share Link ]** in the top navigation bar to copy the worldwide link, scan the live QR code, or share directly via mobile apps!

---

## 🚀 How to Run

### Option 1: Start Everything (Server + Worldwide Public Tunnel)
```bash
cd /home/arch/anonchat
./start.sh
```

### Option 2: Manage Tunnel Independently
```bash
./tunnel.sh start    # Start public tunnel in background
./tunnel.sh status   # Show public link
./tunnel.sh stop     # Stop public tunnel
```

- **💻 Host Laptop (Host Console & Chat)**: [http://localhost:8000](http://localhost:8000)
- **🌐 Outside Wi-Fi (Anyone with the link)**: `https://[your-tunnel-subdomain].trycloudflare.com`
- **📱 Local Wi-Fi (Same router only)**: `http://[your-lan-ip]:8000`

---

## 🛡️ Security, Host Privacy & Anti-Leak Measures

1. **Zero Host Info Leakage**:
   - The host's private Wi-Fi/LAN IP address is **never visible to external visitors** in HTML, JSON APIs, or QR codes.
   - All server signature headers (`Server: uvicorn`) are stripped and replaced with generic signatures.
   - Security headers enforced: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-Robots-Tag: noindex`.
   - Reverse DNS / MAC address lookups are sanitized and host labels are scrubbed.

2. **Host Authentication & Tunnel Isolation**:
   - Remote visitors arriving through the Cloudflare Tunnel (`trycloudflare.com`) can **never auto-authenticate** as Host/Admin.
   - Admin access from remote devices requires entering the host password, with brute-force lockout (5 failed attempts locks for 5 minutes).
   - Secret admin tokens are cryptographically generated (256-bit) and stored strictly in `HttpOnly; SameSite=Lax` cookies.

3. **EXIF Metadata Stripping**:
   - All uploaded images are processed through Pillow to **completely strip EXIF metadata** (GPS coordinates, camera model, date/time, device serials) before saving.
   - Prevents both the host and users from accidentally revealing geographic location.

4. **⚡ 500-User Real-Time Scale Architecture**:
   - **SQLite WAL Mode & Multi-Table Indexes**: Enabled `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, 64MB memory cache, and 6 high-speed B-Tree indexes for instantaneous post queries without table locks.
   - **Debounced Concurrent Broadcasts**: Broadcasts run asynchronously in parallel batches using `asyncio.gather`, ensuring slow mobile users don't stall the server for the other 499 users.
   - **File Descriptor Limit Boost**: `start.sh` automatically raises Linux socket limits (`ulimit -n 65535`) to prevent `Too many open files` errors.
   - **Anti-Flood Rate Limiting**: Max 1 post per 1.2s, max 1,000 chars per message, max 6 concurrent sockets per IP, and 650 total connection cap.

