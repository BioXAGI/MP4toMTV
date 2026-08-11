$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $root "tools"
$models = Join-Path $tools "models"
$zip = Join-Path $env:TEMP "whisper-bin-x64.zip"
New-Item -ItemType Directory -Force -Path $tools, $models | Out-Null

if (-not (Get-ChildItem -Path $tools -Recurse -Filter "whisper-cli.exe" -ErrorAction SilentlyContinue)) {
    Write-Host "Downloading whisper.cpp..."
    $release = Invoke-RestMethod "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -eq "whisper-bin-x64.zip" } | Select-Object -First 1
    if (-not $asset) { throw "The whisper.cpp Windows x64 release could not be found." }
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tools -Force
}

$model = Join-Path $models "ggml-small.bin"
if ((-not (Test-Path $model)) -or ((Get-Item $model).Length -lt 450000000)) {
    Write-Host "Downloading the multilingual Whisper small model (about 466 MB)..."
    & curl.exe -L --fail --retry 3 --continue-at - --output $model "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
    if ($LASTEXITCODE -ne 0) { throw "Whisper model download failed." }
}
if ((Get-Item $model).Length -lt 450000000) {
    throw "The Whisper model is incomplete. Run setup.ps1 again to resume it."
}

Write-Host "Setup complete."
Write-Host 'Run: python .\lyric_sync.py "video.mp4" "lyrics.txt"'
