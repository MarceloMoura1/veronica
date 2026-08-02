# Fase 1.5 — Continuous Conversational Memory

## Arquitetura

A Fase 1.5 preserva `PersonalMemoryManager`, `EntityResolver` e
`ConversationContextBuilder`. A nova classe `ConversationalMemoryAnalyzer`
recebe turnos completos e seleciona apenas informações com valor futuro.

```text
texto ----\
           -> process_conversation_turn -> classificação -> JSON atômico
voz final-/                                  |
                                              -> retrieval unificado
```

Texto e voz chamam exatamente `process_conversation_turn(user_text, channel)`.
O canal existe para observabilidade e provenance; não altera a classificação.

## Fluxo de texto

`server.user_input` constrói o contexto da Fase 1.4 e chama o analisador antes de
enviar a mensagem ao Gemini. Comandos explícitos continuam síncronos e recebem
confirmação. A escrita determinística local é pequena e não depende de outra
chamada de modelo.

## Fluxo de voz

`AudioLoop.receive_audio` continua emitindo deltas de transcrição para a UI, mas
mantém um acumulador separado. O analisador nunca recebe esses fragmentos. Tool
calls podem encerrar um `receive()` intermediário; somente
`server_content.turn_complete` libera `_process_completed_voice_turn()`. O
acumulador e o rastreamento de deltas são zerados juntos depois dessa análise.

## Classificação e memory pollution

As classes mínimas são `fact`, `preference`, `event`, `decision`, `plan`, `update`
e `ignore`. Regras determinísticas priorizam:

- comandos explícitos com confiança 0,99;
- decisões confirmadas com marcadores como "decidimos";
- planos e referências futuras;
- preferências declaradas;
- eventos envolvendo entidades conhecidas;
- atualizações relacionadas ao evento/plano recente.

Saudações, confirmações curtas, risadas, clima e frases sem valor futuro são
ignoradas. Perguntas também nunca criam memória. "Talvez", "acho" e "estou
pensando" não se tornam decisões definitivas. Negações de evento são filtradas.

## Persistência

Foram adicionados arquivos legíveis:

- `events.json`: acontecimentos e seu estado;
- `decisions.json`: decisões ativas, projeto e participantes;
- `plans.json`: planos planejados, tentativos, cancelados ou reagendados;
- `continuity.json`: assunto anterior, IDs recentes, hashes deduplicados e data
  da última sessão.

Eles usam o mesmo temporário, `flush`, `fsync` e `os.replace` da memória
existente. O mecanismo de backup da importação percorre todas as categorias e,
portanto, inclui as novas. Packs V1 continuam compatíveis.

## Memória episódica e atualizações

Eventos guardam ID, entidades, tipo, resumo, detalhes, referência temporal,
`recorded_at`, `occurred_at` quando seguro, `updated_at`, status, fonte e
confiança. Continuações pronominais enriquecem o evento mais recente da entidade
em vez de criar episódios desconectados. O resumo original é preservado e as
mudanças ficam em `details`; por exemplo, uma lesão pode evoluir para
`status=recovering`.

## Decisões, planos e preferências

Decisões registram a frase observável, projeto, participantes, status e
confiança. Hipóteses não sobrescrevem decisões confirmadas. Planos podem ser
cancelados ou reagendados mantendo histórico. Preferências conversacionais usam
uma chave estável por assunto; uma declaração clara mais recente substitui o
estado ativo anterior.

## Recência, deduplicação e continuidade

APIs `get_recent_memories`, `get_recent_events`, `get_recent_decisions`,
`get_recent_plans` e `get_active_plans` ordenam por `updated_at/recorded_at`.
Hashes SHA-256 do texto normalizado evitam analisar o mesmo turno duas vezes,
inclusive após restart. `last_subject` restaura continuações como "E como ele
está?" sem carregar transcript ou toda a memória.

`ConversationContextBuilder` reconhece intents `event`, `decision` e `plan` e
adiciona somente registros recentes relacionados à entidade. Eventos, decisões,
planos e continuidade foram excluídos da busca lexical genérica para impedir
vazamento de registros irrelevantes.

## Encerramento e latência

Não existe fila em background: cada mudança selecionada termina sua gravação
atômica antes do retorno. Portanto o shutdown forçado existente não perde uma
fila pendente. Frases ignoradas fazem apenas normalização e leitura pequena de
metadados; nenhuma classificação chama Gemini adicionalmente.

## Testes

Os testes automatizados cobrem os 18 cenários obrigatórios, perguntas
anti-poluição, continuidade restaurada e uso explícito de `turn_complete`. Todas
as suites das Fases 1.2–1.5 são executadas juntas.

O teste real Gemini Live usou voz portuguesa sintetizada localmente e comprovou:

```text
áudio PCM 16 kHz
-> transcrição final sobre Pedro
-> classification=event channel=voice
-> evento persistido em armazenamento temporário
```

A transcrição reconheceu que Pedro se machucou, machucou o braço e foi ao
hospital; o registro incluiu entidade Pedro, `time_reference=ontem`,
`occurred_at`, `source=voice` e confiança 0,92.

## Arquivos

- Novo: `backend/memory/conversational_memory_analyzer.py`.
- Modificados: `backend/memory/__init__.py`,
  `backend/memory/personal_memory_manager.py`,
  `backend/memory/entity_resolver.py`,
  `backend/memory/conversation_context_builder.py`, `backend/server.py` e
  `backend/ada.py`.
- Testes: `tests/test_conversational_memory_analyzer.py`.

## Limitações

A classificação V1 é determinística e conservadora. Eventos sobre pessoas
ainda desconhecidas normalmente exigem ensino explícito ou cadastro prévio.
Referências temporais relativas resolvem apenas "ontem" com data absoluta;
outras são preservadas textualmente. Similaridade de eventos usa entidade,
recência e texto normalizado, sem embeddings. A fase só deve ser considerada
homologada depois do roteiro físico com HyperX, fechamento e reinício.
