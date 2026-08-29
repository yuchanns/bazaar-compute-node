$ErrorActionPreference = 'Stop'
$target = {{ target }}
$content = {{ content }}
$temporary = "$target.new"
[System.IO.File]::WriteAllText(
    $temporary,
    $content,
    (New-Object System.Text.UTF8Encoding($false))
)
# a fresh file carries inherited permissions, which are not necessarily the ones
# the installed file was given, so the replacement takes the target's own ACL
Set-Acl -LiteralPath $temporary -AclObject (Get-Acl -LiteralPath $target)
[System.IO.File]::Replace($temporary, $target, $null)
