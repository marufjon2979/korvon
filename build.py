import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests

with open("config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

COUNTRIES = CONFIG["countries"]
WORKERS = 15
TIMEOUT = 15
RETRIES = 2

FLAGS = {
    "uz": "🇺🇿", "ru": "🇷🇺", "tr": "🇹🇷",
    "ir": "🇮🇷", "af": "🇦🇫", "tj": "🇹🇯"
}

def sources(code):
    result = [
        f"https://iptv-org.github.io/iptv/countries/{code}.m3u",
        f"https://duckkota.github.io/iptv/playlists/country/{code}.m3u"
    ]
    if code != "tj":
        result.append(
            f"https://raw.githubusercontent.com/Shahrukhalid/"
            f"nexonic-iptv/main/playlists/countries/{code}.m3u"
        )
    return result

def parse_m3u(content):
    channels = []
    lines = content.replace("\r", "").split("\n")
    info = None

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            info = line
        elif line.startswith("http") and info:
            channels.append({"info": info, "url": line})
            info = None

    return channels

def load_country(code):
    found = []
    seen = set()

    for source in sources(code):
        try:
            response = requests.get(
                source,
                timeout=TIMEOUT,
                headers={"User-Agent": "KORVON-IPTV/1.0"}
            )
            response.raise_for_status()

            for channel in parse_m3u(response.text):
                if channel["url"] not in seen:
                    seen.add(channel["url"])
                    found.append(channel)

            print(f"SOURCE OK: {code} — {source}")
        except Exception as error:
            print(f"SOURCE ERROR: {code} — {error}")

    print(f"CANDIDATES {code}: {len(found)}")
    return found

def fetch(url):
    return requests.get(
        url,
        timeout=TIMEOUT,
        headers={
            "User-Agent": "Mozilla/5.0 KORVON-IPTV",
            "Range": "bytes=0-4095"
        }
    )

def stream_works(url):
    try:
        response = fetch(url)
        if response.status_code not in (200, 206):
            return False

        body = response.content
        content_type = response.headers.get("Content-Type", "").lower()

        if not body:
            return False

        is_hls = (
            ".m3u8" in url.lower()
            or "mpegurl" in content_type
            or body.lstrip().startswith(b"#EXTM3U")
        )

        if is_hls:
            text = body.decode("utf-8", errors="ignore")
            lines = [x.strip() for x in text.splitlines() if x.strip()]

            media_url = None

            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF"):
                    for candidate in lines[i + 1:]:
                        if not candidate.startswith("#"):
                            media_url = urljoin(url, candidate)
                            break
                    if media_url:
                        break

            if not media_url:
                for line in lines:
                    if not line.startswith("#"):
                        media_url = urljoin(url, line)
                        break

            if not media_url:
                return False

            segment = fetch(media_url)
            if segment.status_code not in (200, 206):
                return False

            if segment.content.lstrip().startswith(b"#EXTM3U"):
                nested = segment.content.decode("utf-8", errors="ignore")
                for line in nested.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        segment_url = urljoin(media_url, line)
                        part = fetch(segment_url)
                        return (
                            part.status_code in (200, 206)
                            and len(part.content) > 100
                        )
                return False

            return len(segment.content) > 100

        if "text/html" in content_type:
            return False

        return len(body) > 100

    except Exception:
        return False

def check_channel(channel):
    for attempt in range(RETRIES + 1):
        if stream_works(channel["url"]):
            return channel
        if attempt < RETRIES:
            time.sleep(1)
    return None

def prepare_channel(channel, country):
    info = channel["info"]
    code = country["code"]
    flag = FLAGS.get(code, "📺")
    name = country["name"]

    info = re.sub(
        r'group-title="[^"]*"',
        f'group-title="{flag} {name}"',
        info
    )

    if 'group-title=' not in info:
        info = info.replace(
            "#EXTINF:",
            f'#EXTINF:-1 group-title="{flag} {name}" ',
            1
        )

    if 'tvg-logo=' not in info:
        logo = f"https://flagcdn.com/w320/{code}.png"
        info = info.replace(
            "#EXTINF:",
            f'#EXTINF:-1 tvg-logo="{logo}" ',
            1
        )

    return info

def main():
    all_channels = []
    global_seen = set()

    for country in COUNTRIES:
        code = country["code"]
        print(f"\nCHECKING {country['name']}...")

        candidates = load_country(code)
        working = []

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            tasks = [
                executor.submit(check_channel, channel)
                for channel in candidates
            ]

            for task in as_completed(tasks):
                result = task.result()
                if result:
                    working.append(result)

        print(
            f"WORKING {code}: {len(working)} / {len(candidates)}"
        )

        for channel in working:
            url = channel["url"]
            if url in global_seen:
                continue

            global_seen.add(url)
            info = prepare_channel(channel, country)
            all_channels.append((info, url))

    with open("all.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for info, url in all_channels:
            f.write(info + "\n")
            f.write(url + "\n")

    print("\nBUILD COMPLETE")
    print(f"TOTAL WORKING CHANNELS: {len(all_channels)}")

if __name__ == "__main__":
    main()
