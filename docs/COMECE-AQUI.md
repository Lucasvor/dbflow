# DBFlow: comece aqui, passo a passo

Este guia é para quem está usando o DBFlow pela primeira vez e ainda não conhece os termos do projeto. Siga as etapas na ordem. Os comandos são para Windows e PowerShell; substitua os caminhos de exemplo pelo caminho real da sua máquina.

> **O que este produto faz:** ajuda o agente de programação a localizar scripts de banco no projeto, manter revisões e preparar um pacote de entrega que uma pessoa revisa e envia ao DBA. Ele organiza registros e evidências.
>
> **O que ele não faz:** não conecta ao SQL Server, Oracle, PostgreSQL, MongoDB ou outro banco de negócio; não executa SQL; não envia nem lê email; não fica rodando para monitorar o computador quando o aplicativo cliente fecha. Configurar um cliente não comprova que ele carregou a configuração.

O pacote é um piloto funcional. Há testes locais automatizados documentados em [VALIDACAO.md](../VALIDACAO.md), mas a implantação no OpenShift e o reconhecimento visual pelos aplicativos ainda precisam ser verificados no ambiente da sua organização.

## 1. As três pastas que você precisa distinguir

Você pode encontrar três repositórios diferentes. “Repositório” é uma pasta versionada pelo Git, normalmente sincronizada com um servidor Git. Não use uma pasta no lugar da outra.

| Nome neste guia | Para que serve | Quem normalmente mantém |
|---|---|---|
| **Produto DBFlow** | O código do instalador, servidor MCP, exemplos e documentação. É de onde vêm `Install.ps1`, `Build.ps1` e `dbctl.py`. | Equipe que distribui o produto. |
| **Projeto da aplicação** | O checkout da aplicação em que você trabalha. O instalador grava aqui instruções do agente, configuração MCP, hooks e `.db-control`. | Desenvolvedor. |
| **Repositório central de scripts e eventos** | Um projeto GitLab já existente, usado como registro central do ledger de demandas, revisões, pacotes e confirmações. | Organização/equipe responsável pelo GitLab. |

Exemplo visual:

```text
D:\Ferramentas\dbflow\                 <- produto DBFlow: instalador e código
D:\Projetos\PortalFinanceiro\           <- sua aplicação: arquivos de trabalho
https://gitlab.empresa.example/dados/sql  <- repositório central: URL, não pasta local
```

O GitLab central não é criado pelo instalador. Se ninguém forneceu sua URL HTTPS, deixe-a pendente; não invente endereço. No modo Remote também é necessário que um administrador publique o serviço DBFlow e forneça o endereço MCP e um token. Esses valores não são substituíveis por um endereço de exemplo.

O projeto da aplicação pode ter seu próprio repositório Git, independente do repositório GitLab central. Os scripts SQL e o código da aplicação ficam no primeiro; o registro central guarda eventos DBFlow.

## 2. O que instalar

### Para usar em Windows

1. **PowerShell.** Windows inclui o Windows PowerShell. O instalador usa sintaxe compatível com ele. Se sua empresa preferir PowerShell 7, baixe o instalador oficial na [página Microsoft Learn de instalação no Windows](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows). Em computador corporativo, siga a política de instalação da empresa.
2. **Python 3.11 ou mais recente.** O instalador e a CLI usam Python. Obtenha o Python no [site oficial python.org](https://www.python.org/downloads/windows/). Se o instalador oferecer “Add python.exe to PATH”, marque essa opção. O comando abaixo também tenta o launcher `py` do Windows.
3. **.NET 8 ASP.NET Core Runtime.** O servidor local é uma aplicação .NET 8. Na [página oficial de downloads do .NET 8](https://dotnet.microsoft.com/download/dotnet/8.0), escolha **ASP.NET Core Runtime 8** para Windows e a arquitetura do seu computador (normalmente x64). O runtime é suficiente para executar o pacote distribuído já publicado.
4. **.NET 8 SDK**, apenas se você clonou o repositório fonte ou precisa compilar. Na mesma página oficial, escolha **.NET SDK 8** para Windows. O SDK também inclui os runtimes necessários.
5. **Git for Windows**, se for clonar repositórios. Baixe em [git-scm.com/install/windows](https://git-scm.com/install/windows). Se recebeu um ZIP do produto e uma pasta da aplicação já pronta, Git não é necessário para seguir o ensaio Local básico.
6. **Um aplicativo cliente compatível com MCP**, se ainda não usa um. Instale somente o cliente escolhido pela sua equipe, usando o site oficial: [Codex](https://openai.com/codex/), [Claude Code](https://code.claude.com/docs/en/quickstart) ou [Cursor](https://cursor.com/docs/get-started/quickstart). O DBFlow prepara configuração para os três; você não precisa instalar os três. Recursos de MCP/hooks variam por versão e podem depender de políticas da organização.

O código deste pacote tem como destino .NET 8. A política oficial de suporte registra o fim do suporte em 10/11/2026; a organização deve considerar isso antes de adotar o piloto em produção. A versão do SDK/runtime pode mudar, então confira a página oficial de downloads. Não instale um SDK de uma fonte não oficial.

### Conferir o que o PowerShell encontra

Abra o PowerShell pelo menu Iniciar. Digite `PowerShell` e escolha Windows PowerShell ou PowerShell. Não é necessário abrir como administrador para instalar no diretório do seu usuário.

Rode estes comandos, um por vez:

```powershell
python --version
py -3 --version
dotnet --list-runtimes
git --version
```

O resultado esperado é: pelo menos um dos dois comandos Python informa `3.11` ou posterior; `dotnet --list-runtimes` lista uma linha `Microsoft.AspNetCore.App 8...`; e, se pretende clonar, `git --version` informa uma versão. Se instalou algo agora e o comando não é encontrado, feche e abra uma nova janela PowerShell e repita.

Se `python` falhar, mas `py -3 --version` funcionar, você pode usar `py -3` nos comandos Python. Os scripts `Install.ps1` chamam `python`, então para usá-los o executável `python` precisa estar no PATH ou a instalação de Python precisa ser corrigida conforme a política do seu computador.

## 3. Obter o produto DBFlow

Use apenas uma destas formas. O nome e o local da pasta escolhida serão usados nos comandos seguintes.

### Opção A: recebeu o ZIP de distribuição

1. Salve o ZIP fornecido pela equipe de distribuição.
2. No Explorador de Arquivos, clique com o botão direito no ZIP e escolha **Extrair Tudo**.
3. Extraia para uma pasta simples, por exemplo `D:\Ferramentas\dbflow`.
4. Abra essa pasta e confirme que vê `Install.ps1`, `Build.ps1`, `dbctl.py` e a pasta `server`.
5. A distribuição pode trazer `server\publish\DbFlow.Server.dll` compilado. Se estiver presente, o SDK não é necessário para compilar; o runtime ASP.NET Core 8 continua necessário para executar.

### Opção B: clonar o repositório fonte

Clonar significa baixar uma cópia Git do repositório. O código-fonte ignora `server/publish`, portanto você compila antes de usar. A URL abaixo é a URL do produto indicada pelo README deste pacote; se sua empresa fornecer um espelho interno, use o endereço fornecido pela equipe.

```powershell
New-Item -ItemType Directory -Force -Path 'D:\Ferramentas' | Out-Null
Set-Location 'D:\Ferramentas'
git clone https://github.com/Lucasvor/dbflow.git
Set-Location .\dbflow
```

Confirme que está na pasta certa:

```powershell
Get-Location
Get-ChildItem Install.ps1, Build.ps1, dbctl.py
```

O primeiro comando deve terminar em `D:\Ferramentas\dbflow` (ou o caminho onde você clonou). O segundo deve listar os três arquivos. Compile o servidor:

```powershell
.\Build.ps1
```

Resultado esperado: publicação do servidor em `server\publish`, terminando sem erro. `Build.ps1` usa `dotnet publish` para o projeto .NET 8. O SDK .NET 8 deve estar instalado. O instalador com `-Apply` também tenta chamar `Build.ps1` se a DLL publicada não existir; compilar primeiro torna o passo visível.

### Como abrir o PowerShell já na pasta

No Explorador de Arquivos, abra a pasta do produto. Clique na barra de endereço, digite `powershell` e pressione Enter. Para PowerShell 7, digite `pwsh`. A janela abre nessa pasta. Confira com:

```powershell
Get-Location
```

Outra opção é abrir PowerShell pelo menu Iniciar e usar `Set-Location` com o caminho completo, como nos exemplos.

Se a execução de scripts estiver bloqueada pela política local, não altere a política permanentemente nem tente contorná-la sem autorização. Peça à equipe de suporte a forma aprovada de executar o script assinado/distribuído. Uma política corporativa pode prevalecer sobre as instruções gerais deste guia.

## 4. Faça primeiro um ensaio Local

O modo **Local** é a melhor primeira experiência se ainda não há URL GitLab, infraestrutura ou token fornecido. “Local” quer dizer que o journal e as chamadas MCP acontecem no computador/projeto. Sem um repositório GitLab configurado, o ensaio pode gerar estado local e exibir sincronização `not_configured` ou pendente. **Isso não significa que eventos foram sincronizados com outra máquina ou com um servidor.**

O modo Local não exige conexão a um banco de negócio. Também não exige que você invente URL de GitLab. Para registrar no GitLab central mais tarde, você precisará da URL real e de credencial autorizada.

### 4.1 Escolha ou crie uma pasta de aplicação de teste

Use uma pasta de teste separada da aplicação real enquanto aprende. Este exemplo cria uma pasta vazia:

```powershell
New-Item -ItemType Directory -Force -Path 'D:\Projetos\DBFlow-Treino' | Out-Null
```

Se quiser que ela seja um repositório Git local, opcionalmente rode:

```powershell
Set-Location 'D:\Projetos\DBFlow-Treino'
git init
```

O DBFlow não exige `git init` para executar o MCP local, mas manter scripts de uma aplicação em controle de versão costuma ser parte do processo de desenvolvimento da equipe.

### 4.2 Faça uma prévia sem modificar a pasta

Abra PowerShell na pasta do produto e rode:

```powershell
Set-Location 'D:\Ferramentas\dbflow'
.\Install.ps1 -Project 'D:\Projetos\DBFlow-Treino'
```

Sem `-Apply`, o instalador mostra uma prévia. Ele não grava a instalação e não solicita segredo. Leia a saída e confirme que o caminho do projeto é o de treino.

### 4.3 Instale Local, com URLs ainda pendentes

```powershell
.\Install.ps1 -Project 'D:\Projetos\DBFlow-Treino' -Mode Local -Interactive -Apply
```

Quando aparecer a pergunta do modo, pressione Enter para manter `Local`. Quando pedir a URL HTTPS do repositório central, pressione Enter se ainda não recebeu esse endereço. A credencial PAT/OAuth também pode permanecer pendente. Não informe uma senha de banco de negócio; essa não é uma credencial DBFlow.

O `-Apply` grava arquivos gerenciados no diretório do projeto. Se o pacote não trouxe o servidor publicado, o script tenta compilar antes da instalação. Resultado esperado: arquivos de configuração no projeto e mensagem sem erro. Isso comprova que os arquivos foram configurados, não que o aplicativo cliente já os aceitou.

Para não usar as perguntas interativas, a mesma intenção Local é:

```powershell
.\Install.ps1 -Project 'D:\Projetos\DBFlow-Treino' -Mode Local -Apply
```

Essa forma não pede URL. A configuração permanece sem endpoint central, e o status deve refletir esse fato.

## 5. O que a instalação cria no projeto

Abra a pasta da aplicação no Explorador de Arquivos. Com “Exibir itens ocultos” habilitado, procure:

```text
D:\Projetos\DBFlow-Treino\
├── AGENTS.md                              # instruções persistentes para Codex
├── CLAUDE.md                              # instruções persistentes para Claude Code
├── .codex\config.toml                     # MCP e hooks do Codex
├── .codex\agents\dbflow-auditor.toml     # agente auditor do Codex
├── .claude\settings.json                  # hooks do Claude Code
├── .claude\agents\dbflow-auditor.md      # agente auditor do Claude Code
├── .mcp.json                              # MCP do Claude Code
├── .cursor\hooks.json                     # hooks do Cursor
├── .cursor\mcp.json                       # MCP do Cursor
├── .cursor\rules\dbflow.mdc               # regra persistente do Cursor
├── .cursor\agents\dbflow-auditor.md      # agente auditor do Cursor
├── .db-control\project.json              # modo, project_key e configuração não secreta
├── .db-control\WORKFLOW.md               # fluxo persistente para o agente
├── .db-control\runtime\                  # runtime publicado e CLI local
├── .db-control\local\                    # journal, fila e credencial DPAPI locais
├── .db-control\backups\                  # cópias de arquivos substituídos
└── .db-control\install-manifest.json     # arquivos gerenciados pelo instalador
```

Nem todo arquivo aparece para todo cliente ou se a versão existente tinha configuração diferente. `.db-control/local` é estado local e não deve ser adicionado ao Git nem copiado para outro computador como se fosse o registro central. O instalador inclui os caminhos de estado local/backup no `.gitignore` gerenciado.

### Os três clientes: regras, MCP, hooks e auditor

Ao instalar com `Clients` padrão (`codex,claude,cursor`), o DBFlow prepara os três conjuntos de arquivos:

| Cliente | Regra persistente | MCP | Hooks | Auditor independente |
|---|---|---|---|---|
| Codex | `AGENTS.md` ou instrução gerenciada/override existente | bloco MCP stdio em `.codex/config.toml` | eventos nativos `SessionStart`, `PostToolUse` e `Stop` em `.codex/config.toml` | `.codex/agents/dbflow-auditor.toml` |
| Claude Code | `CLAUDE.md` | `.mcp.json` | `.claude/settings.json` | `.claude/agents/dbflow-auditor.md` |
| Cursor | `.cursor/rules/dbflow.mdc`, regra `alwaysApply` | `.cursor/mcp.json` | `.cursor/hooks.json` | `.cursor/agents/dbflow-auditor.md` |

Você pode instalar apenas clientes usados na máquina, passando `-Clients 'codex'`, por exemplo. O padrão do script é preparar todos os três. Os arquivos dos clientes são instruções/configuração; o DBFlow não abre os aplicativos para você e não pode afirmar que eles carregaram os arquivos.

O auditor é um perfil de agente que outro agente principal pode invocar para revisar diff, candidatos, revisões e pacote. A instrução persistente recomenda revisão independente antes de entregar ao DBA. Ele não é um processo que roda em segundo plano e lança uma IA sozinho. Se o aplicativo não oferece subagentes, o agente deve informar essa limitação.

Os hooks executam o helper de auditoria em eventos do cliente, no contexto suportado por cada aplicativo. Eles não são um daemon DBFlow, não observam todos os arquivos o tempo inteiro e não rodam com o aplicativo fechado. A configuração de hook tampouco garante que o cliente a executou.

## 6. Confiar e habilitar no aplicativo cliente

Feche e reabra o aplicativo cliente depois de instalar ou alterar configuração. Abra o diretório da aplicação (o projeto, não a pasta do produto). O cliente pode exigir que você confie no diretório, revise permissões de ferramentas/hooks ou aprove configuração do projeto.

**Codex:** confie o projeto na interface do Codex e abra o comando/painel `/hooks` para revisar e autorizar os hooks DBFlow. A configuração escrita no arquivo não prova que o hook foi autorizado. O Codex instalado precisa ter suporte aos hooks correspondentes. A validação desta versão foi feita com Codex CLI 0.153.4 e hooks estáveis, mas a equipe ainda não observou a interface real carregando a configuração após reabrir.

**Claude Code e Cursor:** use os controles de confiança e ferramentas/MCP/hooks que a sua versão do aplicativo apresentar. Os nomes e telas podem variar por versão. Este guia não presume um botão ou uma tela específica. Se o aplicativo mostrar uma solicitação de aprovação, leia o comando/caminho e autorize apenas se estiver dentro da pasta de projeto e corresponder à instalação esperada. Se não aparecer, verifique os arquivos de configuração listados acima e consulte a documentação oficial do cliente para sua versão.

Não aprove às cegas um comando desconhecido, nem cole token em uma conversa do agente. O cliente pode exigir reinício depois de uma aprovação ou alteração.

## 7. Diagnóstico: o que foi configurado e o que foi observado

Execute a CLI que foi copiada para o projeto:

```powershell
Set-Location 'D:\Projetos\DBFlow-Treino'
python .db-control\runtime\dbctl.py --project . doctor
```

Se `python` não funcionar, corrija o PATH de Python primeiro. Saída esperada: JSON de diagnóstico com campos sobre modo/configuração, runtime MCP, hooks encontrados, última invocação do helper e conectividade. Leia os campos individualmente:

| Campo/valor | O que permite concluir | O que não permite concluir |
|---|---|---|
| `client_hooks.<cliente>.configured: true` | A CLI encontrou no arquivo do cliente os eventos de hook esperados. | Não prova que o cliente confiou, aceitou ou disparou o hook. |
| `last_helper_invocation` com `event` e `last_seen` | O helper DBFlow foi executado em algum momento e registrou evento/horário. | Não identifica, por si só, se foi disparo nativo do cliente ou execução manual. |
| `codex_hooks` indicando aprovação/confiança necessária | O Codex exige que o projeto seja confiável e que os hooks sejam revisados/aceitos no controle `/hooks`. | O registro de configuração não afirma que a etapa de aprovação já ocorreu. |
| `gitlab_connectivity: not_tested_by_doctor` | `doctor` não testou conexão com GitLab. | Não conclua que token, rede, branch ou API estão funcionando. |
| `database_access: not_implemented_by_design` | O DBFlow não tem acesso ao banco de negócio. | Não existe teste de conexão SQL escondido nessa execução. |
| `gitlab_endpoint_configured: false` ou endpoint pendente | URL/projeto GitLab central não está completo na configuração. | Não há sincronização remota comprovada. |
| `registry_configured` no relatório de auditoria | A auditoria local usa esse nome para indicar configuração do registro central. | Não comprova comunicação bem-sucedida com GitLab. |

`last_seen` é um horário da última chamada ao helper, não um monitor contínuo e nem uma prova de execução pela UI. Não há painel de histórico de sessões nem heartbeat quando o cliente está fechado.

Confira também o estado sem segredo:

```powershell
Get-Content .db-control\project.json
$manifest = Get-Content .db-control\install-manifest.json -Raw | ConvertFrom-Json
$manifest | Select-Object updated_at, clients
$manifest.files.PSObject.Properties.Name
```

`project.json` informa modo, `project_key`, URLs e mapas não secretos. O manifest lista arquivos gerenciados pelo instalador e guarda cópias originais codificadas em Base64 para restauração; não mostre nem compartilhe seu conteúdo inteiro. A pasta `.db-control/backups` também pode conter configurações originais privadas. Não publique manifest, backups, pasta `local` ou conteúdo de credenciais DPAPI em chamados.

Para pedir ao cliente uma verificação humana, diga:

> “Consulte o DBFlow neste projeto: mostre o resultado de `dbflow_status`, diga se a sincronização está `synced`, `pending` ou `not_configured`, e diferencie isso de uma confirmação de execução SQL. Se a ferramenta MCP não estiver disponível, pare e explique qual configuração precisa ser verificada.”

O agente só poderá chamar a ferramenta MCP se o próprio cliente a tiver carregado e aprovado.

## 8. Configurar Local quando a URL real chegar

Depois que a equipe informar a URL HTTPS do projeto GitLab central, sua branch e o tipo de credencial autorizado, atualize a configuração. Não adivinhe nomes de grupo, branch ou permissões.

Uma forma é executar novamente o instalador, informando a URL real:

```powershell
Set-Location 'D:\Ferramentas\dbflow'
.\Install.ps1 -Project 'D:\Projetos\DBFlow-Treino' -Mode Local `
  -RepositoryUrl 'https://gitlab.empresa.example/grupo/scripts-banco' -Apply
```

Se a URL GitLab está em subdiretório, por exemplo `https://host/gitlab`, a equipe deve fornecer a URL de instância correta e você pode informar `-GitLabInstanceUrl`. URLs de projeto podem conter subgrupos e `.git`; o instalador normaliza esses formatos. Não use link de arquivo, branch nem credencial embutida na URL.

Para configurar o PAT de forma oculta e guardá-lo criptografado no Windows para sua conta, acrescente `-PromptToken`:

```powershell
.\Install.ps1 -Project 'D:\Projetos\DBFlow-Treino' -Mode Local `
  -RepositoryUrl 'https://gitlab.empresa.example/grupo/scripts-banco' `
  -PromptToken -Apply
```

O prompt não mostra o token enquanto você digita. O servidor grava a credencial com DPAPI do Windows em `.db-control/local/credentials.dpapi.json`. Essa credencial é protegida para a conta Windows/máquina no contexto usado e **não é portátil**: não copie esse arquivo para outra conta ou PC esperando que seja descriptografado. Reabra o aplicativo após rotação de credencial. Alternativamente, a organização pode fornecer `DBFLOW_GITLAB_TOKEN` pelo gerenciador de segredos/ambiente aprovado.

Não escreva token como argumento PowerShell, em arquivo versionado, no `project.json`, em prompt de agente, email, captura de tela ou histórico compartilhado. Não cole segredos nesta conversa. Um PAT individual precisa da permissão GitLab de escrita exigida na branch. Um token de grupo/projeto de serviço costuma ser administrado pela plataforma. Deploy token não serve para a API REST de commits usada pelo DBFlow.

`-Authentication oauth_access_token` aceita um access token OAuth que já foi obtido por um provedor externo. Não há login OAuth interativo em navegador nem renovação automática neste produto. Se a equipe fornece o token OAuth, ela também deve explicar como ele é renovado e entregue com segurança.

Após a configuração, reabra o cliente e peça `dbflow_status`. A sincronização só deve ser tratada como concluída quando o retorno do fluxo indicar `synced`. `doctor` não testa GitLab. Se o GitLab estiver fora da rede ou o token expirar, o registro pode permanecer pendente e deverá ser retomado.

## 9. Configurar Remote quando a plataforma estiver pronta

Use **Remote** quando o administrador já tiver implantado o serviço e fornecido todos estes valores:

- URL HTTPS completa da Route MCP, incluindo `/mcp/<project_key>`;
- token MCP individual autorizado para aquele `project_key`;
- confirmação de que o projeto está autorizado no servidor;
- URL do repositório central GitLab que o servidor usa;
- destinos PRO/PRI e ambientes configurados pelo administrador no servidor.

Exemplo de forma (endereços fictícios; não copie como configuração real):

```powershell
.\Install.ps1 -Project 'D:\Projetos\MinhaAplicacao' -Mode Remote `
  -RepositoryUrl 'https://gitlab.empresa.example/grupo/scripts-banco.git' `
  -McpUrl 'https://dbflow.apps.empresa.example/mcp/minhaaplicacao' `
  -PromptToken -Apply
```

Neste exemplo, o nome da pasta é `MinhaAplicacao`, então o `project_key` inicial é `minhaaplicacao`; o final da URL MCP tem que corresponder exatamente. Se a equipe escolheu outro identificador, alinhe antes de instalar e peça ao administrador que gere a configuração Server com esse mesmo `project_key`. Digite o token MCP no prompt oculto. Ele deve ter 32 caracteres ou mais e fica guardado com DPAPI local. O cliente também pode receber `DBFLOW_SERVICE_TOKEN` pelo mecanismo de segredos aprovado pela empresa. Variável de ambiente tem prioridade sobre DPAPI; feche e reabra o cliente depois de rotacionar ou alterar variável.

No modo Remote, o computador mantém auditoria de arquivos e solicitações locais pendentes, mas a configuração do **servidor** é autoridade para os destinos e fingerprints de banco/ambiente. O `project_key` inicial deriva do nome da pasta da aplicação, normalizado como identificador. A URL MCP precisa terminar exatamente com `/mcp/<project_key>` e esse mesmo identificador precisa existir na configuração do servidor. Por isso, alinhe com o administrador o nome antes de instalar, especialmente se clones de Git usarem nomes diferentes. O comando `configure --project-key` existe na CLI, mas não troque a identidade depois que houver eventos nem sem coordenar com a plataforma; o servidor pode rejeitar a solicitação.

Quando o servidor não está publicado, instalar valores Remote inventados não cria conexão. Faça o ensaio Local enquanto aguarda. Quando receber URL/token reais, altere para Remote e reinstale/atualize, sem compartilhar o token.

## 10. Registrar os destinos de banco e ambiente

**Banco lógico** é um nome curto, como `PRO` ou `PRI`. **Ambiente** é onde a mudança deve ser entregue, como `TI`, `HML` ou `PRD`. Um **alias de instância** e o nome físico identificam o destino configurado sem guardar senha de conexão.

No modo Local, o desenvolvedor pode mapear destinos que a equipe autorizou. Exemplo ilustrativo para `PRO` em `TI`:

```powershell
python .db-control\runtime\dbctl.py --project . map-database `
  --database PRO --engine sqlserver --environment TI `
  --instance-alias sql-ti --physical-name PRO
```

Repita para cada combinação autorizada, por exemplo `PRO`/`HML`, `PRO`/`PRD`, `PRI`/`TI`, `PRI`/`HML` e `PRI`/`PRD`. Substitua aliases e nomes físicos pelos valores fornecidos pela equipe; os exemplos não apontam para servidores reais.

No modo Remote, o comando acima altera configuração local e **não** altera o servidor. O administrador deve configurar os mapas na pasta usada para gerar a configuração Server, regenerar/aplicar a configuração e informar ao desenvolvedor os fingerprints válidos. A resposta de `dbflow_status` deve mostrar os destinos remotos.

O mapa não contém senha/connection string e não abre conexão. O DBFlow pode preparar artefatos para vários engines (`sqlserver`, `oracle`, `mongodb`, `postgresql`, `mysql`, `other`), mas não interpreta/executa SQL nem consulta definições diretamente.

`status` pode informar fingerprint do destino. Trate-o como identificação de configuração para vincular evidências ao destino escolhido, não como prova de que aquele banco foi consultado ou alterado.

## 11. Seu primeiro projeto e primeira demanda

O fluxo de treino começa com arquivos de script sob controle do seu projeto. Não use nomes ou definições reais de produção em um treino aberto. Confirme com a equipe quais scripts e dados podem ser usados.

1. Abra o diretório da aplicação no cliente (Codex, Claude Code ou Cursor) e confirme que MCP/hook foram carregados pelos controles do cliente.
2. Crie ou abra um arquivo `.sql` candidato dentro de uma pasta permitida. O padrão de arquivos examinados está configurado no projeto; `dbflow_scan` procura candidatos locais correspondentes, não bancos. Em uma aplicação antiga, ele pode encontrar scripts históricos que já estavam no checkout. **Candidato não significa mudança nova desta demanda.** Ajude o agente a separar baseline/histórico do diff atual; não atribua trabalho preexistente à demanda. Se um candidato antigo não deve continuar na fila, peça exclusão do hash exato com um motivo (ferramenta `dbflow_exclude_candidate`); uma edição posterior muda o hash e volta a aparecer para análise.
3. Peça ao agente para identificar a demanda, ler o contexto e chamar `dbflow_status` e `dbflow_scan`. Não peça que invente IDs de revisão, fingerprints ou respostas do DBA.
4. O agente identifica um conjunto coerente de mudanças de banco e, como parte do fluxo configurado, registra a revisão completa usando `dbflow_register_revision`, sem exigir confirmação manual por chamada. O usuário revisa o diff/SQL e corrige classificação, demanda, banco, dependências ou conteúdo quando necessário. O agente pergunta se identidade da demanda ou destino continuarem ambíguos; não inventa dados.
5. A revisão imutável contém o conjunto detectado, script completo e metadados retornados. Se a ferramenta não estiver carregada, o agente informa que o registro automático não ocorreu; não deve fingir que registrou.
6. Observe o retorno. `queued` ou `pending` significa que existe uma solicitação/evento ainda não sincronizado; `synced` significa que o evento chegou ao registro configurado. Nenhum dos dois prova aplicação SQL.
7. Antes de preparar entrega para HML/PRD, siga o processo de revisão/aprovação da equipe e use o perfil `dbflow-auditor` como revisão independente. O registro da revisão em DBFlow não substitui essa aprovação.

Prompt copiável para o agente:

> “Estou trabalhando na demanda `<ID>`. Consulte `dbflow_status` e `dbflow_scan`, analise os arquivos SQL alterados, associe candidatos somente quando houver evidência de que pertencem a esta demanda e classifique bancos PRO/PRI, objetos, dependências e verificação. Não execute SQL nem presuma consulta ao banco. Registre automaticamente cada conjunto coerente de mudanças como revisão imutável completa em `dbflow_register_revision`, com ID de operação estável para retry. Pergunte somente se demanda ou destino estiver ambíguo. Mostre o resumo e o estado real (`pending`, `queued` ou `synced`) para eu revisar; não declare aplicação.”

O agente deve manter fatos separados: um arquivo alterado não é automaticamente mudança aplicada; um evento central não é confirmação de execução; uma confirmação num banco não se aplica a outro banco ou ambiente.

### Exercício copiável em sandbox, sem GitLab e sem banco real

Este exercício usa a pasta separada `D:\Projetos\DBFlow-Treino` e nomes de destino fictícios. O DBFlow não conecta ao banco, mas os comandos SQL são exemplos: **não os execute em nenhum banco**. Não use este mapa fictício numa aplicação real. Os aliases e nomes físicos abaixo são apenas texto para provar a geração local de artefatos.

Crie dois scripts de treino:

```powershell
Set-Location 'D:\Projetos\DBFlow-Treino'
New-Item -ItemType Directory -Force -Path '.\database' | Out-Null
$sqlPro = @'
-- DBFlow treino somente; não executar em banco.
CREATE TABLE dbo.DbFlowTreinoPedido (
    Id int NOT NULL,
    Descricao varchar(80) NULL
);
'@
$sqlPri = @'
-- DBFlow treino somente; não executar em banco.
CREATE TABLE dbo.DbFlowTreinoAuditoria (
    Id int NOT NULL,
    Evento varchar(80) NOT NULL
);
'@
$utf8SemBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText((Join-Path (Get-Location) 'database\001_treino_pro.sql'), $sqlPro, $utf8SemBom)
[System.IO.File]::WriteAllText((Join-Path (Get-Location) 'database\002_treino_pri.sql'), $sqlPri, $utf8SemBom)
```

Registre seis destinos fictícios no projeto Local. Os aliases `sem-conexao` não resolvem para nenhum servidor:

```powershell
$treino = @(
    @{ Banco = 'PRO'; Ambiente = 'TI';  Fisico = 'DBFLOW_TREINO_PRO_TI' },
    @{ Banco = 'PRO'; Ambiente = 'HML'; Fisico = 'DBFLOW_TREINO_PRO_HML' },
    @{ Banco = 'PRO'; Ambiente = 'PRD'; Fisico = 'DBFLOW_TREINO_PRO_PRD' },
    @{ Banco = 'PRI'; Ambiente = 'TI';  Fisico = 'DBFLOW_TREINO_PRI_TI' },
    @{ Banco = 'PRI'; Ambiente = 'HML'; Fisico = 'DBFLOW_TREINO_PRI_HML' },
    @{ Banco = 'PRI'; Ambiente = 'PRD'; Fisico = 'DBFLOW_TREINO_PRI_PRD' }
)
foreach ($destino in $treino) {
    python .db-control\runtime\dbctl.py --project . map-database `
      --database $destino.Banco --engine sqlserver --environment $destino.Ambiente `
      --instance-alias 'sem-conexao' --physical-name $destino.Fisico
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao gravar mapa fictício de treino.' }
}
```

Abra essa pasta no aplicativo cliente e peça:

> “Este é um exercício local DBFlow em sandbox, sem GitLab e sem banco real. Consulte status e scan. Os dois arquivos em `database/` são inteiramente fictícios e não devem ser executados. Identifique-os como uma demanda `TREINO-001` para PRO e PRI, registre automaticamente uma revisão completa do conjunto e mostre o ID da revisão e estado de sincronização. Depois, como exercício, prepare um bundle para HML usando somente essa revisão e os mapeamentos fictícios; mostre manifesto, fingerprints e rascunho de email. Não envie email nem registre despacho ou aplicação.”

Resultado esperado: o agente mostra candidatos, cria registro local, retorna ID de revisão e pode gerar artefatos HML se o destino/dependências estiverem completos. Sem URL GitLab, o estado é local/`not_configured` ou pendente, nunca `synced`. O rascunho não é enviado. Não registre dispatch, aplicação, snapshot, aprovação HML ou PRD fictícios: o objetivo é aprender a distinguir conteúdo preparado de ação real. Ao terminar, mantenha a pasta claramente identificada como treino; não copie seus mapas para um projeto real.

DBFlow não mantém um campo de aprovação formal de mudança para PRD. `dbflow_status` mostra revisões, pacotes, destinos, confirmações e sincronização; não prova que CAB/gestor/DBA aprovou uma promoção. A aprovação vem do processo corporativo e precisa ser confirmada fora do DBFlow antes da entrega. O agente pode receber a informação do usuário, mas não deve dizer que o status DBFlow confirmou esse gate.

## 12. Exemplo completo, da demanda à retomada em PRD

Este exemplo acompanha uma alteração fictícia `CHG-4821` em duas bases lógicas, `PRO` e `PRI`. Os nomes e resultados são exemplos. Use sempre IDs, revisões, bundles e fingerprints devolvidos pelas ferramentas, nunca copie identificadores fictícios como se fossem reais.

### A. A demanda nasce no ambiente TI

1. A pessoa recebe a demanda `CHG-4821` e cria/edita os scripts necessários no checkout da aplicação.
2. O desenvolvedor valida o conteúdo e segue o processo autorizado para TI. DBFlow não executa nem valida SQL no banco TI.
3. O agente chama `dbflow_status` e `dbflow_scan`. Se há uma revisão anterior da mesma demanda, recupera-a com `dbflow_get_revision` antes de propor uma filha. Se é a primeira revisão, não inventa uma revisão pai.
4. O agente identifica que há uma mudança para `PRO` e outra para `PRI`, com scripts integrais, objetos, dependências e instruções de verificação separadas.
5. O agente chama `dbflow_register_revision` ao identificar o conjunto coerente, com `demand_id`, `title`, `parent_revision`, `operation_id` estável para retry e `changes[]` completos. Desenvolvedor revisa o resumo/diff e corrige o registro se necessário; não é necessário aprovar individualmente a chamada de captura. A revisão humana independente e a aprovação do processo são feitas antes da entrega a HML/PRD.
6. Se o resultado é `pending`/`queued`, a revisão está no journal local aguardando sincronização; corrija URL, rede ou token e retome. Se `synced`, o evento está no repositório central configurado. Em ambos os casos, aplicação ainda não foi comprovada.

Prompt copiável:

> “Acompanhe CHG-4821. Confira status, candidatos e revisão pai. Ao identificar o conjunto coerente de mudanças para PRO e PRI, registre automaticamente a revisão integral com operation ID estável. Não altere o SQL. Mostre scripts, risco, classificação e estado real de sincronização para minha revisão; pergunte somente se demanda ou destino estiverem ambíguos. Antes de qualquer bundle HML/PRD, aguarde o processo de revisão/aprovação aplicável.”

### B. Preparar o primeiro pacote R1 para HML

1. Confirme que PRO/HML e PRI/HML têm mapeamento válido. Em Remote, o mapa precisa vir do servidor.
2. Com a revisão aprovada, o agente chama `dbflow_prepare_bundle` selecionando explicitamente a revisão aprovada e `environment: HML`.
3. A ferramenta devolve um `bundle_id`, manifesto, scripts, destinos/fingerprints e um `email.txt` de rascunho. Esse arquivo é conteúdo preparado, não uma mensagem enviada.
4. Se o bundle falha por destino ausente, dependência não selecionada, conflito ou sobreposição, resolva com equipe/desenvolvedor antes de tentar outro pacote.
5. Revise pacote, scripts, revisão, banco, ambiente, fingerprint, ordem/dependências e verificação. O auditor independente deve informar suas ressalvas.

Prompt copiável:

> “Prepare o bundle HML para a revisão aprovada de CHG-4821 em PRO e PRI. Use somente os IDs retornados pelo status/revisão, verifique dependências e destinos de HML e mostre manifesto, scripts, fingerprints e rascunho de email para revisão. Não envie o email.”

### C. Envio humano e registro do despacho

1. Um usuário autorizado baixa/copía o pacote e usa o processo de email/ticket da empresa para enviá-lo ao DBA.
2. O usuário confere destinatário e anexos e envia pelo seu próprio cliente de email. DBFlow não consegue enviar ou ler esse email.
3. Depois de enviar, o usuário pede ao agente para registrar o despacho com `dbflow_report_dispatch`, `bundle_id` correto, referência ao ticket/email e horário informado.
4. Registro de despacho quer dizer “usuário relatou que enviou”. Não significa que o DBA recebeu, aplicou ou aprovou.

Prompt copiável:

> “Eu enviei ao DBA o bundle `<bundle_id>` pelo ticket `<referência>`, às `<horário com fuso>`. Registre o relato de despacho usando `dbflow_report_dispatch`. Não marque nenhuma aplicação como concluída.”

### D. Retomar quando vier resposta do DBA

1. Dias depois, quando a pessoa voltar ao projeto, peça para consultar `dbflow_status` e buscar o bundle existente com `dbflow_get_bundle`. Não prepare outro pacote para descobrir o que aconteceu.
2. Leia o retorno humano por banco e mudança. Neste exemplo, o DBA relata que aplicou R1 em `PRO/HML`; `PRI/HML` continua sem resposta.
3. O agente confere o `bundle_id`, IDs de mudança/revisão e fingerprint originais. Registra `manual_applied` para PRO/HML apenas se a pessoa relata a aplicação, com referência e hora. Isso continua sendo relato humano; não é recibo automático.
4. Não marque PRI/HML como aplicado por associação. Não estenda o registro a PRD.
5. Se houver ferramenta de execução autorizada ou exportação do estado do banco coletada pelo usuário, pode haver evidência separada de execução (`execution_succeeded`) ou observação (`state_matches`/`state_differs`). DBFlow não coleta essa evidência por conta própria.

Prompt copiável:

> “Retome CHG-4821. Consulte status e recupere o bundle HML que já foi enviado. A resposta do DBA é: PRO/HML aplicado em `<data/hora>`, referência `<ticket>`; PRI/HML ainda pendente. Registre somente o fato informado para PRO com o bundle, change IDs e fingerprint originais. Liste o que continua pendente; não prepare um pacote novo.”

### E. R2 nasce antes da confirmação de PRI

Uma segunda alteração da demanda pode criar a revisão R2 antes de o DBA responder sobre PRI em R1. R2 é uma revisão separada. Confirmações R1/PRO/HML não migram para R1/PRI, R2/PRO ou R2/PRI. Prepare um bundle R2 explicitamente apenas depois de revisar dependências e conteúdo. O histórico não deve ser reescrito para aparentar que o DBA recebeu R2.

### F. Promoção independente para PRD

1. Aguarde a autorização prevista no processo da empresa. HML não libera PRD automaticamente.
2. Prepare novo bundle selecionando explicitamente a revisão que será promovida e `environment: PRD`.
3. Revise destinos/fingerprints PRD. São diferentes dos destinos/fingerprints HML. Se estiverem ausentes ou inesperados, pare e peça correção ao administrador.
4. Um usuário autorizado envia o novo rascunho pelo canal corporativo; depois relata o despacho ao DBFlow.
5. Quando houver retorno, registre confirmação separada por cada banco/change e evidência. Só indique `execution_succeeded` se houver evidência de execução adequada; só registre `manual_applied` como relato humano.

Prompt copiável:

> “O processo corporativo autorizou a promoção da revisão `<revision_id>` para PRD; essa autorização foi dada fora do DBFlow e eu a estou informando agora. Confira o status da revisão, dependências e os fingerprints de PRO/PRD e PRI/PRD. Prepare uma entrega PRD somente para essa revisão e mostre manifesto e rascunho. O status DBFlow não comprova aprovação formal; não reutilize confirmação HML, não envie email nem registre aplicação.”

Resumo dos fatos que não devem ser confundidos:

| Fato | O que comprova |
|---|---|
| Revisão registrada | O conteúdo e metadados da revisão foram registrados no journal/ledger conforme o estado de sincronização. |
| Bundle preparado | Um conjunto imutável de arquivos/destinos foi congelado para revisão e entrega. |
| Email rascunho gerado | Texto sugerido foi preparado; não houve envio. |
| Dispatch registrado | Usuário informou que enviou; não comprova recebimento ou aplicação. |
| `manual_applied` | Foi registrado um relato manual de aplicação. |
| `execution_succeeded` | Foi registrada evidência de execução reportada pela ferramenta/processo autorizado. |
| Snapshot / `state_matches` | Um usuário coletou/informou estado observado dentro do escopo declarado. Não prova qual script o causou. |

## 13. Inventário externo e snapshots

Para comparar definição de objeto, o usuário pode usar uma ferramenta autorizada da organização (por exemplo, exportador/cliente de banco já aprovado) ou um snapshot de definições recebido de uma fonte confiável. O próprio DBFlow não tem credencial nem conexão MCP com o banco.

`dbflow_record_snapshot` registra conteúdo e cobertura informados pelo usuário. Uma comparação é textual e limitada ao escopo declarado. Inventário parcial não prova que objeto ausente foi apagado. Diferenças também não dizem quem alterou nem em que momento. Não suba dumps de dados de negócio; use somente definições/artefatos permitidos e minimize dados sensíveis.

Prompt copiável:

> “Não conecte ao banco. Vou fornecer um arquivo de definições exportado por uma ferramenta autorizada e informar exatamente quais schemas/objetos ele cobre. Analise a cobertura, faça comparação textual quando aplicável e registre snapshot somente após mostrar o escopo e os limites. Não conclua que objetos fora da cobertura foram removidos.”

## 14. Retomar trabalho, ficar offline e trocar de computador

### Retomar no mesmo checkout

Ao voltar ao projeto, peça ao agente para consultar `dbflow_status`, filtrar a demanda, recuperar revisão e pacote existentes e listar pendências. Não crie uma nova revisão/bundle só porque a sessão anterior acabou. O DBFlow preserva o journal local e a outbox no checkout; o GitLab central é o registro compartilhado quando sincronizado.

Prompt copiável:

> “Retome a demanda `<ID>` pelo estado atual. Consulte `dbflow_status`, recupere revisões com `dbflow_get_revision` e bundles já preparados com `dbflow_get_bundle`. Liste pendências por revisão, banco e ambiente. Não crie registros novos nem presuma que o email ou SQL foi executado.”

### Trabalhar sem conexão central

No modo Local sem URL/token, você pode testar inspeção de candidatos e o fluxo local. Eventos permanecerão locais ou `not_configured`; outros computadores não verão o journal pelo simples fato de usar a mesma pasta de aplicação num ZIP. Quando a rede voltar, configure o endpoint real e a credencial e retome status para sincronizar. Confirme explicitamente o retorno antes de dizer que publicou.

Em Remote, falhas de rede, certificado, endpoint ou token podem manter requisições locais em fila/pending. O servidor não sincroniza continuamente depois que o cliente fecha. Uma chamada posterior ao MCP (por exemplo, status) pode tentar novamente. Requisição rejeitada precisa de correção; use os IDs/estados retornados e não repita com argumentos diferentes sob o mesmo ID de operação.

### Trocar de máquina

1. Instale o runtime ASP.NET Core 8, Python e o cliente de desenvolvimento aprovados na máquina nova.
2. Obtenha o projeto de aplicação pelo Git da empresa ou pelo procedimento corporativo. Antes de atualizar/reinstalar DBFlow, verifique se runtime instalado já está versionado:

   ```powershell
   Set-Location 'D:\Projetos\MinhaAplicacao'
   git ls-files .db-control/runtime
   Test-Path .db-control\install-manifest.json
   ```

   O manifest de instalação é ignorado pelo Git, enquanto alguns checkouts podem conter arquivos `.db-control/runtime` versionados. Nessa situação, o instalador pode recusá-los como arquivos existentes não gerenciados e parar para evitar sobrescrita.
3. Obtenha o produto DBFlow/pacote aprovado. **Não apague runtime versionado nem sobrescreva arquivos para contornar o erro.** Peça à equipe mantenedora um procedimento de migração para esse checkout; este pacote não fornece um fluxo automático para reconstruir o manifest ignorado em outra máquina.
4. Reconfigure Remote/Local e credenciais com a equipe; credenciais DPAPI da máquina/conta antiga não são portáteis.
5. Confirme que o ledger central está sincronizado e acessível antes de considerar o histórico compartilhado. Estado ainda não sincronizado que existe apenas em `.db-control/local` não é migrado automaticamente ao clonar projeto.

Não copie credenciais DPAPI para a máquina nova. Não apague o checkout antigo antes de resolver qualquer estado local pendente. Se o histórico só existe no computador anterior, peça ajuda ao responsável DBFlow para preservar/recuperar o journal de forma segura.

## 15. Atualizar ou remover com cuidado

Antes de atualizar ou remover, confira se há pendências locais e preserve trabalho de aplicação pelo processo normal de Git/backup da empresa. Não apague manualmente o `.db-control/local` se houver evento ainda não sincronizado.

### Atualizar arquivos gerenciados

Obtenha uma nova versão aprovada do produto e rode o instalador novamente na mesma pasta da aplicação, com os mesmos parâmetros reais. Exemplo Local:

```powershell
Set-Location 'D:\Ferramentas\dbflow'
.\Install.ps1 -Project 'D:\Projetos\DBFlow-Treino' -Mode Local -Apply
```

Para Remote, informe os endpoints aprovados novamente; credenciais protegidas permanecem no armazenamento local se a configuração não as substituir. O instalador usa `install-manifest.json`, backups e blocos gerenciados para evitar duplicar entradas e tenta preservar conteúdo alheio. Antes de aceitar alterações de regra, compare os arquivos gerenciados e o diff do seu checkout.

Se clonou **o repositório fonte atualizado**, publique primeiro o servidor para atualizar a cópia `server/publish` e só depois instale:

```powershell
Set-Location 'D:\Ferramentas\dbflow'
.\Build.ps1
.\Install.ps1 -Project 'D:\Projetos\DBFlow-Treino' -Mode Local -Apply
```

`Install.ps1` só chama o Build automaticamente quando `DbFlow.Server.dll` está ausente; não detecta que uma DLL existente ficou antiga. Um ZIP aprovado pode já trazer a DLL publicada da versão correspondente; confirme a versão do pacote antes de pular o Build. A reinstalação também exige o manifest local da instalação anterior. Se o checkout veio do Git e não tem manifest, veja a seção de troca de máquina; não remova arquivos runtime para forçar atualização.

### Prévia de remoção

A CLI tem comando `uninstall`; sem `--apply`, mostra o que removeria ou preservaria. Execute a partir da pasta do produto:

```powershell
python .\dbctl.py --project 'D:\Projetos\DBFlow-Treino' uninstall
```

Leia a saída antes de aplicar. Para executar:

```powershell
python .\dbctl.py --project 'D:\Projetos\DBFlow-Treino' uninstall --apply
```

O uninstall é conservador: usa o manifest e compara hashes; preserva arquivos alterados pelo usuário ou arquivos com alterações não gerenciadas. Não é um comando para apagar a pasta toda. Revise a saída para saber o que ficou. O uninstall remove arquivos gerenciados conforme sua política, mas não apague `.db-control/local`, histórico central ou backups indiscriminadamente. Se precisar remover estado pessoal depois de sincronizar e exportar o que for necessário, siga a política da organização.

## 16. Se algo não funcionar

| Sintoma | O que conferir |
|---|---|
| `python` não é reconhecido | Instale Python oficial, reabra PowerShell e verifique `python --version`. A CLI e o instalador chamam `python`. |
| `dotnet` não é reconhecido ou falta `Microsoft.AspNetCore.App 8` | Instale ASP.NET Core Runtime 8; para compilar fonte, instale .NET SDK 8. Reabra o terminal. |
| `Install.ps1` não encontrado | Você está na pasta errada. `Get-Location` deve mostrar a pasta do produto; `Get-ChildItem Install.ps1` deve listar o arquivo. |
| Bloqueio de execução do PowerShell | A política local controla execução. Não altere política de máquina; peça a forma aprovada ao suporte. |
| O preview não instalou nada | Esperado sem `-Apply`. Revise o preview e rode o mesmo comando com `-Apply` quando estiver pronto. |
| Erro de build / DLL ausente | Repositório fonte não traz `server/publish`. Instale SDK .NET 8 e rode `.\Build.ps1` na pasta do produto. |
| MCP não aparece no cliente | Confirme que abriu a pasta da aplicação, não do produto; reabra o cliente; confira `.codex/config.toml`, `.mcp.json` ou `.cursor/mcp.json`; consulte confiança/aprovação do cliente e `doctor`. |
| Hook não parece executar | `doctor` mostra configuração e timestamp do helper, não ativação pelo cliente. Confie o projeto e revise/autorize no cliente; confira eventos configurados e reinicie. |
| Status `not_configured` | URL central ou projeto não configurado. Peça endereços reais; não use domínios de exemplo. |
| Status `pending` / `queued` | A publicação não está confirmada. Confira rede/VPN, URL final HTTPS, token, escopo, branch e resposta do servidor; depois retome com status. |
| Remote retorna rejeição de projeto/destino | `project_key` ou configuração autorizada não corresponde. Peça ao administrador para verificar projeto, token e mapas no servidor. |
| Token não funciona | PAT e token MCP são credenciais diferentes. Local usa token GitLab; Remote usa token de serviço MCP. Não cole nenhum deles em chat. Peça ao emissor para conferir expiração/escopo. |
| `dbflow_prepare_bundle` recusa operação | Confira destino mapeado, dependências, conflito de revisão, sobreposição de objetos, IDs selecionados e fingerprint. Não force outra operação sem entender a recusa. |
| Estado local existe, mas não aparece em outro PC | O diário local não é automaticamente compartilhado. Só eventos sincronizados com o repositório central/serviço ficam disponíveis aos outros usuários. |
| Mudança no SSMS não apareceu | O DBFlow não observa banco, cliente SQL, caixa de email ou sessão fechada. Informe/registre evidência por fluxo autorizado. |

Ao pedir ajuda, compartilhe versão do produto, comando sem segredos e as mensagens de erro redigidas. Oculte token, URLs internas se a política exigir, nomes de clientes e qualquer dado confidencial. Não envie `credentials.dpapi.json`, conteúdo de `.db-control/local` ou dumps de banco.

## 17. Notas claras para administradores: preparar OpenShift

Esta seção é para a equipe de plataforma/administradores. Um usuário sem infraestrutura pode ignorá-la. O modo `Server` **gera arquivos de configuração**; não executa `docker`, `oc`, push de imagem ou implantação. Os comandos abaixo são um roteiro operacional a revisar com a equipe de plataforma.

### Pré-requisitos antes de gerar

- projeto/namespace OpenShift existente;
- registry acessível e permissão de push/pull;
- acesso a `oc` autenticado no cluster correto;
- projeto GitLab central e branch existentes, com token de serviço autorizado a criar commits;
- `project_key` estável, destinos lógicos e ambientes conhecidos;
- URL/hostname Route aprovado e política de TLS definida;
- StorageClass/PVC que permite escrita para UID arbitrário do OpenShift e acesso compatível com SQLite single-writer;
- identidades MCP individuais e tokens fortes (32+ caracteres aleatórios), além de lista explícita de projetos permitidos;
- decisão sobre CA corporativa do GitLab, secrets manager e procedimento de rotação.

O volume padrão gerado é PVC 2 GiB, uma réplica, estratégia `Recreate`, porta 8080 e SQLite no volume. Não escale várias réplicas compartilhando o mesmo diário SQLite. Readiness de processo/armazenamento não prova acesso ao GitLab. Falha no GitLab pode deixar sincronização pendente. A rota padrão é TLS edge; confirme requisitos de TLS interno e CA corporativa.

### Configurar projeto, identidade e seis destinos

O modo `Server` precisa primeiro de `.db-control/project.json` em uma pasta de configuração. O nome dessa pasta define inicialmente o `project_key`, mas você pode defini-lo explicitamente **antes de criar eventos**. O valor precisa ser idêntico ao usado no checkout dos desenvolvedores e ao final da URL MCP.

Neste exemplo fictício, o `project_key` final será `minhaaplicacao`:

```powershell
Set-Location 'D:\Ferramentas\dbflow'
New-Item -ItemType Directory -Force -Path 'D:\Config\DBFlow-Server' | Out-Null
.\Install.ps1 -Project 'D:\Config\DBFlow-Server' -Mode Server `
  -RepositoryUrl 'https://gitlab.empresa.example/grupo/scripts-banco' `
  -Apply
python .\dbctl.py --project 'D:\Config\DBFlow-Server' configure `
  --project-key minhaaplicacao
```

Agora adicione os destinos aprovados. A tabela usa nomes fictícios; substitua todos os aliases/nomes físicos pelos dados fornecidos pelo DBA. O mapa é metadado, não connection string:

```powershell
$mapas = @(
    @{ Banco = 'PRO'; Ambiente = 'TI';  Alias = 'SQL-TI';  Fisico = 'DB_PRO_TI' },
    @{ Banco = 'PRO'; Ambiente = 'HML'; Alias = 'SQL-HML'; Fisico = 'DB_PRO_HML' },
    @{ Banco = 'PRO'; Ambiente = 'PRD'; Alias = 'SQL-PRD'; Fisico = 'DB_PRO_PRD' },
    @{ Banco = 'PRI'; Ambiente = 'TI';  Alias = 'SQL-TI';  Fisico = 'DB_PRI_TI' },
    @{ Banco = 'PRI'; Ambiente = 'HML'; Alias = 'SQL-HML'; Fisico = 'DB_PRI_HML' },
    @{ Banco = 'PRI'; Ambiente = 'PRD'; Alias = 'SQL-PRD'; Fisico = 'DB_PRI_PRD' }
)
foreach ($destino in $mapas) {
    python .\dbctl.py --project 'D:\Config\DBFlow-Server' map-database `
      --database $destino.Banco --engine sqlserver --environment $destino.Ambiente `
      --instance-alias $destino.Alias --physical-name $destino.Fisico
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao mapear destino; interrompa a geração.' }
}
```

Confira `project.json` e peça ao DBA/plataforma para conferir cada destino antes de gerar servidor. Depois de ter os mapeamentos, gere os recursos Server no mesmo diretório de saída. Gere novamente depois de qualquer alteração em chave, destinos ou permissões para atualizar o ConfigMap/`server-config.json`:

### Gerar arquivos (sem deploy)

No host com produto/SDK adequados, gere o modo Server com repositório real, namespace, host e imagem confirmados. Exemplo de formato:

```powershell
Set-Location 'D:\Ferramentas\dbflow'
.\Install.ps1 -Project 'D:\Config\DBFlow-Server' -Mode Server `
  -RepositoryUrl 'https://gitlab.empresa.example/grupo/scripts-banco' `
  -Namespace 'equipe-dados' `
  -RouteHost 'dbflow.apps.empresa.example' `
  -Image 'registry.empresa.example/equipe-dados/dbflow:0.2.0' `
  -OutputDirectory 'D:\Config\dbflow-gerado' -Apply
```

O comando produz `deployment.json`, `server-config.json`, `secret-inputs.json` e `Apply-Secrets.ps1` no diretório configurado. Revise `project_key`, repositório, branch, usuários/projetos permitidos, engines, ambientes, aliases/fingerprints, host e imagem antes de publicar. `secret-inputs.json` lista os nomes das variáveis esperadas, não os valores. Não comite valores secretos.

Cheque que a URL MCP gerada em stdout termina com `/mcp/minhaaplicacao`. O `project_key` agora precisa corresponder exatamente à configuração local dos desenvolvedores, por exemplo `https://dbflow.apps.empresa.example/mcp/minhaaplicacao`. Host e URL só passam a ser alcançáveis depois de criar Route, DNS/certificado e liberar rede. Se essas etapas ainda não ocorreram, trate o endpoint como pendente; não envie um URL fictício para desenvolvedores.

### Configurar identidades e secrets sem gravar valores no JSON

Abra `secret-inputs.json` no editor e substitua a conta de exemplo `developer` por uma conta para cada pessoa/identidade autorizada. Cada entrada tem estes campos: `actor` (identidade que será auditada), `projects` (lista explícita de `project_key` permitidos) e `token_env` (nome da variável do segredo). Exemplo **de estrutura sem segredos**:

```json
{
  "namespace": "equipe-dados",
  "gitlab_token_envs": ["DBFLOW_GITLAB_TOKEN"],
  "service_accounts": [
    {"actor": "ana.silva", "projects": ["minhaaplicacao"], "token_env": "DBFLOW_SERVICE_TOKEN_ANA"},
    {"actor": "bruno.souza", "projects": ["minhaaplicacao"], "token_env": "DBFLOW_SERVICE_TOKEN_BRUNO"}
  ]
}
```

Use tokens MCP aleatórios com pelo menos 32 caracteres e um token distinto por identidade. O produto não gera tokens. Gere/distribua por gerenciador corporativo de segredos ou gerador aprovado; entregue cada token ao respectivo usuário pelo cofre corporativo. Não cole tokens em Git, `secret-inputs.json`, chat, email ou argumentos do shell. Para aplicar, o script lê as variáveis de ambiente se já existirem ou pede os valores com entrada oculta. Se GitLab token também estiver ausente do ambiente, ele pede esse valor ocultamente. O script não cria token; valida tamanho MCP e duplicação, monta o Secret e o transmite ao `oc` via stdin.

As contas geradas inicialmente são apenas exemplos. `Apply-Secrets.ps1` não concede permissão por si só a pessoas no sistema corporativo, não cria identidade de usuário e não substitui a autorização da organização. Confira lista de projetos permitidos por token antes de aplicar.

Se adicionar vários projetos à mesma instância, gere no mesmo diretório de saída e confirme que a operação preserva as entradas existentes. A Route só fica válida quando cluster/DNS/publicação estiverem configurados; gerar o nome não cria DNS.

### Build, secrets e apply — feitos pelo administrador

Somente após revisão e com imagem/namespace aprovados, siga a política operacional da organização. Primeiro construa e envie a imagem com o código do produto na pasta raiz do repositório DBFlow; então aplique o Secret e os recursos gerados:

```powershell
docker build -t registry.empresa.example/equipe-dados/dbflow:0.2.0 .
docker push registry.empresa.example/equipe-dados/dbflow:0.2.0
oc project equipe-dados
Set-Location 'D:\Config\dbflow-gerado'
.\Apply-Secrets.ps1
oc apply -f .\deployment.json
oc rollout status deployment/dbflow
```

Isso descreve comandos de build/aplicação, não uma implantação já testada. Antes do apply, valide imagem/tag, contexto atual do cluster, namespace, Route/DNS e conteúdo não secreto do manifesto. `Apply-Secrets.ps1` lê variáveis já disponíveis ou solicita valores em entrada oculta e passa o Secret ao `oc` por stdin. Verifique o contexto do cluster (`oc project`/login) e a lista de contas/projetos permitidos. O script pode avisar que o Deployment ainda não existe na primeira execução; aplique o manifesto depois. Para rotação, o script reinicia Deployment quando possível.

Após o rollout, valide no ambiente real: PVC montado e gravável pela SCC/UID atribuído; probes; Route/TLS; CA e conectividade GitLab; escopo do token GitLab; autenticação/isolation de cada token MCP; project keys e mapas; persistência após reinício controlado; e sincronização real de evento de teste aprovado. Remova os dados de teste conforme política. Não declare serviço homologado só porque `oc apply` e rollout terminaram.

### O que a validação atual não cobriu

`VALIDACAO.md` registra testes locais e estrutura dos manifestos. O Docker daemon não estava disponível, e não havia CLI `oc`, cluster OpenShift ou GitLab corporativo nesta máquina. Portanto imagem, PVC, SCC, Route, DNS, TLS, rede, autenticação GitLab real e rollout não foram comprovados. O reconhecimento em UI de Codex/Claude/Cursor também não foi observado. A equipe precisa fazer validação de ambiente e homologação independente.

## 18. Referências dentro do pacote

- [README.md](../README.md): resumo, modos Local/Remote/Server, instalação e comandos.
- [GUIA-DETALHADO.md](GUIA-DETALHADO.md): arquitetura, ferramentas MCP, configuração dos três clientes, geração OpenShift e detalhes do fluxo.
- [VALIDACAO.md](../VALIDACAO.md): testes realizados e limitações externas observadas.
- `Install.ps1`: parâmetros válidos do assistente Windows.
- `Build.ps1`: publicação do servidor .NET.
- `dbctl.py` e `dbflow/cli.py`: comandos CLI efetivamente implementados.
- `server/tools.json` e `server/Control.cs`: ferramentas MCP e comportamento do servidor.

Se este guia e os arquivos do projeto parecerem divergir, verifique a versão exata do pacote distribuído e peça à equipe responsável para confirmar qual documentação corresponde àquela versão.
