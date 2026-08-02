# Fase 1.3 — Memory Data Import

## Fluxo anterior

O seletor existente lia um TXT no frontend e emitia `upload_memory`. O backend
exigia uma sessão Gemini ativa e enviava o arquivo inteiro como contexto
temporário. Nada desse upload era persistido pelo `PersonalMemoryManager`.

## Fluxo novo

O frontend valida 256 KiB, envia o conteúdo e apenas o nome informativo do
arquivo. `upload_memory` valida novamente, chama `import_memory_text`, persiste o
merge em `data/memory/` e responde com `memory_import_result`. A seção Memory
Data mostra contagens de perfil, preferências, pessoas, projetos, fatos e linhas
ignoradas. O pack não é enviado ao Gemini e a importação não depende de uma
sessão de áudio ativa.

## Veronica Memory Pack V1

O formato é UTF-8, legível e determinístico. Comentários começam com `#`;
valores usam `=` ou `:`.

```text
# VERONICA MEMORY PACK V1

[PROFILE]
name = Marcelo

[PREFERENCES]
preferred_title = Chefe
language = pt-BR

[PERSON:Christyan]
relationship = sócio

[PROJECT:FaYerS]
description = Empresa de engenharia e modelagem 3D

[FACTS]
project_orion_internal_code = ZX-4729
```

Também são aceitas seções `[PERSON]` e `[PROJECT]` quando `name` for o
primeiro campo. Seções desconhecidas, linhas fora de seções e pares inválidos
são reportados em `ignored_lines`. Um arquivo sem nenhuma entrada válida é
rejeitado sem escrita.

## Merge e backup

O pack nunca substitui um arquivo JSON inteiro. Chaves e entidades são
comparadas sem diferenciar maiúsculas de minúsculas. Um campo presente no pack
é considerado uma substituição explícita do mesmo campo; campos que não aparecem
são preservados. O resultado informa os caminhos sobrescritos.

Antes de qualquer mudança em uma memória que já contenha dados, os cinco JSONs
são copiados para `data/memory/backups/<timestamp UTC>/`. `data/memory/` já está
no `.gitignore`, portanto dados e backups de execução não são versionados.

## Segurança

O limite é 256 KiB medidos em UTF-8, aplicado no frontend e novamente no
backend. `source_name` é somente metadado de resposta e é reduzido ao nome-base;
ele nunca é usado para abrir ou criar arquivos. Todas as gravações continuam
usando temporário, `flush`, `fsync` e `os.replace`.

## Arquivos alterados

- `backend/memory/personal_memory_manager.py`
- `backend/server.py`
- `src/App.jsx`
- `src/components/SettingsWindow.jsx`
- `tests/test_memory_data_import.py` (novo)
- `docs/PHASE_1_3_MEMORY_DATA_IMPORT.md` (novo)

## Testes e limitações

Os testes cobrem as cinco categorias, persistência, merge, rejeição segura,
backup anterior à sobrescrita e ausência de envio ao Gemini no handler.

Esta versão importa o Memory Pack V1 estruturado; texto livre arbitrário não é
classificado por Gemini. A homologação final ainda requer selecionar um arquivo
na interface, reiniciar a aplicação e consultar um fato persistido.
