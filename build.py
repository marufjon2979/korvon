import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests

with open("config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

COUNTRIES = CONFIG["countries"]
WORKERS = 30
TIMEOUT = (5, 8)
RETRIES = 1

FLAGS = {
    "uz": "🇺🇿", "ru": "🇷🇺", "tr": "🇹🇷",
    "ir": "🇮🇷", "af": "🇦🇫", "tj": "🇹🇯"
}

HEADERS = {"User-Agent": "Mozilla/5.0 KORVON-IPTV/1.0"}

def sources(code):
    urls = [
        f"https://iptv-org.github.io/iptv/countries/{code}.m3u",
        f"https://duckkota.github.io/iptv/playlists/country/{code}.m3u"
    ]
    if code != "tj":
        urls.append(
            f"https://raw.githubusercontent.com/Shahrukhalid/"
            f"nexonic-iptv/main/playlists/countries/{code}.m3u"
        )
    return urls

def parse_m3u(content):
    result = []
    info = None
    for line in content.replace("\r", "").split("\n"):
        line = line.strip()
        if line.startswith("#EXTINF"):
            info = line
        elif line.startswith("http") and info:
            result.append({"info": info, "url": line})
            info = None
    return result

def load_country(code):
    result = []
    seen = set()

    for url in sources(code):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
            r.raise_for_status()
            if "#EXTM3U" not in r.text[:500]:
                continue

            for ch in parse_m3u(r.text):
                if ch["url"] not in seen:
                    seen.add(ch["url"])
                    result.append(ch)

            print(f"SOURCE OK: {code}", flush=True)
        except Exception as e:
            print(f"SOURCE ERROR: {code}: {e}", flush=True)

    print(f"CANDIDATES {code}: {len(result)}", flush=True)
    return result

def read_prefix(url):
    with requests.get(
        url, headers=HEADERS, timeout=TIMEOUT,
        stream=True, allow_redirects=True
    ) as r:
        if r.status_code not in (200, 206):
            return None, "", ""

        content_type = r.headers.get("Content-Type", "").lower()
        data = bytearray()

        for chunk in r.iter_content(chunk_size=1024):
            if chunk:
                data.extend(chunk)
            if len(data) >= 16384:
                break

        return bytes(data), content_type, r.url

def check_url(url, depth=0):
    try:
        data, content_type, final_url = read_prefix(url)
        if not data:
            return False

        is_hls = (
            ".m3u8" in url.lower()
            or "mpegurl" in content_type
            or data.lstrip().startswith(b"#EXTM3U")
        )

        if not is_hls:
            return len(data) > 100 and "text/html" not in content_type

        if depth > 1:
            return False

        text = data.decode("utf-8", errors="ignore")
        lines = [x.strip() for x in text.splitlines() if x.strip()]

        target = None
        for line in lines:
            if not line.startswith("#"):
                target = urljoin(final_url, line)
                break

        if not target:
            return False

        return check_url(target, depth + 1)

    except Exception:
        return False

def check_channel(channel):
    for attempt in range(RETRIES + 1):
        if check_url(channel["url"]):
            return channel
        if attempt < RETRIES:
            time.sleep(1)
    return None

def prepare_info(info, country):
    code = country["code"]
    group = f'{FLAGS.get(code, "")} {country["name"]}'

    info = re.sub(
        r'group-title="[^"]*"',
        f'group-title="{group}"',
        info
    )

    if "group-title=" not in info:
        info = info.replace("#EXTINF:", f'#EXTINF:-1 group-title="{group}" ', 1)

    if "tvg-logo=" not in info:
        logo = f"https://flagcdn.com/w320/{code}.png"
        info = info.replace("#EXTINF:", f'#EXTINF:-1 tvg-logo="{logo}" ', 1)

    return info

def main():
    all_channels = []
    seen = set()

    for country in COUNTRIES:
        code = country["code"]
        print(f"\nCHECKING {country['name']}", flush=True)

        candidates = load_country(code)
        working = []

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [
                pool.submit(check_channel, ch) for ch in candidates
            ]

            for future in as_completed(futures):
                result = future.result()
                if result:
                    working.append(result)

        print(
            f"WORKING {code}: {len(working)} / {len(candidates)}",
            flush=True
        )

        for ch in working:
            if ch["url"] in seen:
                continue
            seen.add(ch["url"])
            all_channels.append(
                (prepare_info(ch["info"], country), ch["url"])
            )

    with open("all.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for info, url in all_channels:
            f.write(info + "\n" + url + "\n")

    print(f"\nBUILD COMPLETE: {len(all_channels)} channels", flush=True)

if __name__ == "__main__":
    main()
