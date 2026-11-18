[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools
Assert-Login
$address = Get-VmAddress
$networkReport = @()
$unexpected = @()
foreach ($port in @(8000,8080,8081,3306,6333,6334,27017,5672,6379)) {
    $tcp = New-Object Net.Sockets.TcpClient
    try {
        $attempt = $tcp.BeginConnect($address, $port, $null, $null)
        if ($attempt.AsyncWaitHandle.WaitOne(1000) -and $tcp.Connected) { $unexpected += $port }
    } finally { $tcp.Dispose() }
}
$networkReport += @{check='external-port-isolation';status=$(if ($unexpected.Count) {'FAIL'} else {'PASS'});
    evidence=$(if ($unexpected.Count) {'Unexpected public TCP ports: ' + ($unexpected -join ',')} else {'Operator-side probes found no public backend, database, vector or broker ports'})}
$nsg = Invoke-Az -Arguments @('network','nsg','show','-g',$c.ResourceGroup,'-n',($c.VmName + '-nsg'))
$badRules = @($nsg.securityRules | Where-Object { $_.direction -eq 'Inbound' -and $_.access -eq 'Allow' -and $_.name -notin @('SSH-operator','HTTPS','HTTP') })
function Rule-Values($Rule, [string[]]$Names) {
    foreach ($name in $Names) {
        $property = $Rule.PSObject.Properties[$name]
        if ($property -and $property.Value) { $property.Value }
    }
}
$sshRule = $nsg.securityRules | Where-Object name -eq 'SSH-operator' | Select-Object -First 1
$httpsRule = $nsg.securityRules | Where-Object name -eq 'HTTPS' | Select-Object -First 1
$httpRule = $nsg.securityRules | Where-Object name -eq 'HTTP' | Select-Object -First 1
$correct = $false
if ($sshRule -and $httpsRule) {
    $sshSources = @(Rule-Values $sshRule @('sourceAddressPrefix','sourceAddressPrefixes'))
    $sshPorts = @(Rule-Values $sshRule @('destinationPortRange','destinationPortRanges'))
    $httpsPorts = @(Rule-Values $httpsRule @('destinationPortRange','destinationPortRanges'))
    $correct = $sshSources.Count -eq 1 -and $sshSources[0] -eq $c.SshSourceCidr -and
        $sshPorts.Count -eq 1 -and $sshPorts[0] -eq '22' -and
        $httpsPorts.Count -eq 1 -and $httpsPorts[0] -eq '443' -and
        $sshRule.protocol -eq 'Tcp' -and $httpsRule.protocol -eq 'Tcp' -and
        $sshRule.access -eq 'Allow' -and $httpsRule.access -eq 'Allow'
}
if ($httpRule) {
    $httpPorts = @(Rule-Values $httpRule @('destinationPortRange','destinationPortRanges'))
    $correct = $correct -and $httpPorts.Count -eq 1 -and $httpPorts[0] -eq '80' -and $httpRule.protocol -eq 'Tcp'
}
$networkReport += @{check='nsg-allowlist';status=$(if ($badRules.Count -or -not $correct) {'FAIL'} else {'PASS'});
    evidence=$(if ($badRules.Count -or -not $correct) {'SSH/HTTPS rules differ from the required allowlist'} else {'No additional custom inbound allow rules in the dedicated NSG'})}
$remoteFailed = $false
try { Invoke-Remote $address 'sudo bash /opt/cardservicing/app/infra/azure/control.sh verify' -Stream }
catch { $remoteFailed = $true; Write-Host 'Remote verification failed; collecting its evidence report.' }
$report = Invoke-Remote $address 'sudo cat /opt/cardservicing/state/smoke-report.json'
$combined = @(ConvertFrom-DeploymentSmokeReport ($report -join "`n")) + @(foreach ($row in $networkReport) { [pscustomobject]$row })
if ($remoteFailed) {
    $combined += [pscustomobject]@{check='remote-verification';status='FAIL';evidence='Remote checker exited unsuccessfully; inspect the failed checks and VM verification output.'}
}
$output = Join-Path $script:StateDir 'smoke-report.json'
[IO.File]::WriteAllText($output, (ConvertTo-Json -InputObject $combined -Depth 8), [Text.UTF8Encoding]::new($false))
Write-Host "Report: $output"
$failures = @($combined | Where-Object { $_.status -eq 'FAIL' })
foreach ($row in $failures) { Write-Host ("FAIL: {0} - {1}" -f $row.check,$row.evidence) }
if ($failures.Count) {
    throw ('Smoke verification failed: ' + (($failures | ForEach-Object { $_.check }) -join ', ') + '. Inspect ' + $output)
}
