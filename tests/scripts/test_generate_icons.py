from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_icon_svg_exists():
    svg = REPO_ROOT / "assets" / "icon.svg"
    assert svg.exists()
    text = svg.read_text(encoding="utf-8")
    assert "#f5efe4" in text
    assert "#6b4f3a" in text
    assert "#a64b2a" in text
    assert 'd="M12 46 Q18 50 24 42 T36 36 T52 28"' in text


import subprocess
import sys


def test_generate_icons_creates_assets():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_icons.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    for size in [16, 32, 48, 256, 512, 1024]:
        png = REPO_ROOT / "assets" / f"icon-{size}.png"
        assert png.exists(), f"{png} missing"

    app_ico = REPO_ROOT / "assets" / "icon.ico"
    assert app_ico.exists()

    favicon_ico = REPO_ROOT / "frontend" / "public" / "favicon.ico"
    assert favicon_ico.exists()
    favicon_png = REPO_ROOT / "frontend" / "public" / "favicon-32x32.png"
    assert favicon_png.exists()
