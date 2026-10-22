# Infraestrutura isolada do Finbase

O Finbase executa API, worker, scheduler, frontend e gateway próprios. Para
economizar memória, usa o PostgreSQL e o Trino que já rodam no projeto
`OpenFinops`, mas recebe o banco PostgreSQL `finbase`, o catálogo Trino
`iceberg_finbase` e um warehouse Iceberg vazio, separados dos dados existentes.

## Primeira inicialização

1. Copie os arquivos locais de ambiente da pasta `OpenFinops` para esta pasta:
   `.env` e `.env-databriks`. Eles são ignorados pelo Git e não devem ser
   versionados.
2. Confirme que os containers `openfinops-postgres-1` e `openfinops-trino-1`
   estão ativos e saudáveis.
3. Crie o banco vazio no PostgreSQL compartilhado:

   ```bash
   docker exec openfinops-postgres-1 psql -U finops -d postgres \
     -c 'CREATE DATABASE finbase OWNER finops'
   ```

4. Recrie o coordenador e os workers Trino para carregar o catálogo novo e o
   mount do warehouse vazio. Execute na pasta `OpenFinops`:

   ```bash
   docker compose up -d --no-deps trino trino-worker trino-worker-2
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
- PostgreSQL compartilhado: porta `5434` no host; banco `finbase`

## Parar a cópia

Na pasta `finbase`, execute:

```bash
docker compose -f docker-compose.finbase.yml down
```

Esse comando para os containers Finbase e mantém o volume de runtime. Não use
`down -v` se quiser preservar esse volume.
