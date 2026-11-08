"""Refresh public SeleneTV sources without a home computer or server."""
import json
import time
import urllib.request
from pathlib import Path
from refresh_live import refresh

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"



def decode58(text):
    value = 0
    for char in text.strip():
        value = value * 58 + ALPHABET.index(char)
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def encode58(data):
    number = int.from_bytes(data, "big")
    result = ""
    while number:
        number, remainder = divmod(number, 58)
        result = ALPHABET[remainder] + result
    return result


def load_curated_sites(path="curated-vod.json"):
    # A scheduled upstream import must not undo measured source selection.
    sites = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(sites, dict) or not sites:
        raise ValueError("Empty curated VOD list")
    from urllib.parse import urlsplit
    for site in sites.values():
        if not isinstance(site, dict) or not site.get("name"):
            raise ValueError("Invalid source entry")
        parsed = urlsplit(site.get("api", ""))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Only direct HTTPS catalogue APIs are accepted")
        if any(parsed.hostname == h or parsed.hostname.endswith("." + h) for h in ("jiduo.me", "jdshipin.com")):
            raise ValueError("Quarantined host")
    return sites


def catalogue_health(sites):
    # Small catalogue checks only, once per existing two-day workflow.
    # Cloud failures must not erase sources already tested on the home network.
    import urllib.parse
    from refresh_live import public_url
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    result = {}
    for key, site in sites.items():
        started = time.monotonic()
        try:
            url = site["api"] + "?" + urllib.parse.urlencode({"ac": "detail", "wd": "海贼王"})
            public_url(url)
            request = urllib.request.Request(url, headers={"User-Agent": "SeleneTV-catalogue-check"})
            with opener.open(request, timeout=12) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000 or not raw.lstrip().startswith(b"{"):
                raise ValueError("Not bounded JSON")
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("list"), list) or not data["list"]:
                raise ValueError("No search matches")
            result[key] = {"catalogue_ok": True, "matches": len(data["list"])}
        except Exception as exc:
            result[key] = {"catalogue_ok": False, "error_type": type(exc).__name__, "retained": True}
        result[key]["seconds"] = round(time.monotonic() - started, 2)
    return result


def main():
    sites = load_curated_sites()
    payload = {
        "api_site": sites,
        "lives": refresh(),
    }
    status = catalogue_health(sites)
    health_path = Path("health.json")
    health = json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else {}
    health["vod_catalogues"] = status
    health["vod_note"] = "Catalogue checks only; not a full playback guarantee. Curated sources retained on transient cloud failures."
    health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    encoded = encode58(compact)
    assert decode58(encoded) == compact
    assert len(encoded) < 1_000_000
    Path("selene.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("selene.txt").write_text(encoded + "\n", encoding="utf-8")
    print(f"Validated {len(sites)} VOD sources and {len(payload['lives'])} live lists")


if __name__ == "__main__":
    main()
