#!/usr/bin/env python3
"""
Auto Media Engine for dulichcheckin.com
- Spotify Podcast RSS (spotify.xml) - Voice: Aoede (Style A: Travel Story)
- Castbox Podcast RSS (castbox.xml) - Voice: Aoede (Style B: Practical Guide)
- Dailymotion HD Video (MP4 Ken Burns + Voice Aoede - Style C: Visual Highlights)
"""

import base64
import datetime
import email.utils
import html
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "dulichcheckin/media")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GEMINI_KEYS = [k.strip() for k in os.environ.get("GEMINI_KEYS", "").split(",") if k.strip()]
DM_API_KEY = os.environ.get("DM_API_KEY", "").strip()
DM_API_SECRET = os.environ.get("DM_API_SECRET", "").strip()

PAGES_BASE = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}"
RELEASE_TAG = "audio-files"
VOICE_NAME = "Aoede"  # Locked brand voice for Du Lich Checkin

ROOT_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT_DIR / "_tmp_media"
WORK_DIR.mkdir(exist_ok=True)


def to_seo_slug(text: str) -> str:
    s = unicodedata.normalize("NFD", text or "du-lich-viet-nam")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:70] or "du-lich-viet-nam"


def clean_html_text(raw: str) -> str:
    if not raw:
        return ""
    s = html.unescape(raw)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s*[-|–]\s*(?:Du\s*Lịch\s*Checkin|dulichcheckin\.com).*$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def scrape_article(article_url: str, fallback_h1: str = "") -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(article_url, headers=headers, timeout=30)
    resp.raise_for_status()
    page_html = resp.text

    h1_m = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", page_html, re.I)
    title_m = re.search(r"<title[^>]*>([^<]+)</title>", page_html, re.I)
    h1_title = clean_html_text(h1_m.group(1) if h1_m else (title_m.group(1) if title_m else fallback_h1))
    if not h1_title:
        h1_title = fallback_h1 or "Du lịch Việt Nam"

    headings = []
    for hm in re.findall(r"<h[23][^>]*>([\s\S]*?)</h[23]>", page_html, re.I):
        ht = clean_html_text(hm)
        if len(ht) > 3 and ht not in headings:
            headings.append(ht)

    # Extract article images
    imgs = []
    for u in re.findall(r"https?://dulichcheckin\.com/wp-content/uploads/[^\"'\s>)]+\.(?:webp|jpg|jpeg|png)", page_html, re.I):
        if re.search(r"logo|avatar|icon|banner|cropped|\.gif", u, re.I):
            continue
        clean_u = re.sub(r"-\d{2,4}x\d{2,4}(\.(?:webp|jpg|jpeg|png))$", r"\1", u, flags=re.I)
        if clean_u not in imgs:
            imgs.append(clean_u)

    body = re.sub(r"<(script|style|nav|footer|aside)[\s\S]*?</\1>", " ", page_html, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()

    return {
        "url": article_url,
        "h1": h1_title,
        "outline": " | ".join(headings[:15]),
        "content": body[:12000],
        "images": imgs[:12],
    }


def call_gemini_json(prompt: str, key_offset: int = 0) -> dict:
    models = ["gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.6-flash"]
    last_err = ""
    for m_idx, model in enumerate(models):
        for k_try in range(min(3, len(GEMINI_KEYS))):
            key = GEMINI_KEYS[(key_offset + m_idx + k_try) % len(GEMINI_KEYS)]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(key)}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.72, "responseMimeType": "application/json"},
            }
            try:
                r = requests.post(url, json=payload, timeout=90)
                if r.status_code == 200:
                    txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    return json.loads(txt)
                last_err = f"HTTP {r.status_code} ({model}): {r.text[:200]}"
                if r.status_code in (429, 503):
                    time.sleep(2)
            except Exception as e:
                last_err = str(e)
    raise RuntimeError(f"Gemini JSON failed: {last_err}")


def call_gemini_tts_to_mp3(speech_script: str, style_direction: str, out_mp3: Path, key_offset: int = 0) -> float:
    full_prompt = f"{style_direction}\n\n{speech_script}"
    last_err = ""
    for k_try in range(len(GEMINI_KEYS)):
        key = GEMINI_KEYS[(key_offset + k_try) % len(GEMINI_KEYS)]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={urllib.parse.quote(key)}"
        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": VOICE_NAME}
                    }
                },
            },
        }
        try:
            r = requests.post(url, json=payload, timeout=120)
            if r.status_code == 200:
                inline = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                mime = inline.get("mimeType", "audio/L16;codec=pcm;rate=24000")
                sr_m = re.search(r"rate=(\d+)", mime)
                sample_rate = int(sr_m.group(1)) if sr_m else 24000
                pcm_bytes = base64.b64decode(inline["data"])

                raw_pcm = out_mp3.with_suffix(".pcm")
                raw_pcm.write_bytes(pcm_bytes)

                subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-f", "s16le", "-ar", str(sample_rate), "-ac", "1",
                        "-i", str(raw_pcm),
                        "-codec:a", "libmp3lame", "-b:a", "128k",
                        str(out_mp3),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                raw_pcm.unlink(missing_ok=True)
                # Get exact duration
                probe = subprocess.check_output(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        str(out_mp3),
                    ],
                    text=True,
                ).strip()
                return float(probe)
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code in (429, 503):
                time.sleep(3)
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"Gemini TTS failed: {last_err}")


def generate_channel_script(channel: str, article: dict) -> dict:
    base = f"""DỮ LIỆU THỰC TẾ TỪ BÀI VIẾT GỐC TRÊN WEBSITE DULICHCHECKIN.COM:
- URL bài viết gốc: {article['url']}
- Thẻ H1 bài viết gốc: {article['h1']}
- Dàn ý H2/H3: {article['outline']}
- Nội dung thực tế: {article['content']}

QUY TẮC BẮT BUỘC:
1. Bám sát 100% thông tin thật từ bài gốc (địa chỉ, sự tích, kiến trúc, giờ mở cửa, kinh nghiệm). Không bịa đặt.
2. Không sáo rỗng, không dùng câu mở đầu rập khuôn ("Giữa nhịp sống hối hả...", "Nếu bạn đang tìm kiếm...").
3. Chuẩn viết hoa tiếng Việt 100%. Tuyệt đối KHÔNG dùng emoji, icon hay ký tự đặc biệt.
4. Kịch bản đọc ("speech_script") phải là văn nói tự nhiên, mạch lạc, dễ nghe khi đọc thành tiếng (không chứa URL, không chứa ký hiệu gạch đầu dòng hay dấu sao)."""

    if channel == "spotify":
        prompt = f"""{base}

NHIỆM VỤ RIÊNG CHO SHOW PODCAST [SPOTIFY - KỂ CHUYỆN TRẢI NGHIỆM]:
- "title": Tiêu đề tập podcast hấp dẫn, chuẩn mực, có dấu tiếng Việt (45-75 ký tự).
- "speech_script": Kịch bản nói dài khoảng 250-310 từ (tương đương 1 phút 30 giây - 2 phút khi đọc). Mở đầu tự nhiên chào thính giả của Du Lịch Checkin, kể theo mạch hành trình trải nghiệm thực tế và chiều sâu văn hóa/lịch sử nổi bật của địa điểm, kết lại gọn gàng mời người nghe xem thêm hướng dẫn chi tiết trên website Du Lịch Checkin.
- "description": Đoạn mô tả tập podcast dài 120-180 từ (văn bản thuần, chia 2 đoạn rõ ràng, KHÔNG chèn URL vì hệ thống tự gắn dòng link chuẩn ở cuối).

Trả về đúng JSON với 3 khóa: "title", "speech_script", "description"."""
        return call_gemini_json(prompt, key_offset=0)

    if channel == "castbox":
        prompt = f"""{base}

NHIỆM VỤ RIÊNG CHO SHOW PODCAST [CASTBOX - CẨM NANG & LƯU Ý THỰC TẾ]:
- "title": Tiêu đề tập cẩm nang ngắn gọn, khác biệt, có dấu tiếng Việt (45-75 ký tự).
- "speech_script": Kịch bản nói dài khoảng 250-310 từ (1 phút 30 giây - 2 phút). PHONG CÁCH KHÁC HOÀN TOÀN kiểu kể chuyện: đi thẳng vào dạng cẩm nang thực tế cho người lần đầu ghé thăm (vị trí, điểm đáng xem nhất, khung giờ đẹp, cách chuẩn bị/lưu ý quan trọng từ bài gốc). Mở đầu tự nhiên giới thiệu chuyên mục Cẩm nang Du Lịch Checkin.
- "description": Đoạn mô tả tập cẩm nang dài 120-180 từ (tập trung liệt kê những thông tin hữu ích người nghe sẽ nhận được, chia 2 đoạn, KHÔNG chèn URL vì hệ thống tự gắn link ở cuối).

Trả về đúng JSON với 3 khóa: "title", "speech_script", "description"."""
        return call_gemini_json(prompt, key_offset=1)

    if channel == "dailymotion":
        prompt = f"""{base}

NHIỆM VỤ RIÊNG CHO VIDEO [DAILYMOTION - ĐIỂM NHẤN TRỰC QUAN 45-60 GIÂY]:
- "title": Tiêu đề video du lịch thu hút, rõ địa điểm, có dấu tiếng Việt (45-75 ký tự).
- "speech_script": Kịch bản thuyết minh video ngắn gọn, cuốn hút dài khoảng 120-150 từ (đọc khoảng 45-60 giây). Đi thẳng vào 3-4 điểm ấn tượng nhất về kiến trúc, cảnh quan và kinh nghiệm thực tế của địa điểm. Kết ngắn gọn mời xem bài viết đầy đủ tại Du Lịch Checkin.
- "description": Đoạn mô tả video dài 130-190 từ, chia 2 đoạn súc tích, KHÔNG tự chèn URL (hệ thống sẽ tự gắn 1 dòng dẫn kèm link trần ở cuối).
- "tags": Chuỗi 5-8 từ khóa tiếng Việt không dấu hoặc có dấu ngăn cách bởi dấu phẩy (VD: "du lich ha noi, den bach ma, du lich viet nam, du lich checkin").

Trả về đúng JSON với 4 khóa: "title", "speech_script", "description", "tags"."""
        return call_gemini_json(prompt, key_offset=2)

    raise ValueError(f"Unknown channel: {channel}")


def ensure_cover_images():
    """Generate clean 1400x1400 podcast covers if missing (required by Spotify & Castbox)."""
    from PIL import Image, ImageDraw, ImageFont

    covers = [
        ("cover-spotify.jpg", "DU LỊCH CHECKIN", "Podcast Trải Nghiệm Việt Nam", (15, 76, 129), (24, 154, 180)),
        ("cover-castbox.jpg", "DU LỊCH CHECKIN", "Cẩm Nang Du Lịch Thực Tế", (14, 98, 81), (38, 166, 154)),
    ]
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    font_path = next((p for p in font_paths if os.path.exists(p)), None)

    for fname, line1, line2, c1, c2 in covers:
        fpath = ROOT_DIR / fname
        if fpath.exists():
            continue
        img = Image.new("RGB", (1400, 1400), c1)
        draw = ImageDraw.Draw(img)
        for y in range(1400):
            ratio = y / 1400.0
            r = int(c1[0] * (1 - ratio) + c2[0] * ratio)
            g = int(c1[1] * (1 - ratio) + c2[1] * ratio)
            b = int(c1[2] * (1 - ratio) + c2[2] * ratio)
            draw.line([(0, y), (1400, y)], fill=(r, g, b))

        # Decorative border
        draw.rectangle([90, 90, 1310, 1310], outline=(255, 255, 255), width=8)
        f_big = ImageFont.truetype(font_path, 98) if font_path else ImageFont.load_default()
        f_sub = ImageFont.truetype(font_path, 54) if font_path else ImageFont.load_default()
        f_url = ImageFont.truetype(font_path, 42) if font_path else ImageFont.load_default()

        draw.text((700, 560), line1, fill=(255, 255, 255), font=f_big, anchor="mm")
        draw.text((700, 690), line2, fill=(254, 240, 138), font=f_sub, anchor="mm")
        draw.text((700, 1180), "dulichcheckin.com", fill=(226, 232, 240), font=f_url, anchor="mm")
        img.save(fpath, "JPEG", quality=92)


def upload_mp3_to_github_release(mp3_path: Path) -> tuple[str, int]:
    """Upload MP3 to GitHub Release tag 'audio-files' and return (direct_url, byte_size)."""
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    api_base = f"https://api.github.com/repos/{REPO}"

    # 1. Get or create release
    r = requests.get(f"{api_base}/releases/tags/{RELEASE_TAG}", headers=headers, timeout=30)
    if r.status_code == 404:
        r = requests.post(
            f"{api_base}/releases",
            headers=headers,
            json={
                "tag_name": RELEASE_TAG,
                "name": "Du Lich Checkin Audio Library",
                "body": "Permanent MP3 hosting for Spotify & Castbox RSS feeds.",
                "draft": False,
                "prerelease": False,
            },
            timeout=30,
        )
    r.raise_for_status()
    rel = r.json()
    release_id = rel["id"]

    # 2. Delete existing asset with same name if present
    asset_name = mp3_path.name
    for asset in rel.get("assets", []):
        if asset["name"] == asset_name:
            requests.delete(f"{api_base}/releases/assets/{asset['id']}", headers=headers, timeout=30)

    # 3. Upload asset
    upload_url = f"https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets?name={urllib.parse.quote(asset_name)}"
    up_headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Content-Type": "audio/mpeg",
    }
    mp3_bytes = mp3_path.read_bytes()
    up_res = requests.post(upload_url, headers=up_headers, data=mp3_bytes, timeout=120)
    up_res.raise_for_status()
    dl_url = up_res.json()["browser_download_url"]
    return dl_url, len(mp3_bytes)


def format_duration_hms(seconds: float) -> str:
    total = max(1, int(round(seconds)))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def update_podcast_rss(channel: str, ep_title: str, ep_desc: str, article_url: str, mp3_url: str, mp3_size: int, duration_sec: float):
    rss_file = ROOT_DIR / f"{channel}.xml"
    is_spotify = (channel == "spotify")
    show_title = "Du Lịch Checkin - Trải Nghiệm Việt Nam" if is_spotify else "Cẩm Nang Du Lịch Checkin"
    show_desc = (
        "Podcast kể chuyện trải nghiệm thực tế, khám phá điểm đến, văn hóa và ẩm thực khắp ba miền Việt Nam cùng Du Lịch Checkin."
        if is_spotify
        else "Chuyên mục cẩm nang du lịch thực tế, chia sẻ lịch trình, mẹo di chuyển và kinh nghiệm tham quan chi tiết từ dulichcheckin.com."
    )
    cover_url = f"{PAGES_BASE}/cover-{channel}.jpg"
    feed_url = f"{PAGES_BASE}/{channel}.xml"

    existing_items = []
    if rss_file.exists():
        raw_xml = rss_file.read_text(encoding="utf-8")
        existing_items = re.findall(r"<item>[\s\S]*?</item>", raw_xml)

    guid = f"{channel}-{to_seo_slug(article_url)}"
    # Remove old item with same guid if re-running
    existing_items = [it for it in existing_items if f">{guid}<" not in it]

    pub_date = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc))
    desc_paragraphs = [p.strip() for p in ep_desc.split("\n") if p.strip()]
    html_desc = "".join(f"<p>{html.escape(p)}</p>" for p in desc_paragraphs)
    html_desc += f'<p>Xem bài viết hướng dẫn chi tiết tại: <a href="{article_url}">{article_url}</a></p>'

    new_item = f"""    <item>
      <title>{html.escape(ep_title)}</title>
      <link>{html.escape(article_url)}</link>
      <guid isPermaLink="false">{html.escape(guid)}</guid>
      <pubDate>{pub_date}</pubDate>
      <description><![CDATA[{html_desc}]]></description>
      <enclosure url="{html.escape(mp3_url)}" length="{mp3_size}" type="audio/mpeg"/>
      <itunes:title>{html.escape(ep_title)}</itunes:title>
      <itunes:author>Du Lịch Checkin</itunes:author>
      <itunes:summary>{html.escape(ep_desc[:350])} Xem chi tiết tại: {html.escape(article_url)}</itunes:summary>
      <itunes:duration>{format_duration_hms(duration_sec)}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>"""

    all_items_xml = "\n".join([new_item] + existing_items[:199])

    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{html.escape(show_title)}</title>
    <link>https://dulichcheckin.com</link>
    <language>vi</language>
    <copyright>Du Lịch Checkin</copyright>
    <atom:link href="{feed_url}" rel="self" type="application/rss+xml"/>
    <description>{html.escape(show_desc)}</description>
    <itunes:author>Du Lịch Checkin</itunes:author>
    <itunes:summary>{html.escape(show_desc)}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:explicit>false</itunes:explicit>
    <itunes:owner>
      <itunes:name>Du Lịch Checkin</itunes:name>
      <itunes:email>contact@dulichcheckin.com</itunes:email>
    </itunes:owner>
    <itunes:image href="{cover_url}"/>
    <itunes:category text="Leisure">
      <itunes:category text="Travel"/>
    </itunes:category>
{all_items_xml}
  </channel>
</rss>
"""
    rss_file.write_text(rss_xml, encoding="utf-8")
    return feed_url


def build_hd_video_from_images(image_urls: list[str], audio_mp3: Path, duration_sec: float, out_mp4: Path):
    """Build a smooth 1280x720 HD slideshow video with gentle Ken Burns zoom + Aoede voiceover."""
    from PIL import Image, ImageOps

    dl_imgs = []
    for idx, u in enumerate(image_urls[:8]):
        try:
            # Use wsrv.nl to convert webp/any image cleanly to 1280x720 jpg
            proxy = f"https://wsrv.nl/?url={urllib.parse.quote(u, safe='')}&w=1280&h=720&fit=cover&output=jpg&q=90"
            r = requests.get(proxy, timeout=25)
            if r.status_code == 200 and len(r.content) > 8000:
                p = WORK_DIR / f"slide_{idx}.jpg"
                p.write_bytes(r.content)
                # Ensure exact 1280x720 RGB
                im = Image.open(p).convert("RGB")
                im = ImageOps.fit(im, (1280, 720), method=Image.Resampling.LANCZOS)
                im.save(p, "JPEG", quality=92)
                dl_imgs.append(p)
        except Exception:
            continue
        if len(dl_imgs) >= 6:
            break

    if not dl_imgs:
        # Fallback to cover image
        ensure_cover_images()
        p = WORK_DIR / "slide_0.jpg"
        im = Image.open(ROOT_DIR / "cover-spotify.jpg").convert("RGB")
        im = ImageOps.fit(im, (1280, 720), method=Image.Resampling.LANCZOS)
        im.save(p, "JPEG", quality=92)
        dl_imgs.append(p)

    # Render each slide into a smooth Ken Burns clip, then concat with audio
    n = len(dl_imgs)
    seg_dur = max(4.0, (duration_sec + 1.5) / n)
    fps = 25
    frames = int(seg_dur * fps)
    clip_paths = []

    for i, img_p in enumerate(dl_imgs):
        clip_p = WORK_DIR / f"clip_{i}.mp4"
        # Alternate gentle zoom-in and zoom-out
        if i % 2 == 0:
            zp = f"zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1280x720:fps={fps}"
        else:
            zp = f"zoompan=z='if(lte(zoom,1.0),1.12,max(1.0,zoom-0.0008))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1280x720:fps={fps}"

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(img_p),
                "-vf", f"scale=1280:720,{zp},format=yuv420p",
                "-t", f"{seg_dur:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-an", str(clip_p),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        clip_paths.append(clip_p)

    concat_txt = WORK_DIR / "concat.txt"
    concat_txt.write_text("\n".join(f"file '{cp.resolve()}'" for cp in clip_paths), encoding="utf-8")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-i", str(audio_mp3),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            str(out_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def upload_to_dailymotion(mp4_path: Path, title: str, description: str, tags: str, article_url: str) -> str:
    # 1. Get Bearer token via Partner API client_credentials
    tok_res = requests.post(
        "https://partner.api.dailymotion.com/oauth/v1/token",
        data={
            "grant_type": "client_credentials",
            "client_id": DM_API_KEY,
            "client_secret": DM_API_SECRET,
            "scope": "manage_videos",
        },
        timeout=30,
    )
    tok_res.raise_for_status()
    access_token = tok_res.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {access_token}"}

    # Decode Partner User/Channel ID ('oid') directly from JWT payload (required instead of /me/ for client_credentials!)
    jwt_parts = access_token.split(".")
    pad = "=" * ((4 - len(jwt_parts[1]) % 4) % 4)
    claims = json.loads(base64.urlsafe_b64decode(jwt_parts[1] + pad).decode("utf-8"))
    dm_user_id = claims.get("oid") or claims.get("uid") or "me"
    print(f"Dailymotion Partner Channel ID (oid): {dm_user_id}")

    # 2. Request upload URL
    up_info = requests.get("https://api.dailymotion.com/file/upload", headers=auth_headers, timeout=30)
    up_info.raise_for_status()
    upload_url = up_info.json()["upload_url"]

    # 3. Upload MP4 file
    with open(mp4_path, "rb") as f:
        file_res = requests.post(upload_url, files={"file": (mp4_path.name, f, "video/mp4")}, timeout=180)
    file_res.raise_for_status()
    uploaded_file_url = file_res.json()["url"]

    # 4. Create & publish video under /user/{oid}/videos (Partner API client_credentials endpoint)
    full_desc = f"{description.strip()}\n\nXem chi tiết lịch trình và kinh nghiệm tham quan tại: {article_url}"
    pub_payload = {
        "url": uploaded_file_url,
        "title": title[:250],
        "description": full_desc[:3000],
        "tags": tags[:250],
        "channel": "travel",
        "language": "vi",
        "published": "true",
        "is_created_for_kids": "false",
    }
    pub_res = requests.post(
        f"https://api.dailymotion.com/user/{dm_user_id}/videos",
        headers=auth_headers,
        data=pub_payload,
        timeout=60,
    )
    if pub_res.status_code >= 400:
        print(f"Dailymotion /user/{dm_user_id}/videos error {pub_res.status_code}: {pub_res.text}")
        pub_res = requests.post(
            f"https://partner.api.dailymotion.com/rest/user/{dm_user_id}/videos",
            headers=auth_headers,
            data=pub_payload,
            timeout=60,
        )
    if pub_res.status_code >= 400:
        raise RuntimeError(f"Dailymotion publish failed HTTP {pub_res.status_code}: {pub_res.text}")
    vid_id = pub_res.json()["id"]
    return f"https://www.dailymotion.com/video/{vid_id}"


def update_status_json(article_url: str, channel: str, info: dict):
    st_file = ROOT_DIR / "status.json"
    data = {}
    if st_file.exists():
        try:
            data = json.loads(st_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if article_url not in data:
        data[article_url] = {}
    data[article_url][channel] = info
    st_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    channel_arg = os.environ.get("INPUT_CHANNEL", "all").strip().lower()
    article_url = os.environ.get("INPUT_ARTICLE_URL", "").strip()
    h1_override = os.environ.get("INPUT_H1_TITLE", "").strip()

    if not article_url:
        print("ERROR: INPUT_ARTICLE_URL is required")
        sys.exit(1)

    ensure_cover_images()
    print(f"Scraping article: {article_url}")
    article = scrape_article(article_url, h1_override)
    slug = to_seo_slug(article["h1"])
    print(f"Article H1: {article['h1']} | Images found: {len(article['images'])}")

    channels = ["spotify", "castbox", "dailymotion"] if channel_arg == "all" else [channel_arg]
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime("%d/%m/%Y %H:%M")

    for ch in channels:
        print(f"\n=== Processing channel: {ch.upper()} ===")
        script_data = generate_channel_script(ch, article)
        ep_title = script_data["title"].strip()
        speech = script_data["speech_script"].strip()
        desc = script_data["description"].strip()

        mp3_path = WORK_DIR / f"{slug}-{ch}.mp3"
        style_dir = (
            "Hãy đọc đoạn podcast du lịch sau bằng giọng nữ tiếng Việt tự nhiên, ngọt ngào, ấm áp, nhịp độ vừa phải, truyền cảm như biên tập viên du lịch đang trò chuyện:"
            if ch != "dailymotion"
            else "Hãy đọc đoạn thuyết minh video du lịch sau bằng giọng nữ tiếng Việt tự nhiên, ngọt ngào, tươi tắn, nhịp độ gọn gàng cuốn hút:"
        )
        duration = call_gemini_tts_to_mp3(speech, style_dir, mp3_path, key_offset=channels.index(ch))
        print(f"Generated MP3: {mp3_path.name} ({duration:.1f}s)")

        if ch in ("spotify", "castbox"):
            mp3_url, mp3_size = upload_mp3_to_github_release(mp3_path)
            feed_url = update_podcast_rss(ch, ep_title, desc, article_url, mp3_url, mp3_size, duration)
            print(f"Updated {ch}.xml -> {feed_url} | MP3: {mp3_url}")
            update_status_json(article_url, ch, {
                "title": ep_title,
                "mp3_url": mp3_url,
                "feed_url": feed_url,
                "duration_sec": round(duration, 1),
                "published_at": now_str,
            })
        elif ch == "dailymotion":
            mp4_path = WORK_DIR / f"{slug}-dailymotion.mp4"
            build_hd_video_from_images(article["images"], mp3_path, duration, mp4_path)
            print(f"Built HD MP4: {mp4_path.name} ({mp4_path.stat().st_size / 1024 / 1024:.2f} MB)")
            dm_url = upload_to_dailymotion(mp4_path, ep_title, desc, script_data.get("tags", "du lich viet nam, du lich checkin"), article_url)
            print(f"Published Dailymotion Video: {dm_url}")
            update_status_json(article_url, "dailymotion", {
                "title": ep_title,
                "video_url": dm_url,
                "duration_sec": round(duration, 1),
                "published_at": now_str,
            })


if __name__ == "__main__":
    main()
