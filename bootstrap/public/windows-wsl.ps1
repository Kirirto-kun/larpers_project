# Embedded by the public Windows starter. Definitions only: no host changes on load.
# Official commands: https://learn.microsoft.com/windows/wsl/basic-commands
function Get-CampusWslNativePath {
    $windows = [Environment]::GetFolderPath('Windows')
    $systemDirectory = 'System32'
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        $systemDirectory = 'Sysnative'
    }
    $path = [IO.Path]::Combine($windows, $systemDirectory, 'wsl.exe')
    if (-not [IO.File]::Exists($path)) {
        throw 'Microsoft wsl.exe is unavailable. Install current Windows updates, then run this same Campus starter again. WSL setup: https://learn.microsoft.com/windows/wsl/install'
    }
    return $path
}

function Get-CampusWslVersion([string]$WslPath) {
    # Version checks neither start a distribution nor stop a running WSL VM.
    # Inbox WSL reports its unsupported --version option on stderr. Treat that
    # as an old version, rather than allowing PS5's native stderr record to abort.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $WslPath --version 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($code -ne 0) { return $null }
    # wsl.exe emits UTF-16 when redirected. PS5/PS7 may decode those bytes with
    # the console encoding; normalize the interleaved NULs before parsing.
    $text = ($output -join "`n").Replace([string][char]0, '')
    $match = [regex]::Match($text, '(?<![\d.])(\d+\.\d+\.\d+(?:\.\d+)?)(?![\d.])')
    if (-not $match.Success) { return $null }
    return [version]$match.Groups[1].Value
}

function Get-CampusWslFeatures {
    $features = @(Get-CimInstance -ClassName Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform' OR Name='Microsoft-Windows-Subsystem-Linux'" -ErrorAction Stop)
    $virtualMachinePlatform = $false
    $legacySubsystem = $false
    foreach ($feature in $features) {
        if ($feature.Name -eq 'VirtualMachinePlatform') { $virtualMachinePlatform = $feature.InstallState -eq 1 }
        if ($feature.Name -eq 'Microsoft-Windows-Subsystem-Linux') { $legacySubsystem = $feature.InstallState -eq 1 }
    }
    # Modern WSL2 needs VirtualMachinePlatform. The other optional feature is
    # needed by WSL1 only; do not enable WSL1 merely to run Docker's WSL2 backend.
    return [pscustomobject]@{ VirtualMachinePlatform = $virtualMachinePlatform; LegacySubsystem = $legacySubsystem }
}

function Assert-CampusWslVirtualization {
    $system = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
    if ($system.HypervisorPresent -eq $true) { return }
    $processors = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop)
    if (-not $processors.Count) { throw 'Unable to check hardware virtualization for WSL2.' }
    foreach ($processor in $processors) {
        if ($processor.VirtualizationFirmwareEnabled -eq $false) {
            throw 'WSL2 requires hardware virtualization. Enable Intel VT-x or AMD SVM in BIOS/UEFI, then run this same Campus starter again.'
        }
    }
}

function Ensure-CampusWSL([string]$StateDir) {
    # StateDir belongs to the caller's private cache. This helper deliberately
    # persists nothing and sends no caller paths, invitations or secrets to UAC.
    # Exactly one pipeline result: 0 ready; 3010 restart Windows and rerun.
    $wsl = Get-CampusWslNativePath
    $features = Get-CampusWslFeatures
    $version = Get-CampusWslVersion $wsl
    Assert-CampusWslVirtualization
    $minimum = [version]'2.1.5'
    if ($features.VirtualMachinePlatform -and $version -and $version -ge $minimum) {
        Write-Host ('WSL2 is ready (WSL ' + $version.ToString() + ').')
        return 0
    }

    if (-not $features.VirtualMachinePlatform) {
        $arguments = @('--install', '--no-distribution', '--web-download', '--no-launch')
        Write-Host 'Installing Microsoft WSL2 components. Windows will ask for administrator approval.'
    } else {
        $arguments = @('--update', '--web-download')
        Write-Host 'Updating Microsoft WSL2. Windows will ask for administrator approval.'
    }
    try {
        # Elevate only Windows' own wsl.exe with fixed machine-setup arguments.
        # The original, non-elevated starter resumes after this process exits.
        $process = Start-Process -FilePath $wsl -ArgumentList $arguments -Verb RunAs -PassThru
    } catch {
        $exception = $_.Exception
        while ($exception) {
            if ($exception -is [ComponentModel.Win32Exception] -and $exception.NativeErrorCode -eq 1223) {
                throw 'WSL2 setup was cancelled at Windows administrator approval. Run this same Campus starter again when ready.'
            }
            $exception = $exception.InnerException
        }
        throw
    }
    if (-not $process.WaitForExit(1800000)) {
        throw 'WSL2 setup has not finished after 30 minutes. Complete the Microsoft setup window, then run this same Campus starter again.'
    }
    $code = $process.ExitCode
    if ($code -eq 3010 -or $code -eq 1641) {
        Write-Host 'WSL2 setup requires a Windows restart. Restart when convenient, then run this same Campus starter again.'
        return 3010
    }
    if ($code -ne 0) { throw ('Microsoft WSL2 setup failed with exit code ' + $code + '. Resolve the message in the setup window, then run this same Campus starter again.') }
    $features = Get-CampusWslFeatures
    $version = Get-CampusWslVersion $wsl
    if ($features.VirtualMachinePlatform -and $version -and $version -ge $minimum) {
        Write-Host ('WSL2 is ready (WSL ' + $version.ToString() + ').')
        return 0
    }
    Write-Host 'WSL2 components were installed. Restart Windows, then run this same Campus starter again.'
    return 3010
}
