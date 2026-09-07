import os
import re
import json
import time
import asyncio
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

import database

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("OLLAMA_DEFAULT_MODEL", "qwen2.5:1.5b")

# Regex for instant pre-screening of high-severity safety violations (< 1ms)
PHONE_REGEX = re.compile(
    r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b|(?:\+?91[\s-]?)?[6-9]\d{9}\b'
)
IP_LOGGER_REGEX = re.compile(
    r'\b(?:https?://)?(?:[a-zA-Z0-9-]+\.)*(?:iplogger\.(?:org|com|ru)|grabify\.link|blasze\.com|2no\.co|yip\.su|partpicker\.site|leaklookup\.com|bit\.ly/.*doxx?)\b',
    re.IGNORECASE
)
THREAT_REGEX = re.compile(
    r'\b(?:i(?:\'ll|\s+will|\s+am\s+going\s+to)?\s+(?:kill|murder|slit|shoot|bomb|burn\s+down)\s+you|kill\s+yourself|kys)\b',
    re.IGNORECASE
)


def check_ollama_alive(timeout: float = 1.0) -> bool:
    """Check if Ollama server is answering on port 11434."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", headers={"User-Agent": "AnonChat-AI/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def get_available_models(timeout: float = 1.5) -> List[str]:
    """Retrieve list of locally installed models from Ollama."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", headers={"User-Agent": "AnonChat-AI/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                return models
    except Exception:
        pass
    return []


def get_fast_moderation_model() -> str:
    """Return the fastest available model for real-time live message moderation."""
    configured = database.get_setting("ai_automod_model")
    if configured:
        return configured
    models = get_available_models(0.8)
    if "qwen2.5:0.5b" in models:
        return "qwen2.5:0.5b"
    if "qwen2.5:1.5b" in models:
        return "qwen2.5:1.5b"
    return DEFAULT_MODEL


def get_automod_config() -> Dict[str, Any]:
    """Retrieve current real-time AI auto-moderation settings."""
    return {
        "enabled": database.get_setting("ai_realtime_automod", "1") == "1",
        "mode": database.get_setting("ai_automod_mode", "delete_and_ban"),
        "model": database.get_setting("ai_automod_model", "qwen2.5:0.5b"),
        "purge": database.get_setting("ai_automod_purge", "1") == "1"
    }


def set_automod_config(enabled: Optional[bool] = None, mode: Optional[str] = None, model: Optional[str] = None, purge: Optional[bool] = None):
    """Update real-time AI auto-moderation settings."""
    if enabled is not None:
        database.set_setting("ai_realtime_automod", "1" if enabled else "0")
    if mode is not None and mode in ("delete_and_ban", "delete_only"):
        database.set_setting("ai_automod_mode", mode)
    if model is not None:
        database.set_setting("ai_automod_model", model)
    if purge is not None:
        database.set_setting("ai_automod_purge", "1" if purge else "0")



def build_system_context(room_id: str = "main", limit: int = 15) -> str:
    """Build a dynamic snapshot of the current live chat for Qwen to reference."""
    try:
        msgs = database.get_messages(room_id=room_id, limit=limit, is_host=True)
        if not msgs:
            return "Current chat has no recent messages."

        lines = [f"=== CURRENT LIVE CHAT SNAPSHOT (Room: {room_id}, Recent {len(msgs)} Messages) ==="]
        for m in msgs:
            post_num = m.get("post_num")
            anon_name = m.get("anon_name", "Anonymous")
            public_id = m.get("public_id", "")
            ip = m.get("ip", "")
            animal = m.get("animal_name") or m.get("animal_slug") or ""
            content = m.get("content", "").replace("\n", " ").strip()
            image = m.get("image_name") or ""
            real_name = m.get("id_real_name") or ""

            tag = f"Post #{post_num} | Author: {anon_name}"
            if public_id:
                tag += f" [PID: {public_id}]"
            if ip:
                tag += f" [IP: {ip}]"
            if animal:
                tag += f" ({animal})"
            if real_name:
                tag += f" [Host Known: {real_name}]"
            
            body = content if content else (f"[Image: {image}]" if image else "[Empty message]")
            lines.append(f"{tag}: {body}")

        lines.append("=== END OF SNAPSHOT ===")
        return "\n".join(lines)
    except Exception as e:
        return f"Could not retrieve chat snapshot: {e}"


async def execute_user_direct_commands(user_prompt: str, manager: Any = None) -> tuple[List[str], List[Dict[str, Any]]]:
    """
    Directly extracts and executes any administrative action ordered by the host in their prompt:
    - "delete post 1055", "delete 1055", "remove post 1055", "delete posts 1050, 1051"
    - "ban author of post 1055", "ban post 1055", "remove post 1055 and ban him"
    - "ban Gray Wolf", "ban author Gray Wolf #0b13", "ban user 0b13"
    - "purge post 1055", "purge Gray Wolf"
    - "clear chat", "clear room", "clear cse"
    """
    actions_taken = []
    messages = []
    p = user_prompt.strip()

    # 1. Post deletions
    del_nums = set()
    multi_del = re.findall(r'(?:delete|remove|purge|erase|drop)\s+(?:posts?|messages?)\s*#?([0-9,\s]+)', p, re.IGNORECASE)
    for m in multi_del:
        for num in re.findall(r'\d+', m):
            del_nums.add(int(num))

    single_del = re.findall(r'(?:delete|remove|erase|drop)\s+(?:post\s+|#)?(\d{1,7})\b(?!\.\d)', p, re.IGNORECASE)
    for num in single_del:
        del_nums.add(int(num))

    for p_num in sorted(del_nums):
        p_info = database.get_post_info(p_num)
        author = p_info.get("anon_name", "Unknown") if p_info else "Unknown"
        ip = p_info.get("ip", "") if p_info else ""
        database.delete_message(p_num)
        if manager:
            try:
                await manager.broadcast_delete(p_num)
            except Exception:
                pass
        actions_taken.append({"action": "delete_post", "post_num": p_num, "status": "executed"})
        database.log_ai_moderation(p_num, author, ip, "delete_post", "Host direct prompt command")
        messages.append(f"🛡️ **[Action Executed: Deleted Post #{p_num}]**")

    # 2. Ban author of post
    ban_post_nums = set()
    direct_ban_posts = re.findall(r'(?:ban|block|kick)\s+(?:the\s+)?(?:author\s+of\s+post\s+|author\s+of\s+|poster\s+of\s+|author\s+|user\s+of\s+post\s+|user\s+of\s+|post\s+|#)?(\d{1,7})\b(?!\.\d)', p, re.IGNORECASE)
    for num in direct_ban_posts:
        ban_post_nums.add(int(num))

    if re.search(r'(?:and\s+)?(?:ban|block|kick)\s+(?:him|her|them|the\s+author|author|user|poster)', p, re.IGNORECASE):
        for num in re.findall(r'#?(\d{1,7})\b(?!\.\d)', p):
            ban_post_nums.add(int(num))

    for p_num in sorted(ban_post_nums):
        p_info = database.get_post_info(p_num)
        if p_info and p_info.get("ip"):
            ip = p_info["ip"]
            author = p_info.get("anon_name", "Author")
            database.set_ban_status(ip, True)
            if manager:
                try:
                    await manager.kick_ip(ip)
                except Exception:
                    pass
            if database.get_setting("ai_automod_purge", "1") == "1":
                cnt, p_list = database.delete_all_from_ip(ip)
                if manager and p_list:
                    try:
                        await manager.broadcast_purge(ip, p_list)
                    except Exception:
                        pass
            actions_taken.append({"action": "ban_author", "post_num": p_num, "ip": ip, "author": author, "status": "executed"})
            database.log_ai_moderation(p_num, author, ip, "ban_author", f"Banned author of post #{p_num} (Host command)")
            messages.append(f"🚫 **[Action Executed: Banned {author} (Author of #{p_num})]**")

    # 3. Purge author of post
    purge_post_nums = set(int(x) for x in re.findall(r'purge\s+(?:author\s+of\s+post\s+|author\s+of\s+|author\s+|post\s+)?#?(\d{1,7})\b(?!\.\d)', p, re.IGNORECASE))
    for p_num in sorted(purge_post_nums):
        p_info = database.get_post_info(p_num)
        if p_info and p_info.get("ip"):
            ip = p_info["ip"]
            author = p_info.get("anon_name", "Author")
            cnt, p_list = database.delete_all_from_ip(ip)
            database.set_ban_status(ip, True)
            if manager:
                try:
                    await manager.broadcast_purge(ip, p_list)
                    await manager.kick_ip(ip)
                except Exception:
                    pass
            actions_taken.append({"action": "purge_author", "post_num": p_num, "ip": ip, "author": author, "purged_count": cnt, "status": "executed"})
            database.log_ai_moderation(p_num, author, ip, "purge_author", f"Purged {cnt} posts & banned (Host command)")
            messages.append(f"🔥 **[Action Executed: Purged {cnt} Posts & Banned {author} (Post #{p_num})]**")

    # 4. Ban by author handle/name
    if not ban_post_nums and not purge_post_nums:
        name_match = re.search(r'(?:ban|block|kick)\s+(?:author\s+|user\s+)?([A-Za-z][A-Za-z0-9_ -]+(?:#[a-f0-9]{4})?)', p, re.IGNORECASE)
        if name_match:
            candidate = name_match.group(1).strip()
            if candidate.lower() not in ("chat", "all", "the", "everyone", "room", "messages", "post", "posts", "him", "her", "them", "user", "author"):
                target_ip = database.get_ip_by_author_name(candidate)
                if target_ip:
                    database.set_ban_status(target_ip, True)
                    if manager:
                        try:
                            await manager.kick_ip(target_ip)
                        except Exception:
                            pass
                    if database.get_setting("ai_automod_purge", "1") == "1":
                        cnt, p_list = database.delete_all_from_ip(target_ip)
                        if manager and p_list:
                            try:
                                await manager.broadcast_purge(target_ip, p_list)
                            except Exception:
                                pass
                    actions_taken.append({"action": "ban_author_by_name", "author": candidate, "ip": target_ip, "status": "executed"})
                    database.log_ai_moderation(None, candidate, target_ip, "ban_author", "Banned by handle (Host command)")
                    messages.append(f"🚫 **[Action Executed: Banned Author '{candidate}']**")

    # 5. Ban by public ID or IP
    ip_match = re.search(r'(?:ban|block|kick)\s+(?:ip\s+)?((?:\d{1,3}\.){3}\d{1,3})\b', p, re.IGNORECASE)
    if ip_match:
        target_ip = ip_match.group(1)
        database.set_ban_status(target_ip, True)
        if manager:
            try:
                await manager.kick_ip(target_ip)
            except Exception:
                pass
        actions_taken.append({"action": "ban_user", "ip": target_ip, "status": "executed"})
        database.log_ai_moderation(None, "IP", target_ip, "ban_user", "Banned by IP (Host command)")
        messages.append(f"🚫 **[Action Executed: Banned IP {target_ip}]**")

    pid_match = re.search(r'(?:ban|block|kick)\s+(?:user\s+|id\s+|pid\s+)?#?([a-f0-9]{4})\b', p, re.IGNORECASE)
    if pid_match and not ip_match and not ban_post_nums:
        uid = pid_match.group(1)
        target_ip = database.get_ip_by_public_id(uid)
        if target_ip:
            database.set_ban_status(target_ip, True)
            if manager:
                try:
                    await manager.kick_ip(target_ip)
                except Exception:
                    pass
            actions_taken.append({"action": "ban_user", "user_id": uid, "ip": target_ip, "status": "executed"})
            database.log_ai_moderation(None, f"User #{uid}", target_ip, "ban_user", "Banned by public ID (Host command)")
            messages.append(f"🚫 **[Action Executed: Banned User #{uid}]**")

    # 6. Clear chat / room
    if re.search(r'(?:clear|purge|empty)\s+(?:chat|all\s+messages|all\s+posts)', p, re.IGNORECASE):
        database.clear_messages()
        if manager:
            try:
                await manager.broadcast_clear()
            except Exception:
                pass
        actions_taken.append({"action": "clear_chat", "status": "executed"})
        database.log_ai_moderation(None, "Admin", "", "clear_chat", "Cleared all messages (Host command)")
        messages.append("🧹 **[Action Executed: Cleared all messages from the board]**")
    else:
        clear_room_match = re.search(r'(?:clear|purge|empty)\s+(?:room\s+|group\s+)([a-zA-Z0-9_-]+)', p, re.IGNORECASE)
        if clear_room_match:
            r_id = clear_room_match.group(1)
            database.clear_room_messages(r_id)
            if manager:
                try:
                    await manager.broadcast_clear()
                except Exception:
                    pass
            actions_taken.append({"action": "clear_room", "room_id": r_id, "status": "executed"})
            database.log_ai_moderation(None, "Admin", "", "clear_room", f"Cleared room {r_id} (Host command)")
            messages.append(f"🧹 **[Action Executed: Cleared all messages in room {r_id}]**")

    return messages, actions_taken


async def execute_admin_actions(reply_text: str, user_prompt: str = "", manager: Any = None, initial_actions: List[Dict[str, Any]] = None, direct_messages: List[str] = None) -> tuple[str, List[Dict[str, Any]]]:
    """
    Parses and autonomously executes administrative action commands generated by Qwen:
    - ACTION:DELETE_POST post_num=1004 (with or without brackets)
    - ACTION:DELETE_POSTS post_nums="1001,1002"
    - ACTION:BAN_POST_AUTHOR post_num=1004 reason="..."
    - ACTION:BAN_AUTHOR name="Gray Wolf #0b13" reason="..."
    - ACTION:BAN_USER user_id="6c84" reason="..."
    - ACTION:BAN_USER ip="192.168.1.5" reason="..."
    - ACTION:PURGE_AUTHOR post_num=1004 reason="..."
    - ACTION:CLEAR_ROOM room_id="cse"
    Also cleans up all raw command strings from the reply text and replaces them with executed badges.
    """
    actions_taken = list(initial_actions or [])
    processed_text = reply_text

    # 1. Match multiple post deletions
    del_multi_matches = list(re.finditer(
        r'(?:Command\s*:\s*)?[`\*\[\(]{0,3}(?:ACTION\s*:\s*)?DELETE(?:_POSTS|\s+POSTS)\s+(?:post_nums=|posts=)?"?([0-9,\s]+)"?[`\*\]\)]{0,3}',
        processed_text,
        re.IGNORECASE
    ))
    for m in del_multi_matches:
        raw_nums = re.findall(r'\d+', m.group(1))
        p_nums = [int(x) for x in raw_nums]
        if p_nums:
            database.delete_multiple_messages(p_nums)
            for pn in p_nums:
                if manager:
                    try:
                        await manager.broadcast_delete(pn)
                    except Exception:
                        pass
                if not any(a.get("action") == "delete_post" and a.get("post_num") == pn for a in actions_taken):
                    actions_taken.append({"action": "delete_post", "post_num": pn, "status": "executed"})
                    database.log_ai_moderation(pn, "Unknown", "", "delete_post", "Batch deletion by AI")
            num_str = ", ".join(f"#{pn}" for pn in p_nums)
            processed_text = processed_text.replace(m.group(0), f"🛡️ **[Action Executed: Deleted Posts {num_str}]**")

    # 2. Match single post deletion (supports ACTION:DELETE_POST post_num=1004, [ACTION:DELETE #1004], etc.)
    del_matches = list(re.finditer(
        r'(?:Command\s*:\s*)?[`\*\[\(]{0,3}(?:ACTION\s*:\s*)?DELETE(?:_POST)?\s+(?:post_num=|post\s*#?|id=|#)?(\d+)[`\*\]\)]{0,3}',
        processed_text,
        re.IGNORECASE
    ))
    for m in del_matches:
        p_num = int(m.group(1))
        database.delete_message(p_num)
        if manager:
            try:
                await manager.broadcast_delete(p_num)
            except Exception:
                pass
        if not any(a.get("action") == "delete_post" and a.get("post_num") == p_num for a in actions_taken):
            actions_taken.append({"action": "delete_post", "post_num": p_num, "status": "executed"})
            database.log_ai_moderation(p_num, "Unknown", "", "delete_post", "Deleted by AI Co-Pilot")
        processed_text = processed_text.replace(m.group(0), f"🛡️ **[Action Executed: Deleted Post #{p_num}]**")

    # 3. Match PURGE_AUTHOR (purges all posts by author and bans them)
    purge_matches = list(re.finditer(
        r'(?:Command\s*:\s*)?[`\*\[\(]{0,3}(?:ACTION\s*:\s*)?PURGE(?:_AUTHOR|_POST_AUTHOR)?\s+(?:post_num=|post\s*#?|id=|#)?(\d+)(?:\s+reason="?([^"\n\]\*]*)"?)?[`\*\]\)]{0,3}',
        processed_text,
        re.IGNORECASE
    ))
    for m in purge_matches:
        p_num = int(m.group(1))
        reason = m.group(2) or "Violating board guidelines"
        p_info = database.get_post_info(p_num)
        if p_info and p_info.get("ip"):
            ip = p_info["ip"]
            author = p_info.get("anon_name", "Author")
            count, p_nums = database.delete_all_from_ip(ip)
            database.set_ban_status(ip, True)
            if manager:
                try:
                    await manager.broadcast_purge(ip, p_nums)
                    await manager.kick_ip(ip)
                except Exception:
                    pass
            if not any(a.get("action") == "purge_author" and a.get("post_num") == p_num for a in actions_taken):
                actions_taken.append({"action": "purge_author", "post_num": p_num, "ip": ip, "author": author, "purged_count": count, "status": "executed"})
                database.log_ai_moderation(p_num, author, ip, "purge_author", f"Purged {count} posts & banned: {reason}")
            processed_text = processed_text.replace(m.group(0), f"🔥 **[Action Executed: Purged {count} Posts & Banned {author} (Post #{p_num}) - {reason}]**")
        else:
            processed_text = processed_text.replace(m.group(0), f"⚠️ **[Action Failed: Post #{p_num} not found]**")

    # 4. Match BAN_POST_AUTHOR
    ban_author_matches = list(re.finditer(
        r'(?:Command\s*:\s*)?[`\*\[\(]{0,3}(?:ACTION\s*:\s*)?BAN(?:_POST)?_AUTHOR\s+(?:post_num=|post\s*#?|id=|#)?(\d+)(?:\s+author_id="?[^"\n\]\*]*"?)?(?:\s+reason="?([^"\n\]\*]*)"?)?[`\*\]\)]{0,3}',
        processed_text,
        re.IGNORECASE
    ))
    for m in ban_author_matches:
        p_num = int(m.group(1))
        reason = m.group(2) or "Violating board guidelines"
        p_info = database.get_post_info(p_num)
        if p_info and p_info.get("ip"):
            ip = p_info["ip"]
            author = p_info.get("anon_name", "Author")
            database.set_ban_status(ip, True)
            if manager:
                try:
                    await manager.kick_ip(ip)
                except Exception:
                    pass
            if not any(a.get("action") == "ban_author" and a.get("post_num") == p_num for a in actions_taken):
                actions_taken.append({"action": "ban_author", "post_num": p_num, "ip": ip, "author": author, "reason": reason, "status": "executed"})
                database.log_ai_moderation(p_num, author, ip, "ban_author", reason)
            processed_text = processed_text.replace(m.group(0), f"🚫 **[Action Executed: Banned {author} (Author of #{p_num}) - {reason}]**")
        else:
            processed_text = processed_text.replace(m.group(0), f"⚠️ **[Action Failed: Post #{p_num} not found]**")

    # 5. Match BAN_AUTHOR by name/handle
    ban_by_name_matches = list(re.finditer(
        r'(?:Command\s*:\s*)?[`\*\[\(]{0,3}(?:ACTION\s*:\s*)?BAN_AUTHOR\s+(?:name|author|anon_name)="?([^"\n\]\*]+)"?(?:\s+reason="?([^"\n\]\*]*)"?)?[`\*\]\)]{0,3}',
        processed_text,
        re.IGNORECASE
    ))
    for m in ban_by_name_matches:
        author_name = m.group(1).strip()
        reason = m.group(2) or "Violating board guidelines"
        target_ip = database.get_ip_by_author_name(author_name)
        if target_ip:
            database.set_ban_status(target_ip, True)
            if manager:
                try:
                    await manager.kick_ip(target_ip)
                except Exception:
                    pass
            if not any(a.get("action") == "ban_author_by_name" and a.get("author") == author_name for a in actions_taken):
                actions_taken.append({"action": "ban_author_by_name", "author": author_name, "ip": target_ip, "reason": reason, "status": "executed"})
                database.log_ai_moderation(None, author_name, target_ip, "ban_author", reason)
            processed_text = processed_text.replace(m.group(0), f"🚫 **[Action Executed: Banned Author '{author_name}' - {reason}]**")
        else:
            processed_text = processed_text.replace(m.group(0), f"⚠️ **[Action Failed: Could not resolve IP for author '{author_name}']**")

    # 6. Match BAN_USER by ID or IP
    ban_user_matches = list(re.finditer(
        r'(?:Command\s*:\s*)?[`\*\[\(]{0,3}(?:ACTION\s*:\s*)?BAN_USER\s+(?:user_id="?([^"\n\]\s\*]+)"?|ip="?([^"\n\]\s\*]+)"?)(?:\s+reason="?([^"\n\]\*]*)"?)?[`\*\]\)]{0,3}',
        processed_text,
        re.IGNORECASE
    ))
    for m in ban_user_matches:
        user_id = m.group(1)
        ip_addr = m.group(2)
        reason = m.group(3) or "Administrative policy enforcement"
        target_ip = ip_addr
        if not target_ip and user_id:
            target_ip = database.get_ip_by_public_id(user_id)
        
        if target_ip:
            database.set_ban_status(target_ip, True)
            if manager:
                try:
                    await manager.kick_ip(target_ip)
                except Exception:
                    pass
            actions_taken.append({"action": "ban_user", "ip": target_ip, "user_id": user_id, "reason": reason, "status": "executed"})
            database.log_ai_moderation(None, user_id or "User", target_ip, "ban_user", reason)
            identifier = f"User #{user_id}" if user_id else f"IP {target_ip}"
            processed_text = processed_text.replace(m.group(0), f"🚫 **[Action Executed: Banned {identifier} - {reason}]**")
        else:
            processed_text = processed_text.replace(m.group(0), f"⚠️ **[Action Failed: Could not resolve IP for user #{user_id}]**")

    # 7. Match CLEAR_ROOM
    clear_matches = list(re.finditer(
        r'(?:Command\s*:\s*)?[`\*\[\(]{0,3}(?:ACTION\s*:\s*)?CLEAR_ROOM\s+(?:room_id=)?"?([^"\n\]\s\*]+)"?[`\*\]\)]{0,3}',
        processed_text,
        re.IGNORECASE
    ))
    for m in clear_matches:
        r_id = m.group(1)
        database.clear_room_messages(r_id)
        if manager:
            try:
                await manager.broadcast_clear()
            except Exception:
                pass
        actions_taken.append({"action": "clear_room", "room_id": r_id, "status": "executed"})
        database.log_ai_moderation(None, "Admin", "", "clear_room", f"Cleared room {r_id}")
        processed_text = processed_text.replace(m.group(0), f"🧹 **[Action Executed: Cleared all messages in room {r_id}]**")

    # 8. Check JSON action blocks
    json_blocks = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', processed_text, re.DOTALL)
    for block in json_blocks:
        try:
            parsed = json.loads(block)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                act = item.get("action")
                if act in ("delete_post", "delete") and item.get("post_num"):
                    pn = int(item["post_num"])
                    database.delete_message(pn)
                    if manager:
                        await manager.broadcast_delete(pn)
                    actions_taken.append({"action": "delete_post", "post_num": pn, "status": "executed"})
                elif act in ("ban_author", "ban_post_author") and item.get("post_num"):
                    pn = int(item["post_num"])
                    p_info = database.get_post_info(pn)
                    if p_info and p_info.get("ip"):
                        database.set_ban_status(p_info["ip"], True)
                        if manager:
                            await manager.kick_ip(p_info["ip"])
                        actions_taken.append({"action": "ban_author", "post_num": pn, "ip": p_info["ip"], "status": "executed"})
        except Exception:
            pass

    # 9. Clean up any remaining raw command syntax e.g. "ACTION:DELETE_POST..."
    processed_text = re.sub(r'(?:Command\s*:\s*)?[`\*\[\(]{0,3}ACTION:[A-Z_]+[^`\*\n\]\)]*[`\*\]\)]{0,3}', '', processed_text, flags=re.IGNORECASE)
    processed_text = re.sub(r'\n{3,}', '\n\n', processed_text)

    # 10. Prepend direct executed badges if they are not already in processed_text
    if direct_messages:
        badges_to_add = []
        for msg in direct_messages:
            p_match = re.search(r'#(\d+)', msg)
            if p_match:
                pn = p_match.group(0)
                is_del = "Deleted Post" in msg
                is_ban = "Banned" in msg
                # Check if processed_text already has an executed badge of the same type for this post
                if is_del and re.search(rf'Action\s+Executed:.*Deleted.*{re.escape(pn)}', processed_text, re.IGNORECASE):
                    continue
                if is_ban and re.search(rf'Action\s+Executed:.*Banned.*{re.escape(pn)}', processed_text, re.IGNORECASE):
                    continue
            else:
                # Check for author or IP in msg
                author_match = re.search(r"Banned Author '([^']+)'", msg)
                if author_match:
                    aname = author_match.group(1)
                    if re.search(rf'Action\s+Executed:.*Banned.*{re.escape(aname)}', processed_text, re.IGNORECASE):
                        continue
            if msg not in processed_text:
                badges_to_add.append(msg)
        if badges_to_add:
            processed_text = "\n".join(badges_to_add) + "\n\n" + processed_text.strip()

    return processed_text.strip(), actions_taken



async def moderate_incoming_message(msg: Dict[str, Any], manager: Any = None) -> Optional[Dict[str, Any]]:
    """
    Autonomous Real-Time Live Message Guardian.
    Evaluates every incoming post in real-time as it arrives.
    If violating content is detected (doxxing, hate speech, threats, harassment, malicious links):
    - Instantly deletes the post from the database and broadcasts delete to all active connections.
    - Bans the author IP and kicks the author from WebSocket connections.
    - Optionally purges all previous messages from the author.
    - Logs the moderation action and broadcasts an event to host laptop.
    """
    try:
        # Check if real-time auto-moderation is active
        is_enabled = database.get_setting("ai_realtime_automod", "1") == "1"
        if not is_enabled:
            return None

        ip = msg.get("ip", "")
        # Protect host laptop / localhost from being auto-banned
        if ip in ("127.0.0.1", "::1", "localhost") or msg.get("is_host"):
            return None

        post_num = msg.get("post_num")
        if not post_num:
            return None

        content = (msg.get("content") or "").strip()
        image_name = (msg.get("image_name") or "").strip()
        anon_name = msg.get("anon_name") or "Anonymous"

        if not content and not image_name:
            return None

        sample_text = f"{content} {image_name}".strip()
        violation_detected = False
        reason = ""
        severity = "medium"

        # Stage 1: Ultra-fast regex / heuristic screening (< 1ms)
        if PHONE_REGEX.search(sample_text):
            violation_detected = True
            reason = "Doxxing / Leaked Phone Number"
            severity = "high"
        elif IP_LOGGER_REGEX.search(sample_text):
            violation_detected = True
            reason = "Phishing / IP Logger URL"
            severity = "high"
        elif THREAT_REGEX.search(sample_text):
            violation_detected = True
            reason = "Violent Threats / Extreme Harassment"
            severity = "high"

        # Stage 2: Fast Local AI Model Assessment via Ollama
        if not violation_detected and check_ollama_alive(0.8):
            target_model = get_fast_moderation_model()
            system_prompt = (
                "You are an autonomous real-time safety moderator for AnonChat.\n"
                "Evaluate this post and respond ONLY with a JSON object in this format:\n"
                "{\"action\": \"allow\"}\n"
                "OR\n"
                "{\"action\": \"delete\", \"reason\": \"<short reason>\"}\n"
                "OR\n"
                "{\"action\": \"delete_and_ban\", \"reason\": \"<short reason>\"}\n\n"
                "Flag for: doxxing (phone numbers, addresses), violent threats, extreme harassment, hate speech/slurs, phishing links, repetitive spam."
            )
            user_msg = f"Post #{post_num} | Author: {anon_name}\nContent: {content[:250]}"
            payload = {
                "model": target_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 45
                }
            }

            def _query():
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{OLLAMA_HOST}/api/chat",
                    data=req_data,
                    headers={"Content-Type": "application/json", "User-Agent": "AnonChat-AI/1.0"}
                )
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
                return None

            res = await asyncio.to_thread(_query)
            if res and "message" in res:
                raw_out = res["message"].get("content", "").strip()
                # Parse JSON output
                m_json = re.search(r'\{.*?\}', raw_out, re.DOTALL)
                if m_json:
                    try:
                        parsed = json.loads(m_json.group(0))
                        act = str(parsed.get("action", "")).lower()
                        if act in ("delete", "delete_and_ban", "ban"):
                            violation_detected = True
                            reason = parsed.get("reason") or "Violating board guidelines"
                            severity = "high" if act in ("delete_and_ban", "ban") else "medium"
                    except Exception:
                        pass

        # Execute Autonomous Real-Time Moderation Actions
        if violation_detected:
            automod_mode = database.get_setting("ai_automod_mode", "delete_and_ban")
            purge_enabled = database.get_setting("ai_automod_purge", "1") == "1"
            should_ban = (automod_mode == "delete_and_ban") or (severity == "high")

            # 1. Real-time Post Deletion
            database.delete_message(post_num)
            if manager:
                try:
                    await manager.broadcast_delete(post_num)
                except Exception:
                    pass

            purged_count = 0
            # 2. Real-time Author Ban & Disconnect
            if should_ban and ip:
                database.set_ban_status(ip, True)
                if manager:
                    try:
                        await manager.kick_ip(ip)
                    except Exception:
                        pass

                # 3. Real-time Author Purge (if enabled)
                if purge_enabled:
                    purged_count, post_nums = database.delete_all_from_ip(ip)
                    if manager and post_nums:
                        try:
                            await manager.broadcast_purge(ip, post_nums)
                        except Exception:
                            pass

            action_type = "delete_and_ban" if should_ban else "delete"
            database.log_ai_moderation(
                post_num=post_num,
                anon_name=anon_name,
                ip=ip,
                action=f"Auto-{action_type}",
                reason=reason
            )

            # 4. Real-time event notification to host
            event_data = {
                "action": action_type,
                "post_num": post_num,
                "anon_name": anon_name,
                "ip": ip,
                "reason": reason,
                "purged_count": purged_count,
                "timestamp": time.strftime("%H:%M:%S")
            }
            if manager:
                try:
                    await manager.broadcast_ai_event(event_data)
                except Exception:
                    pass

            return event_data

    except Exception as e:
        # Failsafe: never crash WebSocket loop on moderation errors
        pass
    return None


async def chat_with_qwen(
    user_prompt: str,
    history: Optional[List[Dict[str, str]]] = None,
    model: str = None,
    include_chat_context: bool = True,
    room_id: str = "main",
    manager: Any = None,
    timeout: float = 60.0
) -> Dict[str, Any]:
    """
    Send chat messages to Qwen via Ollama API with real-time autonomous execution.
    Direct administrative commands from host (delete, ban, purge, clear) are executed immediately.
    Embedded action commands from Qwen are executed and replaced with executed badges.
    Returns response text, model used, latency, and executed actions array.
    """
    target_model = model or DEFAULT_MODEL
    
    # 1. Immediate direct execution of host commands (delete, ban, purge, clear)
    direct_msgs, direct_actions = await execute_user_direct_commands(user_prompt, manager)

    # 2. Check if Ollama is running
    is_alive = await asyncio.to_thread(check_ollama_alive, 1.2)
    if not is_alive:
        if direct_actions:
            return {
                "status": "ok",
                "reply": "\n".join(direct_msgs) + "\n\n*Action executed immediately by server guardian (Note: Ollama local AI is offline).* ",
                "model": "guardian-direct",
                "latency_ms": 2,
                "actions_taken": direct_actions
            }
        return {
            "status": "offline",
            "reply": "⚠️ **Ollama is not running.**\n\nPlease make sure Ollama is active on your laptop:\n```bash\nollama serve\n```\nOr in a new terminal run:\n```bash\nollama run qwen2.5:1.5b\n```",
            "model": target_model,
            "latency_ms": 0
        }

    # Verify if target model exists, else fallback to available model
    models = await asyncio.to_thread(get_available_models, 1.5)
    if models and target_model not in models:
        # Check if any qwen model is present
        qwen_matches = [m for m in models if "qwen" in m.lower()]
        if qwen_matches:
            target_model = qwen_matches[0]
        elif models:
            target_model = models[0]

    # Prepare messages array with direct administrative powers
    system_intro = (
        "You are Qwen AI Admin Co-Pilot for AnonChat, a real-time anonymous message board running on the host laptop. "
        "You assist the host laptop administrator with moderating discussions, detecting toxic or malicious content, "
        "taking administrative actions, and drafting announcements.\n\n"
        "YOU HAVE DIRECT REAL-TIME ADMINISTRATIVE EXECUTION POWERS on this message board to delete any post or ban any author:\n"
        "1. To delete an offending post: ACTION:DELETE_POST post_num=1004\n"
        "2. To delete multiple posts: ACTION:DELETE_POSTS post_nums=\"1001,1002,1003\"\n"
        "3. To ban author of any post: ACTION:BAN_POST_AUTHOR post_num=1004 reason=\"...\"\n"
        "4. To ban author by handle: ACTION:BAN_AUTHOR name=\"Gray Wolf #0b13\" reason=\"...\"\n"
        "5. To ban user by public ID: ACTION:BAN_USER user_id=\"6c84\" reason=\"...\"\n"
        "6. To ban user by IP: ACTION:BAN_USER ip=\"192.168.1.5\" reason=\"...\"\n"
        "7. To purge and ban an author (delete all their messages + ban): ACTION:PURGE_AUTHOR post_num=1004 reason=\"...\"\n"
        "8. To clear all messages in a room: ACTION:CLEAR_ROOM room_id=\"cse\"\n\n"
        "CRITICAL RULES:\n"
        "- When asked to take action or when violating content is found, output the ACTION commands directly. "
        "The server executes these actions in real time, immediately deleting posts and disconnecting banned users.\n"
        "- NEVER tell the admin to manually run commands or ask them to delete/ban posts. You take action directly or confirm the action taken!\n"
        "- Explain what action was taken clearly, concisely, and professionally."
    )

    if direct_actions:
        system_intro += f"\n\nNOTICE: The server has already executed these direct action(s) requested by the admin: {json.dumps(direct_actions)}. Acknowledge them positively as completed. Do not re-issue raw action commands for them."

    if include_chat_context:
        chat_context = await asyncio.to_thread(build_system_context, room_id, 20)
        system_intro += f"\n\nHere is the current live chat context from the board:\n{chat_context}"

    messages = [{"role": "system", "content": system_intro}]

    # Append conversation history if provided
    if history:
        for msg in history[-8:]:  # keep last 8 exchanges to preserve context window
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_prompt})

    payload = {
        "model": target_model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.9,
            "num_predict": 768,
            "num_gpu": 999  # Offload all layers to NVIDIA GPU
        }
    }

    start_time = time.time()
    try:
        def _post_request():
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{OLLAMA_HOST}/api/chat",
                data=req_data,
                headers={"Content-Type": "application/json", "User-Agent": "AnonChat-AI/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
                return None

        result = await asyncio.to_thread(_post_request)
        latency_ms = int((time.time() - start_time) * 1000)

        if result and "message" in result:
            raw_reply = result["message"].get("content", "").strip()
            # Execute any embedded administrative actions and combine with direct_actions
            final_reply, actions_taken = await execute_admin_actions(
                raw_reply,
                user_prompt=user_prompt,
                manager=manager,
                initial_actions=direct_actions,
                direct_messages=direct_msgs
            )
            return {
                "status": "ok",
                "reply": final_reply,
                "model": target_model,
                "latency_ms": latency_ms,
                "actions_taken": actions_taken
            }
        else:
            if direct_actions:
                return {
                    "status": "ok",
                    "reply": "\n".join(direct_msgs) + "\n\n*Action executed immediately by server guardian.*",
                    "model": target_model,
                    "latency_ms": latency_ms,
                    "actions_taken": direct_actions
                }
            return {
                "status": "error",
                "reply": "Received an unexpected response from Ollama.",
                "model": target_model,
                "latency_ms": latency_ms
            }

    except urllib.error.HTTPError as he:
        latency_ms = int((time.time() - start_time) * 1000)
        err_msg = he.read().decode("utf-8", errors="ignore")
        if direct_actions:
            return {
                "status": "ok",
                "reply": "\n".join(direct_msgs) + f"\n\n*Action executed immediately by server guardian (Note: Ollama error: {he.code})*",
                "model": target_model,
                "latency_ms": latency_ms,
                "actions_taken": direct_actions
            }
        if "not found" in err_msg.lower():
            return {
                "status": "model_not_found",
                "reply": f"⚠️ Model `{target_model}` is not downloaded in Ollama yet.\n\nRun this command in terminal to pull it:\n```bash\nollama pull {target_model}\n```",
                "model": target_model,
                "latency_ms": latency_ms
            }
        return {
            "status": "error",
            "reply": f"Ollama HTTP error {he.code}: {err_msg}",
            "model": target_model,
            "latency_ms": latency_ms
        }
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        if direct_actions:
            return {
                "status": "ok",
                "reply": "\n".join(direct_msgs) + f"\n\n*Action executed immediately by server guardian (Note: {str(e)})*",
                "model": target_model,
                "latency_ms": latency_ms,
                "actions_taken": direct_actions
            }
        return {
            "status": "error",
            "reply": f"Failed to communicate with Ollama: {str(e)}",
            "model": target_model,
            "latency_ms": latency_ms
        }


async def scan_moderation(room_id: str = "main", model: str = None, manager: Any = None, auto_enforce: bool = False) -> Dict[str, Any]:
    """Ask Qwen to audit the recent messages for toxicity, harassment, and policy violations."""
    if auto_enforce:
        prompt = (
            "You are executing an autonomous real-time moderation sweep on recent messages in the chat snapshot above.\n"
            "If any message contains violations (doxxing, hate speech, threats, harassment, malicious links, or severe spam):\n"
            "YOU MUST EXECUTE REAL-TIME ACTIONS IMMEDIATELY using the command format:\n"
            "- To delete the post: [ACTION:DELETE_POST post_num=...]\n"
            "- To ban the author: [ACTION:BAN_POST_AUTHOR post_num=... reason=\"...\"]\n\n"
            "Review each flagged post, output the action commands so the server executes them in real time, "
            "and summarize what was deleted and who was banned. If all recent activity is benign, state that clearly."
        )
    else:
        prompt = (
            "Audit the recent messages in the live chat snapshot above for any of the following policy violations:\n"
            "1. Doxxing (real phone numbers, addresses, personal threats)\n"
            "2. Extreme harassment, slurs, or hate speech\n"
            "3. Phishing or malicious URLs\n"
            "4. Spam floods\n\n"
            "List any flagged posts with their Post #, the author, the offending snippet, the severity (High/Medium/Low), "
            "and a recommended action (Delete Post, Warn User, or Ban IP). "
            "If everything looks peaceful and clean, briefly state that all recent activity is benign."
        )
    return await chat_with_qwen(prompt, model=model, include_chat_context=True, room_id=room_id, manager=manager)

