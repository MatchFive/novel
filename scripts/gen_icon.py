"""生成应用图标：藏青圆角底 + 金色书 + 羽毛笔。运行：python scripts/gen_icon.py"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "resources"
OUT.mkdir(exist_ok=True)

BG = (31, 42, 68, 255)        # 藏青
GOLD = (201, 162, 39, 255)    # 金
PAPER = (247, 245, 240, 255)  # 纸白
INK = (45, 55, 72, 255)       # 墨色


def make(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # 圆角背景
    d.rounded_rectangle([0, 0, s, s], radius=int(s * 0.22), fill=BG)

    # 打开的书（纸白两页 + 金色描边）
    cx = s // 2
    top = int(s * 0.34)
    bot = int(s * 0.72)
    spread = int(s * 0.30)
    # 左页
    d.polygon([(cx, top + 6), (cx - spread, top), (cx - spread, bot - 10), (cx, bot)],
              fill=PAPER, outline=GOLD, width=max(2, s // 64))
    # 右页
    d.polygon([(cx, top + 6), (cx + spread, top), (cx + spread, bot - 10), (cx, bot)],
              fill=(252, 250, 246, 255), outline=GOLD, width=max(2, s // 64))
    # 书脊中线
    d.line([(cx, top + 6), (cx, bot)], fill=GOLD, width=max(2, s // 80))

    # 羽毛笔（斜线 + 笔尖）
    pen_x0, pen_y0 = int(s * 0.62), int(s * 0.20)
    pen_x1, pen_y1 = int(s * 0.46), int(s * 0.56)
    d.line([(pen_x0, pen_y0), (pen_x1, pen_y1)], fill=GOLD, width=max(3, s // 42))
    # 笔尖（小三角）
    d.polygon([(pen_x1, pen_y1), (pen_x1 - s // 22, pen_y1 - s // 40), (pen_x1 - s // 50, pen_y1 + s // 40)],
              fill=GOLD)
    # 羽叶（笔端上方两片）
    d.ellipse([pen_x0 - s // 18, pen_y0 - s // 30, pen_x0 + s // 14, pen_y0 + s // 10], fill=GOLD)

    return img


def main() -> None:
    img = make(256)
    img.save(OUT / "icon.png")
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(OUT / "icon.ico", sizes=ico_sizes)
    print("icon.png / icon.ico ->", OUT)


if __name__ == "__main__":
    main()
