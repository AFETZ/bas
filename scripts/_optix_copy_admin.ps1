# Elevated copy: extracted driver files → C:\Windows\System32\lxss\lib\
# Called via Start-Process -Verb RunAs.

$src = "\\wsl.localhost\Ubuntu-Restore\tmp\optix_install_32218\driver-dist"
$dst = "C:\Windows\System32\lxss\lib"

if (-not (Test-Path $src)) {
    Write-Host "[!] source dir missing: $src" -ForegroundColor Red
    Write-Host "Press Enter to close..."
    [Console]::ReadLine()
    exit 1
}

Write-Host "==> Copying OptiX libs from $src → $dst"
$files = Get-ChildItem -Path $src -File
foreach ($f in $files) {
    $target = Join-Path $dst $f.Name
    try {
        Copy-Item -Path $f.FullName -Destination $target -Force -ErrorAction Stop
        Write-Host "  [+] $($f.Name)  ($([math]::Round($f.Length/1MB,1)) MB)" -ForegroundColor Green
    } catch {
        Write-Host "  [!] $($f.Name) failed: $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "==> Verify destination:"
Get-ChildItem -Path $dst | Where-Object { $_.Name -match 'nvoptix|ptxjit|rtcore|gpucomp' } | Format-Table Name, Length

Write-Host ""
Write-Host "==> Next: from regular shell, run 'wsl --shutdown' then re-open WSL."
Write-Host "Press Enter to close..."
[Console]::ReadLine()
