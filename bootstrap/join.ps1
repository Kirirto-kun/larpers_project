[CmdletBinding()]
param(
    [string]$ReleaseBaseUrl,
    [string]$Sha256,
    [string]$Invite,
    [string]$CacheDirectory,
    [string[]]$JoinArguments = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$bootstrapTemporaryFile = $null
$bootstrapExitCode = 1

function Stop-CampusBootstrap {
    param([string]$Message)
    throw [InvalidOperationException]::new('campus bootstrap: ' + $Message)
}

function Assert-CampusNoLinks {
    param([string]$Path)
    $bootstrapCheckedPath = [IO.Path]::GetFullPath($Path)
    while (-not [string]::IsNullOrEmpty($bootstrapCheckedPath)) {
        try {
            $bootstrapItem = Get-Item -LiteralPath $bootstrapCheckedPath -Force -ErrorAction Stop
            if (($bootstrapItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                Stop-CampusBootstrap 'cache paths must not contain symlinks or reparse points'
            }
        }
        catch [System.Management.Automation.ItemNotFoundException] { }
        $bootstrapCheckedParent = [IO.Path]::GetDirectoryName($bootstrapCheckedPath)
        if ($bootstrapCheckedParent -eq $bootstrapCheckedPath) { break }
        $bootstrapCheckedPath = $bootstrapCheckedParent
    }
}

try {
    if ([string]::IsNullOrEmpty($ReleaseBaseUrl) -or [string]::IsNullOrEmpty($Sha256) -or [string]::IsNullOrEmpty($Invite)) {
        Stop-CampusBootstrap 'release base URL, trusted SHA256, and invite are required'
    }
    if ($Sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        Stop-CampusBootstrap 'trusted SHA256 must contain exactly 64 hexadecimal characters'
    }
    $bootstrapExpectedSha = $Sha256.ToLowerInvariant()
    $bootstrapReleaseUri = $null
    if ($ReleaseBaseUrl -notmatch '^https://[^/]' -or $ReleaseBaseUrl -match '[^\x21-\x7e]' -or $ReleaseBaseUrl.Contains('\') -or
        -not [Uri]::TryCreate($ReleaseBaseUrl, [UriKind]::Absolute, [ref]$bootstrapReleaseUri) -or
        $bootstrapReleaseUri.Scheme -ne 'https' -or [string]::IsNullOrEmpty($bootstrapReleaseUri.Host) -or
        -not [string]::IsNullOrEmpty($bootstrapReleaseUri.UserInfo) -or
        -not [string]::IsNullOrEmpty($bootstrapReleaseUri.Query) -or -not [string]::IsNullOrEmpty($bootstrapReleaseUri.Fragment)) {
        Stop-CampusBootstrap 'release base URL must be HTTPS with a host, no credentials, query, fragment, or backslash'
    }

    $bootstrapWindows = [Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([Runtime.InteropServices.OSPlatform]::Windows)
    if ($bootstrapWindows) {
        $bootstrapOS = 'windows'
        $bootstrapSuffix = '.exe'
    }
    elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([Runtime.InteropServices.OSPlatform]::Linux)) {
        $bootstrapOS = 'linux'
        $bootstrapSuffix = ''
    }
    elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([Runtime.InteropServices.OSPlatform]::OSX)) {
        $bootstrapOS = 'darwin'
        $bootstrapSuffix = ''
    }
    else { Stop-CampusBootstrap 'unsupported operating system' }
    switch ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()) {
        'X64' { $bootstrapArch = 'amd64' }
        'Arm64' { $bootstrapArch = 'arm64' }
        default { Stop-CampusBootstrap 'unsupported architecture; native amd64 or arm64 is required' }
    }

    if ([string]::IsNullOrEmpty($CacheDirectory)) {
        if ($bootstrapWindows) {
            $bootstrapCacheBase = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
        }
        elseif (-not [string]::IsNullOrEmpty($env:XDG_CACHE_HOME)) { $bootstrapCacheBase = $env:XDG_CACHE_HOME }
        else { $bootstrapCacheBase = [IO.Path]::Combine([Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile), '.cache') }
        $CacheDirectory = [IO.Path]::Combine($bootstrapCacheBase, 'campus', 'bin')
    }
    $CacheDirectory = [IO.Path]::GetFullPath($CacheDirectory)
    Assert-CampusNoLinks $CacheDirectory
    [IO.Directory]::CreateDirectory($CacheDirectory) | Out-Null
    Assert-CampusNoLinks $CacheDirectory
    if ($bootstrapWindows) {
        $bootstrapUser = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $bootstrapExistingACL = Get-Acl -LiteralPath $CacheDirectory
        if ($bootstrapExistingACL.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $bootstrapUser.Value) {
            Stop-CampusBootstrap 'cache must belong to the current user'
        }
        $bootstrapACL = [Security.AccessControl.DirectorySecurity]::new()
        $bootstrapACL.SetOwner($bootstrapUser)
        $bootstrapACL.SetAccessRuleProtection($true, $false)
        $bootstrapInheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
        $bootstrapRule = [Security.AccessControl.FileSystemAccessRule]::new($bootstrapUser, [Security.AccessControl.FileSystemRights]::FullControl,
            $bootstrapInheritance, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
        $bootstrapACL.AddAccessRule($bootstrapRule)
        Set-Acl -LiteralPath $CacheDirectory -AclObject $bootstrapACL
    }
    else {
        & chmod '700' '--' $CacheDirectory
        if ($LASTEXITCODE -ne 0) { Stop-CampusBootstrap 'cannot restrict cache access' }
    }

    $bootstrapBinaryName = "campus-$bootstrapOS-$bootstrapArch$bootstrapSuffix"
    $bootstrapBinaryPath = [IO.Path]::Combine($CacheDirectory, "campus-$bootstrapOS-$bootstrapArch-$bootstrapExpectedSha$bootstrapSuffix")
    Assert-CampusNoLinks $bootstrapBinaryPath
    if ([IO.File]::Exists($bootstrapBinaryPath)) {
        if ((Get-FileHash -LiteralPath $bootstrapBinaryPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $bootstrapExpectedSha) {
            Stop-CampusBootstrap 'cached runner checksum mismatch; remove that cache entry and retry'
        }
    }
    else {
        if ([IO.Directory]::Exists($bootstrapBinaryPath)) { Stop-CampusBootstrap 'cache entry is not a regular file' }
        $bootstrapTemporaryFile = [IO.Path]::Combine($CacheDirectory, '.campus-download-' + [Guid]::NewGuid().ToString('N'))
        $bootstrapReservation = [IO.File]::Open($bootstrapTemporaryFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $bootstrapReservation.Dispose()
        # Windows PowerShell uses ServicePointManager; modern PowerShell also
        # receives an explicit TLS protocol through Invoke-WebRequest.
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $bootstrapDownload = @{
            Uri = $bootstrapReleaseUri.AbsoluteUri.TrimEnd('/') + '/' + $bootstrapBinaryName
            OutFile = $bootstrapTemporaryFile
            MaximumRedirection = 0
            UseBasicParsing = $true
            PassThru = $true
            TimeoutSec = 120
            ErrorAction = 'Stop'
        }
        if ($PSVersionTable.PSVersion.Major -ge 6) { $bootstrapDownload.SslProtocol = 'Tls12' }
        $bootstrapResponse = Invoke-WebRequest @bootstrapDownload
        if ([int]$bootstrapResponse.StatusCode -ne 200) { Stop-CampusBootstrap 'runner download must return HTTP 200; redirects are refused' }
        if ((Get-Item -LiteralPath $bootstrapTemporaryFile).Length -gt 134217728) { Stop-CampusBootstrap 'runner download exceeds 128 MiB' }
        if ((Get-FileHash -LiteralPath $bootstrapTemporaryFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $bootstrapExpectedSha) {
            Stop-CampusBootstrap 'downloaded runner checksum mismatch'
        }
        Assert-CampusNoLinks $bootstrapBinaryPath
        [IO.File]::Move($bootstrapTemporaryFile, $bootstrapBinaryPath)
        $bootstrapTemporaryFile = $null
    }
    if (-not $bootstrapWindows) {
        & chmod '700' '--' $bootstrapBinaryPath
        if ($LASTEXITCODE -ne 0) { Stop-CampusBootstrap 'cannot make verified runner executable' }
    }
    # The call operator plus an array passes literal argv; no shell expression
    # is constructed from the invite or the owner's additional join options.
    & $bootstrapBinaryPath 'join' $Invite @JoinArguments
    $bootstrapExitCode = $LASTEXITCODE
}
catch {
    if ($_.Exception.Message.StartsWith('campus bootstrap: ')) { [Console]::Error.WriteLine($_.Exception.Message) }
    else { [Console]::Error.WriteLine('campus bootstrap: failed to download, verify, or launch runner') }
    $bootstrapExitCode = 1
}
finally {
    if (-not [string]::IsNullOrEmpty($bootstrapTemporaryFile)) {
        Remove-Item -LiteralPath $bootstrapTemporaryFile -Force -ErrorAction SilentlyContinue
    }
}
exit $bootstrapExitCode
