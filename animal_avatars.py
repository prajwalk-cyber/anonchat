import os
import hashlib
import json
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any

ANIMALS_DIR = Path(__file__).resolve().parent / "static" / "animals"
ANIMALS_DIR.mkdir(parents=True, exist_ok=True)

# Comprehensive list of 160+ animal species from across the planet
ALL_ANIMALS = [
    # Big Cats & Predators
    ("Lion", "Lion", "Panthera leo"),
    ("Bengal Tiger", "Bengal_tiger", "Panthera tigris tigris"),
    ("Snow Leopard", "Snow_leopard", "Panthera uncia"),
    ("Jaguar", "Jaguar", "Panthera onca"),
    ("Cheetah", "Cheetah", "Acinonyx jubatus"),
    ("Leopard", "Leopard", "Panthera pardus"),
    ("Cougar", "Cougar", "Puma concolor"),
    ("Black Panther", "Black_panther", "Panthera pardus"),
    ("Lynx", "Eurasian_lynx", "Lynx lynx"),
    ("Serval", "Serval", "Leptailurus serval"),
    ("Caracal", "Caracal", "Caracal caracal"),
    ("Ocelot", "Ocelot", "Leopardus pardalis"),
    
    # Canines & Hyenas
    ("Gray Wolf", "Gray_wolf", "Canis lupus"),
    ("Arctic Fox", "Arctic_fox", "Vulpes lagopus"),
    ("Red Fox", "Red_fox", "Vulpes vulpes"),
    ("Fennec Fox", "Fennec_fox", "Vulpes zerda"),
    ("Dingo", "Dingo", "Canis dingo"),
    ("Spotted Hyena", "Spotted_hyena", "Crocuta crocuta"),
    ("African Wild Dog", "African_wild_dog", "Lycaon pictus"),
    ("Coyote", "Coyote", "Canis latrans"),
    ("Golden Jackal", "Golden_jackal", "Canis aureus"),
    ("Maned Wolf", "Maned_wolf", "Chrysocyon brachyurus"),
    
    # Bears
    ("Grizzly Bear", "Grizzly_bear", "Ursus arctos horribilis"),
    ("Polar Bear", "Polar_bear", "Ursus maritimus"),
    ("Giant Panda", "Giant_panda", "Ailuropoda melanoleuca"),
    ("Red Panda", "Red_panda", "Ailurus fulgens"),
    ("Sun Bear", "Sun_bear", "Helarctos malayanus"),
    ("Sloth Bear", "Sloth_bear", "Melursus ursinus"),
    ("Spectacled Bear", "Spectacled_bear", "Tremarctos ornatus"),
    
    # Large Herbivores & Ungulates
    ("African Elephant", "African_bush_elephant", "Loxodonta africana"),
    ("Asian Elephant", "Asian_elephant", "Elephas maximus"),
    ("White Rhinoceros", "White_rhinoceros", "Ceratotherium simum"),
    ("Black Rhinoceros", "Black_rhinoceros", "Diceros bicornis"),
    ("Hippopotamus", "Hippopotamus", "Hippopotamus amphibius"),
    ("Giraffe", "Giraffe", "Giraffa camelopardalis"),
    ("Plains Zebra", "Plains_zebra", "Equus quagga"),
    ("Okapi", "Okapi", "Okapia johnstoni"),
    ("Moose", "Moose", "Alces alces"),
    ("Elk", "Elk", "Cervus canadensis"),
    ("Reindeer", "Reindeer", "Rangifer tarandus"),
    ("American Bison", "American_bison", "Bison bison"),
    ("Wild Yak", "Wild_yak", "Bos mutus"),
    ("Alpaca", "Alpaca", "Vicugna pacos"),
    ("Llama", "Llama", "Lama glama"),
    ("Bactrian Camel", "Bactrian_camel", "Camelus bactrianus"),
    ("Dromedary", "Dromedary", "Camelus dromedarius"),
    ("Bighorn Sheep", "Bighorn_sheep", "Ovis canadensis"),
    ("Mountain Goat", "Mountain_goat", "Oreamnos americanus"),
    ("Alpine Ibex", "Alpine_ibex", "Capra ibex"),
    
    # Small & Medium Mammals
    ("Capybara", "Capybara", "Hydrochoerus hydrochaeris"),
    ("Sea Otter", "Sea_otter", "Enhydra lutris"),
    ("Giant Otter", "Giant_otter", "Pteronura brasiliensis"),
    ("Honey Badger", "Honey_badger", "Mellivora capensis"),
    ("Wolverine", "Wolverine", "Gulo gulo"),
    ("Meerkat", "Meerkat", "Suricata suricatta"),
    ("European Hedgehog", "European_hedgehog", "Erinaceus europaeus"),
    ("North American Beaver", "North_American_beaver", "Castor canadensis"),
    ("Crested Porcupine", "Crested_porcupine", "Hystrix cristata"),
    ("Three-toed Sloth", "Three-toed_sloth", "Bradypus"),
    ("Giant Anteater", "Giant_anteater", "Myrmecophaga tridactyla"),
    ("Pangolin", "Pangolin", "Manis"),
    ("Nine-banded Armadillo", "Nine-banded_armadillo", "Dasypus novemcinctus"),
    ("Raccoon", "Raccoon", "Procyon lotor"),
    ("European Badger", "European_badger", "Meles meles"),
    ("Platypus", "Platypus", "Ornithorhynchus anatinus"),
    ("Short-beaked Echidna", "Short-beaked_echidna", "Tachyglossus aculeatus"),
    ("Chinchilla", "Chinchilla", "Chinchilla chinchilla"),
    
    # Marsupials
    ("Koala", "Koala", "Phascolarctos cinereus"),
    ("Red Kangaroo", "Red_kangaroo", "Osphranter rufus"),
    ("Common Wombat", "Common_wombat", "Vombatus ursinus"),
    ("Tasmanian Devil", "Tasmanian_devil", "Sarcophilus harrisii"),
    ("Quokka", "Quokka", "Setonix brachyurus"),
    ("Sugar Glider", "Sugar_glider", "Petaurus breviceps"),
    ("Tree-kangaroo", "Tree-kangaroo", "Dendrolagus"),
    ("Bilby", "Greater_bilby", "Macrotis lagotis"),
    ("Numbat", "Numbat", "Myrmecobius fasciatus"),
    
    # Primates
    ("Chimpanzee", "Chimpanzee", "Pan troglodytes"),
    ("Western Gorilla", "Western_gorilla", "Gorilla gorilla"),
    ("Bornean Orangutan", "Bornean_orangutan", "Pongo pygmaeus"),
    ("Mandrill", "Mandrill", "Mandrillus sphinx"),
    ("Ring-tailed Lemur", "Ring-tailed_lemur", "Lemur catta"),
    ("Lar Gibbon", "Lar_gibbon", "Hylobates lar"),
    ("Japanese Macaque", "Japanese_macaque", "Macaca fuscata"),
    ("Aye-aye", "Aye-aye", "Daubentonia madagascariensis"),
    ("Proboscis Monkey", "Proboscis_monkey", "Nasalis larvatus"),
    ("Golden Lion Tamarin", "Golden_lion_tamarin", "Leontopithecus rosalia"),
    
    # Marine Mammals
    ("Blue Whale", "Blue_whale", "Balaenoptera musculus"),
    ("Humpback Whale", "Humpback_whale", "Megaptera novaeangliae"),
    ("Orca", "Orca", "Orcinus orca"),
    ("Beluga Whale", "Beluga_whale", "Delphinapterus leucas"),
    ("Narwhal", "Narwhal", "Monodon monoceros"),
    ("Bottlenose Dolphin", "Common_bottlenose_dolphin", "Tursiops truncatus"),
    ("West Indian Manatee", "West_Indian_manatee", "Trichechus manatus"),
    ("Walrus", "Walrus", "Odobenus rosmarus"),
    ("Harbor Seal", "Harbor_seal", "Phoca vitulina"),
    ("Elephant Seal", "Southern_elephant_seal", "Mirounga leonina"),
    
    # Birds of Prey & Owls
    ("Bald Eagle", "Bald_eagle", "Haliaeetus leucocephalus"),
    ("Golden Eagle", "Golden_eagle", "Aquila chrysaetos"),
    ("Peregrine Falcon", "Peregrine_falcon", "Falco peregrinus"),
    ("Barn Owl", "Barn_owl", "Tyto alba"),
    ("Snowy Owl", "Snowy_owl", "Bubo scandiacus"),
    ("Great Horned Owl", "Great_horned_owl", "Bubo virginianus"),
    ("Osprey", "Osprey", "Pandion haliaetus"),
    ("Harpy Eagle", "Harpy_eagle", "Harpia harpyja"),
    ("Secretarybird", "Secretarybird", "Sagittarius serpentarius"),
    
    # Exotic & Flightless Birds
    ("Emperor Penguin", "Emperor_penguin", "Aptenodytes forsteri"),
    ("King Penguin", "King_penguin", "Aptenodytes patagonicus"),
    ("Greater Flamingo", "Greater_flamingo", "Phoenicopterus roseus"),
    ("Scarlet Macaw", "Scarlet_macaw", "Ara macao"),
    ("Toco Toucan", "Toco_toucan", "Ramphastos toco"),
    ("Common Kingfisher", "Common_kingfisher", "Alcedo atthis"),
    ("Ruby-throated Hummingbird", "Ruby-throated_hummingbird", "Archilochus colubris"),
    ("Atlantic Puffin", "Atlantic_puffin", "Fratercula arctica"),
    ("Wandering Albatross", "Wandering_albatross", "Diomedea exulans"),
    ("Common Ostrich", "Common_ostrich", "Struthio camelus"),
    ("Emu", "Emu", "Dromaius novaehollandiae"),
    ("Southern Cassowary", "Southern_cassowary", "Casuarius casuarius"),
    ("Kiwi", "Kiwi_(bird)", "Apteryx"),
    ("Indian Peafowl", "Indian_peafowl", "Pavo cristatus"),
    ("Common Raven", "Common_raven", "Corvus corax"),
    ("Shoebill", "Shoebill", "Balaeniceps rex"),
    ("Laughing Kookaburra", "Laughing_kookaburra", "Dacelo novaeguineae"),
    
    # Reptiles & Amphibians
    ("Komodo Dragon", "Komodo_dragon", "Varanus komodoensis"),
    ("Veiled Chameleon", "Veiled_chameleon", "Chamaeleo calyptratus"),
    ("Bearded Dragon", "Central_bearded_dragon", "Pogona vitticeps"),
    ("Tokay Gecko", "Tokay_gecko", "Gekko gecko"),
    ("Saltwater Crocodile", "Saltwater_crocodile", "Crocodylus porosus"),
    ("American Alligator", "American_alligator", "Alligator mississippiensis"),
    ("Galapagos Tortoise", "Galapagos_tortoise", "Chelonoidis niger"),
    ("Green Sea Turtle", "Green_sea_turtle", "Chelonia mydas"),
    ("King Cobra", "King_cobra", "Ophiophagus hannah"),
    ("Green Anaconda", "Green_anaconda", "Eunectes murinus"),
    ("Poison Dart Frog", "Poison_dart_frog", "Dendrobatidae"),
    ("Red-eyed Tree Frog", "Agalychnis_callidryas", "Agalychnis callidryas"),
    ("Axolotl", "Axolotl", "Ambystoma mexicanum"),
    
    # Sharks, Rays & Ocean Fish
    ("Great White Shark", "Great_white_shark", "Carcharodon carcharias"),
    ("Hammerhead Shark", "Great_hammerhead", "Sphyrna mokarran"),
    ("Whale Shark", "Whale_shark", "Rhincodon typus"),
    ("Giant Oceanic Manta Ray", "Giant_oceanic_manta_ray", "Mobula birostris"),
    ("Giant Pacific Octopus", "Enteroctopus_dofleini", "Enteroctopus dofleini"),
    ("Blue-ringed Octopus", "Blue-ringed_octopus", "Hapalochlaena"),
    ("Flamboyant Cuttlefish", "Metasepia_pfefferi", "Metasepia pfefferi"),
    ("Seahorse", "Seahorse", "Hippocampus"),
    ("Clownfish", "Ocellaris_clownfish", "Amphiprion ocellaris"),
    ("Red Lionfish", "Red_lionfish", "Pterois volitans"),
    ("Pufferfish", "Tetraodontidae", "Tetraodontidae"),
    ("Moorish Idol", "Moorish_idol", "Zanclus cornutus"),
    ("Blue Tang", "Paracanthurus", "Paracanthurus hepatus"),
    ("Swordfish", "Swordfish", "Xiphias gladius"),
    ("Chambered Nautilus", "Chambered_nautilus", "Nautilus pompilius"),
    ("Moray Eel", "Moray_eel", "Muraenidae"),
]

def get_animal_for_public_id(public_id: str) -> Dict[str, Any]:
    """Map public_id deterministically to an animal species."""
    val = int(hashlib.md5(public_id.encode("utf-8")).hexdigest(), 16)
    idx = val % len(ALL_ANIMALS)
    name, wiki_title, sci_name = ALL_ANIMALS[idx]
    slug = wiki_title.lower().replace("_(bird)", "").replace("_(mammal)", "")

    handle = f"{name} #{public_id[:4]}"
    avatar_url = f"/api/animal/{slug}"

    return {
        "name": name,
        "handle": handle,
        "wiki_title": wiki_title,
        "sci_name": sci_name,
        "slug": slug,
        "avatar_url": avatar_url
    }

def get_cached_or_fetch_avatar(slug: str) -> bytes:
    """Fetch from local disk or download from Wikimedia Commons API and cache locally."""
    local_path = ANIMALS_DIR / f"{slug}.jpg"
    if local_path.exists() and local_path.stat().st_size > 1000:
        with open(local_path, "rb") as f:
            return f.read()

    # Find the wiki title for this slug
    wiki_title = slug
    for name, title, sci in ALL_ANIMALS:
        s = title.lower().replace("_(bird)", "").replace("_(mammal)", "")
        if s == slug:
            wiki_title = title
            break

    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(wiki_title)}"
        req = urllib.request.Request(url, headers={"User-Agent": "AnonChatClassic/2.0 (local board)"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read())
            thumb_url = data.get("thumbnail", {}).get("source")
            if thumb_url:
                img_req = urllib.request.Request(thumb_url, headers={"User-Agent": "AnonChatClassic/2.0 (local board)"})
                with urllib.request.urlopen(img_req, timeout=4) as ir:
                    img_bytes = ir.read()
                    with open(local_path, "wb") as f:
                        f.write(img_bytes)
                    return img_bytes
    except Exception:
        pass

    # If already cached even if small, return it
    if local_path.exists():
        with open(local_path, "rb") as f:
            return f.read()

    # Minimal fallback SVG if offline
    return generate_fallback_svg(slug.replace("_", " ").title())

def generate_fallback_svg(name: str) -> bytes:
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
        <rect width="100" height="100" fill="#2d3748" rx="8"/>
        <g fill="#a0aec0">
          <circle cx="36" cy="38" r="6"/>
          <circle cx="64" cy="38" r="6"/>
          <circle cx="26" cy="50" r="5"/>
          <circle cx="74" cy="50" r="5"/>
          <path d="M50 48 C38 48, 34 60, 39 68 C44 76, 56 76, 61 68 C66 60, 62 48, 50 48 Z"/>
        </g>
        <text x="50" y="86" font-family="-apple-system, sans-serif" font-size="9" text-anchor="middle" fill="#e2e8f0">{name[:10]}</text>
    </svg>"""
    return svg.encode("utf-8")
