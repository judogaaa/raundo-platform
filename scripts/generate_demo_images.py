"""Generate original demo covers for local RAUNDO showcase data.

Deterministic, no external assets, no real logos or people. Files are written to
private_media/demo (gitignored) and referenced by demo media records.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "private_media" / "demo"
FONT_BOLD = Path(r"C:\Windows\Fonts\arialbd.ttf")
FONT_REG = Path(r"C:\Windows\Fonts\arial.ttf")

COVERS = [
    {
        "file": "skillpath-cover.png",
        "title": "SkillPath AI",
        "subtitle": "Персональные траектории обучения",
        "from": (18, 32, 74),
        "to": (36, 120, 160),
        "accent": (127, 224, 96),
    },
    {
        "file": "skillpath-gallery.png",
        "title": "SkillPath AI",
        "subtitle": "Адаптивная программа под цель",
        "from": (10, 22, 48),
        "to": (28, 88, 118),
        "accent": (127, 224, 96),
    },
    {
        "file": "flowdesk-cover.png",
        "title": "FlowDesk",
        "subtitle": "Задачи, клиенты и процессы в одном окне",
        "from": (34, 28, 62),
        "to": (92, 56, 140),
        "accent": (240, 196, 96),
    },
    {
        "file": "flowdesk-gallery.png",
        "title": "FlowDesk",
        "subtitle": "Порядок в операционных процессах",
        "from": (24, 20, 46),
        "to": (72, 46, 116),
        "accent": (240, 196, 96),
    },
    {
        "file": "carepulse-cover.jpg",
        "title": "CarePulse",
        "subtitle": "Спокойная связь с близкими каждый день",
        "from": (20, 44, 42),
        "to": (44, 118, 104),
        "accent": (236, 244, 214),
    },
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REG
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _gradient(width: int, height: int, start: tuple, end: tuple) -> Image.Image:
    image = Image.new("RGB", (width, height), start)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(start[i] + (end[i] - start[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return image


def _draw_cover(spec: dict) -> Image.Image:
    size = 1200
    image = _gradient(size, 900, spec["from"], spec["to"])
    draw = ImageDraw.Draw(image, "RGBA")
    accent = spec["accent"]

    # Abstract, calm geometry instead of stock imagery.
    for index in range(3):
        radius = 240 + index * 130
        bbox = [size - 160 - radius, 140 + index * 70, size - 160 + radius, 140 + index * 70 + radius * 2]
        draw.ellipse(bbox, outline=(*accent, 40 + index * 25), width=3)
    for index in range(9):
        x = 80 + index * 40
        height = int(120 + (math.sin(index / 2) + 1) * 140)
        draw.rounded_rectangle(
            [x, 620 - height, x + 18, 660],
            radius=9,
            fill=(*accent, 55),
        )

    draw.rounded_rectangle([70, 70, size - 70, 830], radius=36, outline=(255, 255, 255, 40), width=2)
    small = _font(30)
    draw.text((96, 110), "ДЕМО · RAUNDO", font=small, fill=(255, 255, 255, 170))
    draw.text((96, 600), spec["title"], font=_font(86, bold=True), fill=(252, 252, 250))
    draw.text((96, 700), spec["subtitle"], font=_font(36), fill=(230, 236, 240))
    draw.rounded_rectangle([96, 760, 236, 776], radius=8, fill=(*accent, 220))
    return image


def _draw_avatar(initials: str, accent: tuple) -> Image.Image:
    image = Image.new("RGB", (600, 600), (22, 24, 28))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.ellipse([60, 60, 540, 540], fill=(*accent, 40), outline=(*accent, 170), width=6)
    font = _font(180, bold=True)
    bbox = draw.textbbox((0, 0), initials, font=font)
    draw.text(
        (300 - (bbox[2] - bbox[0]) / 2, 300 - (bbox[3] - bbox[1]) / 2 - 20),
        initials,
        font=font,
        fill=(248, 250, 252),
    )
    return image


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for spec in COVERS:
        image = _draw_cover(spec)
        target = OUT / spec["file"]
        if target.suffix.lower() == ".jpg":
            image.convert("RGB").save(target, "JPEG", quality=92, optimize=True)
        else:
            image.save(target, "PNG", optimize=True)
        print(f"demo_image={target.name} bytes={target.stat().st_size}")
    for name, initials, accent in (
        ("investor-angel.png", "АИ", (127, 224, 96)),
        ("investor-tech.png", "ТИ", (240, 196, 96)),
        ("investor-vc.png", "ВП", (140, 190, 255)),
    ):
        target = OUT / name
        _draw_avatar(initials, accent).save(target, "PNG", optimize=True)
        print(f"demo_image={target.name} bytes={target.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
