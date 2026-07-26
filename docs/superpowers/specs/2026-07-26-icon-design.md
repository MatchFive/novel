# Novel Studio 应用图标设计

**日期**：2026-07-26  
**范围**：桌面可执行文件图标、pywebview 窗口图标、前端 favicon  
**决策**：采用「书页 + 双起伏叙事弧线」方案（方案 B2）

## 设计目标

为 Novel Studio 生成一套统一的应用图标，用于：

1. Windows 可执行文件（`NovelStudio.exe`）图标。
2. 桌面客户端启动后，pywebview 窗口在任务栏与标题栏显示的图标。
3. 前端在浏览器标签页显示的 favicon。

图标需传达「长篇小说创作工具」的产品定位，而非通用文档或笔记应用。

## 最终方案

### 视觉概念

- **书页**：矩形书页轮廓与内页横线，直接指向「小说 / 书籍」。
- **双起伏弧线**：一条跨越书页的曲线，包含两次起伏，象征长篇小说的「起承转合」与情节张力。
- **纸墨暖色**：米白书页、深褐描边、砖红弧线，呼应纸质书与墨水的温暖质感。

### SVG 源码

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <rect x="6" y="10" width="52" height="44" rx="3" fill="#f5efe4" stroke="#6b4f3a" stroke-width="3"/>
  <path d="M12 46 Q18 50 24 42 T36 36 T52 28" fill="none" stroke="#a64b2a" stroke-width="3" stroke-linecap="round"/>
  <path d="M12 24h20M12 32h16" stroke="#8c6b4f" stroke-width="2" stroke-linecap="round"/>
</svg>
```

### 色彩规范

| 用途 | 色值 | 说明 |
|------|------|------|
| 书页底色 | `#f5efe4` | 暖米白，类似泛黄书页 |
| 书页描边 / 文字线 | `#6b4f3a` | 深褐色，模拟旧书封套与墨迹 |
| 叙事弧线 | `#a64b2a` | 砖红色，视觉焦点，墨水/印章色 |

## 交付物

| 文件 | 尺寸 / 格式 | 用途 |
|------|------------|------|
| `assets/icon.svg` | 64×64 viewBox，SVG | 矢量源文件，后续缩放基准 |
| `assets/icon-16.png` | 16×16 PNG | 任务栏小图标、列表图标 |
| `assets/icon-32.png` | 32×32 PNG | 窗口标题栏、快捷方式 |
| `assets/icon-48.png` | 48×48 PNG | 资源管理器中等图标 |
| `assets/icon-256.png` | 256×256 PNG | 资源管理器大图标、高 DPI |
| `assets/icon-512.png` | 512×512 PNG | 安装包 / 应用商店 |
| `assets/icon-1024.png` | 1024×1024 PNG | macOS .icns 源图 |
| `assets/icon.ico` | 多分辨率 ICO（16/32/48/256） | PyInstaller Windows 可执行文件 |
| `frontend/public/favicon.ico` | 多分辨率 ICO（16/32） | 浏览器标签页 |
| `frontend/public/favicon-32x32.png` | 32×32 PNG | 现代浏览器 favicon |
| `frontend/public/favicon-16x16.png` | 16×16 PNG | 现代浏览器 favicon |

## 接入位置

### 1. PyInstaller 可执行文件图标

修改 `NovelStudio.spec`，在 `EXE(...)` 块添加：

```python
exe = EXE(
    ...
    icon='assets/icon.ico',
    ...
)
```

### 2. pywebview 窗口图标

在 `backend/desktop_launcher.py` 创建窗口时设置图标。Windows 下通常使用 PNG/ICO 路径：

```python
window = webview.create_window(
    'Novel Studio',
    url,
    icon='assets/icon-256.png',  # 或 .ico
)
```

> 注意：pywebview 的 `icon` 参数在不同平台行为不同；Windows 推荐 ICO 或 256×256 PNG。

### 3. 前端 favicon

在 `frontend/index.html` 的 `<head>` 中添加：

```html
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
```

## 生成方式

使用 Python 脚本基于 SVG 批量生成 PNG 与 ICO：

- **PNG**：`cairosvg` 或 `svglib` + Pillow。
- **ICO**：Pillow 直接将多尺寸 PNG 写入 `.ico`。
- 不依赖外部设计软件，便于 CI / 重新生成。

## 未包含项

- macOS `.icns`：当前主要目标平台为 Windows；如后续需要 macOS 版本，可用 `iconutil` 由 1024×1024 PNG 生成。
- 动态 / 动画图标：超出当前范围。
- 启动画面（Splash Screen）：如需可在后续迭代中基于同一 SVG 扩展。

## 决策记录

- **放弃「翻开的书」**：过于通用，像阅读器或电子书应用。
- **放弃「稿纸 + 火漆印」**：火漆印更像信件/契约，与小说关联弱。
- **放弃「单起伏弧线」**：双起伏更能体现长篇小说的多幕结构。
- **选择砖红色作为焦点色**：在深褐与米白之间形成足够对比，同时保持暖色纸墨氛围。
