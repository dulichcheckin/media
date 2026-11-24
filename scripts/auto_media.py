#!/usr/bin/env python3
"""
Auto Media Engine for dulichcheckin.com
- Spotify Podcast RSS (spotify.xml) - Voice: Aoede (Style A: Travel Story)
- Castbox Podcast RSS (castbox.xml) - Voice: Aoede (Style B: Practical Guide)
- Dailymotion HD Video (MP4 Ken Burns + Voice Aoede - Style C: Visual Highlights)
"""

import array
import base64
import datetime
import email.utils
import hashlib
import html
import json
import math
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
ARCHIVE_ACCESS_KEY = os.environ.get("ARCHIVE_ACCESS_KEY", "").strip()
ARCHIVE_SECRET_KEY = os.environ.get("ARCHIVE_SECRET_KEY", "").strip()
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE", "").strip()
BLUESKY_PASSWORD = os.environ.get("BLUESKY_PASSWORD", "").strip()

PAGES_BASE = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}"
RELEASE_TAG = "audio-files"
VOICE_NAME = "Aoede"  # Locked brand voice for Du Lich Checkin

ROOT_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT_DIR / "_tmp_media"
WORK_DIR.mkdir(exist_ok=True)


def to_clean_unaccented_title(text: str) -> str:
    """Convert Vietnamese title to clean unaccented letters/numbers only (for Castbox 100% clean SEO URL)."""
    s = unicodedata.normalize("NFD", text or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D")
    s = re.sub(r"[^a-zA-Z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


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

    # Extract ALL article images WITH their alt text / figcaption / filename description
    imgs = []
    image_items = []
    img_tag_pattern = re.compile(
        r"(?:<figure[^>]*>[\s\S]*?<img[^>]+>[\s\S]*?(?:<figcaption[^>]*>([\s\S]*?)</figcaption>)?[\s\S]*?</figure>|<img[^>]+>)",
        re.I,
    )
    for m in img_tag_pattern.finditer(page_html):
        block = m.group(0)
        figcap = clean_html_text(m.group(1) or "")
        url_m = re.search(
            r"https?://dulichcheckin\.com/wp-content/uploads/[^\"'\s>)]+\.(?:webp|jpg|jpeg|png)",
            block,
            re.I,
        )
        if not url_m:
            continue
        u = url_m.group(0)
        if re.search(r"logo|avatar|icon|banner|cropped|\.gif", u, re.I):
            continue
        clean_u = re.sub(r"-\d{2,4}x\d{2,4}(\.(?:webp|jpg|jpeg|png))$", r"\1", u, flags=re.I)
        if clean_u in imgs:
            continue
        alt_m = re.search(r'alt=["\']([^"\']*)["\']', block, re.I)
        alt_txt = clean_html_text(alt_m.group(1) if alt_m else "")
        fname_slug = Path(urllib.parse.urlparse(clean_u).path).stem.replace("-", " ")
        desc_label = alt_txt or figcap or fname_slug
        imgs.append(clean_u)
        image_items.append({"url": clean_u, "desc": f"[Ảnh gốc bài web] {desc_label}", "source": "web"})

    # 2. Multi-query Vietnamese Image Search (Bing Images) + Gemini Multimodal Vision Verification
    loc_short = re.split(r"[:|–—?-]", h1_title)[0].strip()
    if len(loc_short) >= 4:
        vision_ext_items = scrape_bing_vietnamese_images_with_gemini_vision(loc_short, h1_title, headings, imgs)
        for vit in vision_ext_items:
            if vit["url"] not in imgs:
                imgs.append(vit["url"])
                image_items.append(vit)

        # Fallback supplemental Wikipedia VI if Bing returned fewer than 6 images
        if len(vision_ext_items) < 6:
            try:
                vi_url = (
                    "https://vi.wikipedia.org/w/api.php?action=query&generator=search"
                    f"&gsrsearch={urllib.parse.quote(loc_short)}&gsrlimit=5&prop=pageimages&pithumbsize=1280&format=json"
                )
                vr = requests.get(vi_url, headers=headers, timeout=10)
                if vr.status_code == 200:
                    for p in vr.json().get("query", {}).get("pages", {}).values():
                        iu = (p.get("thumbnail") or {}).get("source", "")
                        if re.search(r"\.(?:jpg|jpeg|png)$", iu, re.I) and not re.search(r"map|icon|logo|flag", iu, re.I):
                            if iu not in imgs:
                                imgs.append(iu)
                                image_items.append({"url": iu, "desc": f"Ảnh tư liệu {p.get('title', loc_short)}", "source": "wiki"})
            except Exception:
                pass

    body = re.sub(r"<(script|style|nav|footer|aside)[\s\S]*?</\1>", " ", page_html, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()

    return {
        "url": article_url,
        "h1": h1_title,
        "outline": " | ".join(headings[:18]),
        "content": body[:13500],
        "images": imgs[:36],
        "image_items": image_items[:36],
    }


def scrape_bing_vietnamese_images_with_gemini_vision(loc_short: str, h1_title: str, headings: list[str], existing_urls: list[str]) -> list[dict]:
    """Search Bing Images with 4 Vietnamese queries for the destination, then have Gemini Vision inspect thumbnails to filter & describe each photo."""
    import io
    from PIL import Image, ImageOps

    b_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    }
    queries = [
        loc_short,
        f"kiến trúc không gian {loc_short}",
        f"toàn cảnh cổng {loc_short}",
        f"tham quan du lịch {loc_short}",
    ]
    for h in headings[:5]:
        if len(h) < 42 and any(k in h.lower() for k in ("kiến trúc", "không gian", "giếng", "động", "điện", "chùa", "đền", "cổng", "tượng", "cảnh")):
            queries.append(f"{h} {loc_short}")
            if len(queries) >= 5:
                break

    raw_candidates = []
    seen_u = set(existing_urls)
    bad_patterns = re.compile(r"logo|icon|avatar|banner|shopee|lazada|tiki|tiktok|sendo|map|bando|chart|\.svg|\.gif", re.I)

    for q in queries[:5]:
        try:
            b_url = f"https://www.bing.com/images/search?q={urllib.parse.quote(q)}&form=HDRSC2&first=1"
            r = requests.get(b_url, headers=b_headers, timeout=10)
            if r.status_code != 200:
                continue
            matches = re.findall(r"murl&quot;:&quot;(https?://[^&]+?\.(?:jpg|jpeg|png|webp))&quot;", r.text, flags=re.I)
            added_this_q = 0
            for mu in matches:
                u = html.unescape(mu).strip()
                if u in seen_u or bad_patterns.search(u):
                    continue
                seen_u.add(u)
                raw_candidates.append((u, q))
                added_this_q += 1
                if added_this_q >= 7 or len(raw_candidates) >= 26:
                    break
        except Exception as e:
            print(f"Bing query '{q}' note: {e}")

    # Download candidate images and prepare compact 320x200 thumbnails for Gemini Multimodal Vision
    valid_candidates = []
    for u, q in raw_candidates:
        if len(valid_candidates) >= 18:
            break
        try:
            ir = requests.get(u, headers=b_headers, timeout=7)
            if ir.status_code == 200 and len(ir.content) > 8000:
                im = Image.open(io.BytesIO(ir.content)).convert("RGB")
                w, h = im.size
                if w < 420 or h < 260 or (w / float(h)) > 2.8 or (h / float(w)) > 2.2:
                    continue
                thumb = ImageOps.contain(im, (320, 200), method=Image.Resampling.BILINEAR)
                buf = io.BytesIO()
                thumb.save(buf, format="JPEG", quality=72)
                b64_thumb = base64.b64encode(buf.getvalue()).decode("ascii")
                valid_candidates.append({"url": u, "query": q, "b64": b64_thumb})
        except Exception:
            continue

    if not valid_candidates:
        return []

    print(f"Downloaded {len(valid_candidates)} external candidate photos from Bing Images. Running Gemini Vision inspection...")
    parts = [
        {
            "text": (
                f"Bạn là Đạo diễn Hình ảnh cho video du lịch về chủ đề: '{h1_title}' (Địa điểm cốt lõi: '{loc_short}').\n"
                f"Dưới đây là {len(valid_candidates)} bức ảnh thực tế đánh số từ 0 đến {len(valid_candidates)-1}.\n"
                "Hãy NHÌN TRỰC TIẾP vào từng bức ảnh để kiểm duyệt và mô tả:\n"
                "1. CHỈ GIỮ LẠI những bức ảnh chụp cảnh quan, kiến trúc, cổng đền/chùa, ban thờ, tượng thờ, hang động, giếng cổ, thiên nhiên, lễ hội hoặc không gian thực tế phù hợp với địa điểm này.\n"
                "2. LOẠI BỎ NGAY những ảnh bị lỗi, ảnh có chữ quảng cáo/bảng giá đè to giữa hình, ảnh bản đồ, ảnh chụp màn hình văn bản, ảnh chân dung tự sướng cận mặt, hoặc ảnh nhìn rõ là của địa danh khác không liên quan.\n"
                "3. Với mỗi bức ảnh ĐẠT CHUẨN, hãy viết 'visual_desc' (8-15 từ tiếng Việt có dấu) mô tả thật cụ thể những gì mắt bạn nhìn thấy trong bức ảnh đó (VD: 'Cổng tam quan cổ kính dưới tán cây cổ thụ nhìn ra biển', 'Bậc đá rêu phong dẫn lên khu vực hang động và Giếng Tiên', 'Không gian ban thờ chính điện trang nghiêm rực rỡ sắc vàng').\n\n"
                "Trả về đúng JSON theo cấu trúc: {\"approved\": [{\"index\": số_nguyên, \"visual_desc\": \"Mô tả chi tiết những gì thấy trong ảnh\"}]}"
            )
        }
    ]
    for idx, cand in enumerate(valid_candidates):
        parts.append({"text": f"--- ẢNH ỨNG VIÊN #{idx} ---"})
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": cand["b64"]}})

    models = ["gemini-2.5-flash", "gemini-3.8-flash"]
    for m_idx, model in enumerate(models):
        for k_try in range(min(3, len(GEMINI_KEYS))):
            key = GEMINI_KEYS[(m_idx + k_try) % len(GEMINI_KEYS)]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(key)}"
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.25, "responseMimeType": "application/json"},
            }
            try:
                vr = requests.post(url, json=payload, timeout=90)
                if vr.status_code == 200:
                    txt = vr.json()["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(txt)
                    approved_list = parsed.get("approved") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
                    verified = []
                    for ap in (approved_list or []):
                        if not isinstance(ap, dict):
                            continue
                        c_idx = ap.get("index")
                        v_desc = str(ap.get("visual_desc") or "").strip()
                        if isinstance(c_idx, int) and 0 <= c_idx < len(valid_candidates) and v_desc:
                            verified.append({
                                "url": valid_candidates[c_idx]["url"],
                                "desc": f"[Ảnh thật đã qua AI Vision kiểm duyệt] {v_desc}",
                                "source": "bing_vision",
                            })
                    if verified:
                        print(f"Gemini Vision approved {len(verified)}/{len(valid_candidates)} external photos with visual descriptions!")
                        return verified
            except Exception as e:
                print(f"Gemini Vision note ({model}): {e}")

    # Fallback if vision call hit quota: return top 10 valid candidates
    return [
        {"url": c["url"], "desc": f"[Ảnh thực tế tìm kiếm] Khung cảnh {loc_short}", "source": "bing_fallback"}
        for c in valid_candidates[:10]
    ]


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
                    parsed = json.loads(txt)
                    if isinstance(parsed, list):
                        if len(parsed) > 0 and isinstance(parsed[0], dict) and ("title" in parsed[0] or "speech_script" in parsed[0] or "scenes" in parsed[0]):
                            return parsed[0]
                        return {"scenes": parsed}
                    return parsed
                last_err = f"HTTP {r.status_code} ({model}): {r.text[:200]}"
                if r.status_code in (429, 503):
                    time.sleep(2)
            except Exception as e:
                last_err = str(e)
    raise RuntimeError(f"Gemini JSON failed: {last_err}")


def mix_studio_bgm_into_pcm(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    """Apply warm broadcast vocal boost + mix gentle acoustic pentatonic BGM (1.2s intro, ducked under voice, 1.8s outro)."""
    voice_arr = array.array("h")
    voice_arr.frombytes(pcm_bytes)
    v_len = len(voice_arr)

    intro_len = int(sample_rate * 1.2)
    outro_len = int(sample_rate * 1.8)
    total_len = intro_len + v_len + outro_len
    out_arr = array.array("h", [0]) * total_len

    melody_notes = [293.66, 349.23, 440.00, 523.25, 440.00, 392.00, 349.23, 440.00, 587.33, 523.25, 440.00, 349.23]
    bass_notes = [146.83, 146.83, 174.61, 174.61, 130.81, 130.81, 146.83, 146.83, 174.61, 174.61, 130.81, 146.83]
    note_dur = 1.15
    two_pi = 2.0 * math.pi
    total_sec = total_len / float(sample_rate)

    prev_v = 0.0
    for i in range(total_len):
        t = i / float(sample_rate)
        if intro_len <= i < intro_len + v_len:
            v_raw = float(voice_arr[i - intro_len])
            v_warm = v_raw * 1.15 + prev_v * 0.18
            prev_v = v_raw
            duck = 0.58
        else:
            v_warm = 0.0
            prev_v = 0.0
            duck = 1.0

        step_idx = int(t // note_dur)
        n_idx = step_idx % len(melody_notes)
        n_time = t - (step_idx * note_dur)
        freq = melody_notes[n_idx]
        bfreq = bass_notes[n_idx]

        p_env = min(1.0, n_time / 0.04) * math.exp(-2.0 * n_time)
        pluck = (
            math.sin(two_pi * freq * t) * 0.65
            + math.sin(two_pi * (freq * 2.0) * t) * 0.22
            + math.sin(two_pi * (freq * 3.0) * t) * 0.08
        ) * p_env

        pad_env = min(1.0, n_time / 0.25) * math.exp(-0.45 * n_time)
        pad = (
            math.sin(two_pi * bfreq * t) * 0.50
            + math.sin(two_pi * (bfreq * 1.5) * t) * 0.35
            + math.sin(two_pi * (bfreq * 2.0) * t) * 0.20
        ) * pad_env

        if t < 0.8:
            fade = t / 0.8
        elif (total_sec - t) < 1.5:
            fade = max(0.0, (total_sec - t) / 1.5)
        else:
            fade = 1.0

        bgm = (pluck * 3400.0 + pad * 2200.0) * duck * fade
        val = int(round(v_warm + bgm))
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
        out_arr[i] = val

    return out_arr.tobytes()


def call_gemini_tts_raw_pcm(speech_script: str, style_direction: str, key_offset: int = 0) -> tuple[bytes, int]:
    """Generate raw 16-bit mono PCM audio bytes + sample_rate from Gemini 2.5 Flash TTS (voice Aoede)."""
    full_prompt = f"{style_direction}\n\n{speech_script}"
    tts_models = ["gemini-2.5-flash-preview-tts", "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
    last_err = ""
    for m_name in tts_models:
        for k_try in range(len(GEMINI_KEYS)):
            key = GEMINI_KEYS[(key_offset + k_try) % len(GEMINI_KEYS)]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={urllib.parse.quote(key)}"
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
                r = requests.post(url, json=payload, timeout=135)
                if r.status_code == 200:
                    inline = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                    mime = inline.get("mimeType", "audio/L16;codec=pcm;rate=24000")
                    sr_m = re.search(r"rate=(\d+)", mime)
                    sample_rate = int(sr_m.group(1)) if sr_m else 24000
                    pcm_bytes = base64.b64decode(inline["data"])
                    return pcm_bytes, sample_rate
                last_err = f"HTTP {r.status_code} ({m_name}): {r.text[:200]}"
                if r.status_code in (429, 503):
                    time.sleep(2)
            except Exception as e:
                last_err = str(e)
    raise RuntimeError(f"Gemini TTS failed: {last_err}")


def finalize_pcm_to_mp3_with_bgm(pcm_bytes: bytes, sample_rate: int, out_mp3: Path) -> float:
    mixed_pcm = mix_studio_bgm_into_pcm(pcm_bytes, sample_rate)
    raw_pcm = out_mp3.with_suffix(".pcm")
    raw_pcm.write_bytes(mixed_pcm)
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


def call_gemini_tts_to_mp3(speech_script: str, style_direction: str, out_mp3: Path, key_offset: int = 0) -> float:
    pcm_bytes, sample_rate = call_gemini_tts_raw_pcm(speech_script, style_direction, key_offset=key_offset)
    return finalize_pcm_to_mp3_with_bgm(pcm_bytes, sample_rate, out_mp3)


def call_gemini_tts_two_chapters_to_mp3(ch1_script: str, ch2_script: str, style_direction: str, out_mp3: Path, key_offset: int = 3) -> float:
    """Read a ~4.5-5 minute script in 2 chapters and merge seamlessly over one continuous Studio BGM track."""
    pcm1, sr1 = call_gemini_tts_raw_pcm(ch1_script, style_direction, key_offset=key_offset)
    if not ch2_script.strip():
        return finalize_pcm_to_mp3_with_bgm(pcm1, sr1, out_mp3)
    pcm2, _ = call_gemini_tts_raw_pcm(ch2_script, style_direction, key_offset=key_offset + 1)
    # 0.85s natural breath pause between Chapter 1 and Chapter 2
    pause_samples = int(sr1 * 0.85)
    pause_bytes = b"\x00\x00" * pause_samples
    combined_pcm = pcm1 + pause_bytes + pcm2
    return finalize_pcm_to_mp3_with_bgm(combined_pcm, sr1, out_mp3)


def enforce_spotify_html_link(raw_html: str, article_url: str, short_loc: str) -> str:
    """Clean semantic HTML for Spotify (<p>, <h2>, <h3>, <ul>, <li>, <a>) with 70% Anchor / 30% Bare Link at varied position."""
    s = re.sub(r"```(?:html)?", "", raw_html or "", flags=re.I).strip()
    # Strip any pre-existing links to avoid duplicates
    s = re.sub(r"<a\b[^>]*>([\s\S]*?)</a>", r"\1", s, flags=re.I)
    s = re.sub(re.escape(article_url), "", s, flags=re.I)

    # Split into paragraphs if plain text was returned
    if "<p" not in s.lower():
        paras = [f"<p>{html.escape(p.strip())}</p>" for p in re.split(r"\n\s*\n", s) if p.strip()]
    else:
        paras = [p.strip() + "</p>" for p in re.split(r"</p>", s, flags=re.I) if p.strip()]

    if not paras:
        paras = [f"<p>{html.escape(short_loc)}</p>"]

    use_anchor = (random.random() < 0.70)
    r_pos = random.random()
    if len(paras) >= 3:
        target_idx = 0 if r_pos < 0.45 else (len(paras) // 2 if r_pos < 0.85 else len(paras) - 1)
    else:
        target_idx = 0

    lead_templates = [
        f"Bạn có thể tham khảo chi tiết lịch trình và kinh nghiệm thực tế tại {short_loc}",
        f"Xem đầy đủ thông tin hướng dẫn tham quan {short_loc}",
        f"Chi tiết các lưu ý đi lại và trải nghiệm tại {short_loc} được tổng hợp đầy đủ",
    ]
    lead = random.choice(lead_templates)

    if use_anchor:
        # Try replacing first occurrence of short_loc in target paragraph with anchor tag
        p_html = paras[target_idx]
        pattern = re.compile(re.escape(short_loc), re.I)
        if pattern.search(p_html):
            paras[target_idx] = pattern.sub(f'<a href="{article_url}">{short_loc}</a>', p_html, count=1)
        else:
            anchor_tag = f'<a href="{article_url}">{short_loc}</a>'
            anchor_sentence = " " + lead.replace(short_loc, anchor_tag) + "."
            paras[target_idx] = re.sub(r"</p>\s*$", anchor_sentence + "</p>", p_html, flags=re.I)
    else:
        bare_p = f'<p>{lead}: <a href="{article_url}">{article_url}</a></p>'
        paras.insert(min(len(paras), target_idx + 1), bare_p)

    full_html = "\n".join(paras)
    return full_html[:3950]


def enforce_varied_bare_link_plain(raw_text: str, article_url: str, short_loc: str, max_chars: int = 3800) -> str:
    """Clean plain text with 100% standalone bare link placed naturally at top (after P1), middle, or bottom."""
    s = re.sub(r"<[^>]+>", " ", raw_text or "")
    s = s.replace(article_url, "").strip()
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", s) if p.strip()]
    if not paras:
        paras = [short_loc]

    leads = [
        f"Xem đầy đủ cẩm nang và lưu ý thực tế khi ghé thăm {short_loc} tại: {article_url}",
        f"Chi tiết kinh nghiệm di chuyển, giờ mở cửa và lịch trình khám phá {short_loc} xem tại: {article_url}",
        f"Tham khảo bài viết hướng dẫn chi tiết về {short_loc} tại: {article_url}",
    ]
    link_line = random.choice(leads)

    # Budget characters for body paragraphs so link_line is never truncated
    budget = max_chars - len(link_line) - 10
    kept_paras = []
    cur_len = 0
    for p in paras:
        if cur_len + len(p) + 2 <= budget:
            kept_paras.append(p)
            cur_len += len(p) + 2
        else:
            rem = budget - cur_len
            if rem > 80:
                cut = p[:rem].rsplit(" ", 1)[0].rstrip(".,;:") + "."
                kept_paras.append(cut)
            break

    if not kept_paras:
        kept_paras = [paras[0][:budget]]

    r_pos = random.random()
    if len(kept_paras) >= 3:
        insert_pos = 1 if r_pos < 0.45 else (len(kept_paras) // 2 if r_pos < 0.85 else len(kept_paras))
    elif len(kept_paras) == 2:
        insert_pos = 1 if r_pos < 0.65 else 2
    else:
        insert_pos = 1

    kept_paras.insert(insert_pos, link_line)
    return "\n\n".join(kept_paras)


def generate_channel_script(channel: str, article: dict) -> dict:
    style_angles = [
        "kể theo mạch hành trình trải nghiệm thực tế từ lúc đặt chân đến nơi",
        "đi thẳng vào những giá trị cốt lõi đáng giá nhất và kinh nghiệm thực tế cho người đi lần đầu",
        "khai thác chiều sâu lịch sử, kiến trúc, cảnh quan gắn liền với hướng dẫn tham quan thực tế",
        "chia sẻ dưới góc nhìn một biên tập viên du lịch vừa đi thực tế về và đúc kết lại trọn vẹn thông tin",
    ]
    angle = random.choice(style_angles)

    base = f"""DỮ LIỆU THỰC TẾ TỪ BÀI VIẾT GỐC TRÊN WEBSITE DULICHCHECKIN.COM:
- URL bài viết gốc: {article['url']}
- Thẻ H1 bài viết gốc: {article['h1']}
- Dàn ý H2/H3: {article['outline']}
- Nội dung thực tế: {article['content']}

TIÊU CHUẨN BIÊN TẬP BẮT BUỘC (VIẾT NHƯ BIÊN TẬP VIÊN DU LỊCH CHUYÊN NGHIỆP):
1. Xác định chính xác tên địa điểm / chủ đề cốt lõi có dấu đầy đủ ngữ nghĩa ("short_location_name", 3-6 từ, VD: "Đền Bạch Mã", "Chùa Vĩnh Tràng", "các ngôi chùa nổi tiếng ở Hà Nội").
2. Bám sát 100% thông tin thật từ bài gốc (địa chỉ, sự tích, kiến trúc, giờ mở cửa, giá vé, món ăn, lưu ý). Không bịa đặt.
3. Không sáo rỗng, không lan man, cấm tuyệt đối văn mẫu ("Giữa nhịp sống hối hả...", "Nếu bạn đang tìm kiếm...", "Không chỉ là một điểm đến..."). Biến hóa cấu trúc tự nhiên ({angle}).
4. Chuẩn viết hoa tiếng Việt 100% (chỉ viết hoa đầu câu và danh từ riêng). Tuyệt đối KHÔNG dùng emoji, icon hay ký tự trang trí.
5. Không viết dòng tiêu đề rời ở đầu phần mô tả; vào thẳng câu văn chia sẻ hoàn chỉnh có dấu chấm câu."""

    if channel == "spotify":
        prompt = f"""{base}

NHIỆM VỤ RIÊNG CHO SHOW PODCAST [SPOTIFY - TỐI ĐA HÓA ĐỘ DÀI CHUẨN 4000 KÝ TỰ]:
- "short_location_name": Tên địa điểm/chủ đề cốt lõi có dấu đầy đủ ngữ nghĩa (3-6 từ).
- "title": Tiêu đề tập podcast hấp dẫn, chuẩn mực, có dấu tiếng Việt (45-75 ký tự).
- "speech_script": Kịch bản đọc voice dài khoảng 260-320 từ (1 phút 30 giây - 2 phút). Mở đầu chào thính giả của Du Lịch Checkin, kể theo mạch trải nghiệm thực tế và chiều sâu văn hóa/lịch sử/cảnh quan của địa điểm, kết lại mời xem thêm trên website Du Lịch Checkin. (Văn nói tự nhiên, không chứa URL hay ký tự gạch đầu dòng).
- "description": Bài viết chuyên sâu dài khoảng 500-650 từ (tận dụng tối đa khung 3500-3900 ký tự của Spotify, chia 4-5 đoạn văn bằng thẻ <p>...</p>, có thể dùng <h2>/<h3> hoặc <ul>/<li> sạch). Đi sâu phân tích toàn diện các thông tin giá trị từ bài gốc, góc viết KHÁC HOÀN TOÀN kịch bản voice và khác các nền tảng khác. KHÔNG tự chèn URL vào trong đoạn văn (hệ thống sẽ tự xử lý gắn 70% Anchor Text / 30% Link trần).

Trả về đúng JSON với 4 khóa: "short_location_name", "title", "speech_script", "description"."""
        return call_gemini_json(prompt, key_offset=0)

    if channel == "castbox":
        prompt = f"""{base}

NHIỆM VỤ RIÊNG CHO SHOW PODCAST [CASTBOX - TỐI ĐA HÓA ĐỘ DÀI CHUẨN 4000 KÝ TỰ]:
- "short_location_name": Tên địa điểm/chủ đề cốt lõi có dấu đầy đủ ngữ nghĩa (3-6 từ).
- "title": Tiêu đề tập cẩm nang viết bằng TIẾNG VIỆT KHÔNG DẤU, chỉ dùng chữ cái và số, KHÔNG dùng bất kỳ dấu câu hay ký tự đặc biệt nào (để tối ưu URL sạch 100% trên Castbox, dài 45-75 ký tự, VD: "Cam nang tham quan va kinh nghiem di le Den Bach Ma Ha Noi").
- "speech_script": Kịch bản đọc voice (TIẾNG VIỆT CÓ DẤU CHUẨN) dài khoảng 260-320 từ (1 phút 30 giây - 2 phút). PHONG CÁCH KHÁC HOÀN TOÀN Spotify: đi thẳng vào cẩm nang hướng dẫn thực tế (vị trí, cách di chuyển, giờ mở cửa, giá vé, điểm đáng xem nhất và lưu ý quan trọng từ bài gốc).
- "description": Bài cẩm nang chi tiết (TIẾNG VIỆT CÓ DẤU CHUẨN) dài khoảng 500-650 từ (tận dụng tối đa khung 3500-3800 ký tự của Castbox, văn bản thuần chia 4-5 đoạn rõ ràng bằng \\n\\n, KHÔNG dùng thẻ HTML, KHÔNG tự chèn URL vì hệ thống sẽ tự đặt dòng dẫn + link trần ở vị trí đầu/giữa/cuối phù hợp). Nội dung và cấu trúc KHÁC HOÀN TOÀN bài Spotify.

Trả về đúng JSON với 4 khóa: "short_location_name", "title", "speech_script", "description"."""
        return call_gemini_json(prompt, key_offset=1)

    if channel == "dailymotion":
        img_items = article.get("image_items") or []
        img_catalog = "\n".join(
            f"- Ảnh #{idx}: {it.get('desc') or 'Hình ảnh thực tế địa điểm'}"
            for idx, it in enumerate(img_items)
        )
        prompt = f"""{base}

KHO ẢNH ĐA NGUỒN CỦA ĐỊA ĐIỂM (GỒM TOÀN BỘ ẢNH TRÊN BÀI WEB + ẢNH NGOÀI):
{img_catalog}

NHIỆM VỤ RIÊNG CHO VIDEO [DAILYMOTION - KỊCH BẢN BIÊN TẬP VIÊN TRUYỀN HÌNH MẠCH LẠC 1.5 - 2 PHÚT]:
1. QUY TẮC SỐ 1 (ƯU TIÊN MẠCH BÀI NÓI LOGIC & ĐẦY ĐỦ THÔNG TIN TRƯỚC, KHÔNG BỊ BÓ HẸP BỞI THẺ ALT):
   - Hãy đọc toàn bộ nội dung bài viết gốc để soạn một kịch bản thuyết minh du lịch hoàn chỉnh, sâu sắc, mạch lạc từ đầu đến cuối dài khoảng 260-340 từ (tương đương video dài 1 phút 30 giây - 2 phút).
   - Kịch bản phải đầy đủ thông tin giá trị từ bài gốc (vị trí, nguồn gốc/lịch sử/sự tích, những nét kiến trúc hoặc cảnh quan đặc sắc nhất, thời điểm tham quan, giá vé/giờ mở cửa, kinh nghiệm thực tế và lưu ý quan trọng), kể cả những thông tin không nằm trong thẻ alt ảnh!
   - Viết theo lối văn nói Biên tập viên truyền hình ngọt ngào, điềm đạm: chủ động đặt dấu phẩy (,) và dấu gạch ngang (—) ở các nhịp chuyển ý để giọng đọc nhấn nhá truyền cảm.
2. QUY TẮC SỐ 2 (CHIA KỊCH BẢN THÀNH 10 - 15 PHÂN CẢNH & CHỌN ẢNH MINH HỌA PHÙ HỢP):
   - Chia bài thuyết minh hoàn chỉnh đó thành 10 đến 15 phân cảnh nối tiếp nhau ("scenes").
   - Với mỗi phân cảnh, chọn bức ảnh phù hợp nhất trong kho ảnh trên ("image_index": số nguyên từ 0 đến {max(0, len(img_items)-1)}) và viết 1 dòng tiêu đề phụ đề ngắn gọn có dấu ("caption": 5-10 từ tóm tắt ý chính của cảnh đó để hiển thị lên thanh băng chữ truyền hình ở góc dưới video).
   - Hãy sử dụng đa dạng hết các bức ảnh đẹp trong kho ảnh (cả ảnh bài web lẫn ảnh ngoài).
3. "short_location_name": Tên địa điểm/chủ đề cốt lõi có dấu đầy đủ ngữ nghĩa (3-6 từ).
4. "title": Tiêu đề video du lịch thu hút, rõ địa điểm, có dấu tiếng Việt (45-75 ký tự).
5. "description": Bài viết mô tả video chuyên sâu dài khoảng 420-550 từ (tận dụng tối đa khung 2600-2950 ký tự của Dailymotion, văn bản thuần chia 4 đoạn rõ ràng bằng \\n\\n, KHÔNG dùng thẻ HTML, KHÔNG tự chèn URL).
6. "tags": Chuỗi 5-8 từ khóa tiếng Việt ngăn cách bởi dấu phẩy.

Trả về đúng JSON với 5 khóa:
- "short_location_name": chuỗi
- "title": chuỗi
- "scenes": mảng 10-15 object [{{"image_index": số_nguyên, "caption": "Dòng tiêu đề ngắn 5-10 từ hiện trên màn hình", "narration": "Đoạn lời bình 18-28 từ của biên tập viên"}}]
- "description": chuỗi
- "tags": chuỗi"""
        res = call_gemini_json(prompt, key_offset=2)
        if not isinstance(res, dict):
            res = {}
        scenes = res.get("scenes") or []
        if scenes and isinstance(scenes, list):
            res["speech_script"] = " ".join(
                str(s.get("narration", "")).strip() for s in scenes if isinstance(s, dict) and str(s.get("narration", "")).strip()
            )
        if not res.get("speech_script"):
            res["speech_script"] = f"Cùng Du Lịch Checkin khám phá {article['h1']}."
        if not res.get("title"):
            res["title"] = article["h1"]
        if not res.get("description"):
            res["description"] = article["content"][:2200]
        return res

    if channel == "youtube":
        img_items = article.get("image_items") or []
        img_catalog = "\n".join(
            f"- Ảnh #{idx}: {it.get('desc') or 'Hình ảnh thực tế'}"
            for idx, it in enumerate(img_items)
        )
        prompt = f"""{base}

KHO ẢNH THẬT ĐÃ QUA AI VISION NHÌN & MÔ TẢ CHI TIẾT (GỒM ẢNH GỐC BÀI WEB + ẢNH TÌM KIẾM ĐÃ DUYỆT):
{img_catalog}

NHIỆM VỤ RIÊNG CHO PHÓNG SỰ VIDEO [YOUTUBE HD 4.5 - 5 PHÚT (34 - 38 PHÂN CẢNH ĐỔI ẢNH LIÊN TỤC MỖI 7 GIÂY)]:
1. KỊCH BẢN THUYẾT MINH CHUYÊN SÂU DÀI KHOẢNG 660 - 760 TỪ (~4.5 - 5 PHÚT), CHIA THÀNH 34 ĐẾN 38 PHÂN CẢNH ("scenes"):
   - Để người xem không bị nhàm chán, cứ mỗi 18 - 22 từ lời bình (~7 giây) là PHẢI chuyển sang 1 phân cảnh mới với 1 bức ảnh mới khớp 100% với câu đang nói!
   - Chương 1 (Cảnh 1 đến Cảnh 18): Mở đầu ấn tượng, vị trí địa lý, chiều sâu lịch sử, sự tích / truyền thuyết thần thoại và đi sâu khám phá từng nét kiến trúc, cổng đền, chính điện, hang động, giếng cổ, cảnh quan đặc sắc từ bài gốc.
   - Chương 2 (Cảnh 19 đến Cảnh 36+): Cẩm nang thực tế chi tiết từ bài gốc (thời điểm đi lễ/tham quan, giờ mở cửa, giá vé, hướng dẫn di chuyển, cách sắm lễ vật chay/mặn, lưu ý đọc văn khấn, trang phục ứng xử, ẩm thực đặc sản và kinh nghiệm thực tế).
   - Viết theo lối Biên tập viên truyền hình ngọt ngào, truyền cảm, chủ động dùng dấu phẩy (,) và gạch ngang (—) để ngắt nhịp.
2. QUY TẮC CHỌN NGUỒN ẢNH CHO TỪNG PHÂN CẢNH (KẾT HỢP ẢNH CHỤP THẬT & ẢNH AI VẼ MINH HỌA):
   - Với các cảnh nói về KIẾN TRÚC, CỔNG ĐỀN, BAN THỜ, TƯỢNG THỜ, HANG ĐỘNG, GIẾNG TIÊN, CẢNH QUAN THỰC TẾ của địa điểm:
     + Đặt `"type": "photo"` và chọn `"image_index"` (từ 0 đến {max(0, len(img_items)-1)}) có mô tả trong kho ảnh khớp nhất với câu thuyết minh đó. Hãy sử dụng hết toàn bộ kho ảnh thật ở trên!
   - Với khoảng 10 đến 14 phân cảnh nói về các nội dung KHÔNG CẦN ẢNH CHỤP ĐỊA ĐIỂM THẬT (như: **Sự tích / Truyền thuyết / Lịch sử cổ xưa**, **Hướng dẫn sắm lễ / Mâm lễ vật dâng hương**, **Nghi thức đọc văn khấn**, **Chuẩn bị trang phục lịch sự / Hành trang khởi hành / Phương tiện di chuyển / Ẩm thực đặc sản**):
     + Đặt `"type": "ai_gen"`, `"image_index": -1`, và viết `"ai_prompt_en"` (câu lệnh tiếng Anh dài 20-35 từ mô tả khung cảnh điện ảnh chân thực 16:9 chuẩn văn hóa Việt Nam để AI vẽ bức ảnh minh họa tuyệt đẹp cho đúng câu nói đó, tuyệt đối không có chữ trong ảnh).
3. "short_location_name": Tên địa điểm/chủ đề cốt lõi có dấu đầy đủ ngữ nghĩa (3-6 từ).
4. "title": Tiêu đề video YouTube hấp dẫn, chuẩn SEO, có dấu tiếng Việt (50-85 ký tự).
5. "description": Bài cẩm nang mô tả video YouTube chuyên sâu dài khoảng 480-620 từ (chia 4-5 đoạn văn rõ ràng bằng \\n\\n, văn bản thuần KHÔNG dùng thẻ HTML, KHÔNG tự chèn URL).
6. "tags": Chuỗi 8-12 từ khóa tiếng Việt chuẩn SEO YouTube ngăn cách bởi dấu phẩy.

Trả về đúng JSON với 5 khóa:
- "short_location_name": chuỗi
- "title": chuỗi
- "scenes": mảng 34-38 object [{{"type": "photo" hoặc "ai_gen", "image_index": số_nguyên, "ai_prompt_en": "Chuỗi tiếng Anh mô tả ảnh nếu type là ai_gen (hoặc rỗng nếu type là photo)", "caption": "Dòng tiêu đề phụ đề 5-10 từ hiện trên màn hình", "narration": "Đoạn lời bình 18-22 từ (~7 giây) của biên tập viên"}}]
- "description": chuỗi
- "tags": chuỗi"""
        res = call_gemini_json(prompt, key_offset=3)
        if not isinstance(res, dict):
            res = {}
        scenes = [s for s in (res.get("scenes") or []) if isinstance(s, dict) and str(s.get("narration", "")).strip()]
        res["scenes"] = scenes
        if scenes:
            mid = max(1, len(scenes) // 2)
            res["chapter1_speech"] = " ".join(str(s.get("narration", "")).strip() for s in scenes[:mid])
            res["chapter2_speech"] = " ".join(str(s.get("narration", "")).strip() for s in scenes[mid:])
            res["speech_script"] = (res["chapter1_speech"] + " " + res["chapter2_speech"]).strip()
        else:
            res["chapter1_speech"] = f"Cùng khám phá {article['h1']}."
            res["chapter2_speech"] = ""
            res["speech_script"] = res["chapter1_speech"]
        return res

    if channel == "bluesky":
        prompt = f"""{base}

NHIỆM VỤ RIÊNG CHO MẠNG XÃ HỘI VIDEO [BLUESKY - BÀI CHIA SẺ SÚC TÍCH KÈM VIDEO HD]:
- "short_location_name": Tên địa điểm/chủ đề cốt lõi có dấu đầy đủ ngữ nghĩa (3-6 từ).
- "title": Tiêu đề ngắn gọn giới thiệu video điểm đến (45-70 ký tự).
- "description": Đoạn chia sẻ thực tế súc tích, cuốn hút dài khoảng 145-190 ký tự (tuyệt đối không quá 195 ký tự để vừa khung 300 ký tự của Bluesky), bắt buộc có nhắc tự nhiên cụm từ "short_location_name" bên trong câu văn. Không dùng emoji, không sáo rỗng, không tự chèn URL.

Trả về đúng JSON với 3 khóa: "short_location_name", "title", "description"."""
        return call_gemini_json(prompt, key_offset=4)

    if channel == "archive":
        prompt = f"""{base}

NHIỆM VỤ RIÊNG CHO THƯ VIỆN LƯU TRỮ SỐ [INTERNET ARCHIVE (ARCHIVE.ORG) - BÀI VIẾT HTML SEO 500-650 TỪ]:
- "short_location_name": Tên địa điểm/chủ đề cốt lõi có dấu đầy đủ ngữ nghĩa (3-6 từ).
- "title": Tiêu đề tư liệu video & cẩm nang khám phá có dấu tiếng Việt chuẩn mực (50-85 ký tự, khác tiêu đề YouTube và Dailymotion).
- "description": Bài viết chuyên khảo & hướng dẫn du lịch dài khoảng 500-650 từ định dạng HTML sạch (chia 4-5 đoạn bằng thẻ <p>...</p>, có thể dùng <h2>/<h3> và <ul>/<li>). Bám sát 100% thông tin bài gốc nhưng biên tập với góc nhìn tư liệu văn hóa - du lịch hoàn toàn mới, KHÔNG tự chèn URL (hệ thống sẽ tự xử lý 70% Anchor Text / 30% Link trần).
- "tags": Chuỗi 6-10 từ khóa tiếng Việt ngăn cách bởi dấu phẩy.

Trả về đúng JSON với 4 khóa: "short_location_name", "title", "description", "tags"."""
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


def build_episode_cover_1400(image_urls: list[str], short_loc: str, out_jpg: Path) -> str:
    """Create a 1400x1400 square episode cover from the article's real photo and upload to GitHub Release."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://dulichcheckin.com/",
    }
    src_im = None
    for u in image_urls[:5]:
        try:
            r = requests.get(u, headers=headers, timeout=15)
            if r.status_code == 200 and len(r.content) > 6000:
                tmp_p = WORK_DIR / "ep_cov_raw"
                tmp_p.write_bytes(r.content)
                src_im = Image.open(tmp_p).convert("RGB")
                tmp_p.unlink(missing_ok=True)
                break
        except Exception:
            continue

    if src_im is None:
        return ""

    # 1400x1400 square canvas: blurred backdrop + crisp full image centered + subtle brand badge
    bg = ImageOps.fit(src_im, (1400, 1400), method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.58)

    fg = ImageOps.contain(src_im, (1320, 1140), method=Image.Resampling.LANCZOS)
    fw, fh = fg.size
    fx = (1400 - fw) // 2
    fy = (1260 - fh) // 2

    draw = ImageDraw.Draw(bg)
    # Soft shadow & white frame
    draw.rectangle([fx - 6, fy - 6, fx + fw + 6, fy + fh + 6], fill=(255, 255, 255))
    bg.paste(fg, (fx, fy))

    # Bottom title bar
    draw.rectangle([0, 1265, 1400, 1400], fill=(15, 23, 42))
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    font_path = next((p for p in font_paths if os.path.exists(p)), None)
    f_title = ImageFont.truetype(font_path, 44) if font_path else ImageFont.load_default()
    label = f"DU LỊCH CHECKIN  •  {short_loc.upper()}"[:48]
    draw.text((700, 1332), label, fill=(254, 240, 138), font=f_title, anchor="mm")

    bg.save(out_jpg, "JPEG", quality=92)
    cov_url, _ = upload_mp3_to_github_release(out_jpg)
    return cov_url


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
    low_name = asset_name.lower()
    ctype = "video/mp4" if low_name.endswith(".mp4") else ("image/jpeg" if low_name.endswith(".jpg") else "audio/mpeg")
    up_headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Content-Type": ctype,
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


def update_podcast_rss(channel: str, ep_title: str, ep_desc: str, article_url: str, mp3_url: str, mp3_size: int, duration_sec: float, short_loc: str, ep_cover_url: str = ""):
    rss_file = ROOT_DIR / f"{channel}.xml"
    is_spotify = (channel == "spotify")
    show_title = "Du Lịch Checkin - Trải Nghiệm Việt Nam" if is_spotify else "Cam Nang Du Lich Checkin"
    if not is_spotify:
        ep_title = to_clean_unaccented_title(ep_title)
    show_desc = (
        "Podcast kể chuyện trải nghiệm thực tế, khám phá điểm đến, văn hóa và ẩm thực khắp ba miền Việt Nam cùng Du Lịch Checkin."
        if is_spotify
        else "Chuyên mục cẩm nang du lịch thực tế, chia sẻ lịch trình, mẹo di chuyển và kinh nghiệm tham quan chi tiết từ dulichcheckin.com."
    )
    cover_url = f"{PAGES_BASE}/cover-{channel}.jpg"
    item_image_url = ep_cover_url or cover_url
    feed_url = f"{PAGES_BASE}/{channel}.xml"

    existing_items = []
    if rss_file.exists():
        raw_xml = rss_file.read_text(encoding="utf-8")
        existing_items = re.findall(r"<item>[\s\S]*?</item>", raw_xml)

    guid = f"{channel}-{to_seo_slug(article_url)}"
    # Remove old item with same guid if re-running
    existing_items = [it for it in existing_items if f">{guid}<" not in it]

    pub_date = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc))
    if is_spotify:
        # Spotify: 70% Anchor Text / 30% Bare Link inside semantic HTML (<p>, <a href>) at varied top/mid/bottom position
        final_desc_cdata = enforce_spotify_html_link(ep_desc, article_url, short_loc)
        plain_summary = clean_html_text(final_desc_cdata)[:3800]
    else:
        # Castbox: 100% standalone Bare Link at varied top/mid/bottom position in plain text
        final_desc_cdata = enforce_varied_bare_link_plain(ep_desc, article_url, short_loc, max_chars=3800)
        plain_summary = final_desc_cdata[:3800]

    new_item = f"""    <item>
      <title>{html.escape(ep_title)}</title>
      <link>{html.escape(article_url)}</link>
      <guid isPermaLink="false">{html.escape(guid)}</guid>
      <pubDate>{pub_date}</pubDate>
      <description><![CDATA[{final_desc_cdata}]]></description>
      <enclosure url="{html.escape(mp3_url)}" length="{mp3_size}" type="audio/mpeg"/>
      <itunes:image href="{html.escape(item_image_url)}"/>
      <itunes:title>{html.escape(ep_title)}</itunes:title>
      <itunes:author>Du Lịch Checkin</itunes:author>
      <itunes:summary>{html.escape(plain_summary)}</itunes:summary>
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
      <itunes:email>dulichcheckinprofile1@gmail.com</itunes:email>
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


def render_tv_frame_1280x720(raw_im, caption_text: str, short_loc: str, scene_num: int, total_scenes: int, zoom_center: bool = False):
    """Render 100% uncropped image on a blurred matching backdrop with 3D shadow frame & TV lower-third caption bar (no top brand badge)."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

    work_im = raw_im
    if zoom_center:
        # Subtle 12% center focus perspective for second appearance of authentic article photo
        iw, ih = raw_im.size
        if iw > 300 and ih > 200:
            work_im = raw_im.crop((int(iw * 0.06), int(ih * 0.06), int(iw * 0.94), int(ih * 0.94)))

    # 1. Blurred full-screen 1280x720 backdrop from the image itself
    bg = ImageOps.fit(work_im, (1280, 720), method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(26))
    bg = ImageEnhance.Brightness(bg).enhance(0.50)
    canvas = bg.convert("RGBA")

    # 2. 100% UNCROPPED foreground photo (contain inside 1140x565 area above the caption bar)
    fg = ImageOps.contain(work_im, (1140, 565), method=Image.Resampling.LANCZOS)
    fw, fh = fg.size
    fx = (1280 - fw) // 2
    fy = 24 + (575 - fh) // 2

    # 3. 3D Drop Shadow + crisp white border frame
    overlay = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rounded_rectangle([fx + 8, fy + 10, fx + fw + 14, fy + fh + 14], radius=10, fill=(0, 0, 0, 145))
    overlay = overlay.filter(ImageFilter.GaussianBlur(8))
    canvas = Image.alpha_composite(canvas, overlay)

    cdraw = ImageDraw.Draw(canvas)
    cdraw.rounded_rectangle([fx - 4, fy - 4, fx + fw + 4, fy + fh + 4], radius=6, fill=(255, 255, 255, 245))
    canvas.paste(fg.convert("RGBA"), (fx, fy))

    # 4. Sleek TV Lower-Third Caption Bar (No top brand badge!)
    ui_layer = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    udraw = ImageDraw.Draw(ui_layer)

    # Bottom lower-third banner
    udraw.rounded_rectangle([60, 622, 1220, 702], radius=12, fill=(15, 23, 42, 220), outline=(56, 189, 248, 200), width=2)
    udraw.rounded_rectangle([60, 622, 72, 702], radius=6, fill=(250, 204, 21, 255))

    canvas = Image.alpha_composite(canvas, ui_layer)
    fdraw = ImageDraw.Draw(canvas)

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    font_path = next((p for p in font_paths if os.path.exists(p)), None)
    f_loc = ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default()
    f_cap = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()

    loc_header = f"{short_loc.upper()}  •  ({scene_num}/{total_scenes})"[:65]
    fdraw.text((92, 642), loc_header, fill=(125, 211, 252, 255), font=f_loc, anchor="lm")
    clean_cap = (caption_text or short_loc)[:68]
    fdraw.text((92, 676), clean_cap, fill=(255, 255, 255, 255), font=f_cap, anchor="lm")

    return canvas.convert("RGB")


def build_youtube_thumbnail_1280x720(image_urls: list[str], yt_title: str, short_loc: str, out_jpg: Path) -> str:
    """Create a 1280x720 HD YouTube Custom Thumbnail from the article's featured web image and upload to GitHub Release."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://dulichcheckin.com/",
    }
    src_im = None
    for u in image_urls[:5]:
        try:
            r = requests.get(u, headers=headers, timeout=15)
            if r.status_code == 200 and len(r.content) > 6000:
                tmp_p = WORK_DIR / "yt_thumb_raw"
                tmp_p.write_bytes(r.content)
                src_im = Image.open(tmp_p).convert("RGB")
                tmp_p.unlink(missing_ok=True)
                break
        except Exception:
            continue

    if src_im is None:
        return ""

    # Vibrant 1280x720 thumbnail: blurred backdrop + crisp large photo + bold lower title banner
    enhanced = ImageEnhance.Color(ImageEnhance.Contrast(src_im).enhance(1.08)).enhance(1.12)
    bg = ImageOps.fit(enhanced, (1280, 720), method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(22))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    canvas = bg.convert("RGBA")

    fg = ImageOps.contain(enhanced, (1210, 560), method=Image.Resampling.LANCZOS)
    fw, fh = fg.size
    fx = (1280 - fw) // 2
    fy = 16 + (565 - fh) // 2

    cdraw = ImageDraw.Draw(canvas)
    cdraw.rounded_rectangle([fx - 5, fy - 5, fx + fw + 5, fy + fh + 5], radius=8, fill=(255, 255, 255, 250))
    canvas.paste(fg.convert("RGBA"), (fx, fy))

    ui = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    udraw = ImageDraw.Draw(ui)
    udraw.rounded_rectangle([40, 594, 1240, 704], radius=14, fill=(15, 23, 42, 235), outline=(250, 204, 21, 230), width=3)
    udraw.rounded_rectangle([40, 594, 54, 704], radius=7, fill=(250, 204, 21, 255))
    canvas = Image.alpha_composite(canvas, ui)

    fdraw = ImageDraw.Draw(canvas)
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    font_path = next((p for p in font_paths if os.path.exists(p)), None)
    f_sub = ImageFont.truetype(font_path, 22) if font_path else ImageFont.load_default()
    f_main = ImageFont.truetype(font_path, 32) if font_path else ImageFont.load_default()

    fdraw.text((78, 622), f"KHÁM PHÁ & KINH NGHIỆM THỰC TẾ  •  {short_loc.upper()}"[:58], fill=(250, 204, 21, 255), font=f_sub, anchor="lm")
    fdraw.text((78, 668), (yt_title or short_loc)[:56], fill=(255, 255, 255, 255), font=f_main, anchor="lm")

    canvas.convert("RGB").save(out_jpg, "JPEG", quality=92)
    t_url, _ = upload_mp3_to_github_release(out_jpg)
    return t_url


def generate_ai_illustration_raw_image(ai_prompt_en: str, scene_idx: int):
    """Generate a 1280x720 HD 16:9 illustration for legends, offerings, prayers, or travel tips."""
    import io
    from PIL import Image

    clean_prompt = (
        f"Photorealistic cinematic 16:9 Vietnamese cultural & travel photography, {ai_prompt_en.strip()}, "
        "warm natural lighting, ultra detailed, 8k resolution, no text, no watermark, no letters"
    )

    # 1. Try Gemini 2.0 Flash Image Generation with rotating keys
    if GEMINI_KEYS:
        key = GEMINI_KEYS[scene_idx % len(GEMINI_KEYS)]
        g_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-preview-image-generation:generateContent?key={urllib.parse.quote(key)}"
        g_payload = {
            "contents": [{"parts": [{"text": f"Generate a realistic 16:9 horizontal photo without any text: {clean_prompt}"}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        try:
            gr = requests.post(g_url, json=g_payload, timeout=25)
            if gr.status_code == 200:
                parts = gr.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                for pt in parts:
                    inl = pt.get("inlineData")
                    if inl and inl.get("data"):
                        img_bytes = base64.b64decode(inl["data"])
                        return Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception:
            pass

    # 2. Try Pollinations Flux 1280x720 HD (100% free, no API key required)
    seed = 2026 + scene_idx * 53
    for model_param in ("&model=flux", ""):
        p_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(clean_prompt[:380])}?width=1280&height=720&seed={seed}&nologo=true{model_param}"
        try:
            pr = requests.get(p_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=28)
            if pr.status_code == 200 and len(pr.content) > 10000:
                return Image.open(io.BytesIO(pr.content)).convert("RGB")
        except Exception:
            continue
    return None


def build_hd_video_from_images(image_urls: list[str], audio_mp3: Path, duration_sec: float, out_mp4: Path, scenes: list | None = None, short_loc: str = "Du lịch Việt Nam", max_scenes: int = 16):
    """Build a crisp 1280x720 HD video with 100% uncropped images, AI illustrations, blurred backdrop, TV captions & smooth transitions."""
    from PIL import Image

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://dulichcheckin.com/",
    }

    # Build ordered list of (source_url_or_ai, weight_words, caption_text, zoom_center, ai_prompt_en)
    ordered_specs = []
    if scenes and isinstance(scenes, list) and image_urls:
        used_indices = set()
        for sc in scenes:
            if not isinstance(sc, dict):
                continue
            sc_type = str(sc.get("type") or "photo").strip().lower()
            ai_prompt = str(sc.get("ai_prompt_en") or "").strip()
            words = max(5, len(str(sc.get("narration", "")).split()))
            cap = str(sc.get("caption") or sc.get("narration") or short_loc).strip()

            if sc_type == "ai_gen" and ai_prompt:
                fallback_u = image_urls[len(ordered_specs) % len(image_urls)]
                ordered_specs.append((fallback_u, words, cap, False, ai_prompt))
                continue

            idx = sc.get("image_index", 0)
            is_second_view = False
            if not isinstance(idx, int) or idx < 0 or idx >= len(image_urls):
                unused = [i for i in range(len(image_urls)) if i not in used_indices]
                idx = unused[0] if unused else (len(ordered_specs) % len(image_urls))
            elif idx in used_indices:
                unused = [i for i in range(len(image_urls)) if i not in used_indices]
                if unused:
                    idx = unused[0]
                else:
                    is_second_view = True
            used_indices.add(idx)
            ordered_specs.append((image_urls[idx], words, cap, is_second_view, ""))

    if not ordered_specs:
        for u in image_urls[:15]:
            ordered_specs.append((u, 18, short_loc, False, ""))

    dl_imgs = []
    weights = []
    total_spec_count = min(max_scenes, len(ordered_specs))
    for idx, (u, w, cap, z_center, ai_prompt) in enumerate(ordered_specs[:max_scenes]):
        try:
            raw_im = None
            src_label = u
            if ai_prompt:
                raw_im = generate_ai_illustration_raw_image(ai_prompt, idx)
                if raw_im is not None:
                    src_label = f"[AI_GENERATED] {ai_prompt[:45]}..."
            if raw_im is None:
                r = requests.get(u, headers=headers, timeout=20)
                if r.status_code == 200 and len(r.content) > 5000:
                    raw_p = WORK_DIR / f"raw_{idx}"
                    raw_p.write_bytes(r.content)
                    raw_im = Image.open(raw_p).convert("RGB")
                    raw_p.unlink(missing_ok=True)

            if raw_im is not None:
                tv_frame = render_tv_frame_1280x720(raw_im, cap, short_loc, len(dl_imgs) + 1, total_spec_count, zoom_center=z_center)
                p = WORK_DIR / f"slide_{idx}.jpg"
                tv_frame.save(p, "JPEG", quality=93)
                dl_imgs.append(p)
                weights.append(w)
                print(f"Scene #{len(dl_imgs)} ({w} words | zoom={z_center} | '{cap[:35]}') -> {src_label}")
        except Exception as e:
            print(f"Skip scene #{idx} ({u}): {e}")
            continue

    if not dl_imgs:
        raise RuntimeError("Could not download any article images from dulichcheckin.com")

    # Calculate each slide's exact duration proportional to the spoken words in its scene
    total_w = sum(weights) or len(dl_imgs)
    total_target = duration_sec + 0.4
    clip_paths = []

    for i, (img_p, w) in enumerate(zip(dl_imgs, weights)):
        seg_dur = max(2.5, total_target * (w / total_w))
        clip_p = WORK_DIR / f"clip_{i}.mp4"
        fade_Out_st = max(0.5, seg_dur - 0.35)
        # First slide starts at 100% brightness immediately at 0.00s so auto-thumbnails are never dark!
        if i == 0:
            vf_str = f"scale=1280:720,fade=t=out:st={fade_Out_st:.2f}:d=0.35,format=yuv420p"
        else:
            vf_str = f"scale=1280:720,fade=t=in:st=0:d=0.35,fade=t=out:st={fade_Out_st:.2f}:d=0.35,format=yuv420p"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-t", f"{seg_dur:.2f}",
                "-i", str(img_p),
                "-vf", vf_str,
                "-r", "25",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
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
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-r", "25",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{duration_sec:.2f}",
            "-movflags", "+faststart",
            str(out_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def upload_to_dailymotion(mp4_path: Path, title: str, description: str, tags: str, article_url: str, short_loc: str) -> str:
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

    # Decode Partner Channel Owner ID ('ooi' = Du Lich Checkin channel ID) from JWT payload
    jwt_parts = access_token.split(".")
    pad = "=" * ((4 - len(jwt_parts[1]) % 4) % 4)
    claims = json.loads(base64.urlsafe_b64decode(jwt_parts[1] + pad).decode("utf-8"))
    dm_user_id = claims.get("ooi") or claims.get("oid") or "me"
    print(f"Dailymotion Partner Channel Owner ID (ooi): {dm_user_id}")

    # 2. Request upload URL via Partner API
    up_info = requests.get("https://partner.api.dailymotion.com/rest/file/upload", headers=auth_headers, timeout=30)
    up_info.raise_for_status()
    upload_url = up_info.json()["upload_url"]

    # 3. Upload MP4 file
    with open(mp4_path, "rb") as f:
        file_res = requests.post(upload_url, files={"file": (mp4_path.name, f, "video/mp4")}, timeout=180)
    file_res.raise_for_status()
    file_meta = file_res.json()
    print(f"Dailymotion upload response: {json.dumps(file_meta, ensure_ascii=False)}")
    uploaded_file_url = file_meta["url"]

    # 4. Create & publish video under Partner API /rest/user/{ooi}/videos
    full_desc = enforce_varied_bare_link_plain(description, article_url, short_loc, max_chars=2950)
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
        f"https://partner.api.dailymotion.com/rest/user/{dm_user_id}/videos",
        headers=auth_headers,
        data=pub_payload,
        timeout=60,
    )
    if pub_res.status_code >= 400:
        raise RuntimeError(f"Dailymotion publish failed HTTP {pub_res.status_code}: {pub_res.text}")
    vid_id = pub_res.json()["id"]
    return f"https://www.dailymotion.com/video/{vid_id}"


def upload_to_archive_org(mp4_path: Path, title: str, html_desc: str, tags: str, article_url: str, short_loc: str, slug: str) -> str:
    """Upload HD MP4 video + 500-650 word HTML SEO description (70% Anchor / 30% Bare Link) to Internet Archive (archive.org)."""
    if not ARCHIVE_ACCESS_KEY or not ARCHIVE_SECRET_KEY:
        raise RuntimeError("Missing ARCHIVE_ACCESS_KEY / ARCHIVE_SECRET_KEY")

    final_html = enforce_spotify_html_link(html_desc, article_url, short_loc)
    url_hash = hashlib.md5(article_url.encode("utf-8")).hexdigest()[:6]
    item_id = f"dulichcheckin-{slug[:42]}-{url_hash}"
    clean_file = f"{slug[:48]}.mp4"

    def ia_uri(val: str) -> str:
        return "uri(" + urllib.parse.quote((val or "").strip(), safe="") + ")"

    subject_list = [s.strip() for s in re.split(r"[,;]", tags or "") if s.strip()]
    if "Du Lịch Checkin" not in subject_list:
        subject_list.insert(0, "Du Lịch Checkin")
    if short_loc and short_loc not in subject_list:
        subject_list.insert(1, short_loc)
    subjects_str = "; ".join(subject_list[:10])

    headers = {
        "Authorization": f"LOW {ARCHIVE_ACCESS_KEY}:{ARCHIVE_SECRET_KEY}",
        "x-amz-auto-make-bucket": "1",
        "x-archive-meta01-collection": "opensource_movies",
        "x-archive-meta-mediatype": "movies",
        "x-archive-meta-title": ia_uri(title[:200]),
        "x-archive-meta-description": ia_uri(final_html),
        "x-archive-meta-creator": ia_uri("Du Lịch Checkin (dulichcheckin.com)"),
        "x-archive-meta-subject": ia_uri(subjects_str),
        "x-archive-meta-language": "vie",
        "x-archive-meta-external-identifier": article_url,
        "Content-Type": "video/mp4",
    }

    s3_url = f"https://s3.us.archive.org/{item_id}/{clean_file}"
    with open(mp4_path, "rb") as f:
        r = requests.put(s3_url, headers=headers, data=f, timeout=240)
    if r.status_code >= 400:
        raise RuntimeError(f"Archive.org S3 PUT failed HTTP {r.status_code}: {r.text[:300]}")
    return f"https://archive.org/details/{item_id}"


def upload_to_bluesky(mp4_path: Path, title: str, post_text: str, article_url: str, short_loc: str, image_urls: list[str]) -> str:
    """Publish native HD video (or HD visual card fallback) to Bluesky with 70% Anchor Text / 30% Bare Link facet."""
    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        raise RuntimeError("Missing BLUESKY_HANDLE / BLUESKY_PASSWORD")

    sess_res = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_PASSWORD},
        timeout=25,
    )
    sess_res.raise_for_status()
    sess = sess_res.json()
    jwt = sess["accessJwt"]
    did = sess["did"]
    handle = sess.get("handle") or BLUESKY_HANDLE
    pds = (sess.get("didDoc", {}).get("service") or [{}])[0].get("serviceEndpoint", "https://bsky.social").rstrip("/")
    pds_host = pds.replace("https://", "").replace("http://", "").strip("/")
    auth_hdr = {"Authorization": f"Bearer {jwt}"}

    # 1. Build text + UTF-8 byte-accurate link facet (70% Anchor Text / 30% Bare Link)
    clean_body = re.sub(r"\s+", " ", (post_text or "")).replace(article_url, "").strip()
    use_anchor = (random.random() < 0.70)

    if use_anchor and short_loc:
        if len(clean_body) > 210:
            clean_body = clean_body[:208].rsplit(" ", 1)[0].rstrip(".,;:") + "."
        m = re.search(re.escape(short_loc), clean_body, re.I)
        if m:
            full_text = clean_body
            b_start = len(full_text[:m.start()].encode("utf-8"))
            b_end = len(full_text[:m.end()].encode("utf-8"))
        else:
            lead = f" Xem chi tiết kinh nghiệm khám phá {short_loc}."
            full_text = (clean_body.rstrip(".") + "." + lead).strip()
            m2 = re.search(re.escape(short_loc), full_text, re.I)
            b_start = len(full_text[:m2.start()].encode("utf-8"))
            b_end = len(full_text[:m2.end()].encode("utf-8"))
    else:
        max_body = max(60, 275 - len(article_url))
        if len(clean_body) > max_body:
            clean_body = clean_body[:max_body].rsplit(" ", 1)[0].rstrip(".,;:") + "."
        full_text = f"{clean_body}\nXem chi tiết: {article_url}"
        idx_u = full_text.find(article_url)
        b_start = len(full_text[:idx_u].encode("utf-8"))
        b_end = b_start + len(article_url.encode("utf-8"))

    facets = [
        {
            "index": {"byteStart": b_start, "byteEnd": b_end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": article_url}],
        }
    ]

    # 2. Try uploading native MP4 video via Bluesky video service
    embed_obj = None
    try:
        exp_ts = int(time.time()) + 1500
        sa_res = requests.get(
            f"{pds}/xrpc/com.atproto.server.getServiceAuth",
            params={"aud": f"did:web:{pds_host}", "lxm": "com.atproto.repo.uploadBlob", "exp": str(exp_ts)},
            headers=auth_hdr,
            timeout=20,
        )
        if sa_res.status_code == 200:
            sa_token = sa_res.json()["token"]
            vid_bytes = mp4_path.read_bytes()
            up_v = requests.post(
                "https://video.bsky.app/xrpc/app.bsky.video.uploadVideo",
                params={"did": did, "name": f"dulichcheckin_{int(time.time())}.mp4"},
                headers={"Authorization": f"Bearer {sa_token}", "Content-Type": "video/mp4"},
                data=vid_bytes,
                timeout=120,
            )
            if up_v.status_code in (200, 409):
                job = up_v.json().get("jobStatus") or up_v.json()
                job_id = job.get("jobId")
                video_blob = job.get("blob")
                for _ in range(25):
                    if video_blob:
                        break
                    if not job_id or job.get("state") == "JOB_STATE_FAILED":
                        break
                    time.sleep(3)
                    st_r = requests.get(
                        "https://video.bsky.app/xrpc/app.bsky.video.getJobStatus",
                        params={"jobId": job_id},
                        timeout=15,
                    )
                    if st_r.status_code == 200:
                        job = st_r.json().get("jobStatus") or st_r.json()
                        video_blob = job.get("blob")
                if video_blob:
                    embed_obj = {
                        "$type": "app.bsky.embed.video",
                        "video": video_blob,
                        "alt": title[:200],
                        "aspectRatio": {"width": 1280, "height": 720},
                    }
                    print("Bluesky native HD video transcoded & attached!")
    except Exception as ve:
        print(f"Bluesky native video note (using external visual card fallback): {ve}")

    # 3. Fallback to rich website card with HD frame thumbnail if video transcoding wasn't available
    if not embed_obj:
        ext_card = {
            "uri": article_url,
            "title": title[:200],
            "description": clean_body[:250],
        }
        slide_p = WORK_DIR / "slide_0.jpg"
        if slide_p.exists():
            try:
                tb_res = requests.post(
                    f"{pds}/xrpc/com.atproto.repo.uploadBlob",
                    headers={"Authorization": f"Bearer {jwt}", "Content-Type": "image/jpeg"},
                    data=slide_p.read_bytes(),
                    timeout=30,
                )
                if tb_res.status_code == 200:
                    ext_card["thumb"] = tb_res.json()["blob"]
            except Exception:
                pass
        embed_obj = {"$type": "app.bsky.embed.external", "external": ext_card}

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    record = {
        "$type": "app.bsky.feed.post",
        "text": full_text,
        "facets": facets,
        "embed": embed_obj,
        "langs": ["vi"],
        "createdAt": now_iso,
    }
    post_res = requests.post(
        f"{pds}/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
        json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
        timeout=30,
    )
    if post_res.status_code >= 400:
        raise RuntimeError(f"Bluesky createRecord failed HTTP {post_res.status_code}: {post_res.text[:300]}")
    rkey = post_res.json()["uri"].split("/")[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def read_status_json() -> dict:
    st_file = ROOT_DIR / "status.json"
    if st_file.exists():
        try:
            return json.loads(st_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def update_status_json(article_url: str, channel: str, info: dict):
    st_file = ROOT_DIR / "status.json"
    data = read_status_json()
    if article_url not in data:
        data[article_url] = {}
    data[article_url][channel] = info
    st_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_or_build_hd_video(article: dict, slug: str, now_str: str) -> tuple[Path, str, float, str, dict]:
    """Reuse existing HD MP4 from GitHub Release if already built for this article, or render once and cache."""
    article_url = article["url"]
    st = read_status_json().get(article_url) or {}

    # Check if video_cache or dailymotion already has a valid GitHub Release MP4 URL
    cached_url = (st.get("video_cache") or {}).get("mp4_url") or (st.get("dailymotion") or {}).get("mp4_url")
    cached_dur = (st.get("video_cache") or {}).get("duration_sec") or (st.get("dailymotion") or {}).get("duration_sec") or 95.0
    cached_loc = (st.get("video_cache") or {}).get("short_loc") or re.split(r"[:|–—?-]", article["h1"])[0].strip()

    mp4_path = WORK_DIR / f"{slug}-hd-video.mp4"
    if cached_url and mp4_path.exists() and mp4_path.stat().st_size > 100000:
        return mp4_path, cached_url, float(cached_dur), cached_loc, {}

    if cached_url:
        try:
            print(f"Reusing cached HD MP4 from GitHub Release: {cached_url}")
            r = requests.get(cached_url, timeout=60)
            if r.status_code == 200 and len(r.content) > 100000:
                mp4_path.write_bytes(r.content)
                return mp4_path, cached_url, float(cached_dur), cached_loc, {}
        except Exception as e:
            print(f"Cache download note, rebuilding HD MP4: {e}")

    # Render new HD video using master broadcaster script
    master_data = generate_channel_script("dailymotion", article)
    speech = master_data["speech_script"].strip()
    short_loc = (master_data.get("short_location_name") or re.split(r"[:|–—?-]", article["h1"])[0]).strip()
    mp3_path = WORK_DIR / f"{slug}-video-voice.mp3"
    style_dir = (
        "Hãy đọc đoạn podcast du lịch sau bằng giọng nữ tiếng Việt tự nhiên, ngọt ngào, ấm áp, nhịp độ vừa phải, có nhấn nhá truyền cảm và ngắt nghỉ sâu lắng như biên tập viên chương trình du lịch trên truyền hình:"
    )
    duration = call_gemini_tts_to_mp3(speech, style_dir, mp3_path, key_offset=2)
    print(f"Generated Video Voice MP3 (with Studio BGM): {mp3_path.name} ({duration:.1f}s)")
    build_hd_video_from_images(article["images"], mp3_path, duration, mp4_path, master_data.get("scenes"), short_loc)
    print(f"Built HD MP4: {mp4_path.name} ({mp4_path.stat().st_size / 1024 / 1024:.2f} MB)")
    mp4_release_url, _ = upload_mp3_to_github_release(mp4_path)
    print(f"Uploaded HD MP4 to GitHub Release cache: {mp4_release_url}")

    update_status_json(article_url, "video_cache", {
        "mp4_url": mp4_release_url,
        "duration_sec": round(duration, 1),
        "short_loc": short_loc,
        "cached_at": now_str,
    })
    return mp4_path, mp4_release_url, duration, short_loc, master_data


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

    channels = ["spotify", "castbox", "dailymotion", "youtube", "bluesky", "archive"] if channel_arg == "all" else [channel_arg]
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime("%d/%m/%Y %H:%M")

    for ch in channels:
        print(f"\n=== Processing channel: {ch.upper()} ===")

        if ch in ("spotify", "castbox"):
            script_data = generate_channel_script(ch, article)
            ep_title = script_data["title"].strip()
            speech = script_data["speech_script"].strip()
            desc = script_data["description"].strip()
            short_loc = (script_data.get("short_location_name") or re.split(r"[:|–—?-]", article["h1"])[0]).strip()

            mp3_path = WORK_DIR / f"{slug}-{ch}.mp3"
            style_dir = (
                "Hãy đọc đoạn podcast du lịch sau bằng giọng nữ tiếng Việt tự nhiên, ngọt ngào, ấm áp, nhịp độ vừa phải, có nhấn nhá truyền cảm và ngắt nghỉ sâu lắng như biên tập viên chương trình du lịch trên truyền hình:"
            )
            duration = call_gemini_tts_to_mp3(speech, style_dir, mp3_path, key_offset=channels.index(ch))
            print(f"Generated MP3 (with Studio BGM): {mp3_path.name} ({duration:.1f}s)")

            mp3_url, mp3_size = upload_mp3_to_github_release(mp3_path)
            ep_cov_jpg = WORK_DIR / f"{slug}-{ch}-cover.jpg"
            ep_cov_url = build_episode_cover_1400(article["images"], short_loc, ep_cov_jpg)
            feed_url = update_podcast_rss(ch, ep_title, desc, article_url, mp3_url, mp3_size, duration, short_loc, ep_cov_url)
            print(f"Updated {ch}.xml -> {feed_url} | MP3: {mp3_url} | Cover: {ep_cov_url}")
            update_status_json(article_url, ch, {
                "title": ep_title,
                "mp3_url": mp3_url,
                "cover_url": ep_cov_url,
                "feed_url": feed_url,
                "duration_sec": round(duration, 1),
                "published_at": now_str,
            })

        elif ch == "dailymotion":
            mp4_path, mp4_release_url, duration, short_loc, master_data = get_or_build_hd_video(article, slug, now_str)
            script_data = master_data if master_data.get("title") else generate_channel_script("dailymotion", article)
            ep_title = script_data["title"].strip()
            desc = script_data["description"].strip()
            short_loc = (script_data.get("short_location_name") or short_loc).strip()

            dm_url = ""
            dm_note = "ok"
            try:
                dm_url = upload_to_dailymotion(mp4_path, ep_title, desc, script_data.get("tags", "du lich viet nam, du lich checkin"), article_url, short_loc)
                print(f"Published Dailymotion Video: {dm_url}")
            except Exception as dm_err:
                dm_note = str(dm_err)
                print(f"Dailymotion API warning (MP4 still saved!): {dm_note}")
            update_status_json(article_url, "dailymotion", {
                "title": ep_title,
                "mp4_url": mp4_release_url,
                "video_url": dm_url,
                "dm_status": dm_note,
                "duration_sec": round(duration, 1),
                "published_at": now_str,
            })

        elif ch in ("youtube", "preview"):
            is_preview = (ch == "preview")
            yt_data = generate_channel_script("youtube", article)
            yt_title = (yt_data.get("title") or article["h1"]).strip()[:98]
            yt_short_loc = (yt_data.get("short_location_name") or re.split(r"[:|–—?-]", article["h1"])[0]).strip()
            yt_raw_desc = (yt_data.get("description") or article["content"][:2200]).strip()
            yt_full_desc = enforce_varied_bare_link_plain(yt_raw_desc, article_url, yt_short_loc, max_chars=4600)
            yt_tags = (yt_data.get("tags") or f"du lịch checkin, {yt_short_loc}, du lịch việt nam").strip()

            # Build dedicated ~4.5 - 5 minute (2-chapter, 34-38 scenes) HD video
            file_tag = "preview-5min" if is_preview else "youtube-5min"
            yt_mp3_path = WORK_DIR / f"{slug}-{file_tag}.mp3"
            style_dir = (
                "Hãy đọc đoạn podcast du lịch sau bằng giọng nữ tiếng Việt tự nhiên, ngọt ngào, ấm áp, nhịp độ vừa phải, có nhấn nhá truyền cảm và ngắt nghỉ sâu lắng như biên tập viên chương trình du lịch trên truyền hình:"
            )
            ch1_speech = (yt_data.get("chapter1_speech") or yt_data.get("speech_script") or "").strip()
            ch2_speech = (yt_data.get("chapter2_speech") or "").strip()
            yt_duration = call_gemini_tts_two_chapters_to_mp3(ch1_speech, ch2_speech, style_dir, yt_mp3_path, key_offset=3)
            print(f"Generated Dedicated 5-Min Voice MP3 (2 Chapters + Studio BGM): {yt_mp3_path.name} ({yt_duration:.1f}s)")

            yt_mp4_path = WORK_DIR / f"{slug}-{file_tag}.mp4"
            build_hd_video_from_images(
                article["images"],
                yt_mp3_path,
                yt_duration,
                yt_mp4_path,
                yt_data.get("scenes"),
                yt_short_loc,
                max_scenes=40,
            )
            print(f"Built Dedicated 36-Scene HD MP4: {yt_mp4_path.name} ({yt_mp4_path.stat().st_size / 1024 / 1024:.2f} MB)")
            yt_mp4_url, _ = upload_mp3_to_github_release(yt_mp4_path)

            if is_preview:
                print(f"=== PREVIEW VIDEO READY (NOT POSTED TO YOUTUBE) ===\nDirect MP4 URL: {yt_mp4_url}")
                update_status_json(article_url, "preview", {
                    "title": yt_title,
                    "mp4_url": yt_mp4_url,
                    "scene_count": len(yt_data.get("scenes") or []),
                    "duration_sec": round(yt_duration, 1),
                    "published_at": now_str,
                })
            else:
                yt_thumb_jpg = WORK_DIR / f"{slug}-youtube-thumb.jpg"
                yt_thumb_url = build_youtube_thumbnail_1280x720(article["images"], yt_title, yt_short_loc, yt_thumb_jpg)
                print(f"Prepared YouTube 5-Min HD Video Package: '{yt_title}' ({yt_duration:.1f}s) | MP4: {yt_mp4_url} | Thumb: {yt_thumb_url}")

                update_status_json(article_url, "youtube", {
                    "title": yt_title,
                    "description": yt_full_desc,
                    "tags": yt_tags,
                    "mp4_url": yt_mp4_url,
                    "thumb_url": yt_thumb_url,
                    "video_url": "",
                    "duration_sec": round(yt_duration, 1),
                    "ready_for_yt": True,
                    "published_at": now_str,
                })

        elif ch == "bluesky":
            mp4_path, mp4_release_url, duration, short_loc, _ = get_or_build_hd_video(article, slug, now_str)
            bs_data = generate_channel_script("bluesky", article)
            bs_title = (bs_data.get("title") or article["h1"]).strip()
            bs_short_loc = (bs_data.get("short_location_name") or short_loc).strip()
            bs_text = (bs_data.get("description") or f"Khám phá thực tế {bs_short_loc} cùng Du Lịch Checkin.").strip()
            bs_url = ""
            bs_note = "ok"
            try:
                bs_url = upload_to_bluesky(mp4_path, bs_title, bs_text, article_url, bs_short_loc, article["images"])
                print(f"Published Bluesky Video Post: {bs_url}")
            except Exception as b_err:
                bs_note = str(b_err)
                print(f"Bluesky error: {bs_note}")
            update_status_json(article_url, "bluesky", {
                "title": bs_title,
                "post_url": bs_url,
                "mp4_url": mp4_release_url,
                "status": bs_note,
                "duration_sec": round(duration, 1),
                "published_at": now_str,
            })

        elif ch == "archive":
            mp4_path, mp4_release_url, duration, short_loc, _ = get_or_build_hd_video(article, slug, now_str)
            ia_data = generate_channel_script("archive", article)
            ia_title = (ia_data.get("title") or article["h1"]).strip()
            ia_short_loc = (ia_data.get("short_location_name") or short_loc).strip()
            ia_desc = (ia_data.get("description") or f"<p>{article['content'][:2200]}</p>").strip()
            ia_tags = (ia_data.get("tags") or f"Du Lịch Checkin, {ia_short_loc}, Du lịch Việt Nam").strip()
            ia_url = ""
            ia_note = "ok"
            try:
                ia_url = upload_to_archive_org(mp4_path, ia_title, ia_desc, ia_tags, article_url, ia_short_loc, slug)
                print(f"Published Archive.org Video & HTML SEO Article: {ia_url}")
            except Exception as ia_err:
                ia_note = str(ia_err)
                print(f"Archive.org error: {ia_note}")
            update_status_json(article_url, "archive", {
                "title": ia_title,
                "archive_url": ia_url,
                "mp4_url": mp4_release_url,
                "status": ia_note,
                "duration_sec": round(duration, 1),
                "published_at": now_str,
            })


if __name__ == "__main__":
    main()
