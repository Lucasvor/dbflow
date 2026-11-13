param([string]$Inputs = (Join-Path $PSScriptRoot 'secret-inputs.json'))
$ErrorActionPreference = 'Stop'
if (-not (Get-Command oc -ErrorAction SilentlyContinue)) { throw 'Instale o CLI oc e autentique-se no cluster antes de executar.' }
$config = Get-Content -LiteralPath $Inputs -Raw | ConvertFrom-Json
function Read-Credential([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value) {
        $secure = Read-Host "Valor de $Name (oculto; não será salvo em arquivo)" -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    }
    if ([string]::IsNullOrWhiteSpace($value)) { throw "Credencial $Name vazia." }
    return $value
}
$data = @{}
$tokens = @{}
try {
    foreach ($name in $config.gitlab_token_envs) { $data[$name] = Read-Credential $name }
    foreach ($account in $config.service_accounts) {
        $token = Read-Credential $account.token_env
        if ($token.Length -lt 32) { throw 'Tokens de acesso MCP precisam ter pelo menos 32 caracteres aleatórios.' }
        if ($tokens.ContainsKey($token)) { throw 'Cada conta MCP deve ter um token distinto.' }
        $tokens[$token] = @{actor = $account.actor; projects = @($account.projects)}
    }
    $data['DBFLOW_SERVICE_TOKENS'] = ConvertTo-Json -InputObject $tokens -Depth 8 -Compress
    $secret = @{apiVersion = 'v1'; kind = 'Secret'; metadata = @{name = 'dbflow-secrets'; namespace = $config.namespace}; type = 'Opaque'; stringData = $data}
    # Credenciais passam somente por stdin, nunca por argumentos ou arquivos temporários.
    $secret | ConvertTo-Json -Depth 12 -Compress | & oc apply --server-side --field-manager=dbflow-secrets -f -
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao aplicar Secret no OpenShift.' }
    & oc -n $config.namespace rollout restart deployment/dbflow
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Secret criado. Aplique deployment.json se o deployment ainda não existir.' }
}
finally { $data.Clear(); $tokens.Clear(); $token = $null; $secret = $null }
