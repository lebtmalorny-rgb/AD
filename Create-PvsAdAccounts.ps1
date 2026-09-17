# Directory-backed human roles from the PVS model; see README.md.
param(
    [Parameter(Mandatory)][string]$Server,
    [Parameter(Mandatory)][string]$OU,
    [ValidateSet('PVS', 'LCMP', 'Grafana', 'All')][string]$Scope = 'PVS'
)
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory
$domain = Get-ADDomain -Server $Server
Get-ADOrganizationalUnit -Identity $OU -Server $Server | Out-Null
$roles = [ordered]@{
    virtualization_admin = @('PVS_VirtAdmins',     'PVS')
    security_admin       = @('PVS_SecurityAudit',  'PVS')
    vm_developer         = @('PVS_VMDevelopers',   'PVS')
    grafana_admin        = @('PVS_GrafanaAdmins',  'Grafana')
    'platformv.admin'    = @('LCMP_PlatformAdmins','LCMP')
    'platformv.operator' = @('LCMP_RegionAdmins',  'LCMP')
    'platformv.viewer'   = @('LCMP_Viewers',       'LCMP')
}
$selected = @($roles.Keys | Where-Object {
    $Scope -eq 'All' -or $roles[$_][1] -eq $Scope -or ($Scope -eq 'PVS' -and $roles[$_][1] -eq 'Grafana')
})
$existing = @{}

# Validate all names before writes; reuse only matching objects in the chosen OU.
foreach ($login in $selected) {
    foreach ($name in @($login, $roles[$login][0])) {
        if ($name -notmatch '^[A-Za-z0-9_.-]{1,20}$') { throw "Invalid account/group name: $name" }
        $object = Get-ADObject -LDAPFilter "(sAMAccountName=$name)" -SearchBase $domain.DistinguishedName -Server $Server
        $kind = if ($name -eq $login) { 'user' } else { 'group' }
        if ($object -and ($object.ObjectClass -ne $kind -or $object.DistinguishedName -ine "CN=$name,$OU")) {
            throw "Conflict: $name has another type or location. No changes made."
        }
        if ($object -and $kind -eq 'group') {
            $object = Get-ADGroup -Identity $object.DistinguishedName -Server $Server
            if ($object.GroupCategory -ne 'Security' -or $object.GroupScope -ne 'Global') {
                throw "Conflict: $name must be a Global Security group. No changes made."
            }
        }
        $existing[$name] = $object
    }
}
foreach ($login in $selected) {
    $groupName = $roles[$login][0]
    $group = $existing[$groupName]
    if (-not $group) {
        $group = New-ADGroup -Name $groupName -SamAccountName $groupName `
            -GroupCategory Security -GroupScope Global -Path $OU -Server $Server -PassThru
    }
    if ($existing[$login]) {
        $user = Get-ADUser -Identity $existing[$login].DistinguishedName -Server $Server
    } else {
        $password = Read-Host "Password for $login" -AsSecureString
        $user = New-ADUser -Name $login -SamAccountName $login `
            -UserPrincipalName "$login@$($domain.DNSRoot)" -Path $OU -Server $Server `
            -AccountPassword $password -Enabled $true -ChangePasswordAtLogon $false -PassThru
    }
    if (-not (Get-ADGroupMember -Identity $group -Server $Server | Where-Object DistinguishedName -eq $user.DistinguishedName)) {
        Add-ADGroupMember -Identity $group -Members $user -Server $Server
    }
    [pscustomobject]@{ Role = $login; User = $user.UserPrincipalName; GroupDN = $group.DistinguishedName }
}
