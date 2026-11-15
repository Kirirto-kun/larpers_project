# Embedded by the public Windows starter after Assert-NoLinks / Protect-Directory.
# This installs a WINDOWS host driver only. WSL uses that driver; never install a
# Linux display driver inside WSL. Caller obtains GPU consent before calling Ensure.
# Verified 2026-11-14 against NVIDIA's own driver details JSON and supported list:
# https://www.nvidia.com/en-us/drivers/details/283969/ (621.14 WHQL, notebooks)
# https://www.nvidia.com/services/com.nvidia.services/AEMDriversContent/getDownloadDetails?{"ddID":"283969"}
# PCI model identity (not a source for Linux installation):
# https://download.nvidia.com/XFree86/Linux-x86_64/580.119.02/README/supportedchips.html
# 576.88 first adds RTX 5050 desktop/laptop support:
# https://www.nvidia.com/download/driverResults.aspx/248978/1000/
# NVIDIA documents silent switches for EXTRACTED setup.exe, not a reliable fully
# unattended extraction of the downloaded SFX. Launch the signed vendor GUI with
# no guessed switches; do not request a clean install or automatic reboot.
# https://nvidia.custhelp.com/app/answers/detail/a_id/2985/
# https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/latest/windows.html

function Get-CampusNVIDIADevices {
    $entities = @(Get-CimInstance -ClassName Win32_PnPEntity -Filter "PNPDeviceID LIKE 'PCI\\VEN_10DE%'" -ErrorAction Stop)
    $videos = @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop)
    $found = @{}
    foreach ($entity in @($entities) + @($videos)) {
        $id = [string]$entity.PNPDeviceID
        if (-not $id) { $id = [string]$entity.DeviceID }
        if ($id -notmatch '^PCI\\VEN_10DE&DEV_([0-9A-F]{4})(?:&|\\)') { continue }
        $deviceCode = $Matches[1].ToUpperInvariant()
        if ($entity.PSObject.Properties['Present'] -and $entity.Present -eq $false) { continue }
        $isVideo = @($videos | Where-Object { $_.PNPDeviceID -eq $id }).Count -gt 0
        $isDisplay = $entity.PNPClass -eq 'Display' -or
            $entity.ClassGuid -eq '{4d36e968-e325-11ce-bfc1-08002be10318}' -or
            @($entity.CompatibleID | Where-Object { $_ -match '^PCI\\(?:VEN_10DE.*&)?CC_03' }).Count -gt 0
        # These exact PCI IDs identify the laptop even before Windows assigns a
        # class/driver. The desktop 5050 (2D83) is deliberately not mapped here.
        if (-not $isDisplay -and -not $isVideo -and $deviceCode -notin @('2D98', '2DD8')) { continue }
        if (-not $found.ContainsKey($id)) {
            $found[$id] = [pscustomobject]@{
                PNPDeviceID = $id; DeviceID = $id; PCIID = ('10DE:' + $deviceCode)
                DeviceCode = $deviceCode; Name = [string]$entity.Name
                ConfigManagerErrorCode = $entity.ConfigManagerErrorCode
                DriverVersion = [string]$entity.DriverVersion
            }
        } else {
            if ($entity.Name) { $found[$id].Name = [string]$entity.Name }
            if ($entity.DriverVersion) { $found[$id].DriverVersion = [string]$entity.DriverVersion }
            if ($null -ne $entity.ConfigManagerErrorCode) { $found[$id].ConfigManagerErrorCode = $entity.ConfigManagerErrorCode }
        }
    }
    foreach ($id in @($found.Keys | Sort-Object)) { $found[$id] }
}

function ConvertTo-CampusNvidiaVersion([string]$Value) {
    if ($Value -match '^\d+\.\d+$') { return [version]$Value }
    # Microsoft Basic Display Adapter uses 10.x Windows build versions; those
    # are not NVIDIA's 2x/3x WDDM driver-version encoding.
    if ($Value -match '^[2-9]\d\.\d+\.(\d+)\.(\d+)$') {
        # Windows 32.0.16.2114 is NVIDIA 621.14 (last five decimal digits).
        $number = (([long]$Matches[1] * 10000) + [long]$Matches[2]) % 100000
        return [version](([math]::Floor($number / 100)).ToString() + '.' + ($number % 100).ToString())
    }
    return $null
}

function Get-CampusNvidiaSystemDirectory {
    $directory = 'System32'
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) { $directory = 'Sysnative' }
    return [IO.Path]::Combine([Environment]::GetFolderPath('Windows'), $directory)
}

function Invoke-CampusNvidiaSmi {
    $windows = [Environment]::GetFolderPath('Windows')
    $candidates = @((Join-Path (Get-CampusNvidiaSystemDirectory) 'nvidia-smi.exe'))
    $programs = [Environment]::GetEnvironmentVariable('ProgramW6432', 'Process')
    if ($programs) { $candidates += (Join-Path $programs 'NVIDIA Corporation\NVSMI\nvidia-smi.exe') }
    foreach ($path in $candidates) {
        if (-not [IO.File]::Exists($path)) { continue }
        try {
            $lines = @(& $path '--query-gpu=name,driver_version,pci.device_id' '--format=csv,noheader,nounits' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $lines.Count -gt 0) { return $lines }
        } catch { }
    }
    return $null
}

function Test-CampusNvidiaCompute([object[]]$Devices) {
    $lines = @(Invoke-CampusNvidiaSmi)
    if ($lines.Count -eq 0) { return $false }
    foreach ($device in $Devices) {
        $installed = ConvertTo-CampusNvidiaVersion $device.DriverVersion
        if (-not $installed -or $installed -lt [version]'576.88' -or $null -eq $device.ConfigManagerErrorCode -or [int]$device.ConfigManagerErrorCode -ne 0) { return $false }
        $matched = $false
        foreach ($line in $lines) {
            if ([string]$line -match '^\s*.+?,\s*(\d+\.\d+)\s*,\s*(0x[0-9A-F]{8})\s*$') {
                if ([version]$Matches[1] -ge [version]'576.88' -and $Matches[2] -eq ('0x' + $device.DeviceCode + '10DE')) { $matched = $true }
            }
        }
        if (-not $matched) { return $false }
    }
    return $true
}

function Assert-CampusNvidiaPrivateFile([string]$Path) {
    Assert-NoLinks $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer) { throw 'NVIDIA cache payload must be a private regular file' }
    $sections = [Security.AccessControl.AccessControlSections]::Access -bor [Security.AccessControl.AccessControlSections]::Owner
    if ($PSVersionTable.PSEdition -eq 'Desktop') { $acl = [IO.File]::GetAccessControl($Path, $sections) }
    else { $acl = [IO.FileSystemAclExtensions]::GetAccessControl([IO.FileInfo]::new($Path), $sections) }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid -or $rules.Count -ne 1 -or
        $rules[0].IdentityReference.Value -ne $sid -or $rules[0].AccessControlType -ne 'Allow' -or
        $rules[0].FileSystemRights -ne 'FullControl') {
        throw 'NVIDIA cache files must be owned by and accessible only to the current Windows user'
    }
}

function Assert-CampusNvidiaPackage([string]$Path) {
    Assert-CampusNvidiaPrivateFile $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or $item.Length -le 0 -or $item.Length -gt 3221225472) { throw 'NVIDIA package has an invalid size (maximum 3 GiB)' }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction Stop
    $subject = [string]$signature.SignerCertificate.Subject
    if ([string]$signature.Status -ne 'Valid' -or [string]$signature.SignatureType -ne 'Authenticode' -or
        $subject -notmatch '(?:^|,\s*)O=(?:"NVIDIA Corporation"|NVIDIA Corporation)(?:,|$)' -or
        $subject -notmatch '(?:^|,\s*)CN=(?:"NVIDIA Corporation"|NVIDIA Corporation)(?:,|$)') {
        throw 'NVIDIA package signature is invalid or is not signed by NVIDIA Corporation'
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}

function Ensure-CampusNVIDIA([string]$StateDir, [string]$Architecture) {
    $devices = @(Get-CampusNVIDIADevices)
    if ($devices.Count -eq 0) {
        Write-Host 'No physical NVIDIA display GPU detected; no NVIDIA changes are needed.'
        return 0
    }
    if (Test-CampusNvidiaCompute $devices) {
        Write-Host 'The installed NVIDIA Windows driver responds correctly. Campus will verify CUDA in Docker.'
        return 0
    }
    $official = 'https://www.nvidia.com/en-us/drivers/'
    if ($Architecture -ne 'amd64') { throw ('Automatic NVIDIA driver installation requires native Windows amd64. Use your OEM or ' + $official) }
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    if ([int]$os.BuildNumber -lt 22000 -or [int]$os.ProductType -ne 1) { throw ('Automatic NVIDIA driver installation supports Windows 11 only. Use your OEM or ' + $official) }
    foreach ($device in $devices) {
        if ($device.DeviceCode -notin @('2D98', '2DD8')) {
            throw ('No verified automatic driver mapping for NVIDIA ' + $device.PCIID + ' (' + $device.Name + '). Use your notebook manufacturer or ' + $official)
        }
        $installed = ConvertTo-CampusNvidiaVersion $device.DriverVersion
        if ($installed -and $installed -ge [version]'576.88' -and [int]$device.ConfigManagerErrorCode -eq 14) {
            Write-Host 'Windows reports that the NVIDIA driver needs a restart. Restart Windows, then run Connect again.'
            return 3010
        }
        if ($installed -and $installed -ge [version]'621.14') {
            throw 'A current or newer NVIDIA driver is installed, but nvidia-smi is not operational. Restart Windows and retry; repair the driver through your notebook manufacturer or https://www.nvidia.com/en-us/drivers/ if needed. Campus will not downgrade it.'
        }
    }

    $packageUrl = 'https://us.download.nvidia.com/Windows/621.14/621.14-notebook-win10-win11-64bit-international-dch-whql.exe'
    $uri = [uri]$packageUrl
    if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'us.download.nvidia.com' -or -not $uri.IsDefaultPort -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) { throw 'Untrusted NVIDIA package URL' }
    Protect-Directory $StateDir
    $cache = Join-Path $StateDir 'nvidia'
    Protect-Directory $cache
    $package = Join-Path $cache '621.14-notebook-win10-win11-64bit-international-dch-whql.exe'
    $receiptPath = Join-Path $cache '621.14-notebook.receipt.json'
    Assert-NoLinks $package
    Assert-NoLinks $receiptPath
    if ((Test-Path -LiteralPath $package) -and (Test-Path -LiteralPath $receiptPath)) {
        Assert-CampusNvidiaPrivateFile $receiptPath
        $receiptItem = Get-Item -LiteralPath $receiptPath -Force
        if ($receiptItem.PSIsContainer -or $receiptItem.Length -gt 16384) { throw 'Invalid cached NVIDIA package receipt' }
        $receipt = [IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
        $sha = Assert-CampusNvidiaPackage $package
        if ($receipt.url -ne $packageUrl -or $receipt.sha256 -notmatch '^[a-f0-9]{64}$' -or $receipt.sha256 -ne $sha -or
            [long]$receipt.size_bytes -ne (Get-Item -LiteralPath $package).Length) {
            throw 'Cached NVIDIA package checksum/receipt mismatch; the installer was not launched'
        }
    } else {
        $temporary = Join-Path $cache ('.download-' + [guid]::NewGuid().ToString('N') + '.exe')
        try {
            Write-Host 'Downloading NVIDIA 621.14 WHQL for GeForce RTX 5050 Laptop (about 1 GB)...'
            # Native Windows curl uses the system certificate store. Do not pass
            # the Campus CA, disable TLS/revocation checks, or follow redirects.
            $curl = Join-Path (Get-CampusNvidiaSystemDirectory) 'curl.exe'
            $curlArguments = @('--fail', '--silent', '--show-error', '--proto', '=https', '--proto-redir', '=https', '--tlsv1.2', '--max-redirs', '0', '--connect-timeout', '30', '--max-time', '1800', '--max-filesize', '3221225472', '--output', $temporary, '--write-out', '%{http_code}', '--', $packageUrl)
            $status = & $curl @curlArguments
            if ($LASTEXITCODE -eq 130 -or $LASTEXITCODE -eq -1073741510 -or $LASTEXITCODE -eq 3221225786) { throw 'NVIDIA download cancelled' }
            if ($LASTEXITCODE -ne 0 -or [string]$status -ne '200') { throw 'NVIDIA driver download failed or returned an unexpected HTTP status' }
            $sha = Assert-CampusNvidiaPackage $temporary
            if (Test-Path -LiteralPath $package) { Remove-Item -LiteralPath $package -Force }
            [IO.File]::Move($temporary, $package)
            $receipt = @{url=$packageUrl; sha256=$sha; size_bytes=(Get-Item -LiteralPath $package).Length; version='621.14'; source='https://www.nvidia.com/en-us/drivers/details/283969/'; publisher='NVIDIA Corporation'}
            [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Compress))
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        }
    }

    # Recheck the Windows file digest/signature immediately before elevation.
    $verifiedSha = Assert-CampusNvidiaPackage $package
    if ($verifiedSha -ne $receipt.sha256) { throw 'NVIDIA package changed before installation' }
    Write-Host 'Opening the official NVIDIA setup. Approve Windows UAC and finish the NVIDIA driver wizard; choose Restart later if offered. Then return here.'
    try {
        $process = Start-Process -FilePath $package -Verb RunAs -Wait -PassThru -ErrorAction Stop
    } catch [System.ComponentModel.Win32Exception] {
        if ($_.Exception.NativeErrorCode -eq 1223) { throw 'NVIDIA installation cancelled at Windows approval; rerun Connect when ready' }
        throw
    }
    # NVIDIA documents 1 as success/reboot; Windows installers can also return
    # ERROR_SUCCESS_REBOOT_REQUIRED (3010) or ERROR_SUCCESS_REBOOT_INITIATED (1641).
    if ([int]$process.ExitCode -in @(1, 3010, 1641)) {
        Write-Host 'NVIDIA setup requires a restart. Restart Windows, then run Connect again.'
        return 3010
    }
    if ([int]$process.ExitCode -ne 0) { throw ('NVIDIA setup did not finish successfully (exit ' + $process.ExitCode + '). Finish or retry the official installer before continuing.') }
    $updated = @(Get-CampusNVIDIADevices)
    if ($updated.Count -eq 0 -or -not (Test-CampusNvidiaCompute $updated)) {
        if (@($updated | Where-Object { $_.ConfigManagerErrorCode -eq 14 }).Count -gt 0) {
            Write-Host 'Windows reports that the new NVIDIA driver needs a restart. Restart Windows and run Connect again.'
            return 3010
        }
        throw 'NVIDIA setup closed, but a working driver was not detected. Finish the NVIDIA driver wizard, restart Windows if requested, and run Connect again.'
    }
    Write-Host 'The NVIDIA Windows driver responds correctly. Campus will now verify Docker CUDA support.'
    return 0
}
