# Novel Studio 应用图标实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于已批准的图标设计规范，生成 SVG/PNG/ICO 图标资源，并将其接入 PyInstaller 可执行文件、pywebview 窗口和前端 favicon。

**Architecture:** `scripts/generate_icons.py` 同时维护矢量源文件 `assets/icon.svg` 与位图资源；PNG/ICO 使用 Pillow 直接绘制，避免 Windows 上依赖外部 Cairo DLL。PyInstaller 使用 `assets/icon.ico`，pywebview 窗口使用 `assets/icon-256.png`，前端使用 `frontend/public/favicon.*`。

**Tech Stack:** Python 3.11+、Pillow、PyInstaller、pywebview、React + Vite。

## Global Constraints

- 图标几何描述必须同时驱动 SVG 与 PNG/ICO，避免两份源文件不同步。
- 输出尺寸：16、32、48、256、512、1024（PNG）；ICO 包含 16/32/48/256。
- 色彩严格使用设计规范：`#f5efe4`、 `#6b4f3a`、 `#a64b2a`。
- Windows 为主要目标平台；macOS `.icns` 不在本次范围。
- 所有生成步骤必须可在干净环境中通过脚本复现。
- 每次任务结束需独立验证。

---

## Task 1: 添加图标生成依赖

**Files:**
- Modify: `backend/requirements.txt`
- Test: 无（环境准备任务）

**Interfaces:**
- Consumes: 无
- Produces: `backend/requirements.txt` 包含 `Pillow`

- [ ] **Step 1: 在 `backend/requirements.txt` 追加 Pillow**

  在文件末尾添加：

  ```text
  Pillow==10.4.0
  ```

- [ ] **Step 2: 在 backend 虚拟环境中安装依赖**

  Run:
  ```bash
  cd backend
  pip install -r requirements.txt
  ```

  Expected: `Successfully installed Pillow-10.4.0`（或兼容版本）。

- [ ] **Step 3: 验证导入**

  Run:
  ```bash
  cd backend
  python -c "from PIL import Image; print('ok')"
  ```

  Expected: 输出 `ok`。

- [ ] **Step 4: Commit**

  ```bash
  git add backend/requirements.txt
  git commit -m "chore(deps): add Pillow for icon generation"
  ```

---

## Task 2: 创建 SVG 图标源文件

**Files:**
- Create: `assets/icon.svg`
- Test: `tests/scripts/test_generate_icons.py`

**Interfaces:**
- Consumes: 无
- Produces: `assets/icon.svg`

- [ ] **Step 1: 创建 `assets/icon.svg`**

  写入：

  ```svg
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
    <rect x="6" y="10" width="52" height="44" rx="3" fill="#f5efe4" stroke="#6b4f3a" stroke-width="3"/>
    <path d="M12 46 Q18 50 24 42 T36 36 T52 28" fill="none" stroke="#a64b2a" stroke-width="3" stroke-linecap="round"/>
    <path d="M12 24h20M12 32h16" stroke="#8c6b4f" stroke-width="2" stroke-linecap="round"/>
  </svg>
  ```

- [ ] **Step 2: 写测试断言 SVG 存在且关键元素完整**

  创建 `tests/scripts/test_generate_icons.py`：

  ```python
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
  ```

- [ ] **Step 3: 运行测试**

  Run:
  ```bash
  cd f:/python_project/novel
  PYTHONPATH=. pytest tests/scripts/test_generate_icons.py::test_icon_svg_exists -v
  ```

  Expected: `PASSED`。

- [ ] **Step 4: Commit**

  ```bash
  git add assets/icon.svg tests/scripts/test_generate_icons.py
  git commit -m "feat(icons): add SVG icon source"
  ```

---

## Task 3: 实现图标生成脚本

**Files:**
- Create: `scripts/generate_icons.py`
- Modify: `tests/scripts/test_generate_icons.py`

**Interfaces:**
- Consumes: 无（脚本内部定义几何参数，不读取 SVG 渲染）
- Produces: `assets/icon.svg`、`assets/icon-{size}.png`（size ∈ {16,32,48,256,512,1024}）、`assets/icon.ico`、`frontend/public/favicon.ico`、`frontend/public/favicon-16x16.png`、`frontend/public/favicon-32x32.png`

- [ ] **Step 1: 编写 `scripts/generate_icons.py`**

  ```python
  """生成 Novel Studio 图标资源。

  脚本内部定义图标几何，同时输出 SVG 源文件与各尺寸 PNG/ICO。
  使用 Pillow 直接绘制 PNG/ICO，避免 Windows 对 Cairo DLL 的依赖。
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


  def _quadratic_bezier_points(p0: tuple, p1: tuple, p2: tuple, steps: int = 20) -> list[tuple]:
      points = []
      for i in range(steps + 1):
          t = i / steps
          x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
          y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
          points.append((x, y))
      return points


  def _smooth_curve_points(points: list[tuple], steps: int = 20) -> list[tuple]:
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
      draw.rounded_rectangle(page_box, radius=radius, outline=PAGE_STROKE, width=max(1, int(3 * scale)))

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
      svg = f"""\u003csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64"\u003e
    \u003crect x="{PAGE_RECT[0]}" y="{PAGE_RECT[1]}" width="{PAGE_RECT[2] - PAGE_RECT[0]}" height="{PAGE_RECT[3] - PAGE_RECT[1]}" rx="{PAGE_RADIUS}" fill="{PAGE_FILL}" stroke="{PAGE_STROKE}" stroke-width="3"/\u003e
    \u003cpath d="M12 46 Q18 50 24 42 T36 36 T52 28" fill="none" stroke="{ARC_STROKE}" stroke-width="3" stroke-linecap="round"/\u003e
    \u003cpath d="M12 24h20M12 32h16" stroke="{TEXT_STROKE}" stroke-width="2" stroke-linecap="round"/\u003e
  \u003c/svg\u003e"""
      output.write_text(svg, encoding="utf-8")


  def render_png(size: int, output: Path) -> None:
      output.parent.mkdir(parents=True, exist_ok=True)
      img = draw_icon(size)
      img.save(output, format="PNG")


  def create_ico(png_paths: list[Path], output: Path) -> None:
      output.parent.mkdir(parents=True, exist_ok=True)
      images = [Image.open(p) for p in png_paths]
      images[0].save(
          output,
          format="ICO",
          sizes=[(img.width, img.height) for img in images],
      )


  def verify_ico_sizes(ico_path: Path, expected_sizes: set[int]) -> None:
      with open(ico_path, "rb") as f:
          header = f.read(6)
          count = struct.unpack("<HHH", header)[1]
          assert count == len(expected_sizes), f"ICO has {count} images, expected {len(expected_sizes)}"
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
  ```

- [ ] **Step 2: 在测试中补充生成结果断言**

  在 `tests/scripts/test_generate_icons.py` 追加：

  ```python
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
  ```

- [ ] **Step 3: 运行测试**

  Run:
  ```bash
  cd f:/python_project/novel
  PYTHONPATH=. pytest tests/scripts/test_generate_icons.py -v
  ```

  Expected: 两个测试均 `PASSED`，并在标准输出中显示 `Generated ...` 日志。

- [ ] **Step 4: Commit**

  ```bash
  git add scripts/generate_icons.py tests/scripts/test_generate_icons.py
  git commit -m "feat(icons): add Pillow-based icon generation script and tests"
  ```

---

## Task 4: 将图标接入 PyInstaller 可执行文件

**Files:**
- Modify: `NovelStudio.spec`
- Test: 构建后检查 EXE 嵌入图标

**Interfaces:**
- Consumes: `assets/icon.ico`
- Produces: `NovelStudio.spec` 引用 `assets/icon.ico`

- [ ] **Step 1: 修改 `NovelStudio.spec` 的 EXE 块**

  找到 `NovelStudio.spec` 中的 `exe = EXE(...)`，添加 `icon='assets/icon.ico',`：

  ```python
  exe = EXE(
      pyz,
      a.scripts,
      [],
      exclude_binaries=True,
      name='NovelStudio',
      debug=False,
      bootloader_ignore_signals=False,
      strip=False,
      upx=True,
      console=False,
      disable_windowed_traceback=False,
      argv_emulation=False,
      target_arch=None,
      codesign_identity=None,
      entitlements_file=None,
      icon='assets/icon.ico',
  )
  ```

- [ ] **Step 2: 语法检查**

  Run:
  ```bash
  cd f:/python_project/novel
  python -m py_compile NovelStudio.spec
  ```

  Expected: 无输出（成功）。

- [ ] **Step 3: Commit**

  ```bash
  git add NovelStudio.spec
  git commit -m "chore(build): set PyInstaller EXE icon"
  ```

---

## Task 5: 将图标接入 pywebview 窗口

**Files:**
- Modify: `backend/desktop_launcher.py`

**Interfaces:**
- Consumes: `assets/icon-256.png`
- Produces: `webview.create_window(..., icon=...)`

- [ ] **Step 1: 在 `backend/desktop_launcher.py` 添加图标路径常量**

  在 `APP_URL = ...` 后添加：

  ```python
  ICON_PATH = Path(__file__).parent.parent / "assets" / "icon-256.png"
  ```

- [ ] **Step 2: 在 `webview.create_window` 中传入 icon 参数**

  将：

  ```python
  webview.create_window(
      title=settings.app_name,
      url=APP_URL,
      width=1280,
      height=800,
      min_size=(960, 640),
  )
  ```

  改为：

  ```python
  webview.create_window(
      title=settings.app_name,
      url=APP_URL,
      width=1280,
      height=800,
      min_size=(960, 640),
      icon=str(ICON_PATH) if ICON_PATH.exists() else None,
  )
  ```

- [ ] **Step 3: 语法检查**

  Run:
  ```bash
  cd backend
  python -m py_compile desktop_launcher.py
  ```

  Expected: 无输出（成功）。

- [ ] **Step 4: Commit**

  ```bash
  git add backend/desktop_launcher.py
  git commit -m "feat(launcher): set pywebview window icon"
  ```

---

## Task 6: 将 favicon 接入前端

**Files:**
- Modify: `frontend/index.html`
- Test: 浏览器访问 dev server，检查标签页图标

**Interfaces:**
- Consumes: `frontend/public/favicon.ico`、`frontend/public/favicon-16x16.png`、`frontend/public/favicon-32x32.png`
- Produces: 浏览器标签页显示 favicon

- [ ] **Step 1: 修改 `frontend/index.html`**

  在 `<head>` 内 `<title>` 下方添加：

  ```html
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
  ```

  最终 `index.html`：

  ```html
  <!doctype html>
  <html lang="zh-CN">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>Novel Studio</title>
      <link rel="icon" type="image/x-icon" href="/favicon.ico">
      <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
      <link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
    </head>
    <body>
      <div id="root"></div>
      <script type="module" src="/src/main.tsx"></script>
    </body>
  </html>
  ```

- [ ] **Step 2: 验证 favicon 文件存在**

  Run:
  ```bash
  ls frontend/public/favicon.ico frontend/public/favicon-16x16.png frontend/public/favicon-32x32.png
  ```

  Expected: 三个文件均存在。

- [ ] **Step 3: Commit**

  ```bash
  git add frontend/index.html
  git commit -m "feat(frontend): add favicon links"
  ```

---

## Task 7: 端到端验证

**Files:**
- 无新增文件
- Test: 手动 / 命令行验证

**Interfaces:**
- Consumes: 前面所有任务产物
- Produces: 验证报告

- [ ] **Step 1: 重新运行图标生成脚本**

  Run:
  ```bash
  cd f:/python_project/novel
  python scripts/generate_icons.py
  ```

  Expected: 8 条 `Generated ...` 日志，无报错。

- [ ] **Step 2: 检查生成的 ICO 尺寸**

  Run:
  ```bash
  cd f:/python_project/novel
  python -c "
  import struct
  for path in ['assets/icon.ico', 'frontend/public/favicon.ico']:
      with open(path, 'rb') as f:
          _, _, count = struct.unpack('<HHH', f.read(6))
          sizes = set()
          for _ in range(count):
              entry = f.read(16)
              width = entry[0]
              sizes.add(width if width != 0 else 256)
          print(path, count, sizes)
  "
  ```

  Expected:
  ```
  assets/icon.ico 4 {16, 32, 48, 256}
  frontend/public/favicon.ico 2 {16, 32}
  ```

- [ ] **Step 3: 检查 PNG 尺寸**

  Run:
  ```bash
  cd f:/python_project/novel
  python -c "
  from PIL import Image
  from pathlib import Path
  for size in [16, 32, 48, 256, 512, 1024]:
      img = Image.open(f'assets/icon-{size}.png')
      assert img.size == (size, size), img.size
      print(f'icon-{size}.png OK')
  "
  ```

  Expected: 6 行 `icon-{size}.png OK`。

- [ ] **Step 4: 前端 dev server 验证 favicon**

  Run:
  ```bash
  cd frontend
  npm run dev
  ```

  在浏览器打开 `http://127.0.0.1:5173/`，确认标签页显示新 favicon（可能需要清空缓存或强制刷新）。

- [ ] **Step 5: 桌面客户端验证窗口图标**

  Run:
  ```bash
  cd backend
  python desktop_launcher.py
  ```

  确认 Windows 任务栏与窗口标题栏显示新图标。

- [ ] **Step 6: PyInstaller 构建验证（可选但推荐）**

  Run:
  ```bash
  build-exe.bat
  ```

  构建完成后检查 `dist-exe/NovelStudio/NovelStudio.exe` 的属性 → 详细信息/图标，确认使用了新图标。

- [ ] **Step 7: Commit 生成产物（由实现者决定）**

  图标 PNG/ICO 是构建产物，可选是否入仓。建议入仓，以便 clone 后无需安装 Pillow 即可直接构建 PyInstaller。如入仓：

  ```bash
  git add assets/*.png assets/icon.ico frontend/public/favicon.*
  git commit -m "assets: generate icon PNGs and ICOs"
  ```

---

## Self-Review

**Spec coverage：**
- SVG 源文件 → Task 2 / Task 3。
- PNG 尺寸 16/32/48/256/512/1024 → Task 3。
- ICO（16/32/48/256）→ Task 3。
- favicon ICO + PNG → Task 3。
- PyInstaller 接入 → Task 4。
- pywebview 窗口图标 → Task 5。
- 前端 favicon → Task 6。
- 色彩规范 → Task 3 几何常量保证。

**Placeholder scan：**
- 无 TBD/TODO。
- 所有代码块包含完整代码。
- 所有命令包含预期输出。

**Type一致性：**
- `Path` 对象在 `scripts/generate_icons.py` 中统一使用。
- `webview.create_window(icon=...)` 接收 `str | None`。
- Pillow `ImageDraw` 调用一致。

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-26-icon-implementation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
