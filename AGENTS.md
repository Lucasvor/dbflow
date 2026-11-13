# Colaboração neste pacote

Preferência explícita do usuário em 13/11/2026: nas próximas tarefas, usar agentes de menor custo para implementação/revisão e manter o agente principal como orquestrador, responsável por integrar e validar o resultado. Quando houver seleção de modelo, escolher o mais econômico adequado à sub tarefa (por exemplo, gpt-6-luna entre os modelos disponíveis nesta sessão); aumentar capacidade somente quando a complexidade justificar. Não pressupor nomes de modelos disponíveis em outro cliente. Não fazer delegação recursiva.

O servidor e o transporte MCP de produção ficam em `server/`, target net8.0. Python é instalador/hooks e referência de regressão. Nunca implementar acesso aos bancos de negócio ou enviar emails como efeito do registro. Testes: `python -m unittest discover -s tests -v` após publicar, e `dotnet run --project server-tests/DbFlow.Server.Tests.csproj`. Não substituir testes do servidor por resultados da referência Python.

Preservar journal/IDs existentes, regras alheias, arquivos do usuário e segredos fora de configs versionadas. Documentar validação efetivamente realizada e pendências externas; simulação local não é implantação homologada no OpenShift.
