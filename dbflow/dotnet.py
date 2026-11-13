"""Launch the .NET MCP implementation; Python remains installer/hooks only."""
import json
import subprocess
from pathlib import Path
from .common import FlowError

def assembly():
    base = Path(__file__).resolve().parents[1]
    for path in (base / 'dotnet/DbFlow.Server.dll', base / 'server/publish/DbFlow.Server.dll'):
        if path.exists():
            return str(path)
    raise FlowError('Build the .NET 8 server with Build.ps1, then reinstall the project.')

def call(root, tool, args):
    process = subprocess.run(['dotnet', assembly(), '--project', str(root), '--call', tool], input=json.dumps(args, ensure_ascii=False), capture_output=True, encoding='utf-8')
    if process.returncode:
        raise FlowError('The .NET 8 server could not start; check runtime, configuration and credentials.')
    return json.loads(process.stdout)

def stdio(root):
    return subprocess.call(['dotnet', assembly(), '--project', str(root), '--stdio'])
