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

function Normalize-RegistryPath {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return "" }
    $expanded = [Environment]::ExpandEnvironmentVariables(([string]$Value).Trim())
    return $expanded.Trim().Trim('"')
}

function Find-InstalledApp {
    $entries = @(Get-UninstallEntries)
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($entry in $entries) {
        $locationProperty = $entry.PSObject.Properties["InstallLocation"]
        if ($null -ne $locationProperty -and $locationProperty.Value) {
            $installLocation = Normalize-RegistryPath $locationProperty.Value
            if ($installLocation) {
                foreach ($name in $ExecutableNames) {
                    $candidates.Add((Join-Path $installLocation $name))
                }
            }
        }
        $iconProperty = $entry.PSObject.Properties["DisplayIcon"]
        if ($null -ne $iconProperty -and $iconProperty.Value) {
            $iconPath = Normalize-RegistryPath (([string]$iconProperty.Value -split ",", 2)[0])
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

        # The installed desktop host owns the session token through Tauri IPC.
        # This external installer test must not bypass that boundary or invent a
        # second credential channel. Verify that the packaged API rejects callers
        # that do not possess the WebView session token.
        $unauthorized = $false
        try {
            $null = Invoke-RestMethod -Uri "$ApiUrl/api/projects" -TimeoutSec 10
        }
        catch {
            $statusCode = 0
            if ($null -ne $_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }
            if ($statusCode -ne 401) {
                throw
            }
            $unauthorized = $true
        }
        if (-not $unauthorized) {
            throw "$InstallerLabel API accepted an unauthenticated project request."
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
            $closeRequested = $false
            try { $closeRequested = $desktopProcess.CloseMainWindow() } catch {}
            if (-not $closeRequested) {
                & taskkill.exe /PID $desktopProcess.Id /T /F | Out-Null
                try { Wait-Process -Id $desktopProcess.Id -Timeout 15 -ErrorAction SilentlyContinue } catch {}
                throw "$InstallerLabel desktop app did not accept a normal window-close request."
            }
            if (-not $desktopProcess.WaitForExit(20000)) {
                & taskkill.exe /PID $desktopProcess.Id /T /F | Out-Null
                try { Wait-Process -Id $desktopProcess.Id -Timeout 15 -ErrorAction SilentlyContinue } catch {}
                throw "$InstallerLabel desktop app did not exit within 20 seconds after a normal close request."
            }
            if ($desktopProcess.ExitCode -ne 0) {
                throw "$InstallerLabel desktop app exited with code $($desktopProcess.ExitCode) after a normal close request."
            }
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
        $registered = @(Get-UninstallEntries)
        $installed = Find-InstalledApp
        if ($registered.Count -eq 0 -and $null -eq $installed) { return }
        Start-Sleep -Milliseconds 500
    }
    $names = @(Get-UninstallEntries | ForEach-Object { $_.PSObject.Properties["DisplayName"].Value }) -join "; "
    throw "Personal Clipper Studio still has installed files or uninstall registry entries: $names"
}

try {
    $artifactPath = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
    $installers = @(Get-ChildItem $artifactPath -Filter "*-setup.exe" -File)
    if ($installers.Count -ne 1) {
        throw "Expected exactly one NSIS setup.exe installer in $artifactPath; found $($installers.Count)."
    }
    $nsis = $installers[0]
    Write-Host "Installing single NSIS package: $($nsis.Name)"
    Invoke-CheckedProcess -FilePath $nsis.FullName -ArgumentLine "/S" | Out-Null
    $installation = Wait-ForInstalledApp
    Test-InstalledApplication -InstallerLabel "NSIS" -ExecutablePath $installation.Executable
    Uninstall-NsisApplication -Installation $installation
    Wait-ForUninstall
    Wait-ForPortClosed
    Write-Host "Single NSIS installer passed install, desktop launch, bundled API, SQLite, and uninstall checks."
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
