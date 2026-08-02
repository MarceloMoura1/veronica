# Fase 1.2 — Personal Memory Manager

## Arquitetura

Foi adicionada uma camada independente em `backend/memory/`. O
`PersonalMemoryManager` mantém memórias pessoais em JSON legível, sem banco de
dados, embeddings ou dependências externas. O `ProjectManager` não foi alterado
e continua responsável pelos projetos, histórico e artefatos.

## Arquivos

- Adicionados: `backend/memory/__init__.py`,
  `backend/memory/personal_memory_manager.py`,
  `tests/test_personal_memory_manager.py` e este relatório.
- Modificados: `backend/server.py` para captura e recuperação em mensagens de
  texto; `backend/ada.py` para dar prioridade ao título persistido na criação
  da instrução de sistema.

## Dados e salvamento

O diretório padrão é `data/memory/`, com `profile.json`, `preferences.json`,
`people.json`, `facts.json` e `projects.json`. Cada arquivo contém um objeto JSON.
Toda mudança é gravada imediatamente em arquivo temporário no mesmo diretório,
seguida de `flush`, `fsync` e `os.replace`. JSON inválido gera log e fallback
vazio sem sobrescrever automaticamente o arquivo problemático.

## Captura e recuperação

A captura local reconhece apenas comandos explícitos de alta confiança, como
"me chama de", "meu ... é ...", "memorize/lembre/guarde que ..." e código de
projeto. Não há chamada ao Gemini para classificar esses textos.

Antes de uma mensagem textual normal ser enviada, uma busca lexical seleciona
no máximo três itens relacionados. Há um alias específico para perguntas sobre
como tratar o usuário. Somente o pequeno contexto encontrado é anexado; uma
pergunta sem correspondência segue sem memória adicional.

## Identity Profile e ProjectManager

`backend/config/assistant_identity.json` permanece a identidade base. Ao iniciar
o backend, `preferences.preferred_title` substitui `owner_title`, quando existir.
O armazenamento pessoal é separado das pastas gerenciadas pelo `ProjectManager`;
nenhuma API ou responsabilidade dele foi substituída.

## Testes

A suíte focada cobre persistência do título, persistência do código de Orion,
seleção sem FaYerS/MegaDesk, criação automática, fallback de JSON inválido,
captura dos comandos e recuperação da preferência por pergunta natural.

O teste físico com Electron, microfone e uma sessão Gemini autenticada precisa
ser executado no ambiente interativo do proprietário. Ele não é simulado pela
suíte unitária.

## Limitações e próximos passos

- A captura automática desta fase integra mensagens digitadas; fala em tempo real
  continua sendo enviada diretamente ao Gemini e não é interceptada para salvar.
- A busca é lexical e deliberadamente simples; sinônimos amplos e semântica não
  são tratados.
- A escrita manual concorrente aos arquivos durante uma gravação não é mesclada.

Após homologação física, podem ser avaliados mais padrões explícitos, captura
de transcrição final de voz e, somente se necessário, recuperação semântica.
