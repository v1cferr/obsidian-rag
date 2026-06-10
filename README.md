# obsidian-rag

Chatbot pessoal com RAG sobre o vault do Obsidian (sincronizado via Dropbox), com citacao das fontes em cada resposta. Roda localmente no Arch Linux, com deploy web planejado para `obsidian.v1cferr.dev`.

> Status: fase de planejamento/arquitetura. Nada implementado ainda.

## Objetivo

Conversar com o conteudo do vault em `~/Dropbox/Obsidian/v1cferr` e obter respostas fundamentadas nos documentos reais, sempre indicando **qual nota ou documento** embasou cada trecho da resposta.

## Requisitos

Funcionais:

- Chat em linguagem natural sobre todo o conteudo do vault.
- Suporte multimodal: o vault nao e so Markdown (ver levantamento abaixo).
- Citacao de fontes: cada resposta referencia as notas/documentos usados, com link para abrir o arquivo.
- Reindexacao incremental quando o Dropbox sincronizar arquivos novos ou alterados.

Nao funcionais:

- Plataforma principal: Arch Linux (desktop).
- Futuro: acesso via web em `obsidian.v1cferr.dev`, com autenticacao (o conteudo e pessoal).
- O indice nao deve poluir o vault nem ser sincronizado pelo Dropbox.

## Conteudo do vault (levantamento real)

Levantamento por extensao (ignorando `.obsidian/` e `.git/`):

| Tipo | Qtd. | Tratamento previsto |
| --- | --- | --- |
| `pdf` | 324 | Parser MinerU (estrutura + OCR) |
| `md` | 142 | Indexacao direta como texto |
| `png/jpg/jpeg/webp/svg` | ~150 | Pipeline de imagem do RAG-Anything (VLM/caption) |
| `opus` (audio) | 33 | Transcricao previa com Whisper (RAG-Anything nao processa audio) |
| `tex` | 19 | Indexacao como texto (e texto plano) |
| `pptx/doc` | 3 | Parser Docling (requer LibreOffice) |
| `txt/xml` | 5 | Indexacao direta |
| `zip/log/aux/fls/xcf/...` | ~10 | Ignorados (artefatos de build LaTeX, binarios) |

A predominancia de PDFs e a presenca de imagens e audio justificam um pipeline RAG multimodal em vez de um indexador de Markdown puro.

## Decisao de arquitetura

### Premissa que define tudo

O motor de RAG escolhido ([RAG-Anything](https://github.com/HKUDS/RAG-Anything)) e Python. Ou seja, **existira um backend Python de qualquer forma** — Electron, Tauri e Neutralino seriam apenas cascas de UI na frente desse backend. Como tambem existe o requisito futuro de servir na web, a arquitetura mais racional e **web-first**:

```text
[ Vault no Dropbox ] --watcher--> [ Backend Python (FastAPI + RAG-Anything) ] <--HTTP--> [ UI web (chat) ]
```

A mesma dupla backend + frontend atende os tres cenarios sem reescrita:

1. **Local (agora):** servidor roda na maquina, UI abre no navegador em `localhost`.
2. **Desktop (opcional, depois):** wrapper Tauri apontando para a mesma UI, com o backend como *sidecar*.
3. **Web (futuro):** mesmo backend + frontend num VPS atras de proxy reverso com TLS e autenticacao.

### Comparativo dos frameworks desktop

| Criterio | Electron | Tauri | Neutralino |
| --- | --- | --- | --- |
| Tamanho do binario | ~150-250 MB (Chromium embutido) | ~5-15 MB (webview do sistema) | ~2-5 MB |
| Uso de memoria | Alto | Baixo | Baixo |
| Runtime no Linux | Chromium proprio | WebKitGTK (nativo no Arch) | webview do sistema |
| Maturidade/ecossistema | Muito alto | Alto (v2 estavel) | Baixo |
| Sidecar p/ backend Python | Manual | Suporte nativo (sidecar) | Limitado |
| Reaproveita a UI web | Sim | Sim | Sim |

**Decisao:** comecar **sem framework desktop** (web-first). Se/quando uma janela dedicada fizer sentido, usar **Tauri** — binario pequeno, WebKitGTK nativo no Arch, suporte oficial a sidecar para empacotar o backend Python.

- *Electron descartado:* o peso do Chromium nao compra nada aqui, ja que toda a logica pesada fica no Python; a forca do Electron (integracao Node no processo principal) nao seria usada.
- *Neutralino descartado:* leve, porem ecossistema imaturo e API limitada para orquestrar um processo backend.

## Pipeline RAG

Base: [RAG-Anything](https://github.com/HKUDS/RAG-Anything) (HKUDS), construido sobre o LightRAG, com pipeline multimodal de ponta a ponta.

1. **Ingestao**
   - Scanner do vault com lista de exclusao (`.obsidian/`, `.trash/`, artefatos LaTeX, `zip/log`).
   - PDFs e imagens: parser **MinerU** (extracao de estrutura, OCR; GPU acelera mas nao e obrigatoria).
   - Office (`doc/pptx`): parser **Docling** (requer LibreOffice instalado).
   - `md/tex/txt`: texto direto.
   - **Audio `.opus`: etapa propria** — transcricao com `faster-whisper` gerando um `.md` de transcricao que entra no indice (RAG-Anything nao trata audio nativamente).
2. **Indexacao**
   - LightRAG constroi grafo de conhecimento + embeddings, preservando `file_path` como metadado de cada chunk (insumo das citacoes).
   - Diretorio de trabalho do indice **fora do vault** (ex.: `~/.local/share/obsidian-rag/`), para nao ser sincronizado pelo Dropbox.
   - Indexacao usa LLM para extrair entidades/relacoes: prever modelo barato para ingestao e modelo melhor para resposta.
3. **Sincronizacao**
   - Watcher (inotify via `watchdog`) no diretorio do vault, com debounce (o Dropbox grava arquivos em partes).
   - Reindexacao incremental por hash/mtime; remocao de documentos deletados.
4. **Consulta**
   - Endpoint de chat no FastAPI chama a query do RAG-Anything (modos do LightRAG: local/global/hybrid; consultas VLM quando envolver imagens).

### Modelos (LLM e embeddings)

RAG-Anything usa interface compativel com OpenAI, o que deixa duas rotas (intercambiaveis via configuracao):

- **API em nuvem:** melhor qualidade de resposta e de extracao na ingestao.
- **Local via Ollama:** privacidade total (notas pessoais nao saem da maquina), custo zero por token; qualidade dependente do hardware.

Decisao fica para a fase de implementacao; o codigo deve tratar provider como configuracao, nao como acoplamento.

## Citacao de fontes

Design previsto:

1. A recuperacao retorna chunks com metadado `file_path` (caminho relativo ao vault).
2. O prompt instrui o LLM a marcar afirmacoes com referencias numeradas `[1]`, `[2]` mapeadas para esses caminhos.
3. A resposta da API retorna `{ answer, sources: [{ id, path, excerpt }] }`.
4. A UI renderiza as fontes como links:
   - **Local/desktop:** URI `obsidian://open?vault=v1cferr&file=<path>` abre a nota direto no Obsidian.
   - **Web:** rota que renderiza o Markdown/preview do documento citado.

## Stack resumida

| Camada | Escolha |
| --- | --- |
| Motor RAG | RAG-Anything + LightRAG (Python 3.10+) |
| Parsers | MinerU (PDF/imagem), Docling (Office), faster-whisper (audio) |
| Backend/API | FastAPI + uvicorn; gerenciado com `uv` |
| Frontend | SPA (Vite + React + TypeScript + Tailwind) servida como estatico pelo proprio FastAPI |
| Desktop (opcional) | Tauri v2 com backend como sidecar |
| Watcher | watchdog (inotify) |
| Deploy web (futuro) | VPS + Caddy/Traefik (TLS) + autenticacao em `obsidian.v1cferr.dev` |

## Estrutura planejada do repositorio

```text
obsidian-rag/
  backend/
    app/            # FastAPI: rotas de chat, ingestao, fontes
    rag/            # integracao RAG-Anything, watcher, transcricao de audio
    pyproject.toml
  frontend/         # SPA do chat (Vite + React + TS)
  desktop/          # (futuro) wrapper Tauri
  docs/             # decisoes de arquitetura (ADRs)
```

## Roadmap

- [ ] **Fase 0 — Prova de conceito:** script CLI que indexa um subconjunto do vault com RAG-Anything e responde perguntas com `file_path` das fontes no terminal. Valida qualidade e custo antes de qualquer UI.
- [ ] **Fase 1 — Backend:** FastAPI com endpoints de chat e status de indexacao; ingestao completa do vault; transcricao dos `.opus`; reindexacao incremental com watcher.
- [ ] **Fase 2 — UI de chat:** SPA com streaming da resposta e painel de fontes clicaveis (`obsidian://` no local).
- [ ] **Fase 3 — Desktop (opcional):** wrapper Tauri com sidecar, se a janela dedicada se mostrar util.
- [ ] **Fase 4 — Web:** deploy em `obsidian.v1cferr.dev` com autenticacao; estrategia de sincronizacao do vault no servidor (cliente Dropbox headless ou `rclone`).

## Pre-requisitos (Arch Linux)

```sh
sudo pacman -S --needed python uv nodejs npm libreoffice-fresh ffmpeg
# GPU (opcional, acelera MinerU/Whisper): drivers CUDA/ROCm conforme hardware
```

## Decisoes em aberto

- Provider de LLM/embeddings: API em nuvem vs Ollama local (criterio: privacidade x qualidade x custo de indexar ~324 PDFs).
- Modo de consulta padrao do LightRAG (hybrid parece o melhor ponto de partida).
- Autenticacao na fase web (Authelia, OIDC ou passkey simples).
- Como sincronizar o vault no servidor na fase web.
