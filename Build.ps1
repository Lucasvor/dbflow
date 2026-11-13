param([string]$Output = (Join-Path $PSScriptRoot 'server/publish'))
$ErrorActionPreference = 'Stop'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
& dotnet publish (Join-Path $PSScriptRoot 'server/DbFlow.Server.csproj') -c Release --self-contained false -p:UseAppHost=false -o $Output --nologo
if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar servidor .NET 8.' }
