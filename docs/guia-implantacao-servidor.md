# Guia de implantação em servidor

Este guia descreve como copiar o OpenFinops para um servidor Ubuntu mantendo a base de dados canônica e evitando transferir os dados Iceberg locais. Use-o como roteiro para outra IA ou operador.

## Decisões de arquitetura

- A base canônica atual é o PostgreSQL **nativo local**, acessível em `localhost:5433`, banco `finops`.
- O Docker local pode conter volumes antigos ou auxiliares. Não escolha uma base pelo nome do volume; valide pelo endpoint da aplicação e pela contagem em `source_connection`.
- No servidor, a aplicação e sua infraestrutura rodam por `docker compose` em `/opt/OpenFinops`.
- Dados analíticos em `apps/data/iceberg` não devem ser copiados na implantação inicial. Eles devem ser baixados/reprocessados no destino.
- Nunca registrar senhas, tokens ou arquivos `.env` em documentação, commits ou logs.

## Roteiro operacional de produção

Use este fluxo sempre que for criar ou reconstruir um ambiente. Ele assume que
o servidor começará sem dados analíticos e que as fontes serão importadas no
próprio destino.

1. Validar a base PostgreSQL canônica na origem.
2. Criar um dump da aplicação sem os registros de catálogo Iceberg.
3. Copiar código, configurações e dump; não copiar `apps/data/iceberg`.
4. Subir infraestrutura, restaurar PostgreSQL e iniciar API, worker e Trino.
5. Conferir credenciais, catálogo vazio e saúde dos serviços.
6. Importar as fontes no servidor e então executar os reprocessamentos.

Não inicie reprocessamentos antes de confirmar que o worker, o Trino e o
warehouse Iceberg estão saudáveis.

## Atualização de uma instalação já em produção

### Regra de trabalho: DEV primeiro, PRD somente com autorização

Toda solicitação de ajuste deve ser implementada e validada exclusivamente em
DEV. Não publicar commits para PRD, executar deploy, alterar dados ou
configurações de PRD, nem iniciar scripts que possam fazer essas ações como
parte do desenvolvimento. Concluir o ajuste em DEV e deixar o resultado pronto
para validação do usuário. A promoção para PRD só pode ocorrer quando o usuário
declarar explicitamente que finalizou a validação e autorizar a subida. Essa
autorização vale para a promoção solicitada; não é presumida por um pedido para
implementar, corrigir, testar ou fazer commit.

Enquanto aguarda essa autorização, commits e publicação em repositório remoto
não devem acionar qualquer deploy automático para PRD. Se o fluxo de CI/CD do
ambiente publicar automaticamente, interrompa antes da etapa de produção e
ajuste o fluxo para manter o release pendente até a autorização explícita.

Este é o procedimento padrão para publicar alterações de código no host
`finops-brsouth`. Use a partir da raiz do repositório local. O deploy deve
receber somente commits intencionais; não use `scripts/sync_production.sh
publish` quando houver arquivos locais não relacionados, pois esse comando faz
`git add -A`.

As instruções abaixo são exclusivas da etapa de promoção para PRD, iniciada
somente após a autorização explícita acima. Antes disso, não execute os comandos
de publicação ou implantação descritos nesta seção.

O checkout da VM usa um bundle Git local como `origin` para receber releases
incrementais; `git push origin main` sozinho atualiza o GitHub, mas não a VM.
Use o script abaixo, que publica `main`, cria um bundle a partir do commit atual
da VM, transfere o bundle por SSH/SCP, troca o `origin` remoto para esse bundle,
executa o build/restart dos containers pelo script da VM e valida a revisão
resultante:

```bash
scripts/deploy_production_bundle.sh
```

Antes de executar, confira a branch/diff e valide o build/testes exigidos pela
alteração. O script publica apenas commits de `main`; alterações locais sem
commit ficam de fora. Ele interrompe o deploy se o checkout da VM estiver sujo
ou se o commit de produção não for ancestral da branch local. O host padrão é o
alias SSH `finops-brsouth` e o checkout é
`/mnt/openfinops-backups/openfinops`. Para um host/caminho alternativo, defina
`OPENFINOPS_PROD_HOST` ou `OPENFINOPS_PROD_DIR` explicitamente.

O script da VM garante `.env.production`, reconstrói e sobe os serviços com
Compose, instala/testa a configuração Nginx e recarrega o Nginx. Ao final, o
script local exige que o commit remoto seja o esperado, mostra o estado dos
containers e verifica Trino, worker e frontend. Se uma validação falhar, não
rode reprocessamentos como tentativa de recuperação: consulte os logs e corrija
o serviço com o fluxo operacional adequado.

O helper atualiza o checkout **antes** de executar `deploy_vm.sh`, para carregar
a versão nova do próprio script de implantação. O script prepara as imagens e
aguarda a fila (`etl_task` com status `RUNNING` ou `QUEUED`) terminar antes de
recriar containers. Isso evita interromper reclassificações longas. A espera é
ativada por padrão; `OPENFINOPS_DEPLOY_WAIT_IDLE=false` é reservado à recuperação
operacional de uma fila impedida de progredir e exige avaliar as tarefas afetadas.
Não execute dois deploys em paralelo.

Regras de negócio do Forecast são dados persistidos no PostgreSQL, não fazem
parte do bundle de código. Quando uma alteração depender de uma regra nova ou
ajustada, configure-a na tela visível de regras do Forecast após o deploy e
confirme que está ativa, com escopo e opções corretos. Não codifique a regra no
motor nem presuma que o deploy de imagem a criou. Neste rollout foi cadastrada
na configuração 9 a regra ativa `Azure-4P - suspender após 30 dias sem dados`,
com escopo `cloud_origem=azure-4p` e limite editável de 30 dias.

Na correção de 09/10/2026, a regra 8 da configuração 9 foi versionada como
`Compras recorrentes - zerar mês já realizado`, com
`behavior_options.current_month_policy=ZERO_WHEN_REALIZED`. A identificação
existente `chargecategory=Purchase` foi preservada. A configuração aparece no
editor de regras no campo **Mês corrente**. O estado anterior da regra foi salvo
em `/backups/scheduled/forecast-rule-8-before-month-closure-20261009.json` na VM.

O deploy é somente de código/configuração. Não restaure banco, apague volumes,
rode reprocessamentos ou altere dados de forecast/Gold como parte da publicação,
a menos que isso seja solicitado separadamente. Uma regra de negócio necessária
ao release é uma alteração de configuração separada, feita na UI de regras e
validada no banco de produção. `scripts/sync_production.sh deploy` executa
diretamente o script da VM, mas não transfere um bundle novo e por isso não deve
ser usado para releases.

## Pré-requisitos no servidor

O usuário remoto precisa ter acesso SSH e `sudo`.

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 rsync
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Abra uma nova sessão SSH depois de adicionar o usuário ao grupo `docker`.

Confirme os requisitos:

```bash
docker --version
docker compose version
df -h /
free -h
```

## 1. Confirmar a base canônica antes de qualquer dump

Não assuma que um container chamado `postgres` ou `finops-pg` é a origem correta. Consulte a própria API local, que é a fonte usada pela interface.

```bash
curl -sS http://localhost:5173/api/connections/ | jq 'length'
```

Hoje o resultado esperado é `22`. Em seguida, confirme a mesma origem PostgreSQL:

```bash
PGPASSWORD='<senha>' psql -h localhost -p 5433 -U finops -d finops \
  -Atc 'select count(*) from source_connection;'
```

Só continue se as duas contagens forem iguais.

## 2. Gerar o dump correto

Use `pg_dump` do banco de aplicação. Evite `pg_dumpall` com o usuário `finops`: ele pode falhar ao consultar roles globais (`pg_authid`) por falta de privilégio de superusuário.

```bash
mkdir -p backups
PGPASSWORD='<senha>' pg_dump -h localhost -p 5433 -U finops -d finops \
  --clean --if-exists \
  --exclude-table-data=iceberg_tables \
  > backups/openfinops-canonical-db-YYYYMMDD.sql
```

`iceberg_tables` armazena apenas o catálogo (caminhos de metadata e Parquet),
não os dados. Como `apps/data/iceberg` é excluído desta implantação, **nunca**
leve as linhas dessa tabela no dump: elas apontariam para arquivos do servidor
de origem e fariam o Trino retornar `ICEBERG_MISSING_METADATA`. A estrutura da
tabela continua no dump; somente seus registros são omitidos. O catálogo será
recriado pelas importações e reprocessamentos executados no destino.

Valide o arquivo antes de transferir:

```bash
test -s backups/openfinops-canonical-db-YYYYMMDD.sql
```

## 3. Copiar somente código e configuração

No computador de origem, a partir da raiz do projeto:

```bash
ssh <host> 'sudo mkdir -p /opt/OpenFinops && sudo chown $USER:$USER /opt/OpenFinops'

rsync -a \
  --exclude='.git/' \
  --exclude='node_modules/' \
  --exclude='apps/web/node_modules/' \
  --exclude='apps/data/' \
  --exclude='*.pyc' \
  ./ <host>:/opt/OpenFinops/

scp backups/openfinops-canonical-db-YYYYMMDD.sql \
  <host>:/opt/OpenFinops/backups/
```

`apps/data/iceberg` é intencionalmente excluído. Depois da implantação, reimporte as fontes e reprocessar as tabelas no servidor.

### Credenciais: o banco é a fonte de verdade

Credenciais de conectores devem estar no `config_json` da respectiva
`source_connection`, incluindo `tenant_id`, `client_id` e `client_secret` para
Azure. `credential_profile` é apenas um rótulo organizacional; a execução não
deve depender de variáveis de ambiente com segredos.

Antes do dump, valide somente a presença — nunca imprima os valores:

```sql
SELECT id, name,
       (config_json->>'tenant_id' <> '') AS has_tenant,
       (config_json->>'client_id' <> '') AS has_client,
       (config_json->>'client_secret' <> '') AS has_secret
FROM source_connection
WHERE connector_type IN ('azure_automation_jobs', 'azure_resource_graph', 'azure_openai_ptu');
```

## 4. Restaurar a base no servidor

Primeiro faça um backup recuperável do banco remoto, mesmo que ele seja novo:

```bash
cd /opt/OpenFinops
mkdir -p backups
docker compose up -d postgres
docker exec -e PGPASSWORD='<senha>' openfinops-postgres-1 \
  pg_dumpall -U finops --clean --if-exists \
  > backups/pre-restore-YYYYMMDD.sql
```

O dump local pode ter sido gerado por uma versão PostgreSQL mais nova. Caso o restore informe que `transaction_timeout` é desconhecido, remova somente essa diretiva durante o pipe.

Para tornar o servidor uma cópia exata da base canônica, pare a API, recrie o schema `public` e restaure. **Este comando apaga o schema público remoto atual**; só execute após ter criado o backup acima.

```bash
docker compose stop api

docker exec -i -e PGPASSWORD='<senha>' openfinops-postgres-1 \
  psql -v ON_ERROR_STOP=1 -U finops -d finops <<'SQL'
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO finops;
GRANT ALL ON SCHEMA public TO public;
SQL

sed '/^SET transaction_timeout =/d' backups/openfinops-canonical-db-YYYYMMDD.sql \
  | docker exec -i -e PGPASSWORD='<senha>' openfinops-postgres-1 \
      psql -v ON_ERROR_STOP=1 -U finops -d finops

docker compose up -d api
```

Valide a migração:

```bash
docker exec openfinops-postgres-1 \
  psql -U finops -d finops -Atc 'select count(*) from source_connection;'
```

O resultado deve ser igual à contagem confirmada na etapa 1.

## 5. Subir a aplicação

Execute no servidor:

```bash
cd /opt/OpenFinops
docker compose up -d --build
docker compose ps
```

Os serviços esperados são `postgres`, `api`, `worker`, `web`, `trino` e três
réplicas de `trino-worker`.

Espere o Trino terminar a inicialização antes de disparar ETL:

```bash
docker compose ps
docker exec openfinops-api-1 python - <<'PY'
import requests
print(requests.get('http://trino:8080/v1/info', timeout=10).json())
PY
```

O campo `starting` precisa estar como `false`.

## Configurações Docker obrigatórias

Para comunicação entre containers Linux, mantenha estas variáveis no `docker-compose.yml`:

```yaml
api:
  environment:
    DATABASE_URL: postgresql+asyncpg://finops:finops@postgres:5432/finops
    DATABASE_URL_SYNC: postgresql://finops:finops@postgres:5432/finops
    TRINO_URL: http://trino:8080

web:
  environment:
    VITE_API_PROXY_TARGET: http://api:8000
```

Não use `host.docker.internal` no servidor Linux: esse nome não é resolvido automaticamente. Ele provoca erros `ENOTFOUND` no Vite e deixa páginas como `/analysis` carregando indefinidamente.

O `apps/api/Dockerfile` precisa copiar também os scripts Python no nível de `apps/api`, pois o startup importa alguns deles:

```dockerfile
COPY apps/api/app ./app
COPY apps/api/*.py ./
```

### Warehouse Iceberg e Trino

O catálogo Iceberg é SQL/JDBC no PostgreSQL e os arquivos Parquet/metadata são
gravados no volume `./apps/data/iceberg`. Todos os componentes que escrevem ou
leem esses arquivos devem enxergar **o mesmo volume**:

- API e worker: `/data/iceberg`.
- Trino coordinator e todos os `trino-worker`: `/data/iceberg` e
  `/tmp/data/iceberg`.

O segundo mount do Trino é obrigatório porque metadata gravado como
`file:///data/iceberg/...` é resolvido pelo filesystem do Trino como
`/tmp/data/iceberg/...`. Preserve também `fs.local.enabled=true` e
`fs.hadoop.enabled=true` no catálogo `iceberg.properties`.

Antes da primeira importação, o catálogo deve estar vazio ou conter somente
tabelas com metadata existente no volume. Não restaure registros antigos de
`iceberg_tables` sem transferir o warehouse correspondente.

```bash
docker exec openfinops-postgres-1 \
  psql -U finops -d finops -Atc 'select count(*) from iceberg_tables;'
```

Para uma instalação nova, o resultado esperado antes das importações é `0`.

### Capacidade e concorrência

O worker ETL processa uma tarefa de extração por vez para evitar disputa de
catálogo e duplicidade de consumo da fila. Três workers de Trino aceleram as
consultas e reprocessamentos que passam pelo Trino, mas não devem ser tratados
como três workers ETL.

Mantenha `REPROCESS_BATCH_ROWS=100000` como padrão. Aumente esse valor somente
após observar memória disponível durante um reprocessamento real. O worker usa
Pandas/Arrow e pode ter picos de memória acima do tamanho do lote.

## 6. Validação final

No servidor:

```bash
docker compose ps
docker exec openfinops-web-1 node -e \
  "fetch('http://127.0.0.1:5173/api/connections/').then(async r => { const x = await r.json(); console.log(r.status, x.length) })"
docker exec openfinops-web-1 node -e \
  "fetch('http://127.0.0.1:5173/api/analysis/health').then(async r => console.log(r.status, await r.text()))"
docker exec openfinops-api-1 python - <<'PY'
import requests
print(requests.get('http://127.0.0.1:8000/etl/worker-health', timeout=10).json())
PY
```

Verifique no navegador:

- `http://<ip-do-servidor>:5173/connections`: contagem igual à base canônica.
- `http://<ip-do-servidor>:5173/analysis`: API e Trino acessíveis.
- `http://<ip-do-servidor>:8000/docs`: documentação da API.
- `GET /etl/worker-health`: `status` deve ser `ok` antes de enfileirar ETL.

## 7. Primeira carga de dados no destino

1. Em **Conexões**, teste todas as credenciais antes de importar.
2. Faça o scan de cada fonte e selecione somente os períodos desejados.
3. Enfileire extrações em ordem controlada; acompanhe `Worker ETL` e as tarefas
   na tela de reprocessamento.
4. Após a carga Bronze, execute o reprocessamento para publicar Silver/Gold.
5. Valide contagem de linhas e períodos no Trino antes de liberar o ambiente.

Fontes `manual_csv` não possuem download externo. Migre-as por upload na API
ou reenvie os CSVs originais; não copie metadata Iceberg isoladamente.

## Diagnóstico rápido

| Sintoma | Causa provável | Ação |
| --- | --- | --- |
| Frontend carrega, mas chamadas falham com `ENOTFOUND host.docker.internal` | Proxy aponta para host Docker local | Definir `VITE_API_PROXY_TARGET=http://api:8000` e recriar `web`. |
| `/analysis` fica aguardando | API usa `localhost:8080` em vez do container Trino | Definir `TRINO_URL=http://trino:8080` e recriar `api`. |
| API falha com `ModuleNotFoundError: seed_*` | Scripts raiz não entraram na imagem | Adicionar `COPY apps/api/*.py ./` no Dockerfile e rebuildar a API. |
| Restore falha em `transaction_timeout` | Dump de PostgreSQL mais novo | Remover apenas a linha `SET transaction_timeout` durante o restore. |
| Conexões não batem com o local | Dump de uma base/volume errado | Consultar `/api/connections/` local e comparar com `source_connection` antes de migrar. |
| Trino informa `ICEBERG_MISSING_METADATA` | Catálogo aponta para arquivo inexistente ou volume não montado em todos os nós | Verificar `iceberg_tables`, os mounts `/data/iceberg` e `/tmp/data/iceberg`; limpe apenas referências sem arquivo. |
| Worker aparece offline | Container worker parado ou heartbeat/PID não compartilhados com API | Subir `worker`, validar volume `worker_runtime` e consultar `/etl/worker-health`. |
