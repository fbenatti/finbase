# Infraestrutura isolada do Finbase

O Finbase executa API, worker, scheduler, frontend e gateway próprios. Para
economizar memória, usa o PostgreSQL e o Trino que já rodam no projeto
`OpenFinops`, mas recebe o banco PostgreSQL `finbase`, o catálogo Trino
`iceberg_finbase` e um warehouse Iceberg vazio, separados dos dados existentes.

## Primeira inicialização

1. Copie os arquivos locais de ambiente da pasta `OpenFinops` para esta pasta:
   `.env` e `.env-databriks`. Eles são ignorados pelo Git e não devem ser
   versionados.
2. Confirme que o PostgreSQL compartilhado está escutando em `localhost:5433`
   e que o container `openfinops-trino-1` está saudável.
3. Crie o banco vazio no PostgreSQL compartilhado. O `psql` solicitará a senha
   configurada em `.env`:

   ```bash
   psql -h 127.0.0.1 -p 5433 -U finops -d postgres \
     -c 'CREATE DATABASE finbase OWNER finops'
   ```

4. Recrie o coordenador e os workers Trino para carregar o catálogo novo e o
   mount do warehouse vazio. Execute na pasta `OpenFinops`:

   ```bash
   docker compose up -d --no-deps --force-recreate trino trino-worker trino-worker-2
   ```

5. Execute na pasta `finbase`:

   ```bash
   docker compose -f docker-compose.finbase.yml config --quiet
   docker compose -f docker-compose.finbase.yml up -d --build
   ```

O scheduler cria as tabelas de metadados na primeira inicialização. O warehouse
local é `apps/data/iceberg` nesta cópia, criado vazio e montado no Trino em
`/data/finbase-iceberg`.

## Endereços locais

- Aplicação: <http://localhost:5174>
- API: <http://localhost:8011>
- Trino compartilhado: <http://localhost:8080>
- PostgreSQL compartilhado: porta `5433` no host; banco `finbase`

## Parar a cópia

Na pasta `finbase`, execute:

```bash
docker compose -f docker-compose.finbase.yml down
```

Esse comando para os containers Finbase e mantém o volume de runtime. Não use
`down -v` se quiser preservar esse volume.

## Recomendações para o cluster Trino compartilhado

O cluster (`openfinops-trino-1` e dois workers) é operado pelo OpenFinops; este
repositório não altera a configuração dele. O que segue são recomendações para
quem opera o cluster, com a evidência medida em 2026-10-23 no ambiente local
(T5 de `docs/plano-limpeza-e-performance.md`).

**Diagnóstico.** O problema não é volume (281 MB) e sim o custo fixo por query
multiplicado por queries em série: `SELECT 1` 0,3–0,8 s; `COUNT(*)` em 372 k
linhas 1,4–2,8 s; `DESCRIBE silver.dest_billing_cloud` 6,2–9,2 s (2,3 s só de
análise, lendo 172 manifests para 49 parquet); `SELECT MAX(_billing_period)`
2–4 s. A API deixou de fazer `DESCRIBE` e `MAX(period)` por request (lê o
catálogo SQL do PyIceberg, chaveado por snapshot), mas o custo fixo continua em
toda query de dado.

**1. Deixar o coordenador executar queries pequenas.**
`node-scheduler.include-coordinator=false` manda toda query para um worker com
`task.concurrency=1` e `task.max-worker-threads=2`, inclusive um `DESCRIBE`.
Com memória para isso, `include-coordinator=true` (ou um terceiro nó só para
queries curtas via resource groups) tira a fila dos workers para metadados e
agregações de dashboard.

**2. Concorrência dos workers.** `task.concurrency=1` e `task.max-worker-threads=2`
foram escolhidos para a VM de 7,65 GiB. Se os workers ganharem memória,
`task.concurrency=4` e `task.max-worker-threads=8` reduzem o tempo das
agregações largas (hoje `SELECT DISTINCT` em 25 KB abre 22 drivers e leva 3 s).

**3. Cache de metadados do conector Iceberg (versão 483).** No catálogo
`iceberg_finbase`:

```properties
iceberg.metadata-cache.enabled=true
iceberg.file-status-cache.enabled=true
iceberg.file-status-cache.ttl=5m
fs.cache.enabled=true
fs.cache.directories=/tmp/trino-cache
fs.cache.max-sizes=2GB
```

O metadado só muda em publicação, então o TTL pode ser de minutos. Isso ataca
diretamente os 2,3 s de análise do `DESCRIBE` e o planejamento de todo
`SELECT` na tabela.

**4. Retenções mínimas.** `iceberg.expire-snapshots.min-retention` e
`iceberg.remove-orphan-files.min-retention` estão no padrão de 7 dias, por isso
`expire_snapshots(retention_threshold => '1s')` falha no log. Para a Silver e a
Gold do Finbase, que são republicadas por completo, `1d` é seguro e deixa a
manutenção pós-publicação (`app/services/iceberg_maintenance.py`, ligada com
`ICEBERG_MAINTENANCE_APPLY=true`) reduzir os 86 snapshots e 172 manifests de
`dest_billing_cloud`. Até lá a API mantém `7d` como retenção pedida.

**5. Tabelas.** O Finbase cria tabelas com
`write.target-file-size-bytes=128 MB`, pede `optimize(file_size_threshold =>
'64MB')`, `optimize_manifests`, `expire_snapshots` e `remove_orphan_files`
após cada publicação (só plano no log até o item 4 estar combinado) e passa a
gravar o coletor OCI Monitoring em um commit por lote de janelas, não um por
blob (`bronze.raw_oci_monitoring` tinha 389 parquet em 390 commits).
