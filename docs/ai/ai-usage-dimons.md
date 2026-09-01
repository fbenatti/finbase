# Dimons de consumo de IA

Os dashboards GitHub Copilot, Anthropic Claude e Google NotebookLM não leem
mais diretamente `gold.dest_consumo_ia`, `gold.dest_licencas_ia` ou os raws do
GitHub. A Gold permanece como camada normalizada e auditável; as consultas
interativas usam as materializações abaixo.

| Tabela | Grão | Uso |
| --- | --- | --- |
| `dimon.dest_dm_ai_consumption_daily` | dia, provedor, produto, usuário, modelo e dimensões organizacionais | KPIs, séries, modelos, usuários e consumo dos três dashboards |
| `dimon.dest_dm_ai_license_snapshot` | período, provedor, produto e usuário | licenças, atividade e adoção |
| `dimon.dest_dm_copilot_usage_daily` | dia, usuário, modelo, editor, linguagem e origem | modelos e drilldown técnico do desenvolvedor |
| `dimon.dest_dm_copilot_license_snapshot` | período, snapshot e usuário | evolução histórica dos assentos GitHub |
| `dimon.dest_dm_copilot_user_period` | período e usuário | Relação de Desenvolvedores, créditos, franquia e engajamento |

A coluna `source_rows` da Dimon normalizada de consumo mantém a contagem de
linhas da fonte para os indicadores de qualidade, mesmo depois da agregação.
A telemetria GitHub é deduplicada pela versão mais recente de ingestão antes de
ser publicada.

## Atualização

O scheduler reconstrói as tabelas diariamente às 05:10. Também é possível
acionar a atualização completa por `POST /ai-copilot/dimon/reprocess` ou
`POST /visoes-consumo-ia/provider/dimon/reprocess`.

O drilldown da relação de desenvolvedores consulta
`dest_dm_copilot_usage_daily`; não existe fallback silencioso para Gold ou
Bronze. Assim, uma Dimon indisponível resulta em erro explícito em vez de uma
consulta pesada inesperada.
