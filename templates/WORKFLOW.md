# DBFlow — procedimento automático do projeto

## Fronteira

O MCP registra artefatos e evidências no GitLab. Não contém drivers, credenciais, conexões nem execução de SQL contra bancos da empresa. O SQLite local é apenas o diário de arquivos/outbox do instalador. O agente pode usar ferramentas de banco já autorizadas pelo usuário, sem transferir credenciais ao DBFlow. Desenvolvedor executa em TI; DBA executa HML/PRD. O sistema não envia email nem faz commits do código da aplicação.

## Ao iniciar ou retomar

1. Consulte `dbflow_status` e `dbflow_scan`. Se o MCP não carregar, execute `python .db-control/runtime/dbctl.py --project . doctor` e `audit`.
2. Se faltarem GitLab ou mapeamento, continue preparando artefatos locais, informe a pendência uma vez e nunca diga que o Git remoto foi atualizado. Não invente URL, IDs, bancos ou confirmações.
3. Identifique a demanda pelo contexto, branch/ticket e registros existentes. Reuse seu ID. Consulte `dbflow_get_revision` para recuperar SQL/dependências e `dbflow_get_bundle` para uma entrega já preparada, sem criar outra. Quando não houver identificação existente, gere ID estável e persista-o no primeiro registro; não use o título como única identidade. Pergunte somente quando várias demandas/destinos forem igualmente plausíveis.
4. Recupere revisões anteriores e pendências por banco/ambiente. O início de sessão não marca o ambiente como verificado. Não faça consultas amplas em todo banco sem necessidade.

## Durante uma mudança

1. Identifique todos os bancos envolvidos e seus nomes lógicos no mapa, inclusive múltiplos SQL Server, como PRO e PRI. Nome do banco não é ambiente. Cada objeto inclui schema e tipo na identidade (por exemplo `dbo.Pedido:table`).
2. Quando disponível, capture a definição anterior com as ferramentas do usuário; registre origem, horário e limitações. Sem acesso, preserve a ausência de baseline. Não atribua mudanças previamente existentes à demanda atual.
3. Mantenha scripts completos em arquivos do projeto, inclusive migrations em outras linguagens. Não envie dados completos de negócio como inventário. Nunca inclua tokens ou connection strings nos scripts/argumentos.
4. Após cada conjunto coerente de alterações, use `dbflow_register_revision`. Forneça operation_id único e reutilize o MESMO ID em retentativas da MESMA operação; se mudar argumentos, use outro ID. Inclua parent_revision atual, scripts, ordem/dependências e procedimento de verificação. Ao vincular um arquivo, use source_path relativo e source_sha256 dos bytes UTF-8 exatos do script; sem converter quebras de linha inadvertidamente.
5. Uma revisão é imutável. Cada ajuste cria uma revisão nova. O retorno distingue persistência local, sincronização remota e aplicação. `pending`/`not_configured` nunca significam sincronizado.
6. Registre scripts DDL e DML. Um diff de schema não prova a execução de um UPDATE, DELETE ou transformação MongoDB. Mudanças feitas por ferramentas externas sem arquivo devem ser explicitamente materializadas e registradas pelo agente.
7. Dois trabalhos podem tocar o mesmo objeto; não copiar toda HML para uma demanda, pois isso pode capturar trabalho alheio. Compare bases e alterações; resolva conflitos preservando as revisões originais. O servidor recusa publicação se parent_revision divergir do remoto.

## Inventário e comparação

Use `dbflow_record_snapshot` para definições obtidas por consulta autorizada ou exportação. Informe target_fingerprint retornado por status para o destino efetivamente consultado; ele fixa a identidade física. Informe covered_objects somente para identidades integralmente inspecionadas, além de limitations e captured_at. Sem uma revisão de comparação conhecida, o resultado é `baseline_not_established`. Uma coleta parcial nunca prova exclusão.

A comparação implementada é textual (normaliza apenas quebras de linha e bordas); não é um parser semântico de SQL Server/Oracle/MongoDB. O agente analisa os achados conforme o motor e relata limitações. Uma coleta não muda automaticamente estados de aplicação nem substitui a definição oficial.

## Preparar entrega

1. Consulte o estado e verifique fontes modificadas após registro. Se um candidato não tiver impacto no banco, use `dbflow_exclude_candidate` com hash exato e motivo concreto; não exclua só para passar no hook. Arquivos alterados voltam a ser candidatos.
2. Rode auditoria e delegue uma revisão independente ao subagente `dbflow-auditor` com o relatório, diff, revisões e destinos. O auditor não deve chamar outro auditor. Sem suporte a subagentes, declare que só houve conferência pelo agente principal.
3. Selecione explicitamente revisão/demanda e ambiente. `dbflow_prepare_bundle` congela conteúdo, hashes, ordem e mapeamento físico do destino. Dependências devem estar incluídas ou confirmadas no ambiente. Objetos sobrepostos em revisões selecionadas exigem reconciliação explícita.
4. Exporte o pacote e o rascunho de email. A geração não comprova envio. Registre `dbflow_report_dispatch` somente após informação explícita do usuário ou evidência de envio. O sistema não lê a caixa de email.
5. Qualquer alteração posterior produz outra revisão/pacote. Confirmações anteriores não transitam para a nova revisão.

## Confirmação após horas ou dias

`dbflow_confirm` exige revisão, banco, ambiente, target_fingerprint e change_ids exatos. Use o fingerprint do pacote (e bundle_id) quando a confirmação se referir àquela entrega; assim, remapear um ambiente não transfere a confirmação para outra instância. Use:

- `manual_applied`: usuário informa aplicação; preservar quem informou e a declaração.
- `execution_succeeded`: recibo do executor identifica a revisão e etapas realizadas.
- `state_matches`: consulta autorizada encontrou a pós-condição esperada; isto não prova execução desse script específico.
- `state_differs`: pós-condição não corresponde; preservar execução anterior no histórico.
- `failed`: executor informou falha das etapas selecionadas.

Confirme separadamente PRO/PRI e HML/PRD. Uma falha em PRI não apaga o sucesso em PRO. A verificação ocorre quando a IA está ativa, ao retomar, concluir e preparar uma entrega. Não existe monitoramento contínuo em segundo plano nesta versão.

## Limites dos hooks

Codex, Claude Code e Cursor recebem hooks nativos para início, ferramentas/edições e encerramento. Eles disparam auditoria determinística de arquivos. O hook acusa candidatos; o agente é responsável por entender a mudança e registrar os metadados. O follow-up de encerramento é limitado para não prender uma sessão aguardando DBA/credenciais.

Codex: AGENTS.md orienta a consulta/auditoria; MCP e hooks SessionStart, PostToolUse e Stop ficam em .codex/config.toml. O projeto precisa ser confiável e os hooks precisam de revisão/autorização nativa (via /hooks no CLI). Claude Code e Cursor também podem exigir aprovação nativa. O instalador não desativa essas proteções. O diagnóstico distingue configuração instalada e última invocação do helper; uma chamada manual ao helper não comprova que o aplicativo disparou o hook.

Os hooks não observam SSMS, SQL Developer, alterações fora da sessão nem scripts não materializados. Amplie `watch.include` para convenções locais. Relate escopo e cobertura; nenhuma regra textual garante captura universal.

## Recuperação de conflitos

Para uma revisão apenas local cuja publicação conflitou, preserve o conteúdo, recupere a revisão remota e use `dbflow_abandon_local_revision` com motivo explícito, do descendente para o ancestral. A operação verifica que a revisão não foi publicada, preserva o evento original e cancela pacotes não despachados. Revisões com despacho/confirmação exigem reconciliação mantendo essas referências; não descarte evidências de entrega.

No modo remoto, falha de transporte mantém uma requisição pendente; rejeição de argumentos fica visível como `rejected` e não impede consultar status. `dbflow_cancel_remote_request` interrompe somente a retentativa local, com justificativa e histórico. Não desfaz operações no servidor. Após corrigir argumentos, use novo operation_id.
