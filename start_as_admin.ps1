param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptName
)

$scriptPath = Join-Path $PSScriptRoot $ScriptName
if (-not (Test-Path $scriptPath)) {
    Write-Error "Script introuvable: $scriptPath"
    exit 1
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    $quotedScriptPath = '"' + $scriptPath + '"'
    Start-Process -FilePath $py.Source -ArgumentList @('-3', $quotedScriptPath) -WorkingDirectory $PSScriptRoot -Verb RunAs
    exit 0
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $quotedScriptPath = '"' + $scriptPath + '"'
    Start-Process -FilePath $python.Source -ArgumentList @($quotedScriptPath) -WorkingDirectory $PSScriptRoot -Verb RunAs
    exit 0
}

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show('Python 3 x64 est introuvable dans le PATH.', 'L2 Assist') | Out-Null
exit 1
