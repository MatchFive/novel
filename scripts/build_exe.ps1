# Build exe (safe: keeps user data/ and config.toml in dist)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1

$ErrorActionPreference = "Stop"
$dist = "dist\NovelStudio"

# 运行中的应用会锁文件导致打包失败，先结束（有未保存内容请先自行关闭）
$running = Get-Process NovelStudio -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "NovelStudio 正在运行，先结束进程……"
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# Backup user data if present
$backup = $null
if (Test-Path "$dist\data") {
    $backup = Join-Path $env:TEMP ("ns_backup_" + (Get-Random))
    Copy-Item "$dist\data" $backup -Recurse -Force
    Write-Host "Backed up user data -> $backup"
}

# Clean only build artifacts (exe and _internal), keep data/ and config.toml
if (Test-Path $dist) {
    Remove-Item "$dist\NovelStudio.exe" -Force -ErrorAction SilentlyContinue
    Remove-Item "$dist\_internal" -Recurse -Force -ErrorAction SilentlyContinue
}

# 排除重型可选依赖（本机装了但没被项目使用；打进去会让体积爆炸到 GB 级。
# 语义嵌入走 fastembed + onnxruntime，不需要 torch/transformers）
$excludes = @(
    "torch", "torchaudio", "torch_complex", "transformers", "sentence_transformers",
    "scipy", "numba", "llvmlite", "soundfile", "librosa", "sklearn", "tensorboard"
)
$excludeArgs = $excludes | ForEach-Object { "--exclude-module", $_ }

python -m PyInstaller --noconfirm --clean --windowed --name NovelStudio `
    --icon "resources\icon.ico" --add-data "resources;resources" `
    --hidden-import qasync --collect-submodules langgraph @excludeArgs main.py

if ($LASTEXITCODE -ne 0) { Write-Host "BUILD FAILED"; exit 1 }

# Restore user data and config
if ($backup) {
    Copy-Item "$backup" "$dist\data" -Recurse -Force
    Remove-Item $backup -Recurse -Force
    Write-Host "Restored user data -> $dist\data"
}
if (Test-Path "config.toml") { Copy-Item "config.toml" "$dist\config.toml" -Force }

Write-Host "BUILD OK -> $dist\NovelStudio.exe"
