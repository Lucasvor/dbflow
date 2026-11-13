param(
    [Parameter(Mandatory = $true)][string]$Project,
    [ValidateSet('Local', 'Remote', 'Server')][string]$Mode = 'Local',
    [string]$Clients = 'codex,claude,cursor',
    [string]$RepositoryUrl, [string]$GitLabInstanceUrl, [string]$McpUrl,
    [ValidateSet('private_token', 'oauth_access_token')][string]$Authentication = 'private_token',
    [string]$GitLabTokenEnv = 'DBFLOW_GITLAB_TOKEN', [string]$ServiceTokenEnv = 'DBFLOW_SERVICE_TOKEN',
    [string]$Namespace = 'dbflow', [string]$Image, [string]$RouteHost, [string]$CaBundle,
    [string]$OutputDirectory, [switch]$Interactive, [switch]$PromptToken, [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$existingConfig = Join-Path $Project '.db-control/project.json'
if (Test-Path -LiteralPath $existingConfig) {
    $existing = Get-Content -LiteralPath $existingConfig -Raw | ConvertFrom-Json
    if (-not $PSBoundParameters.ContainsKey('Mode') -and $existing.mode -eq 'remote') { $Mode = 'Remote' }
    if (-not $PSBoundParameters.ContainsKey('Authentication') -and $existing.gitlab.auth_type) { $Authentication = $existing.gitlab.auth_type }
    if (-not $PSBoundParameters.ContainsKey('GitLabTokenEnv') -and $existing.gitlab.token_env) { $GitLabTokenEnv = $existing.gitlab.token_env }
    if (-not $PSBoundParameters.ContainsKey('ServiceTokenEnv') -and $existing.remote_token_env) { $ServiceTokenEnv = $existing.remote_token_env }
}
if ($Interactive) {
    $chosen = Read-Host 'Modo: Local, Remote ou Server (Enter mantém o parâmetro atual)'
    if ($chosen) { if ($chosen -notin @('Local','Remote','Server')) { throw 'Modo inválido.' }; $Mode = $chosen }
    if (-not $RepositoryUrl) { $RepositoryUrl = Read-Host 'URL HTTPS do repositório central GitLab (Enter deixa pendente)' }
    if ($Mode -eq 'Remote' -and -not $McpUrl) { $McpUrl = Read-Host 'URL completa MCP: https://host/mcp/project_key (Enter deixa pendente)' }
    if ($Mode -eq 'Server' -and -not $RouteHost) { $RouteHost = Read-Host 'Hostname da Route OpenShift (Enter deixa pendente)' }
    $authChoice = Read-Host 'GitLab: 1 = PAT/token de projeto; 2 = access token OAuth existente (Enter = 1)'
    if ($authChoice -eq '2') { $Authentication = 'oauth_access_token' }
}
$dll = Join-Path $PSScriptRoot 'server/publish/DbFlow.Server.dll'
if ($Apply -and -not (Test-Path -LiteralPath $dll)) { & (Join-Path $PSScriptRoot 'Build.ps1') }
$launcher = Join-Path $PSScriptRoot 'dbctl.py'
$installArgs = @($launcher, '--project', $Project, 'install', '--mode', $(if ($Mode -eq 'Remote') { 'remote' } else { 'local' }), '--auth-type', $Authentication,
    '--token-env', $GitLabTokenEnv, '--service-token-env', $ServiceTokenEnv)
if ($Mode -eq 'Server') { $installArgs += '--clients=' } else { $installArgs += @('--clients', $Clients) }
if ($RepositoryUrl) { $installArgs += @('--repo-url', $RepositoryUrl) }
if ($GitLabInstanceUrl) { $installArgs += @('--instance-url', $GitLabInstanceUrl) }
if ($McpUrl) { $installArgs += @('--mcp-url', $McpUrl) }
if ($Apply) { $installArgs += '--apply' }
& python @installArgs
if ($LASTEXITCODE -ne 0) { throw 'Falha ao configurar projeto.' }
if ($Mode -eq 'Server') {
    if (-not $OutputDirectory) { $OutputDirectory = Join-Path $Project '.db-control/openshift' }
    if (-not $Apply -and -not (Test-Path (Join-Path $Project '.db-control/project.json'))) { Write-Output 'Dry-run: configuração OpenShift será gerada após instalar com -Apply.'; return }
    $deployArgs = @($launcher, '--project', $Project, 'openshift', '--output', $OutputDirectory, '--namespace', $Namespace)
    if ($Image) { $deployArgs += @('--image', $Image) }
    if ($RouteHost) { $deployArgs += @('--route-host', $RouteHost) }
    if ($CaBundle) { $deployArgs += @('--ca-bundle', $CaBundle) }
    if ($Apply) { $deployArgs += '--apply' }
    & python @deployArgs
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar configuração OpenShift.' }
    Write-Output 'No cluster, use Apply-Secrets.ps1 para tokens ou configure o Secret com seu gerenciador corporativo.'
}
elseif ($PromptToken) {
    if (-not $Apply) { Write-Output 'Dry-run: nenhuma credencial será solicitada ou salva.'; return }
    $name = if ($Mode -eq 'Remote') { $ServiceTokenEnv } else { $GitLabTokenEnv }
    $secure = Read-Host "Token $name (criptografado por DPAPI para seu usuário Windows)" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if ($Mode -eq 'Remote' -and $value.Length -lt 32) { throw 'Token MCP deve ter pelo menos 32 caracteres.' }
        $value | & dotnet $dll --project $Project --set-secret $name
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao guardar credencial criptografada.' }
    }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr); $value = $null }
}
