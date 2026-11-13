"""Non-secret installer settings and OpenShift resources. No cluster side effects."""
import copy
import json
import re
import urllib.parse
from pathlib import Path
from .common import FlowError, atomic_write, identifier, load_config


def repository(url, instance_url=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FlowError('Use a GitLab repository HTTPS URL without embedded credentials, query or fragment.')
    path = urllib.parse.unquote(parsed.path).strip('/')
    if path.endswith('.git'):
        path = path[:-4]
    base = parsed.scheme + '://' + parsed.netloc
    if instance_url:
        instance = urllib.parse.urlsplit(instance_url.rstrip('/'))
        if instance.scheme != 'https' or instance.netloc.lower() != parsed.netloc.lower() or instance.username or instance.password or instance.query or instance.fragment:
            raise FlowError('GitLab instance and repository must have the same HTTPS origin.')
        prefix = urllib.parse.unquote(instance.path).strip('/')
        if prefix:
            if not path.startswith(prefix + '/'):
                raise FlowError('Repository is outside the GitLab instance path.')
            path = path[len(prefix) + 1:]
        base = instance_url.rstrip('/')
    if len(path.split('/')) < 2 or any(p in ('', '.', '..', '-') for p in path.split('/')) or any(c in path for c in '\\?#\r\n'):
        raise FlowError('Use the repository URL, including group/subgroups/project, not a file or branch URL.')
    return {'url': base, 'project_id': path, 'repository_url': base + '/' + path}


def settings(config, mode=None, repo_url=None, instance_url=None, mcp_url=None, auth_type=None, token_env=None, service_token_env=None):
    cfg = copy.deepcopy(config)
    if mode:
        cfg['mode'] = mode
    if repo_url:
        cfg['gitlab'].update(repository(repo_url, instance_url))
    if mcp_url:
        parsed = urllib.parse.urlsplit(mcp_url)
        if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1'))) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise FlowError('MCP URL requires HTTPS without embedded credentials. HTTP only for loopback simulation.')
        if parsed.path.rstrip('/') != '/mcp/' + cfg['project_key']:
            raise FlowError('MCP URL must end in /mcp/' + cfg['project_key'])
        cfg['remote_url'] = mcp_url.rstrip('/')
    if auth_type:
        if auth_type not in ('private_token', 'oauth_access_token'):
            raise FlowError('Unsupported GitLab authentication method.')
        cfg['gitlab']['auth_type'] = auth_type
    for value in (token_env, service_token_env):
        if value and not re.fullmatch(r'DBFLOW_[A-Z0-9_]+', value):
            raise FlowError('Credential environment variables must use DBFLOW_ and uppercase letters/numbers/underscores.')
    if token_env:
        cfg['gitlab']['token_env'] = token_env
    if service_token_env:
        cfg['remote_token_env'] = service_token_env
    return cfg


def configure(root, **kwargs):
    cfg = settings(load_config(root), **kwargs)
    atomic_write(Path(root) / '.db-control/project.json', json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
    return {'configured': True, 'mode': cfg['mode'], 'project_key': cfg['project_key'], 'secrets_written': False}


def openshift(config, output, namespace='dbflow', image='', route_host='', ca_bundle=None, apply=False):
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', namespace):
        raise FlowError('Invalid Kubernetes namespace.')
    if route_host and not re.fullmatch(r'[A-Za-z0-9.-]+', route_host):
        raise FlowError('RouteHost is a DNS hostname, without https/path.')
    cfg = copy.deepcopy(config)
    cfg['mode'] = 'local'
    cfg['remote_url'] = ''
    key = identifier(cfg['project_key'])
    host = route_host or 'dbflow.apps.example.invalid'
    server = {'projects': {key: cfg}, 'allowed_hosts': [host, 'localhost', '127.0.0.1'], 'allowed_origins': []}
    previous = Path(output) / 'server-config.json'
    if previous.exists():
        server = json.loads(previous.read_text(encoding='utf-8'))
        server.setdefault('projects', {})[key] = cfg
        server['allowed_hosts'] = list(dict.fromkeys(server.get('allowed_hosts', []) + [host, 'localhost', '127.0.0.1']))
    labels = {'app.kubernetes.io/name': 'dbflow'}
    def resource(api, kind, name, **body):
        return {'apiVersion': api, 'kind': kind, 'metadata': {'name': name, 'namespace': namespace, 'labels': labels}, **body}
    mounts = [{'name': 'data', 'mountPath': '/data'}, {'name': 'config', 'mountPath': '/config', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}]
    volumes = [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'dbflow-data'}}, {'name': 'config', 'configMap': {'name': 'dbflow-config'}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '128Mi'}}]
    env = [{'name': 'DBFLOW_CONFIG', 'value': '/config/server-config.json'}, {'name': 'DBFLOW_DATA', 'value': '/data'}, {'name': 'ASPNETCORE_HTTP_PORTS', 'value': '8080'}]
    items = [resource('v1', 'ConfigMap', 'dbflow-config', data={'server-config.json': json.dumps(server, ensure_ascii=False, indent=2)}),
             resource('v1', 'PersistentVolumeClaim', 'dbflow-data', spec={'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '2Gi'}}})]
    if ca_bundle:
        pem = Path(ca_bundle).read_text(encoding='utf-8')
        if '-----BEGIN CERTIFICATE-----' not in pem:
            raise FlowError('CA bundle must be a PEM trust bundle.')
        items.append(resource('v1', 'ConfigMap', 'dbflow-ca', data={'ca-bundle.pem': pem}))
        mounts.append({'name': 'ca', 'mountPath': '/etc/dbflow-ca', 'readOnly': True})
        volumes.append({'name': 'ca', 'configMap': {'name': 'dbflow-ca'}})
        env.append({'name': 'SSL_CERT_FILE', 'value': '/etc/dbflow-ca/ca-bundle.pem'})
    container = {'name': 'mcp', 'image': image or f'image-registry.openshift-image-registry.svc:5000/{namespace}/dbflow:0.2.0',
                 'ports': [{'name': 'http', 'containerPort': 8080}], 'env': env,
                 'envFrom': [{'secretRef': {'name': 'dbflow-secrets'}}], 'volumeMounts': mounts,
                 'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
                 'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'runAsNonRoot': True, 'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}},
                 'livenessProbe': {'httpGet': {'path': '/health/live', 'port': 'http'}, 'initialDelaySeconds': 10, 'periodSeconds': 15},
                 'readinessProbe': {'httpGet': {'path': '/health/ready', 'port': 'http'}, 'initialDelaySeconds': 5, 'periodSeconds': 10},
                 'startupProbe': {'httpGet': {'path': '/health/live', 'port': 'http'}, 'failureThreshold': 30, 'periodSeconds': 2}}
    items += [resource('apps/v1', 'Deployment', 'dbflow', spec={'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': labels},
                        'template': {'metadata': {'labels': labels}, 'spec': {'automountServiceAccountToken': False, 'terminationGracePeriodSeconds': 60, 'containers': [container], 'volumes': volumes}}}),
              resource('v1', 'Service', 'dbflow', spec={'selector': labels, 'ports': [{'name': 'http', 'port': 8080, 'targetPort': 'http'}]}),
              resource('route.openshift.io/v1', 'Route', 'dbflow', spec={'host': host, 'to': {'kind': 'Service', 'name': 'dbflow'}, 'port': {'targetPort': 'http'}, 'tls': {'termination': 'edge', 'insecureEdgeTerminationPolicy': 'Redirect'}})]
    inputs = {'namespace': namespace, 'gitlab_token_envs': sorted({p['gitlab'].get('token_env', 'DBFLOW_GITLAB_TOKEN') for p in server['projects'].values()}),
              'service_accounts': [{'actor': 'developer', 'projects': [key], 'token_env': 'DBFLOW_SERVICE_TOKEN'}]}
    previous_inputs = Path(output) / 'secret-inputs.json'
    if previous_inputs.exists():
        accounts = json.loads(previous_inputs.read_text(encoding='utf-8'))['service_accounts']
        if not any(key in a['projects'] for a in accounts):
            accounts.append({'actor': 'developer-' + key, 'projects': [key], 'token_env': 'DBFLOW_SERVICE_TOKEN_' + re.sub('[^A-Z0-9_]', '_', key.upper())})
        inputs['service_accounts'] = accounts
    files = {'deployment.json': json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': items}, ensure_ascii=False, indent=2),
             'server-config.json': json.dumps(server, ensure_ascii=False, indent=2), 'secret-inputs.json': json.dumps(inputs, indent=2),
             'Apply-Secrets.ps1': (Path(__file__).resolve().parents[1] / 'templates/Apply-Secrets.ps1').read_text(encoding='utf-8')}
    if apply:
        for name, content in files.items():
            dest = Path(output) / name
            if dest.exists() and dest.read_text(encoding='utf-8') != content:
                atomic_write(dest.with_suffix(dest.suffix + '.bak'), dest.read_bytes())
            atomic_write(dest, content + '\n')
    return {'mode': 'apply' if apply else 'dry_run', 'files': list(files), 'output': str(Path(output).resolve()),
            'mcp_url': 'https://' + host + '/mcp/' + key, 'cluster_modified': False, 'credential_values_written': False,
            'pending': [name for name, present in [('repository_url', bool(cfg['gitlab'].get('url'))), ('route_host', bool(route_host)), ('image_build_and_push', False), ('cluster_secrets', False), ('cluster_deployment', False)] if not present]}
