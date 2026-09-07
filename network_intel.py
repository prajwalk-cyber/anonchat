import socket
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

_dns_executor = ThreadPoolExecutor(max_workers=5)

def get_lan_ip() -> str:
    """Find the best local LAN IP address of this host machine."""
    # First try connecting to a public IP to find default route
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    # Fallback to hostname lookup
    try:
        host_name = socket.gethostname()
        ip = socket.gethostbyname(host_name)
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "127.0.0.1"


import ipaddress

def is_valid_ip(ip: str) -> bool:
    if not ip or not isinstance(ip, str):
        return False
    try:
        ipaddress.ip_address(ip.strip())
        return True
    except ValueError:
        return False

def resolve_hostname_fast(ip: str, timeout: float = 0.3) -> str:
    """Attempt fast reverse DNS lookup with timeout."""
    if not is_valid_ip(ip):
        return ""
    ip = ip.strip()
    if ip in ("127.0.0.1", "::1", "localhost"):
        return "localhost"

    def _lookup():
        try:
            return socket.gethostbyaddr(ip)[0]
        except Exception:
            return ""

    try:
        future = _dns_executor.submit(_lookup)
        res = future.result(timeout=timeout)
        return res or ""
    except Exception:
        return ""


def get_mac_for_ip(ip: str) -> str:
    """Look up MAC address from /proc/net/arp or ip neigh."""
    if not is_valid_ip(ip):
        return ""
    ip = ip.strip()
    if ip in ("127.0.0.1", "::1", "localhost"):
        return ""

    try:
        with open("/proc/net/arp", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == ip:
                    mac = parts[3]
                    if mac != "00:00:00:00:00:00":
                        return mac.upper()
    except Exception:
        pass

    try:
        output = subprocess.check_output(["ip", "neigh", "show", ip], stderr=subprocess.DEVNULL, text=True, timeout=1.0)
        match = re.search(r"lladdr\s+([0-9a-fA-F:]{17})", output)
        if match:
            return match.group(1).upper()
    except Exception:
        pass

    return ""



def parse_device_intel(user_agent: str) -> dict:
    """Parse User-Agent into a clean OS, Browser, and Device summary."""
    ua = user_agent or ""

    # OS detection
    os_name = "Unknown OS"
    if "iPhone" in ua:
        os_name = "Apple iPhone"
    elif "iPad" in ua:
        os_name = "Apple iPad"
    elif "Android" in ua:
        # Check Android version if possible
        m = re.search(r"Android\s+([0-9\.]+)", ua)
        os_name = f"Android {m.group(1)}" if m else "Android"
    elif "Windows NT 10.0" in ua:
        os_name = "Windows 10/11"
    elif "Windows NT 6.3" in ua:
        os_name = "Windows 8.1"
    elif "Windows NT 6.1" in ua:
        os_name = "Windows 7"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_name = "macOS"
    elif "Linux" in ua:
        os_name = "Linux"
    elif "CrOS" in ua:
        os_name = "ChromeOS"

    # Browser detection
    browser_name = "Unknown Browser"
    if "Edg/" in ua or "Edge/" in ua:
        browser_name = "Microsoft Edge"
    elif "OPR/" in ua or "Opera/" in ua:
        browser_name = "Opera"
    elif "SamsungBrowser/" in ua:
        browser_name = "Samsung Browser"
    elif "Chrome/" in ua and "Safari/" in ua:
        browser_name = "Google Chrome"
    elif "Firefox/" in ua:
        browser_name = "Mozilla Firefox"
    elif "Safari/" in ua and "Chrome/" not in ua:
        browser_name = "Apple Safari"
    elif "curl/" in ua:
        browser_name = "curl"

    # Device category
    is_mobile = bool(re.search(r"Mobile|Android|iPhone", ua))
    device_type = "Mobile" if is_mobile else "Desktop / Laptop"
    if "iPad" in ua or "Tablet" in ua:
        device_type = "Tablet"

    summary = f"{device_type} • {os_name} • {browser_name}"
    return {
        "os": os_name,
        "browser": browser_name,
        "device_type": device_type,
        "summary": summary
    }
