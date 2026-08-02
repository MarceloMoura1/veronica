# Fase 1.4 — Unified Conversation Memory

## Problema anterior

Mensagens digitadas consultavam `PersonalMemoryManager` no `server.py`, mas o
áudio era enviado diretamente ao Gemini Live. Quando a transcrição chegava ao
backend, o modelo já podia estar formulando a resposta sem memória. Além disso,
`search()` usava interseção lexical e retornava no máximo três itens, insuficiente
para projetos como FaYerS, MegaDesk e Verônica.

## Arquitetura nova

`ConversationContextBuilder` é a única camada de construção de contexto. Ela
usa o `PersonalMemoryManager`, o `EntityResolver`, a intenção da pergunta e um
estado curto de assunto.

```text
texto -----------------> ConversationContextBuilder ----> contexto ----> Gemini
voz -> Gemini Live -> retrieve_memory tool --------------^ 
```

O texto chama `build_context(..., channel="text")`. O Gemini Live recebe a tool
`retrieve_memory`, obrigatória pela instrução de sistema para perguntas pessoais,
familiares, preferências, objetivos, empresas, projetos e continuações. A tool
chama a mesma instância com `channel="voice"`; ela não exige confirmação. Isso
permite ao Live pausar a resposta, obter fatos e continuar com o contexto correto.

## Entidades e aliases

A resolução é determinística, sem embeddings. Entidades armazenadas em
`people.json` e `projects.json` são descobertas dinamicamente. Aliases de voz
conhecidos complementam os nomes: Fayers/Fayer/Fires/Faiers para `FaYerS`, Mega
Desk/Mega deste para `MegaDesk`, Cristian/Christian para `Christyan`, além de
"minha mãe" e "meu pai". Campos persistidos como `fayers_alias_1` e
`megadesk_alias_2` também são incorporados dinamicamente.

## Intenções, retrieval e orçamento

As intenções suportadas são `identity`, `overview`, `detail`, `operations`,
`goals`, `relationship`, `personal`, `preference`, `future` e `standard`.
Projetos usam prioridades de campos por intenção. Por exemplo, operações da
FaYerS priorizam serviços, fluxo, fabricação, SolidWorks, KeyShot, produtos,
portal e automação. O limite varia de 5 itens para identidade/preferência a 30
para detalhe, com teto adicional de 7.000 caracteres.

`search()` agora separa underscores, hífens e CamelCase e remove palavras comuns
que causavam falsos matches. O builder nunca despeja todos os JSONs em perguntas
sem relação.

## Continuação

O builder mantém `current_subject` e até quatro assuntos anteriores. Expressões
como "mais detalhes", "nela", "qual a meta" e "nessa empresa" reutilizam o
assunto atual. Uma pessoa citada dentro de uma continuação de projeto inclui
tanto seus dados quanto os campos do projeto relacionados a ela.

## Anti-hallucination e naturalidade

O contexto instrui o modelo a tratar os itens como fatos do usuário, responder
naturalmente sem anunciar consulta de memória e declarar ausência de informação
quando o fato solicitado não estiver presente.

## Arquivos

- Novos: `backend/memory/entity_resolver.py`,
  `backend/memory/conversation_context_builder.py`,
  `tests/test_conversation_context_builder.py` e este relatório.
- Modificados: `backend/memory/__init__.py`,
  `backend/memory/personal_memory_manager.py`, `backend/server.py` e
  `backend/ada.py`.

## Testes e latência

Os testes usam um pack reduzido fiel a `veronicamemorytestv2.txt` e cobrem os dez
cenários obrigatórios, aliases, duas sequências de continuação, equivalência de
contexto texto/voz e pergunta irrelevante. Os mesmos dez cenários também foram
executados diretamente contra os JSONs reais importados. A recuperação é local,
determinística e não faz chamadas adicionais de IA; no canal de voz existe apenas
o round-trip normal de uma function tool do Gemini Live.

Uma validação real na API Gemini Live confirmou `retrieve_memory` para "Quem é
minha mãe?" e produziu "Sua mãe é Josiane França de Moura, Chefe". Outra sessão
Live preservou FaYerS em "Me dê mais detalhes" e "O que a gente faria nela";
essa execução revelou e passou a cobrir a reformulação do modelo "O que faríamos
na FaYerS?".

## Limitações

Aliases fonéticos precisam ser conhecidos ou adicionados deterministicamente.
A tool depende do cumprimento de tool calling pelo modelo Live; logs
`[MEMORY_CONTEXT]` permitem verificar cada chamada. A equivalência foi comprovada
na API Live, mas a rodada física completa falando pelo HyperX, dez perguntas e
reinício ainda requer participação do usuário na janela da aplicação.
