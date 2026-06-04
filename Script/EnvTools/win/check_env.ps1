$ScriptRoot = $PSScriptRoot
$BotLauncherRoot = (Get-Item "$ScriptRoot\..\..\..\..").FullName

& "$BotLauncherRoot\Script\EnvTools\win\check_env.ps1"
exit $LASTEXITCODE
