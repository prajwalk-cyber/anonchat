import os
import io
import time
import uuid
import json
import hashlib
import random
import asyncio
import hmac
import re
import ipaddress
from collections import defaultdict
from typing import Dict, List, Optional
from datetime import datetime
import urllib.request
import urllib.parse
import socket
import subprocess

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
try:
    import qrcode
except ImportError:
    qrcode = None

import config
import database
import network_intel
import animal_avatars
import tunnel
import ai_admin

app = FastAPI(title="AnonChat", docs_url=None, redoc_url=None, openapi_url=None)

# Mount static and upload directories
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(config.UPLOAD_DIR)), name="uploads")

templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))

# Rate limiting and brute force tracking in memory
failed_login_attempts: Dict[str, List[float]] = defaultdict(list)
upload_tracker: Dict[str, List[float]] = defaultdict(list)

# Security Headers & Anti-Fingerprinting Middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    # Strip server headers identifying local software / uvicorn
    response.headers["Server"] = "anonchat"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    # Content Security Policy (allows app resources, prevents script injection and external exfiltration)
    csp = (
        "default-src 'self'; "
        "img-src 'self' data: blob: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self' ws: wss:; "
        "font-src 'self' data:; "
        "frame-ancestors 'none';"
    )
    response.headers["Content-Security-Policy"] = csp
    return response

@app.on_event("startup")
async def startup_event():
    database.init_db()

def get_client_ip(request: Request) -> str:
    """Safely extract client IP address, sanitizing against spoofing."""
    client_host = request.client.host if request.client else "127.0.0.1"
    # Only trust proxy headers if request arrived from local loopback (cloudflared tunnel)
    if client_host in ("127.0.0.1", "::1"):
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip and network_intel.is_valid_ip(cf_ip.strip()):
            return cf_ip.strip()
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first_ip = forwarded.split(",")[0].strip()
            if network_intel.is_valid_ip(first_ip):
                return first_ip
    return client_host if network_intel.is_valid_ip(client_host) else "127.0.0.1"

ALLOWED_HOST_IPS = {"127.0.0.1", "::1"}

def is_host_client(client_ip: str, cookies: dict = None, request: Request = None) -> bool:
    """Strict host verification: Host access is restricted EXCLUSIVELY to direct localhost on the host machine."""
    # 1. If the request arrived through a public tunnel (Cloudflare or ngrok),
    # it is a remote visitor. NEVER authenticate as host!
    if request:
        host_hdr = request.headers.get("host") or ""
        if (
            request.headers.get("cf-ray")
            or request.headers.get("cf-connecting-ip")
            or request.headers.get("ngrok-trace-id")
            or "ngrok" in host_hdr
            or "trycloudflare" in host_hdr
        ):
            return False

    # 2. Authenticate ONLY for true direct localhost connections
    return client_ip in ("127.0.0.1", "::1")

def generate_public_id(ip: str, session_id: str) -> str:
    salt = config.ADMIN_TOKEN_SECRET[:16]
    raw = f"{ip}:{session_id}:{salt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]

# Real-time WebSocket connection manager optimized for 500 concurrent users
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[WebSocket, dict] = {}
        self.pending_invites: Dict[str, dict] = {}
        self._last_count_broadcast: float = 0.0
        self._count_task: Optional[asyncio.Task] = None

    def can_connect(self, client_ip: str) -> tuple[bool, str]:
        if len(self.active_connections) >= config.MAX_TOTAL_CONNECTIONS:
            return False, "Chat server is at full capacity (600+ users). Please wait a moment."
        ip_count = sum(1 for info in self.active_connections.values() if info.get("ip") == client_ip)
        if ip_count >= config.MAX_CONNECTIONS_PER_IP:
            return False, "Too many active connections from your IP."
        return True, ""

    async def connect(self, websocket: WebSocket, ip: str, session_id: str, public_id: str, anon_name: str, anon_avatar: str, sci_name: str, is_host: bool, user_agent: str):
        await websocket.accept()
        self.active_connections[websocket] = {
            "ip": ip,
            "session_id": session_id,
            "public_id": public_id,
            "anon_name": anon_name,
            "anon_avatar": anon_avatar,
            "sci_name": sci_name,
            "is_host": is_host,
            "user_agent": user_agent,
            "connected_at": time.time(),
            "last_post_time": 0.0,
            "last_typing_time": 0.0,
            "recent_reactions": []
        }
        self.trigger_debounced_online_count()

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            del self.active_connections[websocket]
        self.trigger_debounced_online_count()

    async def _send_batch(self, websockets: List[WebSocket], text: str):
        """Concurrently send text to a batch of WebSockets without blocking the event loop."""
        if not websockets:
            return
        dead = []
        async def _safe_send(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=3.0)
            except Exception:
                dead.append(ws)
        # Execute batch in parallel
        await asyncio.gather(*[_safe_send(ws) for ws in websockets], return_exceptions=True)
        for ws in dead:
            self.disconnect(ws)

    def trigger_debounced_online_count(self):
        """Debounce online count broadcasts so 500 users connecting doesn't flood CPU."""
        now = time.time()
        if now - self._last_count_broadcast >= 2.0:
            self._last_count_broadcast = now
            asyncio.create_task(self.broadcast_online_count())
        elif self._count_task is None or self._count_task.done():
            async def _delayed():
                await asyncio.sleep(2.0)
                self._last_count_broadcast = time.time()
                await self.broadcast_online_count()
            self._count_task = asyncio.create_task(_delayed())

    def get_online_users(self, is_host: bool = False) -> List[dict]:
        seen = {}
        for ws, info in list(self.active_connections.items()):
            pid = info.get("public_id")
            if pid and pid not in seen:
                u = {
                    "public_id": pid,
                    "anon_name": info.get("anon_name"),
                    "anon_avatar": info.get("anon_avatar"),
                    "sci_name": info.get("sci_name", "")
                }
                if is_host:
                    u["ip"] = info.get("ip")
                    ident = database.get_identity(info.get("ip"))
                    u["real_name"] = ident.get("real_name") if ident else ""
                seen[pid] = u
        return list(seen.values())

    async def send_to_user(self, target_public_id: str, data: dict) -> bool:
        msg = json.dumps(data)
        targets = [ws for ws, info in list(self.active_connections.items()) if info.get("public_id") == target_public_id]
        if targets:
            await self._send_batch(targets, msg)
            return True
        return False

    async def broadcast_to_room(self, room_id: str, data: dict):
        members = database.get_room_members(room_id)
        member_pids = {m["public_id"] for m in members}
        msg = json.dumps(data)
        targets = [ws for ws, info in list(self.active_connections.items()) if info.get("public_id") in member_pids or info.get("is_host")]
        await self._send_batch(targets, msg)

    async def broadcast_online_count(self):
        count = len(self.active_connections)
        msg = json.dumps({"type": "online_count", "count": count})
        await self._send_batch(list(self.active_connections.keys()), msg)

    async def broadcast_message(self, full_msg: dict):
        room_id = full_msg.get("room_id") or "main"
        public_msg = dict(full_msg)
        # CRITICAL PRIVACY FIX: Strip session_id, IP, MAC, hostname, real name
        public_msg.pop("session_id", None)
        public_msg.pop("ip", None)
        public_msg.pop("hostname", None)
        public_msg.pop("mac", None)
        public_msg.pop("user_agent", None)
        public_msg.pop("device_summary", None)
        public_msg.pop("real_name", None)
        public_msg.pop("notes", None)
        public_msg.pop("is_banned", None)

        public_json = json.dumps({"type": "new_message", "message": public_msg})
        host_json = json.dumps({"type": "new_message", "message": full_msg})

        if room_id == "main" or room_id in database.PUBLIC_ROOMS:
            host_sockets = []
            public_sockets = []
            for ws, info in list(self.active_connections.items()):
                if info.get("is_host"):
                    host_sockets.append(ws)
                else:
                    public_sockets.append(ws)
            await asyncio.gather(
                self._send_batch(host_sockets, host_json),
                self._send_batch(public_sockets, public_json),
                return_exceptions=True
            )
        else:
            members = database.get_room_members(room_id)
            member_pids = {m["public_id"] for m in members}
            host_sockets = []
            member_sockets = []
            for ws, info in list(self.active_connections.items()):
                pid = info.get("public_id")
                if info.get("is_host"):
                    host_sockets.append(ws)
                elif pid in member_pids:
                    member_sockets.append(ws)
            await asyncio.gather(
                self._send_batch(host_sockets, host_json),
                self._send_batch(member_sockets, public_json),
                return_exceptions=True
            )

    async def broadcast_delete(self, post_num: int):
        msg = json.dumps({"type": "delete_message", "post_num": post_num})
        await self._send_batch(list(self.active_connections.keys()), msg)

    async def broadcast_clear(self):
        msg = json.dumps({"type": "clear_chat"})
        await self._send_batch(list(self.active_connections.keys()), msg)

    async def broadcast_reaction(self, post_num: int, reactions: dict):
        msg = json.dumps({"type": "reaction_update", "post_num": post_num, "reactions": reactions})
        await self._send_batch(list(self.active_connections.keys()), msg)

    async def broadcast_typing(self, animal_name: str, sender_ws: WebSocket):
        msg = json.dumps({"type": "typing", "animal_name": animal_name})
        recipients = [ws for ws in list(self.active_connections.keys()) if ws != sender_ws]
        await self._send_batch(recipients, msg)

    async def broadcast_identity_update(self, ip: str, real_name: str, notes: str):
        msg = json.dumps({
            "type": "update_identity",
            "ip": ip,
            "real_name": real_name,
            "notes": notes
        })
        targets = [ws for ws, info in list(self.active_connections.items()) if info.get("is_host")]
        await self._send_batch(targets, msg)

    async def broadcast_purge(self, ip: str, post_nums: Optional[List[int]] = None):
        # Notify host with full IP
        host_msg = json.dumps({"type": "purge_ip", "ip": ip})
        host_targets = [ws for ws, info in list(self.active_connections.items()) if info.get("is_host")]
        await self._send_batch(host_targets, host_msg)

        # Notify non-host users only with deleted post numbers (ZERO visitor IP leak!)
        if post_nums:
            pub_msg = json.dumps({"type": "posts_deleted", "post_nums": post_nums})
            pub_targets = [ws for ws, info in list(self.active_connections.items()) if not info.get("is_host")]
            await self._send_batch(pub_targets, pub_msg)

    async def broadcast_announcement(self, text: str):
        msg = json.dumps({"type": "announcement", "text": text})
        await self._send_batch(list(self.active_connections.keys()), msg)

    async def broadcast(self, data: dict):
        msg = json.dumps(data)
        await self._send_batch(list(self.active_connections.keys()), msg)

    async def kick_ip(self, ip: str):
        for ws, info in list(self.active_connections.items()):
            if info.get("ip") == ip:
                try:
                    await ws.send_text(json.dumps({"type": "error", "message": "You have been disconnected by the host."}))
                    await ws.close()
                except Exception:
                    pass

    async def broadcast_ai_event(self, event_data: dict):
        msg = json.dumps({"type": "ai_moderation_event", "event": event_data})
        host_targets = [ws for ws, info in list(self.active_connections.items()) if info.get("is_host")]
        await self._send_batch(host_targets, msg)

manager = ConnectionManager()



# Routes
# Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, response: Response):
    client_ip = get_client_ip(request)
    is_host = is_host_client(client_ip, request.cookies, request=request)

    # CRITICAL PRIVACY: Never expose host laptop LAN IP to non-host visitors!
    lan_ip = network_intel.get_lan_ip() if is_host else ""

    # Generate fresh session_id on each reload/rejoin (giving user a new animal and ID)
    session_id = "sess_" + uuid.uuid4().hex[:16]

    public_url = tunnel.get_public_url()
    cache_v = int(time.time())

    res = templates.TemplateResponse("index.html", {
        "request": request,
        "lan_ip": lan_ip,
        "port": config.PORT,
        "is_host": is_host,
        "client_ip": client_ip,
        "session_id": session_id,
        "public_url": public_url,
        "cache_v": cache_v
    })
    res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    res.headers["Pragma"] = "no-cache"
    res.headers["Expires"] = "0"
    res.set_cookie("session_id", session_id, max_age=365*86400, httponly=False, samesite="lax")
    
    # Only set host_verified cookie automatically if directly on laptop localhost (never via tunnel)
    is_tunnel_req = bool(request.headers.get("cf-ray") or request.headers.get("cf-connecting-ip"))
    if is_host and not is_tunnel_req and client_ip in ("127.0.0.1", "::1"):
        res.set_cookie("host_verified", config.ADMIN_TOKEN_SECRET, httponly=True, samesite="lax")
    return res

@app.get("/admin", response_class=HTMLResponse)
@app.get("/host", response_class=HTMLResponse)
async def admin_page(request: Request, response: Response):
    return await index(request, response)

@app.get("/api/animal/{slug}")
async def get_animal_image(slug: str):
    clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '', slug)[:40]
    data = animal_avatars.get_cached_or_fetch_avatar(clean_slug)
    media_type = "image/svg+xml" if data.startswith(b"<svg") else "image/jpeg"
    return RawResponse(content=data, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})

@app.get("/api/messages")
async def api_messages(request: Request, room_id: str = "main", limit: int = 150):
    client_ip = get_client_ip(request)
    is_host = is_host_client(client_ip, request.cookies, request=request)
    session_id = request.query_params.get("session_id") or request.cookies.get("session_id") or ""
    public_id = generate_public_id(client_ip, session_id)
    clean_room = re.sub(r'[^a-zA-Z0-9_-]', '', room_id)[:50] or "main"
    safe_limit = min(max(int(limit), 1), 200)

    if clean_room not in database.PUBLIC_ROOMS and not is_host:
        if not database.is_user_in_room(clean_room, public_id, is_host):
            raise HTTPException(status_code=403, detail="Not a participant in this room")
    messages = database.get_messages(room_id=clean_room, limit=safe_limit, is_host=is_host)
    return {"status": "ok", "room_id": clean_room, "messages": messages, "is_host": is_host}

@app.get("/api/rooms")
async def api_rooms(request: Request):
    client_ip = get_client_ip(request)
    is_host = is_host_client(client_ip, request.cookies, request=request)
    session_id = request.query_params.get("session_id") or request.cookies.get("session_id") or ""
    public_id = generate_public_id(client_ip, session_id)
    rooms = database.get_user_rooms(public_id, is_host=is_host)
    return {"status": "ok", "rooms": rooms}

@app.get("/api/online_users")
async def api_online_users(request: Request):
    client_ip = get_client_ip(request)
    is_host = is_host_client(client_ip, request.cookies, request=request)
    users = manager.get_online_users(is_host=is_host)
    return {"status": "ok", "users": users}

@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    client_ip = get_client_ip(request)
    if database.is_ip_banned(client_ip):
        raise HTTPException(status_code=403, detail="Banned from uploading")

    # Rate limiting on file uploads (prevents storage & bandwidth exhaustion)
    now = time.time()
    upload_tracker[client_ip] = [t for t in upload_tracker[client_ip] if now - t < 60.0]
    if len(upload_tracker[client_ip]) >= config.RATE_LIMIT_UPLOAD_PER_MIN:
        raise HTTPException(status_code=429, detail="Upload limit reached (max 5 images/minute). Please slow down.")
    upload_tracker[client_ip].append(now)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, GIF, and WEBP supported")

    contents = await file.read()
    if len(contents) > config.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="File too large (Max 8MB)")

    # Sanitize and strip EXIF (removes GPS coordinates, device serial numbers, camera model)
    try:
        raw_img = Image.open(io.BytesIO(contents))
        raw_img.verify()

        file_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        saved_filename = f"{file_id}{ext}"
        orig_path = config.UPLOAD_DIR / saved_filename

        if ext == ".gif":
            # For animated GIFs, write raw validated bytes directly to preserve 100% of frames, duration, and looping
            with open(orig_path, "wb") as f:
                f.write(contents)
            thumb_filename = saved_filename
            img = Image.open(io.BytesIO(contents))
            dims = f"{img.width}x{img.height}"
        else:
            # Re-open for clean processing
            img = Image.open(io.BytesIO(contents))
            w, h = img.size
            dims = f"{w}x{h}"

            # Transpose according to EXIF orientation then strip EXIF completely
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass

            thumb_filename = f"{file_id}_thumb{ext if ext in ['.png', '.webp'] else '.jpg'}"
            thumb_path = config.THUMBNAIL_DIR / thumb_filename

            # Save clean image without metadata
            save_format = img.format if img.format else ("PNG" if ext == ".png" else "JPEG")
            if img.mode in ("RGBA", "P") and save_format.upper() in ("JPEG", "JPG"):
                img = img.convert("RGB")
            img.save(orig_path, format=save_format, quality=90)

            # Generate thumbnail
            img_thumb = img.copy()
            if img_thumb.mode in ("RGBA", "P") and thumb_path.suffix.lower() == ".jpg":
                img_thumb = img_thumb.convert("RGB")
            img_thumb.thumbnail((450, 450))
            img_thumb.save(thumb_path, quality=85)

    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or corrupted image format")

    safe_name = re.sub(r'[^a-zA-Z0-9._ -]', '', os.path.basename(file.filename or "image.jpg"))[:60]
    thumb_url = f"/uploads/{saved_filename}" if ext == ".gif" else f"/uploads/thumbs/{thumb_filename}"
    return {
        "status": "ok",
        "image_path": f"/uploads/{saved_filename}",
        "image_thumb": thumb_url,
        "image_name": safe_name,
        "image_size": len(contents),
        "image_dims": dims
    }

@app.get("/api/stickers_and_gifs")
async def get_stickers_and_gifs():
    stickers_dir = config.BASE_DIR / "static" / "stickers"
    gifs_dir = config.BASE_DIR / "static" / "gifs"

    stickers = []
    if stickers_dir.exists():
        for f in sorted(stickers_dir.glob("*.svg")):
            category = "Reactions"
            if f.name in ("attendance_warning.svg", "bunk_class.svg", "degree_loading.svg", "coffee_fuel.svg", "canteen_chai.svg", "code_bug_404.svg"):
                category = "Campus"
            elif f.name in ("capybara_chill.svg", "cat_vibing.svg", "doge_secret.svg"):
                category = "Mascots"

            clean_name = f.stem.replace("_", " ").title()
            stickers.append({
                "name": clean_name,
                "path": f"/static/stickers/{f.name}",
                "category": category
            })

    gifs = []
    if gifs_dir.exists():
        for f in sorted(gifs_dir.glob("*.gif")):
            clean_name = f.stem.replace("_", " ").title()
            gifs.append({
                "name": clean_name,
                "path": f"/static/gifs/{f.name}"
            })

    return {
        "status": "ok",
        "stickers": stickers,
        "gifs": gifs
    }

def resolve_media_to_direct_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return url
    url_clean = url.strip()
    try:
        parsed = urllib.parse.urlparse(url_clean)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return url_clean
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return url_clean

    is_tenor = hostname == "tenor.com" or hostname.endswith(".tenor.com")
    is_giphy = hostname == "giphy.com" or hostname.endswith(".giphy.com")
    if not (is_tenor or is_giphy):
        return url_clean

    # Already a direct file or direct cdn media
    if "media.tenor.com" in hostname or "c.tenor.com" in hostname or url_clean.endswith(".gif"):
        return url_clean

    if is_tenor and "/view/" in parsed.path:
        try:
            req = urllib.request.Request(
                url_clean,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                media = re.findall(r'https://media\.tenor\.com/[^"\' ]+\.gif', html)
                if media:
                    return media[0]
                og = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
                if og:
                    return og[0]
        except Exception:
            pass
    elif is_giphy and "/gifs/" in parsed.path:
        try:
            req = urllib.request.Request(
                url_clean,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                media = re.findall(r'https://media[0-9]*\.giphy\.com/media/[^"\' ]+/giphy\.gif', html)
                if media:
                    return media[0]
                og = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
                if og:
                    return og[0]
        except Exception:
            pass
    return url_clean

def extract_media_url_from_text(text: str) -> Optional[str]:
    if not text or not isinstance(text, str):
        return None
    pattern = r'(https?://[^\s<>]+?\.(?:gif|webp|png|jpe?g|svg)(?:\?[^\s<>]*)?|https?://(?:[a-zA-Z0-9.-]+\.)?(?:tenor\.com|giphy\.com|klipy\.com)/[^\s<>]+)'
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None

@app.post("/api/resolve_media")
async def resolve_media_endpoint(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    raw_url = (data.get("url") or "").strip()
    if not raw_url:
        return {"status": "error", "message": "No URL provided"}
    resolved = resolve_media_to_direct_url(raw_url)
    return {"status": "ok", "original": raw_url, "resolved": resolved}

@app.get("/api/qrcode")
async def generate_qr(request: Request, custom_url: Optional[str] = None):
    client_ip = get_client_ip(request)
    is_host = is_host_client(client_ip, request.cookies, request=request)
    url = custom_url
    if not url:
        public_url = tunnel.get_public_url()
        if public_url:
            url = public_url.rstrip("/") + "/"
        elif is_host:
            lan_ip = network_intel.get_lan_ip()
            port = config.PORT
            url = f"http://{lan_ip}:{port}/"
        else:
            url = str(request.base_url)

    if qrcode:
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#22272e", back_color="#ffffff")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")
    else:
        # Fallback to system qrencode binary
        try:
            res = subprocess.run(
                ["qrencode", "-o", "-", "-s", "8", "-m", "2", url],
                capture_output=True,
                check=True
            )
            return StreamingResponse(io.BytesIO(res.stdout), media_type="image/png")
        except Exception:
            img = Image.new("RGB", (200, 200), color="#ffffff")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return StreamingResponse(buf, media_type="image/png")

@app.post("/api/leave_all_rooms")
async def leave_all_rooms(request: Request):
    client_ip = get_client_ip(request)
    session_id = request.query_params.get("session_id") or request.cookies.get("session_id") or ""
    public_id = generate_public_id(client_ip, session_id)
    deleted_rooms = database.remove_user_from_all_rooms(public_id)
    for r_id in deleted_rooms:
        await manager.broadcast_to_room(r_id, {
            "type": "room_deleted",
            "room_id": r_id,
            "message": "This group has been deleted because all members left."
        })
    return {"status": "ok", "deleted_rooms": deleted_rooms}

@app.get("/api/share_info")
async def get_share_info(request: Request):
    client_ip = get_client_ip(request)
    is_host = is_host_client(client_ip, request.cookies, request=request)
    public_url = tunnel.get_public_url()
    lan_ip = network_intel.get_lan_ip() if is_host else ""
    lan_url = f"http://{lan_ip}:{config.PORT}/" if (is_host and lan_ip) else None
    return {
        "status": "ok",
        "public_url": public_url,
        "lan_url": lan_url,
        "active_url": public_url if public_url else (lan_url or "")
    }

@app.post("/api/admin/login")
@app.post("/api/host/login")
async def admin_login(request: Request):
    raise HTTPException(status_code=403, detail="Host login is disabled. Host access is strictly restricted to localhost.")

@app.post("/api/admin/logout")
@app.post("/api/host/logout")
async def admin_logout():
    return JSONResponse({"status": "ok"})

# Host-Only API Endpoints (Guarded strictly by is_host_client with request inspection)
@app.get("/api/host/identities")
async def host_identities(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    identities = database.get_all_identities()
    return {"status": "ok", "identities": identities}

@app.post("/api/host/assign_name")
async def host_assign_name(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    ip = data.get("ip")
    real_name = str(data.get("real_name", "")).strip()[:60]
    notes = data.get("notes")

    if not ip or not network_intel.is_valid_ip(ip):
        raise HTTPException(status_code=400, detail="Valid IP is required")

    database.assign_real_name(ip, real_name, notes)
    await manager.broadcast_identity_update(ip, real_name, notes or "")
    return {"status": "ok", "ip": ip, "real_name": real_name}

@app.post("/api/host/ban")
async def host_ban(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    ip = data.get("ip")
    banned = bool(data.get("banned", True))

    if not ip or not network_intel.is_valid_ip(ip):
        raise HTTPException(status_code=400, detail="Valid IP is required")

    database.set_ban_status(ip, banned)
    if banned:
        await manager.kick_ip(ip)
    return {"status": "ok", "ip": ip, "banned": banned}

@app.post("/api/host/ban_by_user")
async def host_ban_by_user(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    user_id = data.get("user_id")
    post_num = data.get("post_num")
    banned = bool(data.get("banned", True))
    
    target_ip = None
    if post_num:
        pinfo = database.get_post_info(int(post_num))
        if pinfo and pinfo.get("ip"):
            target_ip = pinfo["ip"]
    elif user_id:
        clean_id = str(user_id).strip().lstrip('#')
        target_ip = database.get_ip_by_public_id(clean_id)
        
    if not target_ip:
        raise HTTPException(status_code=404, detail="Could not resolve IP for specified user/post")
        
    database.set_ban_status(target_ip, banned)
    if banned:
        await manager.kick_ip(target_ip)
    return {"status": "ok", "ip": target_ip, "banned": banned}

@app.post("/api/host/mute")
async def host_mute(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    ip = data.get("ip")
    muted = bool(data.get("muted", True))
    minutes = int(data.get("minutes", 15))

    if not ip or not network_intel.is_valid_ip(ip):
        raise HTTPException(status_code=400, detail="Valid IP is required")

    database.set_mute_status(ip, muted, minutes)
    return {"status": "ok", "ip": ip, "muted": muted}

@app.post("/api/host/purge_ip")
async def host_purge_ip(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    ip = data.get("ip")

    if not ip or not network_intel.is_valid_ip(ip):
        raise HTTPException(status_code=400, detail="Valid IP is required")

    count, post_nums = database.delete_all_from_ip(ip)
    await manager.broadcast_purge(ip, post_nums)
    return {"status": "ok", "ip": ip, "deleted_count": count}

@app.post("/api/host/pin")
async def host_pin(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    try:
        post_num = int(data.get("post_num"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Valid post_num is required")
    pinned = bool(data.get("pinned", True))

    database.set_pinned(post_num, pinned)
    return {"status": "ok", "post_num": post_num, "pinned": pinned}

@app.post("/api/host/announce")
async def host_announce(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    message = str(data.get("message", "")).strip()[:500]

    if message:
        await manager.broadcast_announcement(message)
    return {"status": "ok", "message": message}

@app.post("/api/host/delete/{post_num}")
async def host_delete(post_num: int, request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    database.delete_message(post_num)
    await manager.broadcast_delete(post_num)
    return {"status": "ok", "post_num": post_num}

@app.post("/api/host/clear")
async def host_clear(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    database.clear_messages()
    await manager.broadcast_clear()
    return {"status": "ok", "message": "All messages cleared"}

@app.get("/api/host/rooms")
async def host_get_rooms(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    rooms = database.get_all_rooms_admin()
    return {"status": "ok", "rooms": rooms}

@app.post("/api/host/delete_room/{room_id}")
async def host_delete_room(room_id: str, request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    if not room_id or room_id == "main":
        raise HTTPException(status_code=400, detail="Cannot delete main public room")
    
    conn = database.get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM rooms WHERE room_id = ?", (room_id,))
    row = cur.fetchone()
    conn.close()
    room_name = row["name"] if row else room_id

    database.delete_room(room_id)

    # Broadcast room deletion to all users
    await manager.broadcast({
        "type": "room_deleted",
        "room_id": room_id,
        "room_name": room_name,
        "reason": f"Group '{room_name}' was deleted by the host admin."
    })
    return {"status": "ok", "room_id": room_id, "room_name": room_name}
    

# ==========================================
# Host AI Assistant Endpoints (Qwen / Ollama)
# ==========================================
@app.get("/api/host/ai/status")
async def host_ai_status(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    is_alive = await asyncio.to_thread(ai_admin.check_ollama_alive, 1.2)
    models = await asyncio.to_thread(ai_admin.get_available_models, 1.5) if is_alive else []
    return {
        "status": "ok",
        "ollama_online": is_alive,
        "models": models,
        "default_model": ai_admin.DEFAULT_MODEL,
        "fast_model": ai_admin.get_fast_moderation_model(),
        "automod_config": ai_admin.get_automod_config()
    }

@app.get("/api/host/ai/config")
async def host_ai_get_config(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    return {"status": "ok", "config": ai_admin.get_automod_config()}

@app.post("/api/host/ai/config")
async def host_ai_set_config(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    enabled = data.get("enabled")
    mode = data.get("mode")
    model = data.get("model")
    purge = data.get("purge")
    ai_admin.set_automod_config(enabled=enabled, mode=mode, model=model, purge=purge)
    return {"status": "ok", "config": ai_admin.get_automod_config()}

@app.get("/api/host/ai/logs")
async def host_ai_logs(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    logs = database.get_ai_moderation_logs(limit=100)
    return {"status": "ok", "logs": logs}

@app.post("/api/host/ai/logs/delete/{log_id}")
async def host_ai_log_delete(log_id: int, request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    success = database.delete_ai_moderation_log(log_id)
    return {"status": "ok", "deleted": success}

@app.post("/api/host/ai/logs/clear")
async def host_ai_logs_clear(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    count = database.clear_ai_moderation_logs()
    return {"status": "ok", "cleared_count": count}


@app.post("/api/host/ai/chat")
async def host_ai_chat(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    
    history = data.get("history", [])
    model = data.get("model")
    include_context = bool(data.get("include_context", True))
    room_id = str(data.get("room_id", "main"))

    result = await ai_admin.chat_with_qwen(
        user_prompt=prompt,
        history=history,
        model=model,
        include_chat_context=include_context,
        room_id=room_id,
        manager=manager
    )
    return result

@app.post("/api/host/ai/scan")
async def host_ai_scan(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    data = await request.json()
    room_id = str(data.get("room_id", "main"))
    model = data.get("model")
    auto_enforce = bool(data.get("auto_enforce", False))
    result = await ai_admin.scan_moderation(room_id=room_id, model=model, manager=manager, auto_enforce=auto_enforce)
    return result

@app.post("/api/host/ai/moderate_post/{post_num}")
async def host_ai_moderate_single_post(post_num: int, request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    p_info = database.get_post_info(post_num)
    if not p_info:
        raise HTTPException(status_code=404, detail=f"Post #{post_num} not found")
    event = await ai_admin.moderate_incoming_message(p_info, manager=manager)
    return {"status": "ok", "post_num": post_num, "moderated": bool(event), "event": event}


# --- Anonymous Feedback Endpoints ---
@app.post("/api/feedback")
async def submit_feedback(request: Request):
    client_ip = get_client_ip(request)
    session_id = request.cookies.get("session_id") or ""
    public_id = generate_public_id(client_ip, session_id)
    animal = animal_avatars.get_animal_for_public_id(public_id)
    
    data = await request.json()
    category = str(data.get("category", "General Suggestion")).strip()[:60]
    message = str(data.get("message", "")).strip()[:1500]
    try:
        rating = min(max(int(data.get("rating", 5)), 1), 5)
    except Exception:
        rating = 5
    
    if not message:
        raise HTTPException(status_code=400, detail="Feedback message cannot be empty")
    
    fid = database.insert_feedback(
        public_id=public_id,
        anon_name=animal["handle"],
        ip=client_ip,
        category=category,
        message=message,
        rating=rating
    )
    return {
        "status": "ok",
        "feedback_id": fid,
        "message": "Thank you! Your feedback has been anonymously submitted to the PESITM administration."
    }

@app.get("/api/host/feedbacks")
async def host_get_feedbacks(request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    feedbacks = database.get_feedbacks(limit=100)
    return {"status": "ok", "feedbacks": feedbacks}

@app.post("/api/host/feedbacks/delete/{feedback_id}")
async def host_delete_feedback(feedback_id: int, request: Request):
    client_ip = get_client_ip(request)
    if not is_host_client(client_ip, request.cookies, request=request):
        raise HTTPException(status_code=403, detail="Access restricted to host laptop")
    deleted = database.delete_feedback(feedback_id)
    return {"status": "ok" if deleted else "not_found", "id": feedback_id}

@app.get("/api/public_rooms")
async def get_public_rooms():
    return {
        "status": "ok",
        "rooms": [
            {"id": "main", "name": "/anon/ Public", "icon": "globe", "desc": "All Campus Anonymous Chat"},
            {"id": "cse", "name": "CSE Stream", "icon": "terminal", "desc": "Computer Science & Engineering"},
            {"id": "ece", "name": "Electronics Stream", "icon": "zap", "desc": "Electronics & Communication"},
            {"id": "mech", "name": "Mechanical Stream", "icon": "tool", "desc": "Mechanical Engineering"},
            {"id": "civil", "name": "Civil Stream", "icon": "home", "desc": "Civil Engineering"},
            {"id": "faculty", "name": "Faculty", "icon": "book-open", "desc": "Faculty & Academics Lounge"},
            {"id": "hostel", "name": "Hostel", "icon": "moon", "desc": "Hostel Life & Late Night Talks"}
        ]
    }



# Real-time WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Origin verification to prevent Cross-Site WebSocket Hijacking (CSWSH)
    origin = websocket.headers.get("origin")
    if origin:
        allowed = False
        try:
            p_origin = urllib.parse.urlparse(origin)
            orig_host = (p_origin.hostname or "").lower()

            # 1. Same-Origin check: Origin host matches request Host header
            host_header = (websocket.headers.get("host") or "").strip()
            if host_header:
                req_host = (
                    host_header.split("]")[0].lstrip("[")
                    if host_header.startswith("[")
                    else host_header.split(":")[0]
                ).lower()
                if orig_host and req_host and orig_host == req_host:
                    allowed = True

            # 2. Reverse proxy / tunnel: Origin host matches X-Forwarded-Host header
            if not allowed:
                fwd_header = (websocket.headers.get("x-forwarded-host") or "").split(",")[0].strip()
                if fwd_header:
                    fwd_host = (
                        fwd_header.split("]")[0].lstrip("[")
                        if fwd_header.startswith("[")
                        else fwd_header.split(":")[0]
                    ).lower()
                    if orig_host and fwd_host and orig_host == fwd_host:
                        allowed = True

            # 3. Local loopback / bind addresses
            if not allowed and orig_host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "127.0.1.1"):
                allowed = True

            # 4. Machine local hostname
            if not allowed:
                try:
                    local_hostname = socket.gethostname().lower()
                    if orig_host in (local_hostname, f"{local_hostname}.local"):
                        allowed = True
                except Exception:
                    pass

            # 5. Configured public tunnel / domain
            if not allowed:
                try:
                    pub_url = tunnel.get_public_url()
                    if pub_url:
                        pub_p = urllib.parse.urlparse(pub_url)
                        if pub_p.hostname and orig_host == pub_p.hostname.lower():
                            allowed = True
                except Exception:
                    pass

            # 6. Known tunnel domain suffixes
            if not allowed:
                allowed_suffixes = (
                    "localhost",
                    "trycloudflare.com",
                    "ngrok-free.app",
                    "ngrok.io",
                    "ngrok-free.dev",
                    "ngrok.app",
                    "ngrok.dev",
                )
                if any(orig_host == sfx or orig_host.endswith("." + sfx) for sfx in allowed_suffixes):
                    allowed = True

            # 7. LAN IP and private network addresses (RFC 1918 / CGNAT / local subnets)
            if not allowed:
                try:
                    lan_ip = network_intel.get_lan_ip()
                    if lan_ip and orig_host == lan_ip:
                        allowed = True
                except Exception:
                    pass

            if not allowed:
                try:
                    ip_obj = ipaddress.ip_address(orig_host)
                    if ip_obj.is_private or ip_obj.is_loopback:
                        allowed = True
                except ValueError:
                    pass

        except Exception:
            allowed = False

        if not allowed:
            await websocket.close(code=1008)
            return

    client_host = websocket.client.host if websocket.client else "127.0.0.1"
    client_ip = client_host
    is_tunnel = False

    if client_host in ("127.0.0.1", "::1"):
        cf_ip = websocket.headers.get("cf-connecting-ip")
        if cf_ip and network_intel.is_valid_ip(cf_ip.strip()):
            client_ip = cf_ip.strip()
            is_tunnel = True
        elif websocket.headers.get("cf-ray") or websocket.headers.get("ngrok-trace-id"):
            is_tunnel = True
        else:
            for header_name, header_val in websocket.headers.items():
                if header_name.lower() == "x-forwarded-for":
                    first_ip = header_val.split(",")[0].strip()
                    if network_intel.is_valid_ip(first_ip):
                        client_ip = first_ip
                        is_tunnel = True
                        break

    # Check if request came through Cloudflare or ngrok Tunnel
    host_hdr = websocket.headers.get("host") or ""
    if (
        websocket.headers.get("cf-connecting-ip")
        or websocket.headers.get("cf-ray")
        or websocket.headers.get("ngrok-trace-id")
        or "ngrok" in host_hdr
        or "trycloudflare" in host_hdr
        or (origin and ("trycloudflare.com" in origin or "ngrok" in origin))
    ):
        is_tunnel = True

    if not network_intel.is_valid_ip(client_ip):
        client_ip = "127.0.0.1"

    # Host verification: Requires valid host token cookie (never auto-granted over WS)
    is_host = False
    if websocket.cookies.get("host_logged_out") != "1":
        verified_token = websocket.cookies.get("host_verified")
        if verified_token and hmac.compare_digest(str(verified_token), str(config.ADMIN_TOKEN_SECRET)):
            if not is_tunnel and not websocket.headers.get("cf-ray") and not websocket.headers.get("cf-connecting-ip") and not websocket.headers.get("ngrok-trace-id"):
                is_host = True



    # Check connection capacity limits for 500+ users
    can_conn, reason = manager.can_connect(client_ip)
    if not can_conn:
        await websocket.accept()
        await websocket.send_text(json.dumps({"type": "error", "message": reason}))
        await websocket.close()
        return

    if database.is_ip_banned(client_ip):
        await websocket.accept()
        await websocket.send_text(json.dumps({"type": "error", "message": "You are currently blocked by the host."}))
        await websocket.close()
        return

    raw_sess = websocket.query_params.get("session_id") or websocket.cookies.get("session_id")
    if raw_sess and len(raw_sess) <= 64 and re.match(r'^[a-zA-Z0-9_-]+$', raw_sess):
        session_id = raw_sess
    else:
        session_id = uuid.uuid4().hex[:12]

    public_id = generate_public_id(client_ip, session_id)
    
    # Assign animal identity
    animal = animal_avatars.get_animal_for_public_id(public_id)

    hostname = network_intel.resolve_hostname_fast(client_ip)
    mac = network_intel.get_mac_for_ip(client_ip)
    intel = network_intel.parse_device_intel(websocket.headers.get("user-agent", ""))
    device_summary = intel["summary"]

    await asyncio.to_thread(database.upsert_identity, ip=client_ip, hostname=hostname, mac=mac, device_summary=device_summary)
    await manager.connect(websocket, client_ip, session_id, public_id, animal["handle"], animal["avatar_url"], animal.get("sci_name", ""), is_host, websocket.headers.get("user-agent", ""))

    # Send client their assigned animal identity
    await websocket.send_text(json.dumps({
        "type": "my_identity",
        "anon_name": animal["handle"],
        "animal_name": animal["name"],
        "sci_name": animal["sci_name"],
        "avatar_url": animal["avatar_url"],
        "public_id": public_id,
        "is_host": is_host
    }))

    last_post_time = 0.0
    last_typing_time = 0.0
    recent_reactions: List[float] = []

    try:
        while True:
            data = await websocket.receive_text()
            # Guard against giant memory-exhausting WebSocket payloads
            if len(data) > 65536:
                continue

            try:
                payload = json.loads(data)
            except Exception:
                continue

            action = payload.get("action")
            now = time.time()

            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "time": payload.get("time")}))

            elif action == "typing":
                # Rate limit typing broadcasts
                if now - last_typing_time < 2.5:
                    continue
                last_typing_time = now
                await manager.broadcast_typing(animal["name"], websocket)

            elif action == "react":
                # Throttled reaction spam prevention
                recent_reactions = [t for t in recent_reactions if now - t < 2.0]
                if len(recent_reactions) >= 6:
                    continue
                recent_reactions.append(now)

                post_num = payload.get("post_num")
                emoji = payload.get("emoji")
                if post_num and emoji and len(emoji) <= 8:
                    try:
                        p_num = int(post_num)
                        reactions = database.toggle_reaction(p_num, emoji, session_id)
                        await manager.broadcast_reaction(p_num, reactions)
                    except Exception:
                        pass


            elif action == "request_private_chat":
                target_public_id = payload.get("target_public_id")
                if not target_public_id or target_public_id == public_id:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Cannot chat with yourself."}))
                    continue

                target_found = False
                target_name = "Anonymous"
                for ws_t, info_t in manager.active_connections.items():
                    if info_t.get("public_id") == target_public_id:
                        target_found = True
                        target_name = info_t.get("anon_name", "Anonymous")
                        break

                if not target_found:
                    await websocket.send_text(json.dumps({"type": "error", "message": "User is currently offline."}))
                    continue

                invite_id = uuid.uuid4().hex[:8]
                manager.pending_invites[invite_id] = {
                    "from_id": public_id,
                    "from_name": animal["handle"],
                    "from_avatar": animal["avatar_url"],
                    "to_id": target_public_id,
                    "created_at": time.time()
                }

                # Send invite to target
                await manager.send_to_user(target_public_id, {
                    "type": "private_chat_invite",
                    "invite_id": invite_id,
                    "from_id": public_id,
                    "from_name": animal["handle"],
                    "from_avatar": animal["avatar_url"]
                })

                await websocket.send_text(json.dumps({
                    "type": "private_chat_sent",
                    "target_name": target_name
                }))

            elif action == "respond_private_chat":
                invite_id = payload.get("invite_id")
                accepted = bool(payload.get("accepted"))
                invite = manager.pending_invites.pop(invite_id, None)
                if not invite:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Invite expired or already handled."}))
                    continue

                sender_id = invite["from_id"]
                sender_name = invite["from_name"]
                sender_avatar = invite["from_avatar"]

                if not accepted:
                    await manager.send_to_user(sender_id, {
                        "type": "private_chat_declined",
                        "declined_by": animal["handle"],
                        "room_id": invite.get("room_id"),
                        "is_group": bool(invite.get("room_id"))
                    })
                    continue

                if invite.get("room_id"):
                    # Joining an EXISTING private group room
                    target_room_id = invite["room_id"]
                    database.add_user_to_room(target_room_id, public_id, animal["handle"], animal["avatar_url"])

                    sys_msg = database.insert_message(
                        session_id="system",
                        public_id="system",
                        anon_name="SYSTEM",
                        anon_avatar="/api/animal/snow_leopard",
                        ip="127.0.0.1",
                        hostname="localhost",
                        mac="",
                        user_agent="",
                        device_summary="",
                        content=f"{animal['handle']} accepted the invite and joined the chat.",
                        room_id=target_room_id
                    )

                    members = database.get_room_members(target_room_id)
                    conn = database.get_db_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM rooms WHERE room_id = ?", (target_room_id,))
                    r_row = cur.fetchone()
                    conn.close()
                    room_title = r_row["name"] if r_row else f"Group ({len(members)} users)"

                    room_info = {
                        "room_id": target_room_id,
                        "name": room_title,
                        "members": members
                    }

                    # Send room joined to the user who accepted
                    await manager.send_to_user(public_id, {
                        "type": "room_joined",
                        "room": room_info,
                        "system_message": sys_msg
                    })

                    # Broadcast update to room members
                    await manager.broadcast_to_room(target_room_id, {
                        "type": "room_updated",
                        "room": room_info,
                        "system_message": sys_msg
                    })

                    await manager.broadcast_message(sys_msg)

                else:
                    # Creating a NEW 1-on-1 private room
                    room_id = f"dm_{uuid.uuid4().hex[:8]}"
                    room_name = f"{sender_name} & {animal['handle']}"
                    members = [
                        {"public_id": sender_id, "anon_name": sender_name, "anon_avatar": sender_avatar},
                        {"public_id": public_id, "anon_name": animal["handle"], "anon_avatar": animal["avatar_url"]}
                    ]
                    room_data = database.create_private_room(room_id, room_name, sender_id, members)

                    sys_msg = database.insert_message(
                        session_id="system",
                        public_id="system",
                        anon_name="SYSTEM",
                        anon_avatar="/api/animal/snow_leopard",
                        ip="127.0.0.1",
                        hostname="localhost",
                        mac="",
                        user_agent="",
                        device_summary="",
                        content=f"Private room created between {sender_name} and {animal['handle']}. Only participants can view these messages.",
                        room_id=room_id
                    )

                    room_event = {
                        "type": "room_joined",
                        "room": room_data,
                        "system_message": sys_msg
                    }

                    await manager.send_to_user(sender_id, room_event)
                    await manager.send_to_user(public_id, room_event)

                    # Inform host so host can see and moderate any created room
                    host_event = {
                        "type": "room_created",
                        "room": room_data,
                        "system_message": sys_msg
                    }
                    for ws_h, info_h in manager.active_connections.items():
                        if info_h.get("is_host") and info_h.get("public_id") not in (sender_id, public_id):
                            try:
                                await ws_h.send_text(json.dumps(host_event))
                            except Exception:
                                pass

            elif action in ("add_user_to_room", "admin_add_user_to_room"):
                target_public_id = payload.get("target_public_id")
                target_room_id = payload.get("room_id")
                if not target_room_id or target_room_id == "main" or not target_public_id:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Invalid room or user."}))
                    continue

                if not is_host and not database.is_user_in_room(target_room_id, public_id, is_host=False):
                    await websocket.send_text(json.dumps({"type": "error", "message": "You must be a member of this chat to add others."}))
                    continue

                # Look up target user info
                target_user = None
                for ws_t, info_t in manager.active_connections.items():
                    if info_t.get("public_id") == target_public_id:
                        target_user = info_t
                        break

                if not target_user:
                    await websocket.send_text(json.dumps({"type": "error", "message": "User is currently offline."}))
                    continue

                target_name = target_user.get("anon_name", "Anonymous")

                # Get room name
                conn = database.get_db_connection()
                cur = conn.cursor()
                cur.execute("SELECT name FROM rooms WHERE room_id = ?", (target_room_id,))
                r_row = cur.fetchone()
                conn.close()
                room_title = r_row["name"] if r_row else "Private Chat"

                # Send invitation request to target user (must accept to join)
                invite_id = uuid.uuid4().hex[:8]
                manager.pending_invites[invite_id] = {
                    "from_id": public_id,
                    "from_name": animal["handle"],
                    "from_avatar": animal["avatar_url"],
                    "to_id": target_public_id,
                    "room_id": target_room_id,
                    "room_name": room_title,
                    "is_group": True,
                    "created_at": time.time()
                }

                await manager.send_to_user(target_public_id, {
                    "type": "private_chat_invite",
                    "invite_id": invite_id,
                    "from_id": public_id,
                    "from_name": animal["handle"],
                    "from_avatar": animal["avatar_url"],
                    "room_id": target_room_id,
                    "room_name": room_title,
                    "is_group": True
                })

                await websocket.send_text(json.dumps({
                    "type": "private_chat_sent",
                    "target_name": target_name,
                    "is_group": True,
                    "room_name": room_title
                }))

            elif action == "leave_room":
                target_room_id = payload.get("room_id")
                if not target_room_id or target_room_id == "main":
                    continue

                was_deleted = database.remove_user_from_room(target_room_id, public_id)

                # Send confirmation to leaving user
                await websocket.send_text(json.dumps({
                    "type": "room_left",
                    "room_id": target_room_id
                }))

                if was_deleted:
                    await manager.broadcast_room({
                        "type": "room_deleted",
                        "room_id": target_room_id,
                        "reason": "All members left the group"
                    }, target_room_id)
                else:
                    sys_msg = database.insert_message(
                        session_id="system",
                        public_id="system",
                        anon_name="SYSTEM",
                        anon_avatar="/api/animal/snow_leopard",
                        ip="127.0.0.1",
                        hostname="localhost",
                        mac="",
                        user_agent="",
                        device_summary="",
                        content=f"{animal['handle']} left the chat.",
                        room_id=target_room_id
                    )

                    remaining_members = database.get_room_members(target_room_id)
                    conn = database.get_db_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM rooms WHERE room_id = ?", (target_room_id,))
                    r_row = cur.fetchone()
                    conn.close()
                    room_title = r_row["name"] if r_row else "Private Chat"

                    room_info = {
                        "room_id": target_room_id,
                        "name": room_title,
                        "members": remaining_members
                    }

                    await manager.broadcast_to_room(target_room_id, {
                        "type": "room_updated",
                        "room": room_info,
                        "system_message": sys_msg
                    })

                    await manager.broadcast_message(sys_msg)

            elif action == "find_someone":
                # Find distinct online users other than the requester
                candidates = []
                seen_pids = set()
                for ws_t, info_t in manager.active_connections.items():
                    target_pid = info_t.get("public_id")
                    if target_pid and target_pid != public_id and target_pid not in seen_pids:
                        seen_pids.add(target_pid)
                        candidates.append(info_t)

                if not candidates:
                    await websocket.send_text(json.dumps({
                        "type": "find_someone_result",
                        "success": False,
                        "message": "No other users are currently online right now. Invite a friend to connect to this Wi-Fi network!"
                    }))
                    continue

                chosen = random.choice(candidates)
                target_public_id = chosen["public_id"]
                target_name = chosen.get("anon_name", "Anonymous")
                target_avatar = chosen.get("anon_avatar", "")

                invite_id = uuid.uuid4().hex[:8]
                manager.pending_invites[invite_id] = {
                    "from_id": public_id,
                    "from_name": animal["handle"],
                    "from_avatar": animal["avatar_url"],
                    "to_id": target_public_id,
                    "created_at": time.time(),
                    "is_random_match": True
                }

                # Send invite request to random user
                await manager.send_to_user(target_public_id, {
                    "type": "private_chat_invite",
                    "invite_id": invite_id,
                    "from_id": public_id,
                    "from_name": animal["handle"],
                    "from_avatar": animal["avatar_url"],
                    "is_random_match": True,
                    "room_name": f"{animal['handle']} & {target_name}"
                })

                # Notify requester of successful dispatch
                await websocket.send_text(json.dumps({
                    "type": "find_someone_result",
                    "success": True,
                    "target_name": target_name,
                    "target_avatar": target_avatar,
                    "message": f"Random match found! Private chat request sent to {target_name}. Waiting for them to accept..."
                }))

            elif action == "post_message":
                if database.is_ip_banned(client_ip):
                    await websocket.send_text(json.dumps({"type": "error", "message": "You are banned."}))
                    break

                if database.is_ip_muted(client_ip):
                    await websocket.send_text(json.dumps({"type": "error", "message": "You are currently muted by the host."}))
                    continue

                # Rate limit message posting (prevents flooding 500 connected users)
                if now - last_post_time < config.RATE_LIMIT_POST_SEC:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Slow down! Wait a second before posting again."}))
                    continue
                last_post_time = now

                room_id = payload.get("room_id") or "main"
                clean_room = re.sub(r'[^a-zA-Z0-9_-]', '', str(room_id))[:50] or "main"
                if clean_room not in database.PUBLIC_ROOMS and not is_host:
                    if not database.is_user_in_room(clean_room, public_id, is_host):
                        await websocket.send_text(json.dumps({"type": "error", "message": "You are not a participant in this room."}))
                        continue

                content = (payload.get("content") or "").strip()
                # Enforce message character limit
                if len(content) > config.MAX_MESSAGE_LENGTH:
                    content = content[:config.MAX_MESSAGE_LENGTH]

                def is_valid_media_path(p):
                    if not p or not isinstance(p, str):
                        return False
                    if ".." in p or "\\" in p or "\x00" in p:
                        return False
                    if p.startswith("/uploads/") or p.startswith("/static/stickers/") or p.startswith("/static/gifs/"):
                        return True
                    if p.startswith("http://") or p.startswith("https://"):
                        return True
                    return False

                image_path = payload.get("image_path")

                # Auto-extract GIF/sticker/media URL if user sent it or Gboard pasted it as content
                if not image_path and content:
                    extracted_media = extract_media_url_from_text(content)
                    if extracted_media:
                        image_path = extracted_media
                        # Remove the media URL from content so it does not show as a text URL!
                        content = content.replace(extracted_media, "").strip()

                if not is_valid_media_path(image_path):
                    image_path = None

                if image_path:
                    # Resolve Tenor/Giphy page links to direct playable media URL
                    image_path = resolve_media_to_direct_url(image_path)
                    if not is_valid_media_path(image_path):
                        image_path = None

                image_thumb = payload.get("image_thumb")
                if not is_valid_media_path(image_thumb):
                    image_thumb = image_path if image_path else None
                elif image_thumb and ("tenor.com/view/" in image_thumb or "giphy.com/gifs/" in image_thumb):
                    image_thumb = image_path

                image_name = payload.get("image_name")
                if image_name:
                    image_name = re.sub(r'[^a-zA-Z0-9._ -]', '', str(image_name))[:60]
                elif image_path:
                    lower_p = image_path.lower()
                    if "sticker" in lower_p or lower_p.endswith(".svg"):
                        image_name = "Sticker"
                    elif "gif" in lower_p or "tenor" in lower_p or "giphy" in lower_p:
                        image_name = "GIF"
                    else:
                        image_name = "Media"

                image_size = payload.get("image_size")
                image_dims = payload.get("image_dims")
                
                reply_to = payload.get("reply_to")
                if reply_to is not None:
                    try:
                        reply_to = int(reply_to)
                    except (TypeError, ValueError):
                        reply_to = None

                if not content and not image_path:
                    continue

                new_msg = database.insert_message(
                    session_id=session_id,
                    public_id=public_id,
                    anon_name=animal["handle"],
                    anon_avatar=animal["avatar_url"],
                    ip=client_ip,
                    hostname=hostname,
                    mac=mac,
                    user_agent=websocket.headers.get("user-agent", ""),
                    device_summary=device_summary,
                    content=content,
                    image_path=image_path,
                    image_thumb=image_thumb,
                    image_name=image_name,
                    image_size=image_size,
                    image_dims=image_dims,
                    reply_to=reply_to,
                    room_id=clean_room
                )

                await manager.broadcast_message(new_msg)

                # Autonomous Real-Time AI Auto-Moderator Hook:
                # Evaluates incoming post in real time to delete offending posts and ban authors instantly
                asyncio.create_task(ai_admin.moderate_incoming_message(new_msg, manager=manager))

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        remaining_conns = [ws for ws, info in manager.active_connections.items() if info.get("public_id") == public_id]
        if not remaining_conns and not is_host:
            deleted_rooms = database.remove_user_from_all_rooms(public_id)
            for r_id in deleted_rooms:
                await manager.broadcast_to_room(r_id, {
                    "type": "room_deleted",
                    "room_id": r_id,
                    "message": "This group has been deleted because all members left."
                })
    except Exception:
        manager.disconnect(websocket)
        remaining_conns = [ws for ws, info in manager.active_connections.items() if info.get("public_id") == public_id]
        if not remaining_conns and not is_host:
            deleted_rooms = database.remove_user_from_all_rooms(public_id)
            for r_id in deleted_rooms:
                await manager.broadcast_to_room(r_id, {
                    "type": "room_deleted",
                    "room_id": r_id,
                    "message": "This group has been deleted because all members left."
                })

