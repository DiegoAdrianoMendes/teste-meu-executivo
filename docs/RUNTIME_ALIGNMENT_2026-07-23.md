# Alinhamento local de runtimes

Data: 23/07/2026

## Decisão

- Frontend: Node.js `24.18.0` LTS, fixado em `frontend/.nvmrc`, `frontend/package.json`, `Dockerfile.frontend`, Compose e gates de release.
- Backend: CPython `3.12.13`, mantido como runtime canônico e validado do projeto.
- Dependências JavaScript: lockfile atualizado sem promover majors do React, Vite ou Vitest sem prova de compatibilidade.
- Dependências Python: `pip check` sem requisitos quebrados e a base instalada não apresentou candidatos pendentes no inventário local.

## Por que Python não foi promovido para 3.14

Python `3.14.6` é uma versão estável disponível, mas este repositório usa CPython 3.12.13 como parte do censo determinístico dos upstreams OSS e das provas de release. Promover a major sem regenerar o censo, o lock universal e a suíte inteira criaria uma falsa sensação de atualização e poderia invalidar evidências. A migração para Python 3.14 fica registrada como sprint própria, com regeneração do censo e prova completa antes de alterar as imagens de produção.

## Evidência executada

- Node `24.18.0`: `npm run verify:node`, `npm run typecheck`, `npm run lint` e `npm run build` aprovados.
- Frontend focado: `79/79` testes aprovados nos fluxos de contexto, conversas, menções, disponibilidade, inventário e ferramentas.
- Python: `22/22` testes de verificação/worker aprovados, `compileall` aprovado e `pip check` sem erros.
- Suíte frontend completa: `166/166` arquivos e `481/481` testes aprovados. O build de produção e o typecheck estão verdes.
- Lint frontend: aprovado com `9` avisos existentes de Fast Refresh, sem erros.
- APIs externas, Meta, WhatsApp, AWS e integrações online continuam bloqueadas.

## Fontes oficiais

- [Node.js Releases](https://nodejs.org/en/about/previous-releases)
- [Python Insider: Python 3.14.6 e 3.13.14](https://blog.python.org/2026/06/python-3146-31314/)
