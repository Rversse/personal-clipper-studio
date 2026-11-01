[CmdletBinding()]
param([string]$ArtifactDirectory = "build/windows-artifacts")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ApiUrl = "http://127.0.0.1:8765"
$ProductName = "Personal Clipper Studio"
$ExecutableNames = @("personal-clipper-studio.exe", "Personal Clipper Studio.exe")
$TempRoot = Join-Path $env:TEMP ("pcs-installer-smoke-" + [guid]::NewGuid().ToString("N"))
$DataRoot = Join-Path $TempRoot "app-data"
New-Item -ItemType Directory -Force -Path $TempRoot, $DataRoot | Out-Null
$env:PCS_DATA_DIR = $DataRoot

function Test-LoopbackPort {
    param([int]$Port = 8765)
    $client = [System.Net.Sockets.TcpClient]::new()
    try { $client.Connect("127.0.0.1", $Port); return $true }
    catch { return $false }
    finally { $client.Dispose() }
}

function Wait-ForPortClosed {
    param([int]$TimeoutSeconds = 30)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Test-LoopbackPort)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Local API port 8765 remained open after closing the desktop process."
}

function Get-UninstallEntries {
    $registryRoots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($root in $registryRoots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($key in (Get-ChildItem $root -ErrorAction SilentlyContinue)) {
            $entry = Get-ItemProperty $key.PSPath -ErrorAction SilentlyContinue
            if ($null -eq $entry) { continue }
            $nameProperty = $entry.PSObject.Properties["DisplayName"]
            if ($null -ne $nameProperty -and $nameProperty.Value -like "$ProductName*") { $entry }
        }
    }
}

function Find-InstalledApp {
    $entries = @(Get-UninstallEntries)
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($entry in $entries) {
        $locationProperty = $entry.PSObject.Properties["InstallLocation"]
        if ($null -ne $locationProperty -and $locationProperty.Value) {
            foreach ($name in $ExecutableNames) {
                $candidates.Add((Join-Path ([string]$locationProperty.Value) $name))
            }
        }
        $iconProperty = $entry.PSObject.Properties["DisplayIcon"]
        if ($null -ne $iconProperty -and $iconProperty.Value) {
            $iconPath = ([string]$iconProperty.Value -split ",", 2)[0].Trim().Trim('"')
            if ($iconPath) { $candidates.Add($iconPath) }
        }
    }

    $knownDirectories = @(
        (Join-Path $env:LOCALAPPDATA "Programs\$ProductName"),
        (Join-Path $env:LOCALAPPDATA $ProductName),
        (Join-Path $env:ProgramFiles $ProductName)
    )
    foreach ($directory in $knownDirectories) {
        foreach ($name in $ExecutableNames) {
            $candidates.Add((Join-Path $directory $name))
        }
    }

    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    if (Test-Path $startMenu) {
        $shell = New-Object -ComObject WScript.Shell
        foreach ($shortcut in (Get-ChildItem $startMenu -Filter "*.lnk" -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*Clipper*" })) {
            $target = $shell.CreateShortcut($shortcut.FullName).TargetPath
            if ($target) { $candidates.Add($target) }
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [pscustomobject]@{
                Executable = (Resolve-Path -LiteralPath $candidate).Path
                Entries = $entries
            }
        }
    }
    return $null
}

function Wait-ForInstalledApp {
    param([int]$TimeoutSeconds = 45)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $found = Find-InstalledApp
        if ($null -ne $found) { return $found }
        Start-Sleep -Milliseconds 500
    }
    $displayNames = @(Get-UninstallEntries | ForEach-Object { $_.PSObject.Properties["DisplayName"].Value }) -join "; "
    throw "Installed application executable was not found. Registered products: $displayNames"
}

function Invoke-CheckedProcess {
    param(
        [string]$FilePath,
        [string]$ArgumentLine,
        [int]$TimeoutSeconds = 180,
        [int[]]$AcceptedExitCodes = @(0)
    )
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentLine -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "Timed out running '$FilePath $ArgumentLine'."
    }
    if ($process.ExitCode -notin $AcceptedExitCodes) {
        throw "Command failed with exit code $($process.ExitCode): '$FilePath $ArgumentLine'."
    }
    return $process.ExitCode
}

function Test-InstalledApplication {
    param([string]$InstallerLabel, [string]$ExecutablePath)
    if (Test-LoopbackPort) { throw "Port 8765 is already occupied before testing $InstallerLabel." }

    $desktopProcess = $null
    try {
        $desktopProcess = Start-Process -FilePath $ExecutablePath -WorkingDirectory (Split-Path -Parent $ExecutablePath) -PassThru
        $deadline = [DateTime]::UtcNow.AddSeconds(60)
        $healthy = $false
        while ([DateTime]::UtcNow -lt $deadline) {
            if ($desktopProcess.HasExited) {
                throw "$InstallerLabel desktop process exited early with code $($desktopProcess.ExitCode)."
            }
            try {
                $health = Invoke-RestMethod -Uri "$ApiUrl/api/health" -TimeoutSec 2
                if ($health.status -eq "ok" -and $health.service -eq "personal-clipper-local-api") {
                    $healthy = $true
                    break
                }
            }
            catch { Start-Sleep -Milliseconds 500 }
        }
        if (-not $healthy) { throw "$InstallerLabel did not start its bundled local API within 60 seconds." }

        $payload = @{
            name = "$InstallerLabel installation smoke test"
            description = "Created by the ephemeral Windows installer verification."
        } | ConvertTo-Json -Compress
        $created = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/projects" -ContentType "application/json" -Body $payload -TimeoutSec 10
        if (-not $created.id) { throw "$InstallerLabel API did not return a project ID after project creation." }

        $projects = @(Invoke-RestMethod -Uri "$ApiUrl/api/projects" -TimeoutSec 10)
        if (-not ($projects | Where-Object { $_.id -eq $created.id })) {
            throw "$InstallerLabel SQLite project was not present in the project list."
        }

        $backend = Get-CimInstance Win32_Process -Filter "Name='pcs-api.exe'" |
            Where-Object { $_.ParentProcessId -eq $desktopProcess.Id } |
            Select-Object -First 1
        if ($null -eq $backend) {
            throw "$InstallerLabel health endpoint responded, but the managed pcs-api.exe child was not found."
        }

        Write-Host "$InstallerLabel install/launch/API/SQLite smoke test passed (desktop PID $($desktopProcess.Id), backend PID $($backend.ProcessId))."
    }
    finally {
        if ($null -ne $desktopProcess -and -not $desktopProcess.HasExited) {
            & taskkill.exe /PID $desktopProcess.Id /T /F | Out-Null
            try { Wait-Process -Id $desktopProcess.Id -Timeout 15 -ErrorAction SilentlyContinue } catch {}
        }
        Wait-ForPortClosed
    }
}

function Uninstall-NsisApplication {
    param($Installation)
    $uninstaller = Join-Path (Split-Path -Parent $Installation.Executable) "uninstall.exe"
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        $entry = $Installation.Entries | Select-Object -First 1
        $uninstallProperty = if ($null -ne $entry) { $entry.PSObject.Properties["UninstallString"] } else { $null }
        if ($null -ne $uninstallProperty -and $uninstallProperty.Value -match '"([^"]+\.exe)"') {
            $uninstaller = $Matches[1]
        }
    }
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        throw "NSIS uninstaller was not found for $($Installation.Executable)."
    }
    Invoke-CheckedProcess -FilePath $uninstaller -ArgumentLine "/S" | Out-Null
}

function Wait-ForUninstall {
    param([int]$TimeoutSeconds = 30)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($null -eq (Find-InstalledApp)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Personal Clipper Studio still appears installed after the uninstall command."
}

try {
    $artifactPath = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
    $nsis = Get-ChildItem $artifactPath -Filter "*-setup.exe" -File | Select-Object -First 1
    $msi = Get-ChildItem $artifactPath -Filter "*.msi" -File | Select-Object -First 1
    if ($null -eq $nsis -or $null -eq $msi) {
        throw "Both NSIS setup.exe and MSI installer files must exist in $artifactPath."
    }

    Write-Host "Installing NSIS package: $($nsis.Name)"
    Invoke-CheckedProcess -FilePath $nsis.FullName -ArgumentLine "/S" | Out-Null
    $nsisInstall = Wait-ForInstalledApp
    Test-InstalledApplication -InstallerLabel "NSIS" -ExecutablePath $nsisInstall.Executable
    Uninstall-NsisApplication -Installation $nsisInstall
    Wait-ForUninstall
    Wait-ForPortClosed
    Write-Host "NSIS install and uninstall passed."

    Write-Host "Installing MSI package: $($msi.Name)"
    $msiLog = Join-Path $TempRoot "msi-install.log"
    $msiArguments = '/i "' + $msi.FullName + '" /qn /norestart /L*v "' + $msiLog + '"'
    Invoke-CheckedProcess -FilePath "msiexec.exe" -ArgumentLine $msiArguments -TimeoutSeconds 240 -AcceptedExitCodes @(0, 3010) | Out-Null
    $msiInstall = Wait-ForInstalledApp
    Test-InstalledApplication -InstallerLabel "MSI" -ExecutablePath $msiInstall.Executable

    $uninstallArguments = '/x "' + $msi.FullName + '" /qn /norestart'
    Invoke-CheckedProcess -FilePath "msiexec.exe" -ArgumentLine $uninstallArguments -TimeoutSeconds 180 -AcceptedExitCodes @(0, 1605, 3010) | Out-Null
    Wait-ForUninstall
    Wait-ForPortClosed
    Write-Host "MSI install and uninstall passed."

    Write-Host "Both Windows installer formats passed install, desktop launch, bundled API, SQLite, and uninstall checks."
}
finally {
    if (Test-LoopbackPort) {
        Get-CimInstance Win32_Process -Filter "Name='pcs-api.exe'" |
            Where-Object { $_.CommandLine -like "*--port 8765*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\PCS_DATA_DIR -ErrorAction SilentlyContinue
}
