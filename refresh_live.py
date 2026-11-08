"""Fetch, probe and deduplicate public HLS channels. No home server required."""
import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import time
import urllib.request
from collections import defaultdict, Counter
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, quote

UPSTREAMS = {
    "CCSH": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/live.m3u",
    "vbskycn": "https://raw.githubusercontent.com/vbskycn/iptv/refs/heads/master/tv/iptv4.m3u",
}
BASE = "https://raw.githubusercontent.com/Bitithao/tv-subscriptions/main/"
PRIORITY = ["TVB翡翠台", "广东卫视", "深圳卫视", "浙江卫视", "大湾区卫视", "广东珠江", "凤凰中文", "凤凰资讯"]
TIMEOUT = 5
MAX_PER_CHANNEL = 8
CN_RANGES = "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/cncidr.txt"
BLOCKED_HOSTS = ("jiduo.me", "jdshipin.com")
# Exact origin pairs observed in response headers; this is not a global redirect allowance.
REDIRECT_TARGETS = {
    'http://74.91.26.218:82': {'http://142.54.179.26:81'},
    'http://173.208.212.130:8181': {'http://208.110.86.66:82'},
    'http://198.204.228.26:80': {'http://208.110.86.66:82'},
}


def blocked_host(host):
    host = (host or "").lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in BLOCKED_HOSTS)


def candidate_allowed(url):
    p = urlsplit(url)
    return p.scheme in {"http", "https"} and p.path.lower().endswith(".m3u8") and not blocked_host(p.hostname)


def origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}:{p.port or (443 if p.scheme == 'https' else 80)}"


def approved_candidate(item, allowed):
    return candidate_allowed(item['url']) and origin(item['url']) in allowed.get(item['name'], [])
# Public CCSH direct routes. The first was measured at 1920x1080 and played on the TCL TV.
# These are preferences only: each must pass the same live-segment probe on every run.
PREFERRED = {
    "TVB翡翠台": [
        "http://218.13.139.26:9901/tsfile/live/1079_1.m3u8",
        "http://183.239.108.37:1698/hls/92/index.m3u8",
    ],
    "广东卫视": [
        "http://58.56.162.102:4466/newlive/live/hls/25/live.m3u8",
        "http://183.11.239.36:808/hls/80/index.m3u8",
    ],
    "深圳卫视": ["http://61.136.172.236:9901/tsfile/live/0126_1.m3u8"],
}


def public_url(url):
    p = urlsplit(url)
    if blocked_host(p.hostname):
        raise ValueError("Quarantined host")
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Unsupported URL")
    for entry in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(entry[4][0])
        local_proxy_test = os.environ.get("TV_ALLOW_FAKE_IP") == "1" and not os.environ.get("GITHUB_ACTIONS") and address in ipaddress.ip_network("198.18.0.0/15")
        if not address.is_global and not local_proxy_test:
            raise ValueError("Non-public destination")
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def __init__(self, allow_media_redirects=False):
        super().__init__()
        self.allow_media_redirects = allow_media_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req is None:
            raise ValueError("Missing redirect source")
        reviewed = origin(newurl) in REDIRECT_TARGETS.get(origin(req.full_url), set())
        direct_media = self.allow_media_redirects and candidate_allowed(req.full_url) and candidate_allowed(newurl)
        if not (reviewed or direct_media):
            raise ValueError("Unreviewed redirect destination")
        if urlsplit(req.full_url).scheme == 'https' and urlsplit(newurl).scheme != 'https':
            raise ValueError("HTTPS downgrade rejected")
        if not urlsplit(newurl).path.lower().endswith(('.m3u8', '.ts', '.m4s', '.mp4', '.aac')):
            raise ValueError("Non-media redirect rejected")
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def http_error_302(self, req, fp, code, msg, headers):
        # urllib's default redirect handler reads the redirect page body. We do not.
        try:
            count = getattr(req, '_media_redirect_count', 0)
            if count >= self.max_redirections:
                raise ValueError('Media redirect limit exceeded')
            location = headers.get('Location')
            if not location:
                raise ValueError('Redirect without Location')
            new_request = self.redirect_request(req, fp, code, msg, headers, urljoin(req.full_url, location))
            new_request._media_redirect_count = count + 1
        finally:
            fp.close()
        return self.parent.open(new_request, timeout=req.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def fetch(url, limit=1_000_000):
    public_url(url)
    # No explicit HTTP proxy; the desktop may still have an operating-system TUN.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect(allow_media_redirects=candidate_allowed(url)))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache", "Range": f"bytes=0-{limit-1}"})
    with opener.open(request, timeout=TIMEOUT) as response:
        if response.headers.get_content_type() in {"text/html", "application/xhtml+xml", "text/javascript", "application/javascript"}:
            raise ValueError("HTML or script response rejected before reading body")
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
            if wanted(current["name"]) and candidate_allowed(line.split("|", 1)[0]):
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
        initial_origin = origin(url)
        media_origins = {initial_origin} | REDIRECT_TARGETS.get(initial_origin, set())
        if not candidate_allowed(url):
            raise ValueError("Not a direct M3U8 candidate")
        def same_origin_fetch(target, limit):
            if origin(target) not in media_origins:
                raise ValueError("Off-origin media reference rejected")
            data, effective = fetch(target, limit)
            # fetch permits bounded public M3U8 redirects and rejects script/HTML responses.
            if candidate_allowed(target) and data.lstrip(b'\xef\xbb\xbf').startswith(b'#EXTM3U'):
                media_origins.add(origin(effective))
            if origin(effective) not in media_origins:
                raise ValueError("Unreviewed effective media origin")
            return data, effective
        for _ in range(4):
            body, effective = same_origin_fetch(url, 512_000)
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
            if any(origin(urljoin(effective, ref)) not in media_origins for ref in refs):
                raise ValueError("Off-origin media reference rejected")
            segment, _ = same_origin_fetch(urljoin(effective, refs[-1]), 262_144)
            if len(segment) < 8192 or not media_signature(segment):
                raise ValueError("No recognized video media")
            target = re.search(r"#EXT-X-TARGETDURATION:(\d+)", text)
            time.sleep(min(12, max(3, int(target.group(1)) + 1 if target else 8)))
            advanced, _ = same_origin_fetch(effective, 512_000)
            advanced_text = advanced.decode("utf-8-sig", "replace")
            latest = [x.strip() for x in advanced_text.splitlines() if x.strip() and not x.startswith("#")]
            if not advanced_text.startswith("#EXTM3U") or not latest or latest[-1] == refs[-1]:
                raise ValueError("Live playlist did not advance")
            if any(origin(urljoin(effective, ref)) not in media_origins for ref in latest):
                raise ValueError("Off-origin media reference rejected")
            next_segment, _ = same_origin_fetch(urljoin(effective, latest[-1]), 262_144)
            if len(next_segment) < 8192 or not media_signature(next_segment):
                raise ValueError("Next live segment failed")
            return dict(item, checked_at=time.time(), probe_ms=round((time.monotonic() - start) * 1000)), None
        raise ValueError("Playlist nesting limit")
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ValueError) else str(getattr(exc, 'code', ''))
        return None, type(exc).__name__ + (': ' + detail if detail else '')


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


def channel_logo(item):
    logo = item.get("logo", "")
    prefix = "https://ghfast.top/https://raw.githubusercontent.com/CCSH/IPTV/"
    if logo.startswith(prefix):
        logo = logo.removeprefix("https://ghfast.top/")
    if not logo and item["name"] in PREFERRED:
        logo = "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/logo/" + item["name"] + ".png"
    return quote(logo, safe="/:?=&%+#").replace('"', "").replace("\n", "")


def playlist(items):
    lines = ["#EXTM3U"]
    for item in sorted(items, key=lambda x: rank(x["name"])):
        name = item["name"]
        display_name = item.get("display_name", name).replace("\n", "")
        logo = channel_logo(item)
        lines.extend([f'#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" group-title="{group_name(name)}",{display_name}', item["url"]])
    return "\n".join(lines) + "\n"


def refresh():
    allowed = json.loads(Path('allowed-origins.json').read_text(encoding='utf-8'))
    previous = {}
    if Path("live-state.json").exists():
        previous = json.loads(Path("live-state.json").read_text(encoding="utf-8"))
    old = previous.get("selected", [])
    candidates = defaultdict(list)
    # Retest yesterday's winners first; keep ordering stable when still healthy.
    for item in old:
        if approved_candidate(item, allowed):
            candidates[item["name"]].append(item)
    for name, urls in PREFERRED.items():
        for url in urls:
            item = {'name':name, 'url':url, 'logo':'', 'source':'Selected direct candidate'}
            if approved_candidate(item, allowed):
                candidates[name].append(item)
    source_errors = []
    cn_starts, cn_ends = [], []
    try:
        raw_cn, _ = fetch(CN_RANGES, 2_000_000)
        matches = re.findall(rb"\d+\.\d+\.\d+\.\d+/\d+", raw_cn)
        nets = sorted(ipaddress.collapse_addresses(ipaddress.ip_network(x.decode()) for x in matches))
        cn_starts = [int(n.network_address) for n in nets]
        cn_ends = [int(n.broadcast_address) for n in nets]
    except Exception as exc:
        source_errors.append({"source": "CN routing preference", "error": type(exc).__name__})
    def domestic(url):
        try:
            address = int(ipaddress.IPv4Address(urlsplit(url).hostname))
            index = bisect_right(cn_starts, address) - 1
            return index >= 0 and address <= cn_ends[index]
        except ValueError:
            return False
    fetched, ignored = 0, 0
    for label, url in UPSTREAMS.items():
        try:
            raw, _ = fetch(url, 8_000_000)
            if not raw.lstrip(b"\xef\xbb\xbf").startswith(b"#EXTM3U"):
                raise ValueError("Invalid source")
            for item in parse_m3u(raw.decode("utf-8-sig"), label):
                if not approved_candidate(item, allowed):
                    ignored += 1
                    continue
                candidates[item["name"]].append(item)
                fetched += 1
        except Exception as exc:
            source_errors.append({"source": label, "error": type(exc).__name__})
    def check_channel(pair):
        name, items = pair
        preferred = PREFERRED.get(name, [])
        items = sorted(items, key=lambda item: (preferred.index(item["url"]) if item["url"] in preferred else len(preferred), not domestic(item["url"])))
        seen, good, failed = set(), [], 0
        reasons = Counter()
        for item in items:
            if not approved_candidate(item, allowed):
                continue
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
                reasons[error] += 1
        print(f"{name}: {len(good)} live routes; {failed} failed probes", flush=True)
        return name, good, failed, dict(reasons)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        checked = list(pool.map(check_channel, sorted(candidates.items(), key=lambda x: rank(x[0]))))
    primary, backup = [], []
    for name, good, _, _ in checked:
        if good:
            primary.append(good[0])
        if len(good) > 1:
            backup.append(good[1])
    names = {x["name"] for x in primary}
    # A cloud outage/geo restriction must not overwrite the household's last usable list.
    healthy = len(primary) >= 10 and "TVB翡翠台" in names and bool({"广东卫视", "深圳卫视", "浙江卫视"} & names)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "location": "GitHub hosted runner" if os.environ.get('GITHUB_ACTIONS') else "household PC with its existing network routing",
              "published": healthy, "candidates": fetched, "unapproved_candidates_not_requested": ignored, "channels_passed": len(primary), "backup_channels": len(backup),
              "failed_probes": sum(x[2] for x in checked), "source_errors": source_errors,
              "missing_channels": [name for name, good, _, _ in checked if not good],
              "failure_reasons": {name: reasons for name, _, _, reasons in checked if reasons},
              "note": "Only selected per-channel entry origins are probed. Public M3U8 redirects are bounded and checked without reading redirect-page bodies; blocked hosts and private addresses are rejected. Media must remain on the selected or resolved playlist origin. Two advancing segments are checked. This is not a safety certification or guarantee of home playback."}
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
