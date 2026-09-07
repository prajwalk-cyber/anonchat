import hashlib

ADJECTIVES = [
    "Neon", "Cyber", "Cosmic", "Chill", "Ghost", "Hyper", "Vibe", "Pixel",
    "Turbo", "Savage", "Retro", "Astral", "Gloomy", "Electric", "Silent", "Shadow"
]

NOUNS = [
    "Fox", "Panda", "Goblin", "Phantom", "Alien", "Gremlin", "Viper", "Cat",
    "Falcon", "Wolf", "Otter", "Koala", "Raven", "Dino", "Hawk", "Toad"
]

EMOJIS = [
    "⚡", "👾", "🛸", "👻", "🦊", "🐸", "🐱", "🐼", "🚀", "💀", "✨", "🔥", "🍄", "🗿", "🧋", "🥑"
]

GRADIENTS = [
    "linear-gradient(135deg, #8B5CF6, #EC4899)",
    "linear-gradient(135deg, #3B82F6, #10B981)",
    "linear-gradient(135deg, #F59E0B, #EF4444)",
    "linear-gradient(135deg, #06B6D4, #3B82F6)",
    "linear-gradient(135deg, #EC4899, #F43F5E)",
    "linear-gradient(135deg, #10B981, #06B6D4)",
    "linear-gradient(135deg, #6366F1, #8B5CF6)",
    "linear-gradient(135deg, #F97316, #EAB308)",
]

def generate_genz_identity(public_id: str):
    """Deterministically generate a fun Gen Z handle and avatar based on public_id."""
    val = int(hashlib.md5(public_id.encode("utf-8")).hexdigest(), 16)
    
    adj = ADJECTIVES[val % len(ADJECTIVES)]
    noun = NOUNS[(val // len(ADJECTIVES)) % len(NOUNS)]
    emoji = EMOJIS[(val // (len(ADJECTIVES) * len(NOUNS))) % len(EMOJIS)]
    grad = GRADIENTS[val % len(GRADIENTS)]
    
    handle = f"{adj} {noun} #{public_id[:4]}"
    return {
        "handle": handle,
        "emoji": emoji,
        "gradient": grad
    }
