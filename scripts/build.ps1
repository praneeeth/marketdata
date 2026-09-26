param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"

function Invoke-CheckedCommand {
    param([string]$Command, [string[]]$Arguments)

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
    }
}

foreach ($command in "node", "pnpm", "docker") {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is required"
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$staticDirectory = Join-Path $projectRoot "static"
$imageName = if ($env:IMAGE_NAME) { $env:IMAGE_NAME } else { "candlewise" }
$fullImage = "${imageName}:$Version"

Push-Location $projectRoot
try {
    Write-Host "🚀 Candlewise build script"
    Write-Host "Version: $Version"

    Write-Host "📦 Building the frontend..."
    Push-Location "frontend"
    try {
        Invoke-CheckedCommand "pnpm" @("install", "--frozen-lockfile")
        Invoke-CheckedCommand "pnpm" @("build")
    }
    finally {
        Pop-Location
    }

    Write-Host "📁 Copying static files..."
    Remove-Item -LiteralPath $staticDirectory -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $staticDirectory | Out-Null
    Copy-Item -Path (Join-Path $projectRoot "frontend\dist\*") -Destination $staticDirectory -Recurse -Force

    Write-Host "🐳 Building the Docker image (linux/amd64)..."
    Invoke-CheckedCommand "docker" @("build", "--platform", "linux/amd64", "--build-arg", "VERSION=$Version", "-t", $fullImage, ".")

    if ($Version -ne "latest") {
        Invoke-CheckedCommand "docker" @("tag", $fullImage, "${imageName}:latest")
        Write-Host "✅ Image built: $fullImage and ${imageName}:latest"
    }
    else {
        Write-Host "✅ Image built: $fullImage"
    }
}
finally {
    Remove-Item -LiteralPath $staticDirectory -Recurse -Force -ErrorAction SilentlyContinue
    Pop-Location
}
