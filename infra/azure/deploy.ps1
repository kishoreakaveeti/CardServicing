#Requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath)
. "$PSScriptRoot/common.ps1"
$c = Read-Deployment $ConfigPath
Assert-Tools -Docker
if (-not (Test-Path -LiteralPath $c.SshPrivateKey) -or -not (Test-Path -LiteralPath ($c.SshPrivateKey + '.pub'))) { throw 'Create an SSH key pair first; see README.' }
if (-not (Test-Path -LiteralPath $c.AnthropicKeyFile)) { throw 'AnthropicKeyFile is missing.' }
$key = (Get-Content -LiteralPath $c.AnthropicKeyFile -Raw).Trim()
if ($key -notmatch '^[A-Za-z0-9_-]{20,}$') { throw 'AnthropicKeyFile must contain only a valid API key, without quotes.' }
Write-DeploymentStage 'Stage 3/9: verifying Azure login, subscription and VM sizing.'
Assert-Login
Write-Host "Verified subscription: $($c.SubscriptionId). Application group: $($c.ResourceGroup)."
Write-Warning 'Provisioning incurs VM, disk, static IP and Blob charges. Deallocate stops compute; destroy removes deployment resources.'
Assert-DeploymentVmSize
# Complete actual Linux preflight before incurring any resource charges.
Write-DeploymentStage 'Stage 4/9: LOCAL Docker preflight. Downloads/builds can take a long time on the first run. This run has not provisioned Azure resources yet.'
& "$PSScriptRoot/validate.ps1"
if (-not $?) { throw 'Local Linux validation failed; no resources have been provisioned.' }
Write-DeploymentStage 'Stage 5/9: local preflight passed; now creating/reusing the Azure resource group. Resources can begin appearing in the portal from this point.'
$group = Get-OwnedGroup
if (-not $group) {
    Invoke-Az -Arguments @('group','create','-n',$c.ResourceGroup,'-l',$c.Location,
        '--tags',('cardservicingDeployment=' + $c.DeploymentId),'application=cardservicing-demo','managed-by=cardservicing-scripts') | Out-Null
}
Assert-ExpectedResources
$rg = $c.ResourceGroup
Write-DeploymentStage 'Stage 6/9: provisioning networking, static public IP/DNS and Ubuntu VM. Azure operations can take several minutes.'
# Named resources and ownership-checked group make reruns converge on this demo.
Invoke-Az -Arguments @('network','vnet','create','-g',$rg,'-n',($c.VmName + '-vnet'),'-l',$c.Location,
    '--address-prefixes','10.71.0.0/16','--subnet-name','application','--subnet-prefixes','10.71.1.0/24') | Out-Null
Invoke-Az -Arguments @('network','nsg','create','-g',$rg,'-n',($c.VmName + '-nsg'),'-l',$c.Location) | Out-Null
Invoke-Az -Arguments @('network','nsg','rule','create','-g',$rg,'--nsg-name',($c.VmName + '-nsg'),'-n','SSH-operator',
    '--priority','100','--access','Allow','--protocol','Tcp','--direction','Inbound','--source-address-prefixes',$c.SshSourceCidr,
    '--destination-port-ranges','22') | Out-Null
Invoke-Az -Arguments @('network','nsg','rule','create','-g',$rg,'--nsg-name',($c.VmName + '-nsg'),'-n','HTTPS',
    '--priority','110','--access','Allow','--protocol','Tcp','--direction','Inbound','--source-address-prefixes','Internet',
    '--destination-port-ranges','443') | Out-Null
Invoke-Az -Arguments @('network','public-ip','create','-g',$rg,'-n',($c.VmName + '-ip'),'-l',$c.Location,
    '--sku','Standard','--allocation-method','Static','--version','IPv4') | Out-Null
if ($c.PSObject.Properties['DnsLabel']) {
    $pip = Invoke-Az @('network','public-ip','update','-g',$rg,'-n',($c.VmName + '-ip'),'--dns-name',$c.DnsLabel)
    if ($pip.dnsSettings.fqdn -ne $c.AppDomain) { throw 'Unexpected Azure DNS hostname.' }
}
Invoke-Az @('network','nsg','rule','create','-g',$rg,'--nsg-name',($c.VmName + '-nsg'),'-n','HTTP',
    '--priority','120','--access','Allow','--protocol','Tcp','--direction','Inbound','--source-address-prefixes','Internet',
    '--destination-port-ranges','80') | Out-Null
Invoke-Az -Arguments @('network','nic','create' ,'-g',$rg,'-n',($c.VmName + '-nic'),'-l',$c.Location,
    '--vnet-name',($c.VmName + '-vnet'),'--subnet','application','--network-security-group',($c.VmName + '-nsg'),
    '--public-ip-address',($c.VmName + '-ip')) | Out-Null
$vms = @(Invoke-Az -Arguments @('vm','list','-g',$rg))
if (-not ($vms | Where-Object name -eq $c.VmName)) {
    Invoke-Az -Arguments @('vm','create','-g',$rg,'-n',$c.VmName,'-l',$c.Location,
        '--image','Canonical:ubuntu-24_04-lts:server:latest','--size',$c.VmSize,
        '--nics',($c.VmName + '-nic'),'--admin-username',$c.AdminUsername,
        '--authentication-type','ssh','--ssh-key-values',($c.SshPrivateKey + '.pub'),
        '--assign-identity','--security-type','TrustedLaunch','--enable-secure-boot','true','--enable-vtpm','true',
        '--os-disk-name',($c.VmName + '-os'),'--storage-sku','StandardSSD_LRS','--os-disk-size-gb',([string]$c.OsDiskSizeGB)) | Out-Null
} else {
    Invoke-Az -Arguments @('vm','start','-g',$rg,'-n',$c.VmName) | Out-Null
    Invoke-Az -Arguments @('vm','identity','assign','-g',$rg,'-n',$c.VmName) | Out-Null
}
Write-DeploymentStage 'Stage 7/9: configuring VM identity, private Blob storage and scoped permissions.'
$vm = Invoke-Az -Arguments @('vm','show','-g',$rg,'-n',$c.VmName)
Invoke-Az -Arguments @('storage','account','create','-g',$rg,'-n',$c.StorageAccount,'-l',$c.Location,
    '--sku','Standard_LRS','--kind','StorageV2','--https-only','true','--min-tls-version','TLS1_2',
    '--allow-blob-public-access','false','--allow-shared-key-access','false') | Out-Null
$account = Invoke-Az -Arguments @('storage','account','show','-g',$rg,'-n',$c.StorageAccount)
foreach ($resource in @(Invoke-Az @('resource','list','-g',$rg))) {
    Invoke-Az @('tag','update','--resource-id',$resource.id,'--operation','Merge','--tags',
        ('cardservicingDeployment=' + $c.DeploymentId),'application=cardservicing-demo','managed-by=cardservicing-scripts') | Out-Null
}
# Container creation through ARM avoids requiring the operator's storage data-plane role.
$containerId = $account.id + '/blobServices/default/containers/' + $c.StorageContainer
$containerBody = Join-Path $script:StateDir 'container.json'
[IO.File]::WriteAllText($containerBody, '{"properties":{"publicAccess":"None"}}', [Text.UTF8Encoding]::new($false))
Invoke-Az -Arguments @('rest','--method','put','--url',($containerId + '?api-version=2023-05-01'),
    '--body',('@' + $containerBody)) | Out-Null
$assignments = @(Invoke-Az -Arguments @('role','assignment','list','--scope',$containerId,
    '--assignee-object-id',$vm.identity.principalId,'--fill-principal-name','false','--role','Storage Blob Data Contributor'))
if ($assignments.Count -eq 0) {
    Invoke-Az -Arguments @('role','assignment','create','--assignee-object-id',$vm.identity.principalId,
        '--assignee-principal-type','ServicePrincipal','--role','Storage Blob Data Contributor','--scope',$containerId) | Out-Null
}
$address = Get-VmAddress
Write-Host "VM address: $address. HTTPS hostname: $($c.AppDomain). DNS must resolve only to this address."
Write-Host 'Retrieving SSH host key through Azure and waiting for SSH.'
Set-TrustedHost $address
Wait-Ssh $address
$archive = Join-Path $script:StateDir 'release.tar.gz'
Write-DeploymentStage 'Stage 8/9: packaging/uploading source and deploying containers on the VM. Remote installation/build output follows; keep this terminal open.'
# Explicit artifact allowlist; no local .env, databases, keys, Git or virtualenv.
Invoke-Native 'tar' @('-czf',$archive,'--exclude=frontend/node_modules','--exclude=frontend/dist',
    '--exclude=frontend/.env*','--exclude=__pycache__','-C',$script:RepoRoot,
    'Dockerfile','Dockerfile.fincrime-api','Dockerfile.fincrime-agent','pyproject.toml','README.md','AZURE_DEPLOYMENT.md',
    'compose.azure.yaml','src','policies','docs/cards.openapi.json','frontend','infra/azure',
    'scripts/setup_stack.py','scripts/service_entrypoint.py') | Out-Null
$bootstrap = Join-Path $script:StateDir 'bootstrap.json'
try {
    $settings = @{AppDomain=$c.AppDomain; IdentityDomain=$c.IdentityDomain; AcmeEmail=$c.AcmeEmail;
        StorageAccount=$c.StorageAccount; StorageContainer=$c.StorageContainer; ModelId=$c.ModelId;
        AnthropicApiKey=$key; DeploymentId=$c.DeploymentId; PublicIp=$address}
    [IO.File]::WriteAllText($bootstrap, ($settings | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    # Transfer into the operator's private SSH home, then move under root ownership.
    Invoke-Remote $address 'umask 077; mkdir -p .cardservicing-upload; chmod 700 .cardservicing-upload' | Out-Null
    Send-File $address $archive '.cardservicing-upload/release.tar.gz'
    Send-File $address $bootstrap '.cardservicing-upload/bootstrap.json'
    Send-File $address (Join-Path $PSScriptRoot 'bootstrap.sh') '.cardservicing-upload/bootstrap.sh'
    Invoke-Remote $address 'sudo bash .cardservicing-upload/bootstrap.sh' -Stream
} finally {
    $key = $null
    if (Test-Path -LiteralPath $bootstrap) { Remove-Item -LiteralPath $bootstrap -Force }
}
Write-DeploymentStage 'Stage 9/9: verifying public HTTPS, login, banking workflows, dependencies and persistence.'
& "$PSScriptRoot/smoke-test.ps1" -ConfigPath $ConfigPath
Write-DeploymentStage 'Deployment completed: required health checks passed.'
Write-Host "Application: https://$($c.AppDomain)"
Write-Host "Identity: https://$($c.IdentityDomain)"
Write-CostReminder
