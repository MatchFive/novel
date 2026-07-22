@echo off
chcp 65001 > nul
setlocal

REM Novel Studio 可执行文件打包脚本
REM 运行前请确保已安装后端依赖：cd backend && uv pip install -r requirements.txt pyinstaller

cd /d "%~dp0"

echo [1/2] 构建前端...
cd frontend
call npm run build
if errorlevel 1 (
    echo 前端构建失败
    exit /b 1
)
cd ..

echo [2/2] 打包可执行文件...
backend\.venv\Scripts\pyinstaller ^
    --name "NovelStudio" ^
    --onedir ^
    --windowed ^
    --clean ^
    --noconfirm ^
    --distpath dist-exe ^
    --workpath build-exe ^
    --add-data "frontend/dist;frontend/dist" ^
    --hidden-import aiosqlite ^
    --hidden-import aiosqlite.core ^
    --hidden-import sqlalchemy.dialects.sqlite ^
    backend\desktop_launcher.py

if errorlevel 1 (
    echo 打包失败
    exit /b 1
)

echo.
echo 打包完成：dist-exe\NovelStudio\NovelStudio.exe
echo 首次运行会在 exe 同级目录创建 data 文件夹存放数据库。
endlocal
