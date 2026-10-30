# ADR 0003 — Dashboard SPA sobre os contratos REST existentes

Status: aceito para a Sprint 05, conforme arquitetura e ajustes aprovados.

## Contexto

O backend oferece CRUD paginado, último estado e histórico/métricas por serviço.
Não há resumo global de estados, pesquisa textual ou endpoint de séries temporais.
SSRF, isolamento do PostgreSQL, worker único e telemetria interna são invariantes.

## Decisão

Implementar uma SPA React/TypeScript/Vite, React Router declarativo, TanStack Query
e Mantine. Usar Mantine Form e Mantine Charts/Recharts para reduzir integrações
visuais independentes. Separar páginas, features e código compartilhado; manter
estado remoto no Query e filtros/paginação na URL. Carregar páginas e gráficos
sob demanda. Não introduzir gerenciador global de estado nem autenticação.

Gerar tipos com openapi-typescript a partir de `app.openapi()` no checkout atual,
sem servidor/lifespan/banco. Usar openapi-fetch com a origem do navegador e erros
normalizados, incluindo os formatos distintos de 422 existentes no backend.
Typescript não constitui validação de respostas em runtime; o backend permanece a
fonte de verdade. Aplicar regras locais de formulário para feedback imediato.

Distinguir inventário global e estados exclusivos da página, atividade e resultado,
erro de consulta e resultado DOWN. Preservar nulos e as definições de disponibilidade
e p95 do backend. Consultar o p95 completo separadamente das 12 janelas dos gráficos.
Limitar todas as consultas de métricas a quatro simultâneas com fila cancelável.

Fazer polling apenas dos dados atuais visíveis, com pausa em segundo plano. Fixar
janelas históricas e não fazer polling/refetch por foco dessas consultas. Atualizar
explicitamente a janela e invalidar caches após mutações. Não presumir snapshot
entre chamadas ou imutabilidade absoluta dos dados históricos persistidos.

Servir a SPA com Nginx sem privilégios e proxy restrito às rotas usadas pelo produto.
Manter `127.0.0.1:8080` como único binding do frontend e acesso somente à rede HTTP
da API. Não adicionar CORS ou alterar backend/contratos. Usar PostgreSQL em tmpfs e
projetos descartáveis para E2E com Playwright, além de testes Vitest/RTL/MSW e axe.

## Consequências

Mantine acelera consistência e acessibilidade, com dependência do seu sistema de
estilos e necessidade de verificar contraste real. Gráficos aumentam consultas;
o carregamento sob demanda, cache e limite de concorrência controlam esse custo.
Não prometemos resumo global de estado ou filtros inexistentes na API. Antes de
ampliar escala, aprovar desenho de agregação/lote e orçamento de carga.

A SPA não depende da disponibilidade do Collector ou de acesso do navegador à
telemetria. A stack permanece local, sem autenticação e sem exposição pública.
Uma publicação externa exigirá decisão específica de acesso e segurança.
