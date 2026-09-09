# Registra no Agendador de Tarefas do Windows o worker e o dashboard, para iniciarem no logon do usuário
# (o navegador automatizado precisa de sessão de usuário aberta; não use "executar sem logon").
$root = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $root ".venv\Scripts\licitabot.exe"
$user = "$env:USERDOMAIN\$env:USERNAME"

foreach ($t in @(@{n = "LicitaBot Worker"; a = "worker" }, @{n = "LicitaBot Web"; a = "web" })) {
    $action = New-ScheduledTaskAction -Execute $exe -Argument $t.a -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Days 365) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $t.n -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
    Write-Host "Tarefa registrada: $($t.n)"
}
Write-Host "Inicie agora com: Start-ScheduledTask 'LicitaBot Worker'; Start-ScheduledTask 'LicitaBot Web'"
