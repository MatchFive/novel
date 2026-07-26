# Novel Studio 应用图标实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于已批准的图标设计规范，生成 SVG/PNG/ICO 图标资源，并将其接入 PyInstaller 可执行文件、pywebview 窗口和前端 favicon。

**Architecture:** 以 `assets/icon.svg` 为唯一矢量源，通过 `scripts/generate_icons.py` 批量渲染出各尺寸 PNG 与 ICO；PyInstaller 使用 `assets/icon.ico`，pywebview 窗口使用 `assets/icon-256.png`，前端使用 `frontend/public/favicon.*`。

**Tech Stack:** Python 3.11+、Pillow、cairosvg、PyInstaller、pywebview、React + Vite。

## Global Constraints

- 图标源文件必须唯一：`assets/icon.svg`。
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
- Produces: `backend/requirements.txt` 包含 `Pillow` 与 `cairosvg`

- [ ] **Step 1: 在 `backend/requirements.txt` 追加 Pillow 和 cairosvg**

  在文件末尾添加：

  ```text
  Pillow==10.4.0
  cairosvg==2.7.1
  ```

- [ ] **Step 2: 在 backend 虚拟环境中安装依赖**

  Run:
  ```bash
  cd backend
  pip install -r requirements.txt
  ```

  Expected: `Successfully installed Pillow-10.4.0 cairosvg-2.7.1`（或兼容版本）。

- [ ] **Step 3: 验证导入**

  Run:
  ```bash
  cd backend
  python -c "from PIL import Image; import cairosvg; print('ok')"
  ```

  Expected: 输出 `ok`。

- [ ] **Step 4: Commit**

  ```bash
  git add backend/requirements.txt
  git commit -m "chore(deps): add Pillow and cairosvg for icon generation"
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
- Consumes: `assets/icon.svg`
- Produces: `assets/icon-{size}.png`（size ∈ {16,32,48,256,512,1024}）、`assets/icon.ico`、`frontend/public/favicon.ico`、`frontend/public/favicon-16x16.png`、`frontend/public/favicon-32x32.png`

- [ ] **Step 1: 编写 `scripts/generate_icons.py`**

  ```python
  """基于 assets/icon.svg 生成各尺寸 PNG 与 ICO 图标。"""
  from __future__ import annotations

  import struct
  from pathlib import Path

  import cairosvg
  from PIL import Image

  REPO_ROOT = Path(__file__).parent.parent
  SVG_PATH = REPO_ROOT / "assets" / "icon.svg"
  ASSETS_DIR = REPO_ROOT / "assets"
  PUBLIC_DIR = REPO_ROOT / "frontend" / "public"

  SIZES = [16, 32, 48, 256, 512, 1024]


  def render_png(size: int, output: Path) -> None:
      output.parent.mkdir(parents=True, exist_ok=True)
      cairosvg.svg2png(
          url=str(SVG_PATH),
          write_to=str(output),
          output_width=size,
          output_height=size,
      )


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
              width, height = entry[0], entry[1]
              sizes.add(width if width != 0 else 256)
          assert sizes == expected_sizes, f"ICO sizes {sizes} != {expected_sizes}"


  def main() -> None:
      pngs = []
      for size in SIZES:
          png_path = ASSETS_DIR / f"icon-{size}.png"
          render_png(size, png_path)
          pngs.append(png_path)
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
  git commit -m "feat(icons): add icon generation script and tests"
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
          _, count, _ = struct.unpack('<HHH', f.read(6))
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

  图标 PNG/ICO 是构建产物，可选是否入仓。建议入仓，以便 clone 后无需安装 cairosvg 即可直接构建 PyInstaller。如入仓：

  ```bash
  git add assets/*.png assets/icon.ico frontend/public/favicon.*
  git commit -m "assets: generate icon PNGs and ICOs"
  ```

---

## Self-Review

**Spec coverage：**
- SVG 源文件 → Task 2。
- PNG 尺寸 16/32/48/256/512/1024 → Task 3。
- ICO（16/32/48/256）→ Task 3。
- favicon ICO + PNG → Task 3。
- PyInstaller 接入 → Task 4。
- pywebview 窗口图标 → Task 5。
- 前端 favicon → Task 6。
- 色彩规范 → Task 2 SVG 与 Task 3 渲染结果共同保证。

**Placeholder scan：**
- 无 TBD/TODO。
- 所有代码块包含完整代码。
- 所有命令包含预期输出。

**Type consistency：**
- `Path` 对象在 `scripts/generate_icons.py` 中统一使用。
- `webview.create_window(icon=...)` 接收 `str | None`。
- `PIL.Image.open` 与 `cairosvg.svg2png` 调用一致。

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-26-icon-implementation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
