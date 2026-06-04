$ScriptRoot = $PSScriptRoot
$BotLauncherRoot = (Get-Item "$ScriptRoot\..\..\..\..").FullName

& "$BotLauncherRoot\Script\EnvTools\win\install_env.ps1"
exit $LASTEXITCODE
