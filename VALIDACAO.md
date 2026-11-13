# Validação da versão 0.2

Atualizado em 13/11/2026. Servidor C# com target `net8.0`, SDK MCP oficial 1.2.0 e journal SQLite compatível com a versão anterior. Compilado com SDK local 9.0.100 para .NET 8 e executado no runtime .NET 8 disponível nesta máquina. Dockerfile compila com SDK 8 e executa em ASP.NET Core 8.

## Evidência automatizada

Resultado final: **39 testes Python/integrados aprovados**, incluindo 10 testes do executável .NET e 3 testes de hooks Codex; **8 cenários C# do adaptador GitLab aprovados**. A [execução no GitHub Actions](https://github.com/Lucasvor/dbflow/actions/runs/43362703966) concluiu com sucesso nos runners Windows e Ubuntu, compilando com SDK .NET 8 e usando Python 3.13. O teste DPAPI se aplica somente ao Windows. Na primeira execução de CI, uma comparação de caminhos temporários no Windows falhou; a verificação passou a comparar os dois caminhos resolvidos, preservando a exigência de que o pacote esteja dentro do projeto.

- Publicação Release do servidor, sem avisos/erros na compilação final.
- Integração real do executável .NET via stdio: initialize, tools/list e reconhecimento da versão 0.2.0.
- HTTP loopback com SDK oficial: autenticação, rejeição de Origin, isolamento entre projetos, respostas JSON/SSE e cliente remoto.
- Registro idempotente, confirmação parcial PRO/PRI, R1/R2, mudança de destino, pacote, snapshot parcial e abandono.
- Migração de SQLite Python: hashes Unicode/acentos/emoji, IDs e confirmação repetida; pacotes já exportados permanecem utilizáveis e scripts modificados são recusados.
- Credencial fictícia criptografada por DPAPI e recuperada no processo local; ausência do valor em arquivo/stdout/stderr.
- Instalação repetida, preservação de configuração alheia, uninstall conservador e manifesto OpenShift sem valores secretos.
- Testes C# com adaptador HTTP GitLab simulado: perda da resposta após commit, retomada do journal, ausência de duplicação, conflito entre revisões concorrentes, abandono preservando histórico, IDs de revisão duplicados e headers PAT/OAuth.

Comandos reproduzíveis estão no README. As credenciais dos testes são fictícias, usadas somente no processo/local loopback. Não há banco de negócio ou instância GitLab real envolvida.

## Limites observados

Docker CLI existe, mas o daemon Linux do Docker Desktop estava indisponível. Não foi possível construir/rodar a imagem ou validar UID/PVC numa instância OpenShift nesta máquina. Não há CLI `oc`, cluster ou GitLab corporativo configurado. Os manifests foram inspecionados/testados como estrutura; a homologação no cluster ainda precisa validar imagem, SCC, StorageClass, permissões, TLS, rede e escopo do token.

O reconhecimento visual nos aplicativos Codex/Claude/Cursor após reabrir o piloto não foi observado. Teste do transporte não implica aprovação/confiança automática desses aplicativos.

O piloto `Teste-Piloto-Scripts` foi atualizado para o comando MCP .NET 8 nos três clientes, preservando as regras e o journal. URL GitLab, URL MCP e mapeamentos reais continuam vazios por não existir o ambiente corporativo nesta máquina. O pacote contém `openshift-example` com destinos fictícios PRO/PRI e URLs de exemplo; não foram aplicados ao cluster nem confundidos com confirmação de banco real.

Atualização dos hooks: Codex agora possui `SessionStart`, `PostToolUse` e `Stop` nativos inline em `.codex/config.toml`. Claude e Cursor mantêm seus hooks e regras. O `doctor` do piloto confirmou todos os eventos configurados e nenhuma invocação de helper observada naquele checkout; o carregamento efetivo pelo aplicativo exige confiança/revisão. CLI local verificada: `codex-cli 0.153.4`, feature `hooks stable true`. Testes validaram preservação de hooks alheios, reinstalação, resposta do helper, caminhos com espaços e distinção entre configurado e invocado. [Schema e revisão de confiança oficiais](https://learn.chatgpt.com/docs/hooks).

O assistente PowerShell também foi executado em pasta de teste no modo Server, gerando os quatro arquivos de implantação com sucesso. A gravação atômica Python passou a falhar rapidamente em diretórios protegidos do Windows, evitando milhares de retentativas internas de arquivo temporário.

Não há OAuth interativo/refresh, MR automático, merge semântico de SQL, execução de banco, envio de email ou daemon de monitoramento. O servidor aceita access token OAuth obtido externamente. A versão continua sendo um piloto funcional; não foi submetida a carga/failover de produção.

Conforme o requisito, o target segue .NET 8, cujo suporte oficial terminou em 10/11/2026. Essa restrição precisa ser considerada antes da adoção em produção: https://dotnet.microsoft.com/en-us/platform/support/policy/dotnet-core.
