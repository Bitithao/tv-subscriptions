"""Recheck the exact household-tested live routes on the existing two-day job."""

import json
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

from refresh_live import approved_candidate, candidate_allowed, fetch, media_signature, origin, playlist, probe, public_url


CANDIDATES = "tvbox-live-candidates.json"
ORIGINS = "tvbox-live-origins.json"
MAIN = "tvbox-live.m3u"
BACKUP = "tvbox-backup.m3u"


def sample_vod_media(entry):
    """Check one bounded representative HLS segment from a catalogue match."""
    groups = str(entry.get("vod_play_url") or "").replace("$$$", "#").split("#")
    for item in groups:
        link = item.partition("$")[-1] if "$" in item else item
        if not candidate_allowed(link):
            continue
        try:
            for _ in range(3):
                data, effective = fetch(link, 500_000)
                text = data.decode("utf-8-sig")
                if not text.startswith("#EXTM3U") or "#EXT-X-KEY:" in text:
                    return {"ok": False, "reason": "manifest_unverified"}
                refs = [x.strip() for x in text.splitlines() if x.strip() and not x.startswith("#")]
                if not refs:
                    return {"ok": False, "reason": "no_segments"}
                target = urljoin(effective, refs[0])
                if origin(target) != origin(effective):
                    return {"ok": False, "reason": "off_origin_reference"}
                if "#EXTINF:" in text:
                    segment, _ = fetch(target, 262_144)
                    ok = len(segment) >= 8192 and media_signature(segment)
                    return {"ok": ok, "reason": "segment_read" if ok else "unrecognized_media"}
                link = target
            return {"ok": False, "reason": "playlist_depth"}
        except Exception as exc:
            return {"ok": False, "reason": type(exc).__name__}
    return {"ok": False, "reason": "no_direct_hls"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_catalogue(site):
    result = {"catalogue_ok": False}
    try:
        parsed = urlsplit(site["api"])
        if (site["type"] != 1 or parsed.scheme != "https" or parsed.query or parsed.fragment):
            raise ValueError("Expected a direct HTTPS CMS source")
        url = site["api"] + "?" + urlencode({"ac": "videolist", "wd": "海贼王"})
        public_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "TVBox-health-check"})
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=9) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Catalogue response is too large")
        data = json.loads(raw)
        entry = next((x for x in data["list"] if x.get("vod_name") in {"海贼王", "航海王"}), None)
        if entry is None:
            raise ValueError("Expected title is missing")
        result.update(catalogue_ok=True, sample=sample_vod_media(entry))
    except Exception as exc:
        result["reason"] = type(exc).__name__
    return site["key"], result


def refresh_tvbox(root=Path(".")):
    source = root / CANDIDATES
    if not source.is_file():
        return None
    rows = json.loads(source.read_text(encoding="utf-8"))
    allowed = json.loads((root / ORIGINS).read_text(encoding="utf-8"))
    checked = [row for row in rows if approved_candidate(row, allowed)]
    expected = {row["name"] for row in rows}
    successes = defaultdict(list)
    with ThreadPoolExecutor(max_workers=10) as pool:
        for row, (result, _) in zip(checked, pool.map(probe, checked)):
            if result is not None:
                successes[row["name"]].append(result)

    primary, backup = [], []
    for name, candidates in successes.items():
        first = candidates[0]
        primary.append(first)
        host = urlsplit(first["url"]).hostname
        second = next((x for x in candidates[1:] if urlsplit(x["url"]).hostname != host), None)
        if second:
            backup.append(second)

    # A cloud-wide outage or a missing household priority channel must not erase the last good lists.
    jade_ok = "TVB翡翠台" not in expected or "TVB翡翠台" in successes
    publish = bool(primary) and jade_ok and len(primary) * 5 >= len(expected) * 4
    if publish:
        outputs = {MAIN: playlist(primary), BACKUP: playlist(backup)}
        for name, contents in outputs.items():
            (root / (name + ".tmp")).write_text(contents, encoding="utf-8")
        for name in outputs:
            (root / (name + ".tmp")).replace(root / name)
    return {"routes_checked": len(checked), "expected_channels": len(expected),
            "passing_primary": len(primary), "passing_backup": len(backup),
            "unavailable_channels": sorted(expected - successes.keys()),
            "published": publish, "retained_previous": not publish}


def main():
    root = Path(".")
    config = json.loads((root / "tvbox.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=5) as pool:
        vod = dict(pool.map(check_catalogue, config["sites"]))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "vod": vod, "live": refresh_tvbox(root)}
    (root / "tvbox-health.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
