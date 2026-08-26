"""Product photography for the SKU catalogue, from Wikimedia Commons.

Openly-licensed photographs rather than scraped manufacturer imagery: a dashboard that
might one day be shown outside the building should not carry other companies' product
shots without permission. Every image keeps its licence and author, and the UI shows
both -- most Commons licences (CC-BY, CC-BY-SA) require attribution, so dropping it
would breach the terms.

Images are downloaded once into web/public/sku/ and served locally. Hotlinking would
leave the dashboard dependent on someone else's uptime and hammer their servers.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from pipeline.common import Config, ROOT, log

LOG = log("sku_images")

API = "https://commons.wikimedia.org/w/api.php"
UA = "sonalika-demand-compass/0.1 (internal analytics prototype)"
OUT = ROOT / "web" / "public" / "sku"
MANIFEST = ROOT / "pipeline" / "config" / "sku_images.json"

# Search terms per SKU. Generic implement words return the right machine far more often
# than a brand-specific query, and Commons is stronger on European farm equipment than
# Indian, so the terms lean on the machine type rather than the market.
TERMS = {
    "ROTAVATOR_5FT": ["rotary tiller tractor", "rotavator"],
    "ROTAVATOR_7FT": ["rotary tiller field", "power tiller rotary"],
    "CULTIVATOR_9T": ["tractor cultivator field", "spring tine cultivator"],
    "DISC_HARROW_16": ["disc harrow tractor", "disc harrow field"],
    "MB_PLOUGH_2F": ["mouldboard plough tractor", "plough field tractor"],
    "REV_PLOUGH_2F": ["reversible plough", "rollover plough tractor"],
    "POWER_HARROW": ["power harrow", "rotary harrow tractor"],
    "SUBSOILER": ["subsoiler", "chisel plough"],
    "LASER_LEVELER": ["land leveller tractor", "land levelling agriculture"],
    "SEED_DRILL_11T": ["seed drill", "seed drill tractor"],
    "SEED_FERT_DRILL_13T": ["seed drill fertiliser", "combine seed drill"],
    "SUPER_SEEDER": ["happy seeder", "zero till drill"],
    "HAPPY_SEEDER": ["happy seeder punjab", "zero tillage drill"],
    "PNEUMATIC_PLANTER": ["precision planter", "pneumatic seeder"],
    "MULTICROP_PLANTER": ["maize planter", "row crop planter"],
    "RAISED_BED_PLANTER": ["bed planter agriculture", "ridge planter"],
    "RICE_TRANSPLANTER": ["rice transplanter", "paddy transplanter"],
    "BOOM_SPRAYER": ["boom sprayer tractor", "field sprayer agriculture"],
    "ORCHARD_SPRAYER": ["orchard sprayer", "air blast sprayer vineyard"],
    "HTP_SPRAYER": ["knapsack sprayer agriculture", "pesticide sprayer farm"],
    "FERT_BROADCASTER": ["fertilizer spreader tractor", "fertiliser broadcaster"],
    "PTO_PUMP": ["irrigation pump agriculture", "farm water pump"],
    "WATER_TANKER_3000L": ["water tanker trailer farm", "agricultural water bowser"],
    "REAPER_BINDER": ["reaper binder", "harvesting reaper"],
    "TRACTOR_REAPER": ["reaper harvester wheat", "crop reaper machine"],
    "POTATO_HARVESTER": ["potato harvester", "groundnut harvester"],
    "STRAW_REAPER": ["straw reaper", "straw chopper agriculture"],
    "MULCHER": ["flail mulcher tractor", "mulcher agriculture"],
    "ROUND_BALER": ["round baler", "hay baler field"],
    "HAY_RAKE": ["hay rake tractor", "rotary rake hay"],
    "MULTICROP_THRESHER": ["threshing machine agriculture", "thresher wheat"],
    "CHAFF_CUTTER": ["chaff cutter", "forage chopper"],
    "MAIZE_SHELLER": ["maize sheller", "corn sheller machine"],
    "TROLLEY_2W_5T": ["tractor trailer farm", "agricultural tipping trailer"],
    "TROLLEY_4W_8T": ["farm trailer four wheel", "agricultural trailer tandem"],
    "AGRI_DRONE": ["agricultural drone spraying", "crop spraying drone"],
    "GPS_GUIDANCE_KIT": ["tractor gps guidance", "precision agriculture display"],
}

# Commons carries plenty of unrelated pictures; reject obvious misses by title.
REJECT = ("dynamite", "1911", "map", "logo", "diagram", "coat of arms", "stamp", "postage", "portrait",
          "monument", ".svg", "chart", "graph", "cover page", "title page", "book",
          "volume", "manuscript", "poster", "banner", "flag", "seal", "emblem",
          "screenshot", "painting", "drawing", "engraving", "sketch")

# Words that make a Commons title plausibly about farm machinery. A title sharing no
# word with the query is almost always a false positive -- that is how a book cover
# ended up standing in for a GPS guidance kit.
RELEVANT = ("tractor", "plough", "plow", "harrow", "tiller", "cultivator", "seeder",
            "drill", "planter", "sprayer", "spraying", "harvest", "reaper", "baler",
            "thresh", "trailer", "mower", "rake", "mulch", "spreader", "combine",
            "agricultur", "farm", "field", "crop", "hay", "straw", "irrigat", "pump",
            "drone", "machinery", "implement", "sowing", "tillage", "transplanter",
            "subsoil", "leveller", "leveler", "sheller", "chaff", "fodder")


def _get(url: str, timeout: int = 30) -> bytes:
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout).read()


def _search(term: str, n: int = 6) -> list[dict]:
    # Unquoted: a quoted phrase demands an exact match and Commons file descriptions
    # rarely contain the exact wording, which is why the first pass found 6 of 37.
    q = {"action": "query", "format": "json", "generator": "search",
         "gsrsearch": f"filetype:bitmap {term}", "gsrlimit": str(n),
         "gsrnamespace": "6", "prop": "imageinfo",
         "iiprop": "url|extmetadata|size", "iiurlwidth": "480"}
    try:
        r = json.loads(_get(f"{API}?{urllib.parse.urlencode(q)}"))
    except Exception as exc:                                       # noqa: BLE001
        LOG.debug("search failed for %s: %s", term, exc)
        return []
    out = []
    for p in (r.get("query", {}).get("pages") or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata", {})
        title = p.get("title", "")
        low = title.lower()
        if any(b in low for b in REJECT):
            continue
        if not any(r in low for r in RELEVANT):
            continue                      # title says nothing about farm machinery
        if not ii.get("thumburl"):
            continue
        out.append({
            "title": title,
            "thumb": ii["thumburl"],
            "page": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title)}",
            "licence": (md.get("LicenseShortName") or {}).get("value") or "see source",
            "artist": _strip_html((md.get("Artist") or {}).get("value", ""))[:80],
            "width": ii.get("width", 0),
        })
    return out


def _strip_html(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s or "").strip()


def build(force: bool = False) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    skus = Config.skus()
    # One photograph standing in for three different machines makes the catalogue look
    # careless and tells the reader nothing; a drawn icon is the better fallback.
    used_titles = {v["title"] for v in manifest.values()}

    for i, sku in enumerate(skus, 1):
        sid = sku["id"]
        if not force and sid in manifest and (OUT / manifest[sid]["file"]).exists():
            continue
        # Progressively broader: specific machine terms, then the catalogue name, then
        # the category, so every SKU ends with something rather than an empty frame.
        terms = list(TERMS.get(sid, [])) + [
            sku["name"],
            sku["category"].replace("_", " ") + " agricultural machinery"]
        hit = None
        for term in terms:
            for cand in _search(term):
                try:
                    data = _get(cand["thumb"], timeout=40)
                except Exception:                                  # noqa: BLE001
                    continue
                if len(data) < 4000:            # too small to be a real photograph
                    continue
                if cand["title"] in used_titles:
                    continue                    # already standing in for another SKU
                ext = ".jpg" if cand["thumb"].lower().endswith((".jpg", ".jpeg")) else ".png"
                fname = f"{sid.lower()}{ext}"
                (OUT / fname).write_bytes(data)
                hit = {"file": fname, "title": cand["title"], "page": cand["page"],
                       "licence": cand["licence"], "artist": cand["artist"],
                       "term": term, "source": "Wikimedia Commons"}
                break
            if hit:
                break
            time.sleep(0.25)
        if hit:
            manifest[sid] = hit
            used_titles.add(hit["title"])
            LOG.info("[%2d/%d] %-22s %s (%s)", i, len(skus), sid,
                     hit["title"][:44], hit["licence"])
        else:
            LOG.warning("[%2d/%d] %-22s no image found", i, len(skus), sid)
        time.sleep(0.2)

    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    LOG.info("images for %d/%d SKUs -> %s", len(manifest), len(skus), OUT)
    return manifest


if __name__ == "__main__":
    import sys
    build(force="--force" in sys.argv)
