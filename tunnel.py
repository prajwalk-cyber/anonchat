import os
import re
import sys
import time
import signal
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
URL_FILE = BASE_DIR / ".tunnel_url"
PID_FILE = BASE_DIR / ".tunnel.pid"
LOG_FILE = BASE_DIR / "tunnel.log"

CLOUDFLARED_BIN = "/sbin/cloudflared" if Path("/sbin/cloudflared").exists() else "cloudflared"


NGROK_BIN = "/sbin/ngrok" if Path("/sbin/ngrok").exists() else "ngrok"
NGROK_DOMAIN_FILE = BASE_DIR / ".ngrok_domain"
TOKEN_FILE = BASE_DIR / ".tunnel_token"
DOMAIN_FILE = BASE_DIR / ".tunnel_domain"


def get_public_url() -> str:
    """Return ngrok domain, custom domain, or cached public quick tunnel URL."""
    if NGROK_DOMAIN_FILE.exists():
        try:
            dom = NGROK_DOMAIN_FILE.read_text(encoding="utf-8").strip()
            if dom:
                if not dom.startswith("http"):
                    dom = "https://" + dom
                return dom.rstrip("/")
        except Exception:
            pass

    if DOMAIN_FILE.exists():
        try:
            dom = DOMAIN_FILE.read_text(encoding="utf-8").strip()
            if dom:
                if not dom.startswith("http"):
                    dom = "https://" + dom
                return dom.rstrip("/")
        except Exception:
            pass

    if URL_FILE.exists():
        try:
            url = URL_FILE.read_text(encoding="utf-8").strip()
            if url.startswith("https://"):
                return url
        except Exception:
            pass
    return ""


def is_tunnel_running() -> bool:
    """Check if the background tunnel process is currently alive."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def stop_tunnel():
    """Stop running tunnel process (ngrok or cloudflared) cleanly."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.4)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except Exception:
            pass
        finally:
            try:
                PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    # Ensure no lingering background tunnel processes
    subprocess.run(["pkill", "-f", "cloudflared"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", "ngrok http"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        URL_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def start_tunnel_background(port: int = 8000, timeout: int = 15) -> str:
    """Start background tunnel (ngrok if configured, or Cloudflare)."""
    if is_tunnel_running():
        url = get_public_url()
        if url:
            return url
        stop_tunnel()

    stop_tunnel()
    log_fd = open(LOG_FILE, "w", encoding="utf-8")

    # 1. Check if user configured an ngrok permanent domain
    if NGROK_DOMAIN_FILE.exists():
        raw_domain = NGROK_DOMAIN_FILE.read_text(encoding="utf-8").strip()
        clean_domain = raw_domain.replace("https://", "").replace("http://", "").rstrip("/")
        if clean_domain:
            cmd = [NGROK_BIN, "http", f"--url={clean_domain}", str(port)]
            proc = subprocess.Popen(
                cmd,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setpgrp
            )
            PID_FILE.write_text(str(proc.pid), encoding="utf-8")
            time.sleep(1.5)
            url = f"https://{clean_domain}"
            URL_FILE.write_text(url, encoding="utf-8")
            return url

    # 2. Check for Cloudflare Named Tunnel token
    token = os.getenv("CLOUDFLARE_TUNNEL_TOKEN")
    if not token and TOKEN_FILE.exists():
        try:
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    if token:
        cmd = [CLOUDFLARED_BIN, "tunnel", "run", "--token", token]
        proc = subprocess.Popen(
            cmd,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setpgrp
        )
        PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        time.sleep(2)
        url = get_public_url()
        return url or "https://your-custom-domain.com"

    # 3. Free Cloudflare Quick Tunnel (random trycloudflare.com URL)
    cmd = [CLOUDFLARED_BIN, "tunnel", "--url", f"http://127.0.0.1:{port}"]
    proc = subprocess.Popen(
        cmd,
        stdout=log_fd,
        stderr=subprocess.STDOUT,
        text=True,
        preexec_fn=os.setpgrp
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    start_time = time.time()
    url = ""
    while time.time() - start_time < timeout:
        if proc.poll() is not None:
            break
        if LOG_FILE.exists():
            try:
                content = LOG_FILE.read_text(encoding="utf-8", errors="ignore")
                matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                if matches:
                    url = matches[-1]
                    URL_FILE.write_text(url, encoding="utf-8")
                    break
            except Exception:
                pass
        time.sleep(0.4)

    return url


def set_ngrok(domain: str):
    """Set your permanent free ngrok domain and launch ngrok."""
    clean_domain = domain.strip().replace("https://", "").replace("http://", "").rstrip("/")
    if not clean_domain:
        print("[-] Invalid domain specified.")
        return
    NGROK_DOMAIN_FILE.write_text(clean_domain, encoding="utf-8")
    URL_FILE.write_text(f"https://{clean_domain}", encoding="utf-8")
    # Clear other tunnel types to avoid conflicts
    TOKEN_FILE.unlink(missing_ok=True)
    DOMAIN_FILE.unlink(missing_ok=True)
    print(f"[+] Configured permanent ngrok domain: https://{clean_domain}")
    print("[*] Starting ngrok tunnel in the background...")
    stop_tunnel()
    start_tunnel_background()
    print(f"[+] Tunnel Active! Anyone can connect at: https://{clean_domain}")


def set_named_tunnel(token: str, domain: str):
    """Configure a permanent Cloudflare Named Tunnel token and custom domain."""
    token = token.strip()
    domain = domain.strip()
    if not domain.startswith("http"):
        domain = "https://" + domain
    TOKEN_FILE.write_text(token, encoding="utf-8")
    DOMAIN_FILE.write_text(domain, encoding="utf-8")
    NGROK_DOMAIN_FILE.unlink(missing_ok=True)
    print(f"[+] Configured Cloudflare Named Tunnel token.")
    print(f"[+] Configured Custom Domain: {domain}")
    print("[*] Restarting tunnel to activate custom domain...")
    stop_tunnel()
    start_tunnel_background()
    print(f"[+] Done! Active at: {domain}")


def clear_tunnels():
    """Remove ngrok and named tunnel configs, revert to default."""
    NGROK_DOMAIN_FILE.unlink(missing_ok=True)
    TOKEN_FILE.unlink(missing_ok=True)
    DOMAIN_FILE.unlink(missing_ok=True)
    print("[+] Cleared custom tunnel configurations.")
    stop_tunnel()


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    port = int(os.getenv("PORT", 8000))

    if action == "start":
        print(f"[*] Starting tunnel for port {port}...")
        url = start_tunnel_background(port=port)
        if url:
            print(f"[+] Public Tunnel Active: {url}")
            print("[+] Anyone with this link can now access the website from outside your Wi-Fi!")
        else:
            print("[-] Could not retrieve tunnel URL. Check tunnel.log for details.")
            sys.exit(1)

    elif action == "stop":
        print("[*] Stopping tunnel...")
        stop_tunnel()
        print("[+] Tunnel stopped.")

    elif action == "status":
        running = is_tunnel_running()
        url = get_public_url()
        mode = "ngrok" if NGROK_DOMAIN_FILE.exists() else ("Cloudflare Named" if TOKEN_FILE.exists() else "Cloudflare Quick")
        if running and url:
            print(f"[+] Tunnel is RUNNING ({mode}): {url}")
        elif running:
            print(f"[*] Tunnel process is alive ({mode}), waiting for URL...")
        else:
            print(f"[-] Tunnel is NOT running (Configured Mode: {mode}).")

    elif action == "url":
        url = get_public_url()
        if url:
            print(url)
        else:
            sys.exit(1)

    elif action == "set-ngrok":
        if len(sys.argv) < 3:
            print("Usage: python3 tunnel.py set-ngrok <YOUR_NGROK_DOMAIN>")
            print("Example: python3 tunnel.py set-ngrok anonchat-board.ngrok-free.app")
            sys.exit(1)
        set_ngrok(sys.argv[2])

    elif action == "set-named":
        if len(sys.argv) < 4:
            print("Usage: python3 tunnel.py set-named <TOKEN> <CUSTOM_DOMAIN>")
            print("Example: python3 tunnel.py set-named eyJh... https://chat.mysite.com")
            sys.exit(1)
        set_named_tunnel(sys.argv[2], sys.argv[3])

    elif action == "clear" or action == "set-quick":
        clear_tunnels()
        print("[*] Starting free Quick Tunnel...")
        url = start_tunnel_background()
        if url:
            print(f"[+] Quick Tunnel Active: {url}")

    else:
        print("Usage: python3 tunnel.py [start|stop|status|url|set-ngrok <domain>|set-named <token> <domain>|clear]")


if __name__ == "__main__":
    main()


