# obsidian-rag

Chatbot pessoal com RAG sobre o vault do Obsidian (sincronizado via Dropbox), com citacao das fontes em cada resposta. Stack 100% local (Ollama + Whisper) rodando no Arch Linux, app desktop em Tauri e, no futuro, deploy web em `obsidian.v1cferr.dev` servido por um servidor on-prem.

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
- Privacidade: nada do vault sai da maquina — LLM, embeddings, visao e transcricao rodam localmente (Ollama/Whisper).
- Futuro: acesso via web em `obsidian.v1cferr.dev` servido por servidor on-prem, com autenticacao (o conteudo e pessoal).
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

1. **Local (agora):** backend roda como servico do usuario (`systemd --user`), UI abre no navegador em `localhost`.
2. **Desktop (decidido: Tauri):** app Tauri empacota a mesma SPA e consome o backend local. Empacotar o Python como *sidecar* fica como opcao futura de distribuicao — PyInstaller com stack de ML (torch/MinerU) gera binarios de varios GB; como o Ollama ja roda como servico separado de qualquer forma, o modelo "servicos locais + UI fina" e mais simples e consistente.
3. **Web (futuro):** mesma dupla no servidor on-prem, atras de proxy reverso com TLS e autenticacao.

### Comparativo dos frameworks desktop

| Criterio | Electron | Tauri | Neutralino |
| --- | --- | --- | --- |
| Tamanho do binario | ~150-250 MB (Chromium embutido) | ~5-15 MB (webview do sistema) | ~2-5 MB |
| Uso de memoria | Alto | Baixo | Baixo |
| Runtime no Linux | Chromium proprio | WebKitGTK (nativo no Arch) | webview do sistema |
| Maturidade/ecossistema | Muito alto | Alto (v2 estavel) | Baixo |
| Sidecar p/ backend Python | Manual | Suporte nativo (sidecar) | Limitado |
| Reaproveita a UI web | Sim | Sim | Sim |

**Decisao: Tauri** — binario pequeno, WebKitGTK nativo no Arch e suporte oficial a sidecar caso um dia o backend precise ser empacotado junto. A UI continua web-first: a mesma SPA roda no navegador, no app Tauri e na web.

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
   - Indexacao usa LLM para extrair entidades/relacoes (uma chamada por chunk): com Ollama, vale um modelo menor/rapido na ingestao e um maior na resposta.
3. **Sincronizacao**
   - Watcher (inotify via `watchdog`) no diretorio do vault, com debounce (o Dropbox grava arquivos em partes).
   - Reindexacao incremental por hash/mtime; remocao de documentos deletados.
   - Granularidade por arquivo: a ingestao completa acontece uma unica vez; depois, arquivo novo entra sozinho no indice, arquivo alterado e re-inserido sozinho (delete + insert daquele documento no LightRAG) e o restante do indice nao e tocado.
   - Dentro de um arquivo alterado, o custo e ~proporcional ao que mudou: chunks sao identificados por hash de conteudo e o cache de LLM do LightRAG faz trechos inalterados custarem quase zero — so o conteudo realmente novo paga extracao.
4. **Consulta**
   - Endpoint de chat no FastAPI chama a query do RAG-Anything (modos do LightRAG: local/global/hybrid; consultas VLM quando envolver imagens).

### Modelos (LLM e embeddings)

**Decisao: local via Ollama**, pelo endpoint compativel com OpenAI (`http://localhost:11434/v1`). Privacidade total e custo zero por token. A URL base fica em configuracao: quando o servidor on-prem existir, migrar = apontar para outra maquina, sem mudar codigo.

Hardware de referencia: **RTX 3050 8 GB** + i5-11400 + 16 GB RAM (Ollama com CUDA ja instalado).

Conjunto inicial (validar qualidade e velocidade na Fase 0):

| Papel | Modelo | Observacao |
| --- | --- | --- |
| Extracao na ingestao | `qwen3:4b` (baixar) | milhares de chamadas com contexto de 32k: modelo pequeno mantem peso + KV cache inteiros na VRAM e acelera a ingestao |
| Chat/resposta | `qwen3.5` (6.6 GB, ja baixado) ou `llama3.1:8b` (ja baixado) | com contexto 32k o qwen3.5 faz offload parcial para RAM; comparar os dois na Fase 0 |
| Embeddings | `bge-m3` (baixar) | multilingue — o `nomic-embed-text` ja baixado e focado em ingles, fraco para vault em PT-BR |
| Visao (imagens/VLM) | `gemma4:e4b-it-qat` (baixar, 6.1 GB) | geracao nova, multimodal (imagem e audio), 4.5B efetivos — cabe nos 8 GB; alternativa mais leve: `gemma4:e2b-it-qat` (4.3 GB) |
| Transcricao de audio | faster-whisper `large-v3` (int8) | fora do Ollama; familia Whisper e o estado da arte em PT-BR. O `gemma4:e4b` tambem aceita audio — vale testar contra o Whisper na Fase 0 |

```sh
ollama pull qwen3:4b && ollama pull bge-m3 && ollama pull gemma4:e4b-it-qat
```

Ajustes no servico do Ollama para caber contexto de 32k em 8 GB — requisito documentado pelo LightRAG: os prompts de extracao estouram o contexto padrao do Ollama e a qualidade do grafo degrada silenciosamente:

```ini
# systemctl edit ollama  ->  [Service]
Environment="OLLAMA_CONTEXT_LENGTH=32768"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=1"
```

Notas de capacidade:

- Um modelo pesado por vez na GPU (o Ollama descarrega/carrega sozinho conforme a chamada); `bge-m3` e pequeno e convive com o modelo de extracao.
- `gemma4:12b` (7.6 GB) e maiores nao servem nesta GPU: so os pesos ja ocupam os 8 GB, sem sobrar nada para o KV cache de 32k, e o offload pesado cai numa RAM ja saturada. Modelos 12b+ ficam para o servidor on-prem.
- Com 16 GB de RAM o sistema ja opera perto do limite no uso diario: rodar a ingestao completa (324 PDFs) em lote/madrugada — MinerU e o offload do Ollama empurram o excedente para ZRAM/swap.
- A Fase 0 mede tokens/s e projeta o tempo total de ingestao a partir de um subconjunto, antes de indexar o vault inteiro.

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
| LLM/embeddings/visao | Ollama local (endpoint OpenAI-compativel); futuramente em servidor on-prem |
| Parsers | MinerU (PDF/imagem), Docling (Office), faster-whisper (audio) |
| Backend/API | FastAPI + uvicorn; gerenciado com `uv` |
| Frontend web | SPA (Vite + React + TypeScript + Tailwind) em `frontend/web` |
| Desktop | Tauri v2 em `frontend/desktop`, empacotando a mesma SPA |
| Monorepo | `uv` (backend) + pnpm workspaces (frontends) |
| Watcher | watchdog (inotify) |
| Deploy web (futuro) | servidor on-prem + Caddy/Traefik (TLS) + autenticacao em `obsidian.v1cferr.dev` |

## Estrutura do monorepo

```text
obsidian-rag/
  backend/              # Python (uv): nucleo RAG + CLI (Fase 0), FastAPI na Fase 1
    rag/                # config, scanner do vault, engine RAG-Anything/Ollama, CLI
    app/                # (Fase 1) rotas FastAPI: chat, ingestao, fontes
    pyproject.toml
  frontend/
    web/                # (Fase 2) SPA do chat (Vite + React + TS + Tailwind)
    desktop/            # (Fase 3) app Tauri v2 (src-tauri/), consome a SPA de web/
  docs/                 # decisoes de arquitetura (ADRs)
  pnpm-workspace.yaml
```

## Roadmap

- [ ] **Fase 0 — Prova de conceito:** script CLI que indexa um subconjunto do vault com RAG-Anything sobre o Ollama e responde perguntas com `file_path` das fontes no terminal. Valida qualidade da extracao, tempo de ingestao e VRAM antes de qualquer UI.
- [ ] **Fase 1 — Backend:** FastAPI com endpoints de chat e status de indexacao; ingestao completa do vault; transcricao dos `.opus`; reindexacao incremental com watcher.
- [ ] **Fase 2 — UI de chat:** SPA em `frontend/web` com streaming da resposta e painel de fontes clicaveis (`obsidian://` no local).
- [ ] **Fase 3 — Desktop:** app Tauri em `frontend/desktop` empacotando a SPA, consumindo o backend rodando como servico local.
- [ ] **Fase 4 — Web:** deploy no servidor on-prem (backend + Ollama) com TLS e autenticacao em `obsidian.v1cferr.dev`; sincronizacao do vault no servidor (cliente Dropbox headless ou `rclone`).

## Pre-requisitos (Arch Linux)

```sh
sudo pacman -S --needed python uv nodejs npm pnpm rustup webkit2gtk-4.1 base-devel ollama libreoffice-fresh ffmpeg
rustup default stable   # toolchain Rust para o Tauri
# GPU: trocar ollama por ollama-cuda ou ollama-rocm conforme hardware (acelera LLM/MinerU/Whisper)
```

## Como rodar (Fase 0 — CLI)

Os comandos `uv run` abaixo rodam de dentro de `backend/` (da raiz do repo,
acrescente `--project backend`). Para indexar o vault inteiro de madrugada,
siga [docs/ingestao-completa.md](docs/ingestao-completa.md).

```sh
# 1. dependencias do backend (uma vez)
cd backend && uv sync

# 2. modelos locais (uma vez)
ollama pull qwen3:4b && ollama pull bge-m3 && ollama pull gemma4:e4b-it-qat

# 3. ver o que seria indexado (nao toca o indice)
uv run obsidian-rag scan

# 4. indexar um subconjunto (ex.: 20 notas Markdown)
uv run obsidian-rag index --ext md --limit 20

# 5. perguntar, com as fontes do vault na saida
uv run obsidian-rag ask "Sobre o que falam minhas notas de novembro de 2024?"

# extras
uv run obsidian-rag status              # configuracao efetiva + estado do indice
uv run obsidian-rag index --dry-run     # lista sem indexar
uv run obsidian-rag ask "..." --show-context
```

Notas de implementacao da Fase 0:

- Defaults: vault em `~/Dropbox/Obsidian/v1cferr`, indice em `~/.local/share/obsidian-rag/`. Tudo sobrescrevivel por variaveis `OBSIDIAN_RAG_*` (ver `backend/.env.example`).
- `.md/.txt/.tex` entram por **insercao direta de texto** no LightRAG. O caminho padrao do RAG-Anything converteria a nota em PDF (ReportLab) para parsear com MinerU — lento, baixa modelos de OCR a toa e perde estrutura. PDF/imagem/Office continuam no MinerU.
- Antes de indexar em volume, aplique o override do servico do Ollama (secao Modelos): sem ele o KV cache de 32k em fp16 nao cabe nos 8 GB — medido na pratica: qwen3:4b ocupando 9.5 GB com split 42/58 CPU/GPU; com o override, 5.3 GB e 100% GPU.
- O qwen3 e modelo hibrido de raciocinio: o engine envia o soft switch `/no_think` (o parametro `think=false` da API nao surte efeito no template) e limpa blocos `<think>` da saida — sem isso a extracao estourava o timeout de 360s por chunk.
- As referencias citam o nome do arquivo (o LightRAG normaliza `file_path` para o basename). Mapear de volta para o caminho completo do vault — necessario para o link `obsidian://` — fica para a API da Fase 1.

## Decisoes tomadas

- UI desktop: **Tauri v2** (Electron e Neutralino descartados — ver comparativo acima).
- Layout: **monorepo** com `backend/`, `frontend/web/` e `frontend/desktop/`.
- Modelos: **Ollama local**; servidor on-prem no futuro reaproveita a mesma configuracao trocando a URL base.
- Transcricao de audio: **familia Whisper** via faster-whisper (melhor suporte a PT-BR entre os ASR locais).
- Conjunto inicial de modelos dimensionado para a **RTX 3050 8 GB** (tabela na secao Modelos).

## Decisoes em aberto

- Fase 0 confirma: modelo de resposta (`qwen3.5` com offload parcial vs `llama3.1:8b` inteiro na VRAM), se o `gemma4:e4b-it-qat` da conta da visao e se ele compete com o Whisper na transcricao em PT-BR.
- Modo de consulta padrao do LightRAG (hybrid parece o melhor ponto de partida).
- Distribuicao do desktop: backend como servico local vs sidecar empacotado (PyInstaller) — so importa se o app for distribuido para terceiros.
- Autenticacao na fase web (Authelia, OIDC ou passkey simples).
- Como sincronizar o vault no servidor on-prem na fase web.
