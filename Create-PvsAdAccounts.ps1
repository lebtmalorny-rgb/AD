# First-time setup: three AD users and their security groups in an existing OU.
param(
    [Parameter(Mandatory)][string]$Server,
    [Parameter(Mandatory)][string]$OU
)
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory
$domain = Get-ADDomain -Server $Server
Get-ADOrganizationalUnit -Identity $OU -Server $Server | Out-Null
$roles = [ordered]@{
    virtualization_admin = 'PVS_VirtAdmins'
    security_admin       = 'PVS_SecurityAudit'
    vm_developer         = 'PVS_VMDevelopers'
}

# Stop before creation if any requested account/group name already exists.
foreach ($name in (@($roles.Keys) + @($roles.Values))) {
    if (Get-ADObject -LDAPFilter "(sAMAccountName=$name)" -SearchBase $domain.DistinguishedName -Server $Server) {
        throw "Already exists in AD: $name. No existing accounts will be changed."
    }
}
foreach ($login in $roles.Keys) {
    $password = Read-Host "Password for $login" -AsSecureString
    $group = New-ADGroup -Name $roles[$login] -SamAccountName $roles[$login] `
        -GroupCategory Security -GroupScope Global -Path $OU -Server $Server -PassThru
    $user = New-ADUser -Name $login -SamAccountName $login `
        -UserPrincipalName "$login@$($domain.DNSRoot)" -Path $OU -Server $Server `
        -AccountPassword $password -Enabled $true -ChangePasswordAtLogon $false -PassThru
    Add-ADGroupMember -Identity $group -Members $user -Server $Server
    [pscustomobject]@{ User = $user.UserPrincipalName; GroupDN = $group.DistinguishedName }
}
