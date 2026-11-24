#!/usr/bin/env python3
"""data/entries.json から静的な HTML を生成する。

使い方: python3 scripts/build.py
引数はない。リポジトリのどこから実行しても、このファイルの位置から
data/entries.json、index.html、entries/ を決める。
標準ライブラリだけを使う。成功すると終了コード 0、データが不正なら 1、
引数があるなら 2。
"""

from __future__ import annotations

import html
import json
import os
import re
import struct
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "entries.json"
ENTRIES_DIR = ROOT / "entries"
IMAGES_DIR = ROOT / "images"
INDEX_PATH = ROOT / "index.html"

WEEKDAYS = "月火水木金土日"
SECTION_ORDER = (
    ("invite", "何がどう行為を誘っているか"),
    ("contrast", "惜しい例・悪い例との対比"),
    ("use", "使いどころのひとこと"),
)
ENTRY_KEYS = ("number", "date", "title", "category", "sections", "sources", "images")
SECTION_KEYS = frozenset(key for key, _heading in SECTION_ORDER)
SOURCE_KEYS = frozenset({"title", "url"})
IMAGE_KEYS = frozenset({"src", "alt", "caption"})
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"})
FIRST_ENTRY_DAY = date(2026, 11, 16)
INDEX_TITLE = "アフォーダンス図鑑　毎日ひとつのアフォーダンスの好例"
INDEX_DESCRIPTION = (
    "毎日ひとつのアフォーダンスの好例を図とともに残す図鑑。"
    "行為の可能性と、それを伝える印を、日常の実例で見る。"
)
README_URL = "https://github.com/MLabPages/affordance-zukan/blob/main/README.md"
GENERATED = "scripts/build.py が data/entries.json から生成。手で編集しない。"


class BuildError(Exception):
    """入力データがスキーマに合わない。"""


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def write_text(path: Path, text: str) -> None:
    if "\r" in text:
        raise BuildError(f"{path.name}: 生成結果に CR が含まれています")
    if not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(text.encode("utf-8"))
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def format_date(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日（{WEEKDAYS[value.weekday()]}）"


def require_object(value: object, loc: str) -> dict:
    if not isinstance(value, dict):
        raise BuildError(f"{loc}: オブジェクトである必要があります")
    return value


def require_text(value: object, loc: str, *, allow_newlines: bool) -> str:
    if not isinstance(value, str):
        raise BuildError(f"{loc}: 文字列である必要があります")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    allowed = {"\n", "\t"} if allow_newlines else set()
    if any(ord(ch) < 32 and ch not in allowed for ch in normalized):
        raise BuildError(f"{loc}: 制御文字は使えません")
    if not allow_newlines and "\n" in normalized:
        raise BuildError(f"{loc}: 改行は使えません")
    text = normalized.strip()
    if not text:
        raise BuildError(f"{loc}: 空でない文字列である必要があります")
    return text


def unknown_keys(value: dict, allowed: frozenset[str] | tuple[str, ...], loc: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise BuildError(f"{loc}: 不明なキーです: {', '.join(unknown)}")


def paragraphs(text: str, loc: str) -> str:
    blocks = re.split(r"\n[ \t]*\n", text)
    parts: list[str] = []
    for block in blocks:
        lines: list[str] = []
        for line in block.split("\n"):
            cleaned = line.strip(" \t")
            if cleaned:
                lines.append(esc(cleaned))
        if lines:
            parts.append("<p>" + "<br>\n".join(lines) + "</p>")
    if not parts:
        raise BuildError(f"{loc}: 空でない本文である必要があります")
    return "\n".join(parts)


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    suffix = path.suffix.lower()
    try:
        if suffix == ".png":
            return png_size(data)
        if suffix == ".gif":
            return gif_size(data)
        if suffix in {".jpg", ".jpeg"}:
            return jpeg_size(data)
        if suffix == ".webp":
            return webp_size(data)
        if suffix == ".svg":
            return svg_size(data)
    except (struct.error, ValueError, IndexError):
        return None
    return None


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        return None
    return width, height


def gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        return None
    width, height = struct.unpack("<HH", data[6:10])
    if width < 1 or height < 1:
        return None
    return width, height


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    sof = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    index = 2
    length = len(data)
    while index + 1 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        while index < length and data[index] == 0xFF:
            index += 1
        if index >= length:
            return None
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > length:
            return None
        segment = struct.unpack(">H", data[index:index + 2])[0]
        if segment < 2:
            return None
        if marker in sof:
            if index + 7 > length:
                return None
            height, width = struct.unpack(">HH", data[index + 3:index + 7])
            if width < 1 or height < 1:
                return None
            return width, height
        index += segment
    return None


def webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        if width < 1 or height < 1:
            return None
        return width, height
    if chunk == b"VP8 ":
        start = data.find(b"\x9d\x01\x2a")
        if start == -1 or start + 7 > len(data):
            return None
        width, height = struct.unpack("<HH", data[start + 3:start + 7])
        width &= 0x3FFF
        height &= 0x3FFF
        if width < 1 or height < 1:
            return None
        return width, height
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    return None


def svg_size(data: bytes) -> tuple[int, int] | None:
    text = data.decode("utf-8", errors="ignore")
    match = re.search(
        r"viewBox\s*=\s*[\"']\s*[-+\d.eE]+\s+[-+\d.eE]+\s+"
        r"([+\d.eE]+)\s+([+\d.eE]+)\s*[\"']",
        text,
    )
    if not match:
        return None
    width = round(float(match.group(1)))
    height = round(float(match.group(2)))
    if width < 1 or height < 1:
        return None
    return width, height


def check_image_src(src: str, loc: str) -> tuple[str, tuple[int, int] | None]:
    if src.startswith("/") or "\\" in src:
        raise BuildError(f"{loc}: images/ファイル名 の形で書いてください")
    parts = src.split("/")
    if ".." in parts or len(parts) != 2 or parts[0] != "images" or not parts[1]:
        raise BuildError(f"{loc}: images/ファイル名 の形で書いてください")
    name = parts[1]
    if name.startswith(".") or any(ch.isspace() or ch in "?#%<>\"'\\" for ch in name):
        raise BuildError(f"{loc}: ファイル名に空白や URL の特殊文字は使えません")
    suffix = Path(name).suffix
    if suffix not in IMAGE_EXTS:
        raise BuildError(
            f"{loc}: 拡張子は小文字の jpg, jpeg, png, gif, webp, svg のいずれかです"
        )
    path = IMAGES_DIR / name
    if not path.is_file():
        raise BuildError(f"{loc}: ファイルがありません: {src}")
    if path.stat().st_size == 0:
        raise BuildError(f"{loc}: ファイルが空です: {src}")
    images_root = IMAGES_DIR.resolve()
    resolved = path.resolve()
    try:
        common = Path(os.path.commonpath([str(images_root), str(resolved)]))
    except ValueError as exc:
        raise BuildError(f"{loc}: images/ の外は参照できません") from exc
    if common != images_root:
        raise BuildError(f"{loc}: images/ の外は参照できません")
    return src, image_size(path)


def check_url(url: object, loc: str) -> str:
    if not isinstance(url, str):
        raise BuildError(f"{loc}: 文字列である必要があります")
    if any(ord(ch) < 32 or ch.isspace() for ch in url) or not url:
        raise BuildError(f"{loc}: http または https のURLを書いてください")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BuildError(f"{loc}: http または https のURLを書いてください")
    return url


def load_entries() -> list[dict]:
    if not DATA_PATH.is_file():
        raise BuildError("data/entries.json がありません")
    try:
        raw = DATA_PATH.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BuildError("data/entries.json: UTF-8 で保存してください") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BuildError(
            f"data/entries.json: JSONとして読めません ({exc.lineno}行目 {exc.colno}列目)"
        ) from exc
    data = require_object(data, "data/entries.json")
    unknown_keys(data, ("entries",), "data/entries.json")
    items = data["entries"]
    if not isinstance(items, list):
        raise BuildError("entries: 配列である必要があります")

    cleaned: list[dict] = []
    seen: dict[int, int] = {}
    for index, item in enumerate(items):
        loc = f"entries[{index}]"
        entry = require_object(item, loc)
        unknown_keys(entry, ENTRY_KEYS, loc)
        missing = [key for key in ENTRY_KEYS if key not in entry]
        if missing:
            raise BuildError(f"{loc}: 必要なキーがありません: {', '.join(missing)}")

        number = entry["number"]
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise BuildError(f"{loc}.number: 1以上の整数である必要があります（1.0 や文字列は不可）")
        if number in seen:
            raise BuildError(
                f"{loc}.number: {number} は entries[{seen[number]}] と重複しています"
            )
        seen[number] = index

        try:
            parsed_date = date.fromisoformat(entry["date"]) if isinstance(entry["date"], str) else None
        except ValueError:
            parsed_date = None
        if (
            not isinstance(entry["date"], str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["date"])
            or parsed_date is None
            or parsed_date.isoformat() != entry["date"]
        ):
            raise BuildError(f"{loc}.date: YYYY-MM-DD 形式の実在する日付である必要があります")

        title = require_text(entry["title"], f"{loc}.title", allow_newlines=False)
        category = require_text(entry["category"], f"{loc}.category", allow_newlines=False)
        if category == "all":
            raise BuildError(f"{loc}.category: all は絞り込み用に予約されているため使えません")

        sections = require_object(entry["sections"], f"{loc}.sections")
        unknown_keys(sections, SECTION_KEYS, f"{loc}.sections")
        missing_sections = [key for key, _heading in SECTION_ORDER if key not in sections]
        if missing_sections:
            raise BuildError(
                f"{loc}.sections: 必要なキーがありません: {', '.join(missing_sections)}"
            )
        section_text = {
            key: require_text(sections[key], f"{loc}.sections.{key}", allow_newlines=True)
            for key, _heading in SECTION_ORDER
        }
        for key, _heading in SECTION_ORDER:
            paragraphs(section_text[key], f"{loc}.sections.{key}")

        sources_raw = entry["sources"]
        if not isinstance(sources_raw, list) or len(sources_raw) < 1:
            raise BuildError(f"{loc}.sources: 1件以上の配列である必要があります")
        sources: list[dict] = []
        for source_index, source_item in enumerate(sources_raw):
            source_loc = f"{loc}.sources[{source_index}]"
            source = require_object(source_item, source_loc)
            unknown_keys(source, SOURCE_KEYS, source_loc)
            if "title" not in source:
                raise BuildError(f"{source_loc}: title が必要です")
            source_title = require_text(source["title"], f"{source_loc}.title", allow_newlines=False)
            source_clean: dict = {"title": source_title}
            if "url" in source:
                source_clean["url"] = check_url(source["url"], f"{source_loc}.url")
            sources.append(source_clean)

        images_raw = entry["images"]
        if not isinstance(images_raw, list) or not 1 <= len(images_raw) <= 2:
            raise BuildError(f"{loc}.images: 1枚または2枚である必要があります")
        images: list[dict] = []
        for image_index, image_item in enumerate(images_raw):
            image_loc = f"{loc}.images[{image_index}]"
            image = require_object(image_item, image_loc)
            unknown_keys(image, IMAGE_KEYS, image_loc)
            missing_image = [key for key in ("src", "alt", "caption") if key not in image]
            if missing_image:
                raise BuildError(
                    f"{image_loc}: 必要なキーがありません: {', '.join(missing_image)}"
                )
            src = require_text(image["src"], f"{image_loc}.src", allow_newlines=False)
            alt = require_text(image["alt"], f"{image_loc}.alt", allow_newlines=False)
            caption = require_text(image["caption"], f"{image_loc}.caption", allow_newlines=False)
            src, size = check_image_src(src, f"{image_loc}.src")
            images.append({
                "src": src,
                "alt": alt,
                "caption": caption,
                "width": None if size is None else size[0],
                "height": None if size is None else size[1],
            })

        cleaned.append({
            "number": number,
            "date": parsed_date.isoformat(),
            "date_label": format_date(parsed_date),
            "title": title,
            "category": category,
            "sections": section_text,
            "sources": sources,
            "images": images,
            "filename": f"{number:03d}.html",
        })
    return cleaned


def prefix(depth: int) -> str:
    return "../" * depth


def img_tag(src: str, alt: str, width: int | None, height: int | None, attrs: dict[str, str]) -> str:
    parts = [f'src="{esc(src)}"', f'alt="{esc(alt)}"']
    if width and height:
        parts.append(f'width="{width}"')
        parts.append(f'height="{height}"')
    for key, value in attrs.items():
        parts.append(f'{key}="{esc(value)}"')
    return "<img " + " ".join(parts) + ">"


def head(title: str, description: str, depth: int, extra: list[str]) -> str:
    root = prefix(depth)
    lines = [
        "<!DOCTYPE html>",
        f"<!-- {GENERATED} -->",
        '<html lang="ja">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="theme-color" content="#f3efe4">',
        '<meta name="color-scheme" content="light">',
        '<meta name="format-detection" content="telephone=no">',
        '<meta name="generator" content="scripts/build.py">',
        f'<meta name="description" content="{esc(description)}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        '<meta property="og:locale" content="ja_JP">',
        f"<title>{esc(title)}</title>",
        f'<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">',
        f'<link rel="stylesheet" href="{root}styles.css">',
    ]
    lines.extend(extra)
    lines.append("</head>")
    return "\n".join(lines)


def masthead(depth: int, is_index: bool) -> str:
    if is_index:
        brand = '<h1 class="brand">アフォーダンス図鑑</h1>'
    else:
        brand = (
            f'<p class="brand"><a href="{prefix(depth)}index.html">アフォーダンス図鑑</a></p>'
        )
    return "\n".join([
        '<header class="masthead">',
        '  <div class="wrap">',
        f"    {brand}",
        '    <p class="tagline">毎日ひとつのアフォーダンスの好例</p>',
        '    <div class="masthead-rule" aria-hidden="true"></div>',
        "  </div>",
        "</header>",
    ])


def footer() -> str:
    return "\n".join([
        '<footer class="colophon">',
        '<div class="wrap">',
        "    <p>文章は出典をもとにAIが学習用に要約したものです。詳しくは各回の出典へ。</p>",
        "    <p>図はAIが書いたコードで描いています。</p>",
        "</div>",
        "</footer>",
    ])


def page(title: str, description: str, depth: int, body_class: str, main: str, extra_head: list[str], extra_end: str) -> str:
    parts = [
        head(title, description, depth, extra_head),
        f'<body class="{body_class}">',
        '<a class="skip" href="#main">本文へスキップ</a>',
        masthead(depth, body_class == "page-index"),
        '<main id="main" tabindex="-1">',
        '<div class="wrap">',
        main.rstrip("\n"),
        "</div>",
        "</main>",
        footer(),
    ]
    if extra_end:
        parts.append(extra_end.rstrip("\n"))
    parts.extend(["</body>", "</html>"])
    return "\n".join(parts) + "\n"


def intro() -> str:
    return """<section class="intro">
  <p class="lede">この図鑑は、毎日ひとつの好例を、図とともに残していく記録です。扉や道具、家電のつまみ、公共のサイン、画面のなかの界面、ゲーム、乗り物、遊具、誰もが使いやすい工夫、自然の地形までを扱います。よい例を一つ取り上げ、惜しい例と並べます。</p>
  <div class="concepts">
    <section class="concept">
      <h2>アフォーダンス</h2>
      <p>アフォーダンスとは、環境が動物に対して開いている行為の可能性のことです。心理学者ジェームズ・J・ギブソンが、生態心理学のなかでアフォーダンスと名付け、1979年の著書でまとめて論じました。</p>
      <p class="cite-line"><cite lang="en">The Ecological Approach to Visual Perception</cite><span class="cite-ja">邦題『生態学的視覚論』</span></p>
      <p>その動物にとって十分に広く、ほぼ水平で、平らで、硬い面は、体を支えることを可能にします。手に合う取っ手は、つかむことを可能にします。ギブソンにとってそれは、その動物との関係で決まる、環境の側にある性質です。見る人が後から貼りつけた印象ではありません。</p>
    </section>
    <section class="concept">
      <h2>シグニファイア</h2>
      <p>可能であることと、人がそれに気づくことは同じではありません。ドナルド・A・ノーマンは、デザインの場でアフォーダンスという言葉が二つの意味に使われてきた、と整理しました。一つは、何ができるかという行為の可能性。もう一つは、どこをどう扱えばよいかを知らせる手がかりです。手がかりの側を、ノーマンはシグニファイア（<span lang="en">signifier</span>）と呼びます。扉の押板、棒状の取っ手、「押す」「引く」の文字、ボタンの凹みは、その印にあたります。要約すれば、アフォーダンスはどんな行為が可能かを決め、シグニファイアはその行為をどこでするのかを伝えます。</p>
      <p>この区別が明確に書かれているのは、2013年の改訂・拡張版です。日本語では『誰のためのデザイン？』として知られています。1988年の本でデザインに導入したのは、主として知覚されたアフォーダンスでした。その本は、のちに改題されています。</p>
      <p class="cite-line"><cite lang="en">The Design of Everyday Things</cite><span class="cite-ja">2013年、改訂・拡張版</span></p>
      <p class="cite-line"><cite lang="en">The Psychology of Everyday Things</cite><span class="cite-ja">1988年。のちに <cite lang="en">The Design of Everyday Things</cite> と改題</span></p>
    </section>
  </div>
  <p class="reading">各回のページでは、図を先に置きます。本文は三つの欄です。「何がどう行為を誘っているか」「惜しい例・悪い例との対比」「使いどころのひとこと」。一覧は新しい回が先頭です。分野のチップで絞り込めます。</p>
</section>"""


def empty_state() -> str:
    first = format_date(FIRST_ENTRY_DAY)
    return f"""<div class="empty">
  <div class="empty-inner">
    <p class="empty-label">第1回</p>
    <p class="empty-title">まだ、一枚もありません</p>
    <p class="empty-copy">第1回は、{first}の20:00（日本時間）ごろに加わります。それから先は、毎日ひとつの好例が、この一覧に並びます。</p>
  </div>
</div>"""


def render_cards(entries: list[dict]) -> str:
    cards: list[str] = []
    for index, entry in enumerate(entries):
        image = entry["images"][0]
        loading = "eager" if index < 2 else "lazy"
        attrs = {"loading": loading}
        if loading == "lazy":
            attrs["decoding"] = "async"
        image_html = img_tag(image["src"], "", image["width"], image["height"], attrs)
        cards.append(
            "\n".join([
                f'    <li class="card" data-category="{esc(entry["category"])}">',
                f'      <a class="card-link" href="entries/{entry["filename"]}">',
                '        <div class="card-media">',
                f"          {image_html}",
                "        </div>",
                '        <div class="card-body">',
                '          <p class="card-meta">',
                f'            <span class="card-no">第{entry["number"]}回</span>',
                f'            <time datetime="{entry["date"]}">{esc(entry["date_label"])}</time>',
                "          </p>",
                f'          <p class="card-title">{esc(entry["title"])}</p>',
                f'          <p class="card-category">{esc(entry["category"])}</p>',
                "        </div>",
                "      </a>",
                "    </li>",
            ])
        )
    return '<ul class="cards">\n' + "\n".join(cards) + "\n  </ul>"


def render_filters(categories: list[str]) -> str:
    chips = [
        '    <button type="button" class="chip is-active" data-filter="all" aria-pressed="true">すべて</button>'
    ]
    for category in categories:
        chips.append(
            f'    <button type="button" class="chip" data-filter="{esc(category)}" aria-pressed="false">{esc(category)}</button>'
        )
    return "\n".join([
        '<div class="collection-tools">',
        '  <p class="filters-label" id="filters-label">分野</p>',
        '  <div class="filters" role="group" aria-labelledby="filters-label">',
        "\n".join(chips),
        "  </div>",
        "</div>",
        '<noscript><p class="noscript-note">JavaScript が無効でも、全件はこの下に出ます。分野の絞り込みだけ動きません。</p></noscript>',
        '<p id="filter-empty" class="filter-empty" hidden>この分野の回は、まだありません。</p>',
    ])


def render_index(entries: list[dict]) -> str:
    newest = sorted(entries, key=lambda entry: (entry["date"], entry["number"]), reverse=True)
    categories: list[str] = []
    for entry in newest:
        if entry["category"] not in categories:
            categories.append(entry["category"])
    blocks = [
        intro(),
        '<section class="collection" aria-labelledby="collection-heading">',
        '  <div class="collection-head">',
        '    <h2 id="collection-heading">収録</h2>',
    ]
    if newest:
        blocks.append(f'    <p class="collection-note">全{len(newest)}回・新しい順</p>')
    blocks.extend([
        "  </div>",
    ])
    if newest:
        blocks.append(render_filters(categories))
        blocks.append("  " + render_cards(newest).replace("\n", "\n  "))
    else:
        blocks.append(empty_state())
    blocks.append("</section>")
    extra_end = '<script src="site.js"></script>' if newest else ""
    return page(
        INDEX_TITLE,
        INDEX_DESCRIPTION,
        0,
        "page-index",
        "\n".join(blocks),
        ['<meta property="og:type" content="website">'],
        extra_end,
    )


def pager_link(kind: str, label: str, entry: dict | None, empty_text: str) -> str:
    css = "prev" if kind == "prev" else "next"
    if entry is None:
        return "\n".join([
            f'  <span class="{css} is-disabled">',
            f'    <span class="pager-dir">{label}</span>',
            f'    <span class="pager-title">{empty_text}</span>',
            "  </span>",
        ])
    rel = ' rel="prev"' if kind == "prev" else ' rel="next"'
    title = f'第{entry["number"]}回　{esc(entry["title"])}'
    return "\n".join([
        f'  <a class="{css}" href="{entry["filename"]}"{rel}>',
        f'    <span class="pager-dir">{label}</span>',
        f'    <span class="pager-title">{title}</span>',
        "  </a>",
    ])


def render_entry(entry: dict, previous: dict | None, nxt: dict | None) -> str:
    plates: list[str] = []
    for index, image in enumerate(entry["images"], start=1):
        src = prefix(1) + image["src"]
        attrs = {"loading": "eager", "fetchpriority": "high"} if index == 1 else {
            "loading": "lazy",
            "decoding": "async",
        }
        image_html = img_tag(src, image["alt"], image["width"], image["height"], attrs)
        plates.append(
            "\n".join([
                '    <figure class="plate">',
                f"      {image_html}",
                "      <figcaption>",
                f'        <span class="fig-no">図{index}</span> {esc(image["caption"])}',
                "      </figcaption>",
                "    </figure>",
            ])
        )
    sections: list[str] = []
    for key, heading in SECTION_ORDER:
        body = paragraphs(entry["sections"][key], f"entries[{entry['number']}].sections.{key}")
        indented = "\n".join("    " + line if line else line for line in body.split("\n"))
        sections.append(
            "\n".join([
                '  <section class="entry-section">',
                f"    <h2>{heading}</h2>",
                indented,
                "  </section>",
            ])
        )
    source_items: list[str] = []
    for source in entry["sources"]:
        if "url" in source:
            source_items.append(
                f'      <li><a href="{esc(source["url"])}">{esc(source["title"])}</a></li>'
            )
        else:
            source_items.append(f'      <li>{esc(source["title"])}</li>')
    sections.append(
        "\n".join([
            '  <section class="entry-section sources">',
            "    <h2>出典</h2>",
            "    <ol>",
            "\n".join(source_items),
            "    </ol>",
            "  </section>",
        ])
    )
    main = "\n".join([
        '<article class="entry">',
        '  <p class="back"><a href="../index.html">一覧へ戻る</a></p>',
        '  <header class="entry-header">',
        '    <p class="entry-kicker">',
        f'      <span class="entry-no">第{entry["number"]}回</span>',
        f'      <time datetime="{entry["date"]}">{esc(entry["date_label"])}</time>',
        f'      <span class="entry-category">{esc(entry["category"])}</span>',
        "    </p>",
        f'    <h1 class="entry-title">{esc(entry["title"])}</h1>',
        "  </header>",
        '  <div class="plates">',
        "\n".join(plates),
        "  </div>",
        "\n".join(sections),
        '  <nav class="pager" aria-label="前後の回">',
        pager_link("prev", "前の回", previous, "これが最初の回です"),
        pager_link("next", "次の回", nxt, "まだ次の回はありません"),
        "  </nav>",
        "</article>",
    ])
    extra = [
        '<meta property="og:type" content="article">',
        f'<meta property="article:published_time" content="{entry["date"]}">',
    ]
    if previous is not None:
        extra.append(f'<link rel="prev" href="{previous["filename"]}">')
    if nxt is not None:
        extra.append(f'<link rel="next" href="{nxt["filename"]}">')
    description = f'第{entry["number"]}回（{entry["date_label"]}）。{entry["title"]}。分野は{entry["category"]}。'
    title = f'{entry["title"]}｜第{entry["number"]}回｜アフォーダンス図鑑'
    return page(title, description, 1, "page-entry", main, extra, "")


def remove_stale(expected: set[str]) -> list[str]:
    if not ENTRIES_DIR.exists():
        return []
    removed: list[str] = []
    for path in sorted(ENTRIES_DIR.glob("*.html")):
        if path.name not in expected:
            path.unlink()
            removed.append(path.name)
    for path in ENTRIES_DIR.glob("*.html.tmp"):
        path.unlink()
    return removed


def render(entries: list[dict]) -> list[str]:
    by_number = sorted(entries, key=lambda entry: entry["number"])
    write_text(INDEX_PATH, render_index(entries))
    expected: set[str] = set()
    for index, entry in enumerate(by_number):
        previous = by_number[index - 1] if index > 0 else None
        nxt = by_number[index + 1] if index + 1 < len(by_number) else None
        write_text(ENTRIES_DIR / entry["filename"], render_entry(entry, previous, nxt))
        expected.add(entry["filename"])
    return remove_stale(expected)


def main() -> int:
    if len(sys.argv) != 1:
        print("使い方: python3 scripts/build.py", file=sys.stderr)
        print("引数はありません。data/entries.json から index.html と entries/NNN.html を書き出します。", file=sys.stderr)
        return 2
    try:
        entries = load_entries()
        removed = render(entries)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"生成しました: index.html、項目 {len(entries)} 件")
    for entry in sorted(entries, key=lambda item: item["number"]):
        print(f"  entries/{entry['filename']}")
    for name in removed:
        print(f"削除しました: entries/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
