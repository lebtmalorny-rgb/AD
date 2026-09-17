# Offline behavioral tests: every AD/password command is replaced below.
$ErrorActionPreference = 'Stop'
$target = Join-Path (Split-Path $PSScriptRoot) 'Create-PvsAdAccounts.ps1'
$global:AdTestOu = 'OU=PVS,DC=example,DC=local'
function Reset-FakeAd {
    $global:AdTestObjects = @{}
    $global:AdTestMembers = @{}
    $global:AdTestWrites = 0
    $global:AdTestPrompts = 0
}
function Assert($condition, $message) { if (-not $condition) { throw $message } }
function Import-Module { param($Name) Assert ($Name -eq 'ActiveDirectory') 'Unexpected module' }
function Get-ADDomain { param($Server) [pscustomobject]@{ DNSRoot='example.local'; DistinguishedName='DC=example,DC=local' } }
function Get-ADOrganizationalUnit { param($Identity, $Server) [pscustomobject]@{ DistinguishedName=$Identity } }
function Get-ADObject {
    param($LDAPFilter, $SearchBase, $Server)
    $name = $LDAPFilter -replace '^\(sAMAccountName=|\)$', ''
    $global:AdTestObjects[$name]
}
function Get-ADGroup { param($Identity, $Server) $global:AdTestObjects.Values | Where-Object DistinguishedName -eq $Identity }
function Get-ADUser { param($Identity, $Server) $global:AdTestObjects.Values | Where-Object DistinguishedName -eq $Identity }
function Read-Host {
    param($Prompt, [switch]$AsSecureString)
    Assert $AsSecureString 'Password prompt must use SecureString'
    $global:AdTestPrompts++
    ConvertTo-SecureString 'Fake-test-only-42!' -AsPlainText -Force
}
function New-ADGroup {
    param($Name, $SamAccountName, $GroupCategory, $GroupScope, $Path, $Server, [switch]$PassThru)
    Assert ($GroupCategory -eq 'Security' -and $GroupScope -eq 'Global') 'Invalid group type'
    $g = [pscustomobject]@{ Name=$Name; ObjectClass='group'; GroupCategory=$GroupCategory; GroupScope=$GroupScope; DistinguishedName="CN=$Name,$Path" }
    $global:AdTestObjects[$SamAccountName] = $g
    $global:AdTestMembers[$g.DistinguishedName] = @()
    $global:AdTestWrites++
    $g
}
function New-ADUser {
    param($Name, $SamAccountName, $UserPrincipalName, $Path, $Server, $AccountPassword, $Enabled, $ChangePasswordAtLogon, [switch]$PassThru)
    Assert ($AccountPassword -is [System.Security.SecureString]) 'Password is not secure'
    Assert ($Enabled -eq $true -and $ChangePasswordAtLogon -eq $false) 'Unexpected account flags'
    $u = [pscustomobject]@{ Name=$Name; ObjectClass='user'; UserPrincipalName=$UserPrincipalName; DistinguishedName="CN=$Name,$Path" }
    $global:AdTestObjects[$SamAccountName] = $u
    $global:AdTestWrites++
    $u
}
function Get-ADGroupMember {
    param($Identity, $Server)
    $dn = if ($Identity -is [string]) { $Identity } else { $Identity.DistinguishedName }
    foreach ($member in $global:AdTestMembers[$dn]) { [pscustomobject]@{ DistinguishedName=$member } }
}
function Add-ADGroupMember {
    param($Identity, $Members, $Server)
    $dn = if ($Identity -is [string]) { $Identity } else { $Identity.DistinguishedName }
    $global:AdTestMembers[$dn] += $Members.DistinguishedName
    $global:AdTestWrites++
}
function Run-Script($scope = '') {
    if ($scope) { & $target -Server dc01.example.local -OU $global:AdTestOu -Scope $scope }
    else { & $target -Server dc01.example.local -OU $global:AdTestOu }
}

Reset-FakeAd
$result = @(Run-Script)
Assert ($result.Count -eq 4) 'Expected four default PVS/Grafana roles'
Assert ($global:AdTestObjects.Count -eq 8) 'Expected four users and four groups'
Assert ($global:AdTestPrompts -eq 4 -and $global:AdTestWrites -eq 12) 'Unexpected first-run operations'
foreach ($guest in @('vm_admin','vm_user','vm_auditor','pvs.git.service','kolana_admin','platformv.admin','platformv.operator','platformv.viewer')) {
    Assert (-not $global:AdTestObjects.ContainsKey($guest)) "Unexpected AD account: $guest"
}
foreach ($group in $global:AdTestMembers.Keys) { Assert ($global:AdTestMembers[$group].Count -eq 1) 'Cross-role membership' }
$global:AdTestWrites = 0; $global:AdTestPrompts = 0
Run-Script | Out-Null
Assert ($global:AdTestWrites -eq 0 -and $global:AdTestPrompts -eq 0) 'Repeat modified existing objects/passwords'

$global:AdTestMembers['CN=PVS_VMDevelopers,' + $global:AdTestOu] = @()
Run-Script | Out-Null
Assert ($global:AdTestWrites -eq 1 -and $global:AdTestPrompts -eq 0) 'Missing membership was not repaired alone'

Reset-FakeAd
Run-Script 'PVS' | Out-Null
Assert ($global:AdTestObjects.Count -eq 8) 'PVS scope must include Grafana and exclude LCMP'

Reset-FakeAd
Run-Script 'All' | Out-Null
Assert ($global:AdTestObjects.Count -eq 14) 'All scope must create seven users/groups'

Reset-FakeAd
Run-Script 'LCMP' | Out-Null
Assert ($global:AdTestObjects.Count -eq 6 -and $global:AdTestObjects.ContainsKey('platformv.admin')) 'LCMP scope mismatch'

Reset-FakeAd
Run-Script 'Grafana' | Out-Null
Assert ($global:AdTestObjects.Count -eq 2 -and $global:AdTestObjects.ContainsKey('grafana_admin')) 'Grafana name mismatch'

Reset-FakeAd
$global:AdTestObjects['PVS_VMDevelopers'] = [pscustomobject]@{ ObjectClass='group'; DistinguishedName='CN=PVS_VMDevelopers,OU=Other,DC=example,DC=local' }
$caught = $false
try { Run-Script | Out-Null } catch { $caught = $_.Exception.Message -like '*Conflict*' }
Assert ($caught -and $global:AdTestWrites -eq 0 -and $global:AdTestPrompts -eq 0) 'Foreign OU was not rejected before writes'

Reset-FakeAd
$global:AdTestObjects['security_admin'] = [pscustomobject]@{ ObjectClass='group'; DistinguishedName='CN=security_admin,' + $global:AdTestOu }
$caught = $false
try { Run-Script | Out-Null } catch { $caught = $_.Exception.Message -like '*Conflict*' }
Assert ($caught -and $global:AdTestWrites -eq 0) 'Wrong object type was not rejected'

Reset-FakeAd
$global:AdTestObjects['PVS_VirtAdmins'] = [pscustomobject]@{ ObjectClass='group'; DistinguishedName='CN=PVS_VirtAdmins,' + $global:AdTestOu; GroupCategory='Distribution'; GroupScope='Global' }
$caught = $false
try { Run-Script | Out-Null } catch { $caught = $_.Exception.Message -like '*Conflict*' }
Assert ($caught -and $global:AdTestWrites -eq 0) 'Wrong group category was not rejected'
Write-Output 'PASS: 10 offline AD scenarios'
