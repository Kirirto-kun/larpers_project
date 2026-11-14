# Definitions only. Docker is a manual prerequisite; WSL/NVIDIA setup is opt-in
# through running this starter and the explicit GPU answer below.
function Find-CampusDockerDesktop {
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += [IO.Path]::Combine($env:LOCALAPPDATA, 'Programs', 'DockerDesktop') }
    if ($env:ProgramW6432) { $roots += [IO.Path]::Combine($env:ProgramW6432, 'Docker', 'Docker') }
    if ($env:ProgramFiles) { $roots += [IO.Path]::Combine($env:ProgramFiles, 'Docker', 'Docker') }
    foreach ($root in $roots) {
        $desktop = [IO.Path]::Combine($root, 'Docker Desktop.exe')
        $bin = [IO.Path]::Combine($root, 'resources', 'bin')
        if ([IO.File]::Exists($desktop) -and [IO.File]::Exists([IO.Path]::Combine($bin, 'docker.exe'))) {
            return [pscustomobject]@{ Bin=$bin; Desktop=$desktop }
        }
    }
    throw 'Install Docker Desktop manually from https://docs.docker.com/desktop/setup/install/windows-install/ and run this same Campus file again.'
}

function Initialize-CampusWindows([string]$StateDir, [string]$Architecture) {
    $docker = Find-CampusDockerDesktop
    $devices = @(Get-CampusNVIDIADevices)
    $gpu = $false
    if ($devices.Count) {
        foreach ($device in $devices) { Write-Host ('NVIDIA hardware: ' + $device.Name) }
        $answer = ([string](Read-Host 'Enable NVIDIA setup and GPU computation? [y/N]')).Trim().ToLowerInvariant()
        switch ($answer) {
            { $_ -in @('y','yes') } { $gpu=$true; break }
            { $_ -in @('','n','no') } { $gpu=$false; break }
            default { throw 'Enter y or n. No prerequisite installer was started.' }
        }
    }
    $wsl = Ensure-CampusWSL -StateDir $StateDir
    if ($wsl -ne 0 -and $wsl -ne 3010) { throw 'Unexpected WSL setup result; device was not connected.' }
    $driver = 0
    if ($gpu) { $driver = Ensure-CampusNVIDIA -StateDir $StateDir -Architecture $Architecture }
    if ($driver -ne 0 -and $driver -ne 3010) { throw 'Unexpected NVIDIA setup result; device was not connected.' }
    return [pscustomobject]@{ DockerBin=$docker.Bin; GPU=$gpu; RestartRequired=($wsl -eq 3010 -or $driver -eq 3010) }
}
