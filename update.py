"""Refresh public SeleneTV sources without a home computer or server."""
import json
import time
import urllib.request
from pathlib import Path
from refresh_live import refresh

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SOURCE = "https://raw.githubusercontent.com/hafrey1/LunaTV-config/main/jin18.txt"


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


def main():
    for attempt in range(3):
        try:
            request = urllib.request.Request(SOURCE, headers={"User-Agent": "SeleneTV-public-config"})
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(1_000_001)
            assert len(raw) <= 1_000_000, "Upstream subscription too large"
            upstream = json.loads(decode58(raw.decode().strip()))
            sites = upstream["api_site"]
            assert isinstance(sites, dict) and sites, "No usable upstream sources"
            assert all(isinstance(v, dict) and v.get("api", "").startswith(("http://", "https://")) for v in sites.values())
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
    payload = {
        "api_site": sites,
        "lives": refresh(),
    }
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    encoded = encode58(compact)
    assert decode58(encoded) == compact
    assert len(encoded) < 1_000_000
    Path("selene.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("selene.txt").write_text(encoded + "\n", encoding="utf-8")
    print(f"Validated {len(sites)} VOD sources and {len(payload['lives'])} live lists")


if __name__ == "__main__":
    main()
