"""生成 Novel Studio 图标资源。

脚本内部定义图标几何，同时输出 SVG 源文件与各尺寸 PNG/ICO。
使用 Pillow 直接绘制 PNG/ICO，避免 Windows 上对 Cairo DLL 的依赖。
"""
from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
PUBLIC_DIR = REPO_ROOT / "frontend" / "public"

SIZES = [16, 32, 48, 256, 512, 1024]

# 设计规范色值
PAGE_FILL = "#f5efe4"
PAGE_STROKE = "#6b4f3a"
ARC_STROKE = "#a64b2a"
TEXT_STROKE = "#8c6b4f"

# 64x64 viewBox 下的几何参数
PAGE_RECT = (6, 10, 58, 54)
PAGE_RADIUS = 3
ARC_POINTS = [(12, 46), (18, 50), (24, 42), (36, 36), (52, 28)]
TEXT_LINES = [((12, 24), (32, 24)), ((12, 32), (28, 32))]


def _quadratic_bezier_points(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    steps: int = 20,
) -> list[tuple[float, float]]:
    points = []
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
        points.append((x, y))
    return points


def _smooth_curve_points(
    points: list[tuple[float, float]], steps: int = 20
) -> list[tuple[float, float]]:
    """把 ARC_POINTS 视为连续二次贝塞尔控制点序列，生成平滑曲线。"""
    if len(points) < 3:
        return points
    result = [points[0]]
    for i in range(0, len(points) - 2, 2):
        p0 = points[i]
        p1 = points[i + 1]
        p2 = points[i + 2]
        segment = _quadratic_bezier_points(p0, p1, p2, steps)
        result.extend(segment[1:])
    return result


def draw_icon(size: int) -> Image.Image:
    """使用 Pillow 绘制指定尺寸的图标。"""
    scale = size / 64.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def s(value: float) -> float:
        return value * scale

    def s_tuple(rect: tuple) -> tuple:
        return tuple(s(v) for v in rect)

    # 书页底色（圆角矩形）
    page_box = s_tuple(PAGE_RECT)
    radius = s(PAGE_RADIUS)
    draw.rounded_rectangle(page_box, radius=radius, fill=PAGE_FILL)
    # 书页描边
    stroke_width = max(1, int(3 * scale))
    draw.rounded_rectangle(
        page_box, radius=radius, outline=PAGE_STROKE, width=stroke_width
    )

    # 叙事弧线
    arc = _smooth_curve_points(ARC_POINTS)
    arc_scaled = [(s(x), s(y)) for x, y in arc]
    arc_width = max(1, int(3 * scale))
    draw.line(arc_scaled, fill=ARC_STROKE, width=arc_width, joint="curve")

    # 文字线
    text_width = max(1, int(2 * scale))
    for start, end in TEXT_LINES:
        draw.line([s_tuple(start), s_tuple(end)], fill=TEXT_STROKE, width=text_width)

    return img


def write_svg(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <rect x="{PAGE_RECT[0]}" y="{PAGE_RECT[1]}" width="{PAGE_RECT[2] - PAGE_RECT[0]}" height="{PAGE_RECT[3] - PAGE_RECT[1]}" rx="{PAGE_RADIUS}" fill="{PAGE_FILL}" stroke="{PAGE_STROKE}" stroke-width="3"/>
  <path d="M12 46 Q18 50 24 42 T36 36 T52 28" fill="none" stroke="{ARC_STROKE}" stroke-width="3" stroke-linecap="round"/>
  <path d="M12 24h20M12 32h16" stroke="{TEXT_STROKE}" stroke-width="2" stroke-linecap="round"/>
</svg>"""
    output.write_text(svg, encoding="utf-8")


def render_png(size: int, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    img = draw_icon(size)
    img.save(output, format="PNG")


def create_ico(png_paths: list[Path], output: Path) -> None:
    """手动构造多分辨率 ICO（Pillow 的 save 只写单帧）。"""
    output.parent.mkdir(parents=True, exist_ok=True)

    png_bytes = [p.read_bytes() for p in png_paths]
    sizes = []
    for p in png_paths:
        with Image.open(p) as img:
            sizes.append((img.width, img.height))

    count = len(png_paths)
    header = struct.pack("<HHH", 0, 1, count)

    # 目录大小 + 每个图像数据偏移
    directory_size = 6 + count * 16
    offsets = []
    current_offset = directory_size
    for data in png_bytes:
        offsets.append(current_offset)
        current_offset += len(data)

    entries = b""
    for (width, height), data, offset in zip(sizes, png_bytes, offsets):
        w = width if width < 256 else 0
        h = height if height < 256 else 0
        entries += struct.pack(
            "<BBBBHHII",
            w,
            h,
            0,  # colors
            0,  # reserved
            1,  # planes
            32,  # bit count
            len(data),
            offset,
        )

    with open(output, "wb") as f:
        f.write(header)
        f.write(entries)
        for data in png_bytes:
            f.write(data)


def verify_ico_sizes(ico_path: Path, expected_sizes: set[int]) -> None:
    with open(ico_path, "rb") as f:
        header = f.read(6)
        _, _, count = struct.unpack("<HHH", header)
        assert count == len(
            expected_sizes
        ), f"ICO has {count} images, expected {len(expected_sizes)}"
        sizes = set()
        for _ in range(count):
            entry = f.read(16)
            width = entry[0]
            sizes.add(width if width != 0 else 256)
        assert sizes == expected_sizes, f"ICO sizes {sizes} != {expected_sizes}"


def main() -> None:
    # 同步写 SVG 源文件
    write_svg(ASSETS_DIR / "icon.svg")
    print(f"Generated {ASSETS_DIR / 'icon.svg'}")

    for size in SIZES:
        png_path = ASSETS_DIR / f"icon-{size}.png"
        render_png(size, png_path)
        print(f"Generated {png_path}")

    # 应用图标 ICO（16/32/48/256）
    app_ico = ASSETS_DIR / "icon.ico"
    app_pngs = [ASSETS_DIR / f"icon-{s}.png" for s in [16, 32, 48, 256]]
    create_ico(app_pngs, app_ico)
    verify_ico_sizes(app_ico, {16, 32, 48, 256})
    print(f"Generated {app_ico}")

    # 前端 favicon ICO（16/32）
    favicon_ico = PUBLIC_DIR / "favicon.ico"
    favicon_pngs = [ASSETS_DIR / f"icon-{s}.png" for s in [16, 32]]
    create_ico(favicon_pngs, favicon_ico)
    verify_ico_sizes(favicon_ico, {16, 32})
    print(f"Generated {favicon_ico}")

    # 前端 favicon PNG
    for size in [16, 32]:
        src = ASSETS_DIR / f"icon-{size}.png"
        dst = PUBLIC_DIR / f"favicon-{size}x{size}.png"
        dst.write_bytes(src.read_bytes())
        print(f"Generated {dst}")


if __name__ == "__main__":
    main()
