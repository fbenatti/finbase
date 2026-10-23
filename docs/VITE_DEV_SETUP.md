# Frontend em modo dev

## Estado temporario

O dominio publico e atendido por **Nginx** (nao Apache). Atualmente, a raiz do
dominio usa o frontend compilado via gateway Docker em `127.0.0.1:5173`. Durante
o trabalho no backend, a rota `/api/` aponta para a API FastAPI iniciada no host
em `127.0.0.1:8001`.

Foi usada a porta `5174` para nao conflitar com o gateway Docker, que publica
`5173` no host.

## Ativar o Vite

Na raiz do repositorio:

```bash
cd apps/web
npm install
npm run dev -- --port 5174
```

O processo deve permanecer ativo no host da aplicacao. Depois, valide o Nginx
e recarregue-o:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Acesse `https://<seu-dominio>/dimon-dashboard`.

## Ativar a API no host

Inicie apenas a API na porta `8001`, a partir do ambiente virtual do projeto:

```bash
cd apps/api
../../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

O modo `--reload` aplica alteracoes do backend sem reconstruir o container. O
Nginx encaminha `/api/` para essa porta enquanto este modo estiver ativo.

## Reverter para o frontend compilado

1. Pare o processo Vite em `5174`.
2. Caso o frontend esteja em Vite DEV, em `infra/nginx/openfinops.conf`, altere:

```nginx
proxy_pass http://127.0.0.1:5174;
```

para:

```nginx
proxy_pass http://127.0.0.1:5173;
```

3. No mesmo arquivo, restaure a API conteinerizada:

```nginx
proxy_pass http://127.0.0.1:8001;
```

para:

```nginx
proxy_pass http://127.0.0.1:8000;
```

4. Valide e recarregue o Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

5. Suba/reconstrua os servicos `web` e `api` do Docker conforme o procedimento de
   deploy vigente.

Nao remova a configuracao de `/api/` ao fazer essa reversao.