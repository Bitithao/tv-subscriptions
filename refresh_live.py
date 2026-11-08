"""Fetch, probe and deduplicate public HLS channels. No home server required."""
import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

UPSTREAMS = {
    "CCSH": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/live.m3u",
    "vbskycn": "https://raw.githubusercontent.com/vbskycn/iptv/refs/heads/master/tv/iptv4.m3u",
}
BASE = "https://raw.githubusercontent.com/Bitithao/tv-subscriptions/main/"
PRIORITY = ["TVB翡翠台", "广东卫视", "深圳卫视", "浙江卫视", "大湾区卫视", "广东珠江", "凤凰中文", "凤凰资讯"]
TIMEOUT = 5
MAX_PER_CHANNEL = 30


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Unsupported URL")
    for entry in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(entry[4][0])
        local_proxy_test = os.environ.get("TV_ALLOW_FAKE_IP") == "1" and not os.environ.get("GITHUB_ACTIONS") and address in ipaddress.ip_network("198.18.0.0/15")
        if not address.is_global and not local_proxy_test:
            raise ValueError("Non-public destination")
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit=1_000_000):
    public_url(url)
    # No explicit HTTP proxy; the desktop may still have an operating-system TUN.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Range": f"bytes=0-{limit-1}"})
    with opener.open(request, timeout=TIMEOUT) as response:
        return response.read(limit), response.geturl()


def channel_name(raw):
    name = re.sub(r"\s+", "", raw).replace("高清", "").replace("超清", "")
    name = re.sub(r"(?:HD|FHD|4K|8K)$", "", name, flags=re.I)
    name = re.sub(r"CCTV[-－ ]?", "CCTV", name, flags=re.I)
    if name in {"翡翠台", "翡翠", "TVB翡翠"}:
        name = "TVB翡翠台"
    return name


def wanted(name):
    return bool(name in PRIORITY or re.fullmatch(r"CCTV(?:[1-9]|1[0-7])\+?", name) or
                (name.endswith("卫视") and len(name) <= 7) or
                name in {"广州综合", "广州新闻", "广东民生", "广东新闻", "广东体育", "广东经济科教"})


def parse_m3u(text, source):
    current = None
    for line in text.lstrip("\ufeff").splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            name = channel_name(line.rsplit(",", 1)[-1])
            logo = re.search(r'tvg-logo="([^"\r\n]*)"', line)
            current = {"name": name, "logo": logo.group(1) if logo else "", "source": source}
        elif line and not line.startswith("#") and current:
            if wanted(current["name"]) and urlsplit(line).scheme in {"http", "https"}:
                yield dict(current, url=line.split("|", 1)[0])
            current = None


def media_signature(data):
    # TS sync bytes at 188-byte intervals, or ISO BMFF boxes used by fragmented MP4.
    ts = any(all(i + j * 188 < len(data) and data[i + j * 188] == 0x47 for j in range(5))
             for i in range(min(188, len(data))))
    mp4 = len(data) > 4096 and any(tag in data[:128] for tag in (b"ftyp", b"styp", b"moof"))
    return ts or mp4


def probe(item):
    start = time.monotonic()
    try:
        url = item["url"]
        for _ in range(4):
            body, effective = fetch(url, 512_000)
            text = body.decode("utf-8-sig", "replace")
            if not text.startswith("#EXTM3U"):
                raise ValueError("Not an HLS playlist")
            lines = text.splitlines()
            refs = [x.strip() for x in lines if x.strip() and not x.startswith("#")]
            if not refs or "#EXT-X-ENDLIST" in text:
                raise ValueError("Empty or non-live playlist")
            if "#EXTINF:" not in text:
                url = urljoin(effective, refs[0])
                continue
            if re.search(r"#EXT-X-KEY:.*METHOD=(?!NONE)", text):
                raise ValueError("Encrypted media not validated")
            # Test an actual recent video segment, not just an HTTP 200 playlist.
            segment, _ = fetch(urljoin(effective, refs[-1]), 262_144)
            if len(segment) < 8192 or not media_signature(segment):
                raise ValueError("No recognized video media")
            return dict(item, checked_at=time.time(), probe_ms=round((time.monotonic() - start) * 1000)), None
        raise ValueError("Playlist nesting limit")
    except Exception as exc:
        return None, type(exc).__name__


def rank(name):
    if name in PRIORITY:
        return (0, PRIORITY.index(name))
    if name.startswith("CCTV"):
        return (1, int(re.search(r"\d+", name).group()), "+" in name)
    return (2, name)


def group_name(name):
    if name.startswith("CCTV"):
        return "央视"
    if name.startswith(("广东", "广州")) or name in {"深圳卫视", "大湾区卫视"}:
        return "广东地方"
    if name.startswith(("TVB", "凤凰")):
        return "港澳"
    return "地方卫视"


def playlist(items):
    lines = ["#EXTM3U"]
    for item in sorted(items, key=lambda x: rank(x["name"])):
        name = item["name"]
        logo = item.get("logo", "").replace('"', "").replace("\n", "")
        lines.extend([f'#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" group-title="{group_name(name)}",{name}', item["url"]])
    return "\n".join(lines) + "\n"


def refresh():
    previous = {}
    if Path("live-state.json").exists():
        previous = json.loads(Path("live-state.json").read_text(encoding="utf-8"))
    old = previous.get("selected", [])
    candidates = defaultdict(list)
    # Retest yesterday's winners first; keep ordering stable when still healthy.
    for item in old:
        candidates[item["name"]].append(item)
    source_errors = []
    fetched = 0
    for label, url in UPSTREAMS.items():
        try:
            raw, _ = fetch(url, 8_000_000)
            if not raw.lstrip(b"\xef\xbb\xbf").startswith(b"#EXTM3U"):
                raise ValueError("Invalid source")
            for item in parse_m3u(raw.decode("utf-8-sig"), label):
                candidates[item["name"]].append(item)
                fetched += 1
        except Exception as exc:
            source_errors.append({"source": label, "error": type(exc).__name__})
    def check_channel(pair):
        name, items = pair
        seen, good, failed = set(), [], 0
        for item in items:
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            if len(seen) > MAX_PER_CHANNEL:
                break
            result, error = probe(item)
            if result:
                good.append(result)
                if len(good) == 2:
                    break
            else:
                failed += 1
        return name, good, failed
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        checked = list(pool.map(check_channel, sorted(candidates.items(), key=lambda x: rank(x[0]))))
    primary, backup = [], []
    for name, good, _ in checked:
        if good:
            primary.append(good[0])
        if len(good) > 1:
            backup.append(good[1])
    names = {x["name"] for x in primary}
    # A cloud outage/geo restriction must not overwrite the household's last usable list.
    healthy = len(primary) >= 10 and "TVB翡翠台" in names and bool({"广东卫视", "深圳卫视", "浙江卫视"} & names)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "location": "GitHub hosted runner" if os.environ.get('GITHUB_ACTIONS') else "household PC with its existing network routing",
              "published": healthy, "candidates": fetched, "channels_passed": len(primary), "backup_channels": len(backup),
              "failed_probes": sum(x[2] for x in checked), "source_errors": source_errors,
              "missing_channels": [name for name, good, _ in checked if not good],
              "note": "Checks playlist and a video segment. Cloud reachability is not a guarantee of home playback; failed publication keeps the last usable playlist."}
    Path("health.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if healthy:
        Path("live.m3u").write_text(playlist(primary), encoding="utf-8")
        Path("backup.m3u").write_text(playlist(backup), encoding="utf-8")
        state = {"published_at": report["checked_at_utc"], "selected": primary + backup}
        Path("live-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif not Path("live.m3u").exists():
        raise RuntimeError("Initial live validation gate failed; inspect health.json")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return {
        "verified": {"name": "精选直播（去重主线）", "url": BASE + "live.m3u", "from": "https://github.com/Bitithao/tv-subscriptions"},
        "backup": {"name": "直播备用（每台一条）", "url": BASE + "backup.m3u", "from": "https://github.com/Bitithao/tv-subscriptions"},
    }


if __name__ == "__main__":
    refresh()
