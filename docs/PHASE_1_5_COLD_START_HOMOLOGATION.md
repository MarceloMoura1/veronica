# Fase 1.5 - correção de cold-start restoration

## Causa raiz

A primeira implementação persistia eventos, decisões, planos e o último sujeito, mas
somente o caminho de reconnect injetava histórico na sessão Gemini Live. No primeiro
connect depois de reiniciar o Electron/Python, nenhuma informação da conversa anterior
era enviada ao modelo.

## Arquitetura corrigida

O arquivo conversation_state.json é separado da memória pessoal e da memória episódica.
Ele mantém um pacote limitado com IDs de conversa, tópico e entidades ativos, tópicos
recentes, referências episódicas, resumo compacto, até doze turnos importantes de
Marcelo e da Verônica e timestamps.

O arquivo usa escrita temporária, fsync e substituição atômica. JSON inválido é
preservado com timestamp antes do fallback seguro.

No primeiro connect de AudioLoop.run(), o pacote restaurável é enviado com
end_of_turn=False antes de listen_audio() iniciar. O carregamento é silencioso e não
pede resposta espontânea. Reconnect permanece um caminho distinto e conserva a
restauração do histórico da execução corrente.

## Provas

Os testes destroem e recriam toda a pilha em diretório temporário e comprovam o evento
de Pedro com referência vaga, a decisão de R$700 no MegaDesk, o plano da página de
clientes, rotação do ID, limite compacto, recuperação de JSON inválido e preload
silencioso.

Em teste real com uma nova sessão Gemini Live, o estado reconstruído tinha 1.278
caracteres. Após o preload, o modelo respondeu corretamente que Marcelo falava de Pedro,
que se machucou jogando bola, foi ao hospital e já estava melhor.

A homologação física final continua dependendo do roteiro interativo com o HyperX.

## Correção final de Session Resume e Active Topic

Saudações com “Verônica” eram interpretadas como assunto porque o resolvedor não
distinguia vocativo de sujeito. Além disso, perguntas como “O que a gente tava
conversando?” não tinham uma intenção própria e podiam produzir contexto vazio.

A correção acrescenta:

- distinção determinística entre vocativo, saudação e assunto real;
- last_meaningful_topic, preservado durante saudações e trivialidades;
- janela de três interações após cold start;
- intenção session_resume com consulta direta ao Conversation State;
- restauração do current_subject após a retomada;
- has_context explícito para impedir falsa alegação de contexto ausente;
- refresh silencioso do estado no Gemini Live depois de uma saudação;
- obrigatoriedade de retrieve_memory em pedidos de retomada por voz.

Uma prova real com Gemini Live reproduziu cold start, “Bom dia, Verônica, está me
ouvindo?” e “O que a gente tava conversando?”. O tópico permaneceu Pedro e a resposta
recuperou Pedro, tornozelo e três meses de fisioterapia.

Os JSON reais foram auditados como UTF-8 e continham corretamente “Verônica”, “notícia”
e “fisioterapia”. O mojibake observado era da exibição do console, não dos dados.
