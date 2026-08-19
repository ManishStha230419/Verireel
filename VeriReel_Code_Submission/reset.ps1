$ErrorActionPreference = "Stop"

$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\', '/')
$rootPrefix = $projectRoot + [IO.Path]::DirectorySeparatorChar
$venvRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot ".venv"))
$venvPrefix = $venvRoot + [IO.Path]::DirectorySeparatorChar

function Remove-VeriReelItem {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $target = [IO.Path]::GetFullPath((Join-Path $projectRoot $RelativePath))
    if (-not $target.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the VeriReel folder: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "[REMOVED] $RelativePath"
    }
}

Write-Host "[INFO] Stopping VeriReel processes from this folder..."
$processIds = [Collections.Generic.HashSet[int]]::new()

# Find both the launcher executable and a managed-Python child whose command
# line points back to this project's isolated environment.
try {
    foreach ($process in (Get-CimInstance Win32_Process -ErrorAction Stop)) {
        $insideEnvironment = $false
        if ($process.ExecutablePath) {
            try {
                $executable = [IO.Path]::GetFullPath($process.ExecutablePath)
                $insideEnvironment = $executable.StartsWith(
                    $venvPrefix,
                    [StringComparison]::OrdinalIgnoreCase
                )
            } catch {
                $insideEnvironment = $false
            }
        }
        $usesEnvironment = $process.CommandLine -and $process.CommandLine.IndexOf(
            (Join-Path $venvRoot "Scripts\waitress-serve.exe"),
            [StringComparison]::OrdinalIgnoreCase
        ) -ge 0
        if ($insideEnvironment -or $usesEnvironment) {
            [void]$processIds.Add([int]$process.ProcessId)
        }
    }
} catch {
    # Get-Process still identifies the normal virtual-environment executables
    # on systems where detailed command-line inspection is restricted.
    foreach ($process in (Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if ($process.Path -and [IO.Path]::GetFullPath($process.Path).StartsWith(
                $venvPrefix,
                [StringComparison]::OrdinalIgnoreCase
            )) {
                [void]$processIds.Add([int]$process.Id)
            }
        } catch {
            continue
        }
    }
}

foreach ($processId in $processIds) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}
if ($processIds.Count -gt 0) {
    Start-Sleep -Milliseconds 500
    Write-Host "[OK] Stopped $($processIds.Count) VeriReel process(es)."
} else {
    Write-Host "[OK] VeriReel was not running."
}

Write-Host "[INFO] Removing downloaded runtime files and local data..."
foreach ($relativePath in @(
    ".venv",
    ".bootstrap",
    ".env",
    "logs",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov"
)) {
    Remove-VeriReelItem $relativePath
}

# Remove Python caches nested below source folders.
Get-ChildItem -LiteralPath $projectRoot -Directory -Filter "__pycache__" -Recurse -Force -ErrorAction SilentlyContinue |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
Get-ChildItem -LiteralPath $projectRoot -File -Filter "*.pyc" -Recurse -Force -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

# Preserve the tracked placeholder while clearing all temporary job data.
$tempDirectory = Join-Path $projectRoot "temp"
if (Test-Path -LiteralPath $tempDirectory) {
    Get-ChildItem -LiteralPath $tempDirectory -Force |
        Where-Object { $_.Name -ne ".gitkeep" } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
}
Get-ChildItem -LiteralPath $projectRoot -File -Filter "flask_*.txt" -Force -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

Write-Host "[OK] Fresh-start cleanup complete. Source code and setup files were preserved."
