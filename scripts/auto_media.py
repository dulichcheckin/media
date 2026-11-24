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
        image_items.append({"url": clean_u, "desc": desc_label})

    # Optional: search 2-3 verified external images from Wikipedia VI / Wikimedia matching the exact location name
    loc_short = re.split(r"[:|–—?-]", h1_title)[0].strip()
    if len(loc_short) >= 4:
        try:
            w_url = (
                "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
                f"&gsrsearch=filetype:bitmap|{urllib.parse.quote(loc_short)}&gsrlimit=6"
                "&prop=imageinfo&iiprop=url|extmetadata&format=json"
            )
            wr = requests.get(w_url, headers=headers, timeout=12)
            if wr.status_code == 200:
                pages = wr.json().get("query", {}).get("pages", {})
                loc_slug_words = [w for w in to_seo_slug(loc_short).split("-") if len(w) > 2]
                ext_added = 0
                for p in pages.values():
                    t_slug = to_seo_slug(p.get("title", ""))
                    if loc_slug_words and not all(w in t_slug for w in loc_slug_words[:2]):
                        continue
                    ii = (p.get("imageinfo") or [{}])[0]
                    iu = ii.get("url", "")
                    if re.search(r"\.(?:jpg|jpeg|png)$", iu, re.I) and not re.search(r"map|icon|logo|flag|stamp", iu, re.I):
                        if iu not in imgs:
                            imgs.append(iu)
                            image_items.append({"url": iu, "desc": f"Ảnh góc nhìn thực tế {loc_short} ({p.get('title', '').replace('File:', '')})"})
                            ext_added += 1
                            if ext_added >= 3:
                                break
        except Exception:
            pass

    body = re.sub(r"<(script|style|nav|footer|aside)[\s\S]*?</\1>", " ", page_html, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()

    return {
        "url": article_url,
        "h1": h1_title,
        "outline": " | ".join(headings[:15]),
        "content": body[:12000],
        "images": imgs[:16],
        "image_items": image_items[:16],
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
            anchor_sentence = f' {lead.replace(short_loc, f\'<a href="{article_url}">{short_loc}</a>\')}.'
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

DANH SÁCH TOÀN BỘ BỘ ẢNH THỰC TẾ SẼ HIỂN THỊ TRONG VIDEO (ĐÃ ĐỌC TỪ THẺ ALT / CHÚ THÍCH ẢNH TRÊN BÀI WEB):
{img_catalog}

NHIỆM VỤ RIÊNG CHO VIDEO [DAILYMOTION - ĐẠO DIỄN KỊCH BẢN KHỚP TỪNG BỨC ẢNH & MÔ TẢ DÀI CHI TIẾT]:
1. "short_location_name": Tên địa điểm/chủ đề cốt lõi có dấu đầy đủ ngữ nghĩa (3-6 từ).
2. "title": Tiêu đề video du lịch thu hút, rõ địa điểm, có dấu tiếng Việt (45-75 ký tự).
3. "scenes": Xem kỹ danh sách ảnh ở trên để lên kịch bản gồm 7 đến 12 phân cảnh đi qua toàn bộ bộ ảnh:
   - Mỗi phân cảnh là 1 object {{"image_index": số_thứ_tự_ảnh, "narration": "câu thuyết minh 12-20 từ khớp chính xác với chi tiết trong bức ảnh đó"}}.
   - Các câu "narration" khi ghép lại phải tạo thành một bài thuyết minh video hoàn chỉnh, mạch lạc, tự nhiên từ đầu đến cuối.
4. "description": Bài viết mô tả video chuyên sâu dài khoảng 400-520 từ (tận dụng tối đa khung 2500-2900 ký tự của Dailymotion, văn bản thuần chia 4 đoạn rõ ràng bằng \\n\\n, KHÔNG dùng thẻ HTML, KHÔNG tự chèn URL vì hệ thống sẽ tự đặt dòng dẫn + link trần ở vị trí đầu/giữa/cuối). Cung cấp trọn vẹn thông tin thực tế, lịch trình và kinh nghiệm từ bài gốc, cấu trúc KHÁC HOÀN TOÀN Spotify và Castbox.
5. "tags": Chuỗi 5-8 từ khóa tiếng Việt ngăn cách bởi dấu phẩy.

Trả về đúng JSON với 5 khóa: "short_location_name", "title", "scenes", "description", "tags"."""
        res = call_gemini_json(prompt, key_offset=2)
        scenes = res.get("scenes") or []
        if scenes and isinstance(scenes, list):
            res["speech_script"] = " ".join(
                str(s.get("narration", "")).strip() for s in scenes if str(s.get("narration", "")).strip()
            )
        return res

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
    ctype = "video/mp4" if asset_name.lower().endswith(".mp4") else "audio/mpeg"
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


def update_podcast_rss(channel: str, ep_title: str, ep_desc: str, article_url: str, mp3_url: str, mp3_size: int, duration_sec: float, short_loc: str):
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


def build_hd_video_from_images(image_urls: list[str], audio_mp3: Path, duration_sec: float, out_mp4: Path, scenes: list | None = None):
    """Build a crisp 1280x720 HD slideshow video where each image & its duration match the exact spoken scene."""
    from PIL import Image, ImageOps

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://dulichcheckin.com/",
    }

    # Build ordered list of (image_url, weight_words) from Gemini's scene-by-scene plan
    ordered_specs = []
    if scenes and isinstance(scenes, list) and image_urls:
        for sc in scenes:
            idx = sc.get("image_index", 0)
            if not isinstance(idx, int) or idx < 0 or idx >= len(image_urls):
                idx = min(len(ordered_specs), len(image_urls) - 1)
            words = max(4, len(str(sc.get("narration", "")).split()))
            ordered_specs.append((image_urls[idx], words))

    if not ordered_specs:
        for u in image_urls[:12]:
            ordered_specs.append((u, 15))

    dl_imgs = []
    weights = []
    for idx, (u, w) in enumerate(ordered_specs[:14]):
        try:
            r = requests.get(u, headers=headers, timeout=20)
            if r.status_code == 200 and len(r.content) > 5000:
                raw_p = WORK_DIR / f"raw_{idx}"
                raw_p.write_bytes(r.content)
                p = WORK_DIR / f"slide_{idx}.jpg"
                im = Image.open(raw_p).convert("RGB")
                im = ImageOps.fit(im, (1280, 720), method=Image.Resampling.LANCZOS)
                im.save(p, "JPEG", quality=92)
                raw_p.unlink(missing_ok=True)
                dl_imgs.append(p)
                weights.append(w)
                print(f"Scene #{len(dl_imgs)} ({w} words) -> image: {u}")
        except Exception as e:
            print(f"Skip image {u}: {e}")
            continue

    if not dl_imgs:
        raise RuntimeError("Could not download any article images from dulichcheckin.com")

    # Calculate each slide's exact duration proportional to the spoken words in its scene
    total_w = sum(weights) or len(dl_imgs)
    total_target = duration_sec + 0.4
    clip_paths = []

    for i, (img_p, w) in enumerate(zip(dl_imgs, weights)):
        seg_dur = max(2.2, total_target * (w / total_w))
        clip_p = WORK_DIR / f"clip_{i}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-t", f"{seg_dur:.2f}",
                "-i", str(img_p),
                "-vf", "scale=1280:720,fade=t=in:st=0:d=0.35,format=yuv420p",
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
        short_loc = (script_data.get("short_location_name") or re.split(r"[:|–—?-]", article["h1"])[0]).strip()

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
            feed_url = update_podcast_rss(ch, ep_title, desc, article_url, mp3_url, mp3_size, duration, short_loc)
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
            build_hd_video_from_images(article["images"], mp3_path, duration, mp4_path, script_data.get("scenes"))
            print(f"Built HD MP4: {mp4_path.name} ({mp4_path.stat().st_size / 1024 / 1024:.2f} MB)")
            # Always upload HD MP4 to GitHub Releases so user can watch/download immediately!
            mp4_release_url, _ = upload_mp3_to_github_release(mp4_path)
            print(f"Saved HD MP4 to GitHub Release: {mp4_release_url}")
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


if __name__ == "__main__":
    main()
