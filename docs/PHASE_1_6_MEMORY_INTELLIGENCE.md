# Fase 1.6 — Memory Intelligence

## Princípio e arquitetura

A Fase 1.6 adiciona retrieval local multi-entidade e cross-domain sem alterar o
princípio: **Verônica = Global Brain; contexto = projeção seletiva**.

Fluxo:

query → resolve_entities → intent/time filter → search_global → scoring →
consolidação → seleção equilibrada → context budget → texto/Gemini Live.

O caminho monofocal homologado da Fase 1.5 foi preservado. A nova camada é usada para
múltiplas entidades e consultas episódicas/temporais que precisam de ranking.

## Multi-Entity Resolver

resolve_entities retorna lista ordenada pela posição na frase. resolve_entity continua
compatível e retorna a primeira entidade.

O resolvedor:

- preserva aliases;
- distingue vocativo de assunto;
- representa Marcelo em “eu e Christyan”;
- suporta “nas duas”, “os dois”, “ambos” e variações;
- restaura active_entities após cold start.

current_subject continua sendo apenas foco. Em consultas multi-entidade, o projeto
mencionado pode virar foco sem limitar as outras memórias.

## Cross-Domain Aggregator

ConversationContextBuilder agrega campos e memórias episódicas de todas as entidades.
A seleção é equilibrada: cada domínio recebe uma cota antes do preenchimento global.

O retorno estruturado inclui entities, selected_memories, category, entity, field,
score, reasons, timestamp e ranking_time_ms. Esses dados servem para depuração e não
são exibidos normalmente ao usuário.

## Ranking global

O score combina:

- relevância lexical;
- entity match;
- intent match;
- recência;
- importância;
- confiança;
- relações;
- status ativo.

Estados superseded, cancelled, completed e historical recebem penalidade ou são
excluídos quando a consulta pede somente o estado atual. Uma decisão confirmada pode
superar uma hipótese mais recente.

## Timeline

Filtros suportados incrementalmente:

- recent;
- today;
- yesterday;
- this_week;
- last_week;
- this_month;
- last_n_days.

São usados updated_at, recorded_at ou occurred_at. Recência participa do score, mas
não é o único critério.

## Importância e confiança

importance aceita low, medium, high, critical ou valor numérico. Relações existentes
podem contribuir quando importance não estiver declarada. Não existem nomes
hardcoded.

Eventos recebem importância derivada da entidade. Decisões confirmadas usam
importância alta e confiança 0,94. Planos confirmados usam importância média;
hipóteses permanecem tentative e com confiança menor.

## Contradições e atualizações

Decisões suportam active e superseded, com preparação para reverted. Quando uma
decisão confirmada muda, a antiga recebe superseded_by e a nova recebe supersedes.
Consultas atuais ocultam a versão superada; consultas históricas podem recuperar ambas.

Preferências atuais continuam usando a chave estável homologada. Histórico completo de
preferências fica para evolução futura, evitando burocratizar fatos simples.

## Planos

Planos suportam planned, tentative, cancelled e completed. Cancelamento e conclusão
preservam history e updated_at. get_active_plans e consultas de planos ativos retornam
somente planned/tentative.

## Consolidação

O ranking elimina duplicatas exatas e registros da mesma categoria/entidade com alta
similaridade lexical. IDs e timestamps não entram na comparação semântica.

Nenhuma memória é apagada: consolidação atua somente na seleção. Não existe
esquecimento agressivo nesta fase.

## Relações cross-entity

Eventos guardam relações related_to. Decisões guardam projeto, participantes e
relações related_to/participant. Planos guardam relação com o projeto.

Isso permite ranking transversal e prepara Life Graph sem implementar Knowledge Graph
completo.

## Context budget, custo e latência

- consultas simples mantêm o contexto compacto existente;
- duas entidades dividem a seleção;
- três ou mais aumentam moderadamente candidatos;
- o contexto nunca ultrapassa max_context_chars.

Não há embeddings, banco vetorial ou chamada Gemini extra. O retrieval é local e
determinístico.

Logs registram query, entities, intent, selected, context_chars e ranking_ms sem
despejar a memória completa.

## API

PersonalMemoryManager expõe search_global(query, entities, intent, time_filter,
max_items). MemoryIntelligence consulta profile, preferences, facts, people, projects,
events, decisions e plans. Conversation State participa apenas quando pertinente e não
vaza histórico para consultas atuais.

## Global Brain

- armazenamento continua único;
- contexto é filtrado, não particionado;
- troca de assunto não elimina conhecimento;
- texto e voz compartilham o builder;
- referências plurais reutilizam entidades recentes;
- Session Resume e cold start preservam múltiplas entidades;
- futuros agentes podem devolver relações, confiança, importância e proveniência ao
  mesmo Memory Core.

## Testes

test_memory_intelligence cobre os 20 requisitos obrigatórios: multi-entidade,
Marcelo/Christyan/MegaDesk, troca de foco, contexto transversal, budget, confiança,
superseded, cancelled/completed, timeline, planos ativos, aliases, pluralidade,
consolidação, ausência de silos, texto/voz, Session Resume, cold start e importação.

As suítes das Fases 1.2–1.5 permanecem como regressão obrigatória.

## Limitações

- NLP temporal usa padrões incrementais.
- Supersession usa projeto, tema e marcadores explícitos.
- Similaridade é lexical, sem embeddings.
- Relações ainda não formam um grafo navegável.
- Decay é somente metadata/conceito futuro.
- Impacto empresarial é analisado pelo Gemini usando contexto agregado, não por regra
  rígida do backend.

## Preparação para Life Graph

entities, project, participants, relationships, status, supersedes, superseded_by,
confidence, importance e timestamps formam nós e arestas iniciais. Uma fase futura
poderá indexá-los como grafo sem criar memórias isoladas.

## Homologação

A Fase 1.6 fica tecnicamente pronta após testes, regressões, build e diff. Ela somente
deve ser considerada homologada depois do teste físico real de voz.
