# ADR 0001 — Transporte HTTP fixado e worker independente

Status: aceito para a Sprint 03.

## Contexto

O cadastro valida URLs, mas essa validação não impede respostas DNS privadas nem
DNS rebinding. O monitoramento também precisa respeitar intervalos sem executar
schedulers duplicados nos processos Uvicorn. O projeto já usa SQLAlchemy e
psycopg síncronos com PostgreSQL e testes locais SQLite.

## Decisão

Usar HTTP Core HTTP/1.1 com backend de rede próprio: resolver DNS, validar todos
os endereços e conectar socket diretamente a um IP numérico autorizado. Preservar
o hostname original para Host/SNI e validação TLS. Usar uma conexão por tentativa,
sem proxies, redirects, retries, autenticação ou cookies. Declarar HTTP Core como
dependência direta, pois seu uso deixa de ser apenas transitivo do HTTPX de testes.

Executar um único worker asyncio separado. Manter SQLAlchemy síncrono, isolando
operações em threads com sessões próprias, evitando a introdução de um segundo
driver/modelo de persistência. Selecionar apenas tarefas que cabem na capacidade
livre. Usar último resultado persistido e prazos monotônicos em memória para os
intervalos, sem transações abertas durante HTTP.

Persistir enum textual com CHECK constraint para portabilidade dos testes, UUIDs,
TIMESTAMPTZ e índice composto por serviço/horário/UUID. Excluir resultados em
cascata com o serviço. Calcular agregações no banco, com p95 contínuo PostgreSQL;
SQLite usa a mesma interpolação com consulta de no máximo dois valores.

## Consequências

Há custo de DNS/TCP/TLS por tentativa, escolhido para simplificar o vínculo entre
validação e conexão. Não há fallback automático para outro IP do mesmo domínio.
Um IP indisponível pode causar falha mesmo com outros endereços utilizáveis.
Falhas antes do commit podem perder resultados; não há fila durável. Alterações
concorrentes de cadastro são observadas na próxima leitura, sem cancelamento
retroativo de uma conexão já iniciada. Retenção de histórico será tratada depois.

Uma única réplica do worker é uma restrição operacional explícita. Antes de
replicar, implementar leases/coordenação distribuída e revisar idempotência.
Controles SSRF da aplicação devem ser reforçados por egress de infraestrutura;
roteamento especial e intermediários públicos ficam fora da garantia do socket.

Detalhes operacionais, classificação dos estados e ameaças estão em
[monitoring.md](../monitoring.md).
