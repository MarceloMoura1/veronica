# Veronica Global Brain Architecture

## Status

Regra arquitetural permanente, formalizada no encerramento homologado da Fase 1.5.
Este documento orienta as fases futuras e não representa o início da Fase 1.6.

## Princípio central

Verônica é a autoridade cognitiva central e global do ecossistema.

MegaDesk, FaYerS, Developer Agent, CAD Agent e agentes futuros são especializações e
braços de execução. Eles não substituem a identidade, a memória ou a visão global da
Verônica.

O modelo arquitetural é:

Marcelo → Verônica Global Brain → memória global → retrieval seletivo →
planejamento/orquestração → agentes especializados → ferramentas.

## Global Brain + Scoped Context

A memória é global. O contexto enviado a cada modelo ou agente é filtrado por
relevância, finalidade, permissões e orçamento de tokens.

Scoped Context é uma projeção temporária da memória global, nunca uma partição da
memória. Ausência de um fato no contexto atual não significa ausência no Global Brain.

## Global Memory

O Memory Core deve evoluir como fonte global de conhecimento sobre:

- Marcelo, pessoas, relações e vida pessoal;
- preferências, conversas, acontecimentos, decisões e planos;
- MegaDesk, FaYerS, outras empresas e projetos;
- tarefas, documentos, agentes e resultados produzidos;
- estados operacionais relevantes e relações entre domínios.

Informações globais não devem ser duplicadas em silos por agente. Estado técnico local
pode existir quando necessário, mas descobertas com valor global devem retornar à
Verônica.

## Current subject é foco, não escopo

active_topic, last_meaningful_topic e current_subject representam apenas o foco
conversacional presente. Eles podem priorizar retrieval e resolver pronomes, mas nunca:

- limitar quais categorias podem ser pesquisadas;
- esconder outras empresas, pessoas ou projetos;
- apagar contexto histórico;
- definir propriedade exclusiva de conhecimento.

Uma referência explícita a outra entidade sempre pode substituir o foco:
MegaDesk → FaYerS → Pedro → MegaDesk, sem perda de acesso às memórias anteriores.

## Selective Retrieval

O contexto deve conter somente informação relevante para a interação. A seleção pode
usar entidade, intenção, recência, relações, importância e permissões.

Economizar tokens não autoriza excluir permanentemente conhecimento do Global Brain.
Quando uma pergunta muda de domínio, o retrieval deve consultar a memória global, não
somente o contexto do turno anterior.

## Cross-domain awareness

A arquitetura deve permitir perguntas que correlacionem pessoas, projetos, empresas,
finanças, decisões, planos e eventos. Exemplos:

- pendências no MegaDesk e na FaYerS;
- decisões recentes nas empresas;
- impacto de um acontecimento do MegaDesk sobre a FaYerS;
- decisões tomadas com Christyan em diferentes projetos;
- planos importantes em múltiplos domínios.

A Fase 1.5 resolve corretamente mudanças sequenciais de domínio. A resolução
simultânea de múltiplas entidades ainda é monofocal: o EntityResolver retorna uma
entidade por consulta. Isso não cria silo no armazenamento, mas exige futuramente um
Multi-Entity Resolver e um agregador de contexto transversal.

## Agent orchestration

Verônica atua como Orchestrator / Global Brain. Um agente especializado recebe:

- tarefa claramente delimitada;
- contexto necessário e filtrado;
- permissões;
- ferramentas;
- critérios de retorno.

O agente retorna:

- resultado;
- descobertas;
- artefatos;
- decisões ou alterações;
- fatos com possível valor global;
- proveniência e confiança.

Verônica decide o que deve ser incorporado à memória global, ao estado conversacional
ou somente ao registro técnico. Agentes não devem gravar fatos globais sem contrato de
retorno, validação e proveniência.

## Prevenção de silos

Fases futuras devem evitar:

- um banco cognitivo isolado por empresa;
- agentes que acumulam descobertas invisíveis à Verônica;
- current_subject usado como filtro obrigatório de armazenamento;
- cópias divergentes do mesmo fato;
- resultados técnicos sem retorno ao orquestrador;
- recuperação limitada ao agente que originou a informação.

Estado especializado pode ser local; conhecimento relevante deve ser globalmente
endereçável.

## Compatibilidade auditada na Fase 1.5

- PersonalMemoryManager usa um único diretório e categorias globais.
- Projects e people coexistem no mesmo Memory Core.
- ConversationContextBuilder pesquisa a entidade explícita independentemente do
  current_subject anterior.
- current_subject apenas sustenta foco e referências vagas.
- Conversation State preserva histórico e referências episódicas sem particionar a
  memória.
- Texto e Gemini Live usam a mesma camada de retrieval.
- Eventos, decisões e planos permanecem globalmente acessíveis por entidade e intenção.

Não foi encontrada estrutura que crie um silo cognitivo por projeto ou agente.

## Requisitos para próximas fases

Sem alterar a Fase 1.5 homologada, a evolução deverá considerar:

1. resolução de múltiplas entidades na mesma consulta;
2. agregação e ranking cross-domain;
3. relações explícitas entre entidades e memórias;
4. contrato Agent → Veronica para descobertas e proveniência;
5. deduplicação global;
6. políticas de confiança, atualização e conflito;
7. retrieval transversal com orçamento de tokens;
8. rastreabilidade de qual agente produziu cada conhecimento.

Qualquer nova fase deve demonstrar que preserva este princípio antes de criar
armazenamento ou contexto especializado.
