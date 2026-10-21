# Catálogo de módulos e disponibilidade por cliente

A fonte única dos menus é `apps/api/app/core/menu_catalog.json`. O backend sincroniza
esse catálogo com o banco no bootstrap; o frontend recebe as entradas por
`GET /navigation` e usa as mesmas entradas para registrar rotas e montar os dois layouts.

## Administração

Abra `/administracao/modulos`, escolha um cliente e ative ou desative os módulos.
Desativar um grupo desabilita suas páginas. A administração de módulos é essencial
para permitir que um administrador reverta a configuração.

A seção de vínculos associa cada usuário a um cliente. A identificação do cliente
vem do usuário autenticado no backend; um parâmetro ou cabeçalho enviado pelo navegador
não pode selecionar outro cliente. Usuários anteriores ao cadastro de clientes usam
o cliente padrão. As configurações dos clientes são independentes.

As tabelas são:

- `navigation_module`: identidade, hierarquia, rota, página, metadados e módulo de permissão.
- `navigation_customer`: cadastro dos clientes.
- `customer_module`: disponibilidade específica por cliente, com autor e data da alteração.
- `navigation_membership`: associação entre usuário e cliente.

O catálogo mantém as escolhas dos clientes ao sincronizar uma nova versão. Entradas
removidas da versão ficam inativas. Módulos novos começam disponíveis e aparecem
automaticamente na administração. Registros com `visible: false` preservam rotas
retiradas anteriormente da navegação, sem recolocá-las nos menus.

## Como adicionar um menu

1. Acrescente uma entrada ao catálogo com `code` estável, `parent_code`, título,
   `path` exclusivo, `renderer`, ícone, ordem e `permission_module`.
2. Use uma rota real, sem `?tab=`, `?executive=` ou outro seletor de página.
   Grupos também têm rotas de apresentação com o renderer `ModuleIndex`.
3. Para uma implementação nova, registre seu componente em `CATALOG_RENDERERS`
   no `App.tsx`. Páginas existentes podem reutilizar um renderer e seus `view_params`.
4. Acrescente os prefixos exclusivos de recursos de API a `api_prefixes`, quando
   existirem. Eles permitem aplicar a disponibilidade mesmo sem o cabeçalho da interface.
5. Execute os testes de navegação e a compilação do frontend. No bootstrap seguinte,
   o registro aparecerá no banco, na tela Módulos e na navegação dos clientes habilitados.

`view_params` são detalhes internos para selecionar a implementação legada. O hook
`useMenuSearchParams` combina esses valores fixos com os filtros dos widgets. Os
seletores de página recebidos pela URL são ignorados, portanto acrescentar
`?executive=historia-contrato` a outra página não troca sua identidade nem suas permissões.
Favoritos antigos são redirecionados à rota registrada, preservando filtros como mês.

## Disponibilidade e permissões

O acesso exige disponibilidade no cliente e em todos os grupos superiores, além da
permissão existente de leitura. O `code` do menu funciona como escopo estável do RBAC;
permissões amplas de módulo continuam válidas. A tela Módulos exige a permissão
administrativa `modules` para ler ou alterar configurações.

A interface valida o acesso no backend antes de montar a página e informa sua identidade
nas chamadas de API. O middleware verifica essa identidade contra a sessão. Recursos
exclusivos declarados no catálogo, como `/planning/contract-story`, são verificados
independentemente do cabeçalho, impedindo contorno por omissão ou troca do código.
O RBAC existente continua protegendo os endpoints compartilhados. Este controle organiza
a disponibilidade das funcionalidades; não introduz separação dos dados financeiros
armazenados por cliente.

## Verificação

- Backend: `PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests/test_navigation_modules.py -q`
- Frontend: em `apps/web`, `npx vitest run src/navigation/routes.test.ts` e `npm run build`.

Os testes usam banco isolado e cobrem clientes diferentes, restrições de administração,
desativação de grupos, escopos de leitura, acesso direto ao recurso de contratos e
migração dos links. A validação do catálogo confere rotas únicas e renderers registrados.
