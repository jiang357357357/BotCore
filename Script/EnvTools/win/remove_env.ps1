$ScriptRoot = $PSScriptRoot
$BotLauncherRoot = (Get-Item "$ScriptRoot\..\..\..\..").FullName

& "$BotLauncherRoot\Script\EnvTools\win\remove_env.ps1"
exit $LASTEXITCODE
