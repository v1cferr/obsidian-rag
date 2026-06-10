# Ingestao completa do vault (rodando de madrugada)

Guia para indexar o vault inteiro deixando o processo rodando sem supervisao
(noite/dia inteiro), com acompanhamento de progresso e retomada segura.

## O que vai acontecer

A ingestao roda em estagios por tipo de arquivo, do mais barato ao mais caro,
para evitar troca constante de modelos na GPU (8 GB comportam um modelo
pesado por vez):

1. **Texto** (`md`, `txt`, `tex`): insercao direta no LightRAG + extracao de
   entidades com o qwen3:4b. Notas longas levam alguns minutos cada.
2. **PDFs** (324 arquivos): parsing com MinerU (no primeiro PDF ele baixa os
   modelos de layout/OCR — alguns GB, precisa de rede) + extracao. E o estagio
   mais demorado; pode atravessar mais de uma noite.
3. **Imagens** (`png`, `jpg`, ...): captioning com o modelo de visao
   (gemma4:e4b-it-qat) + extracao.
4. **Passada final sem filtro**: pega o que sobrou (`pptx`, `doc` — requerem
   LibreOffice instalado) e confirma que nada ficou para tras (arquivos ja
   indexados sao pulados por hash, custo ~zero).

Interromper no meio **nao perde trabalho**: documentos concluidos ficam no
indice, o parse de PDFs fica em cache e chamadas de LLM repetidas batem no
cache. Rodar o comando de novo continua de onde parou.

## Antes de comecar

- Confirme o override do Ollama ativo (flash attention + KV q8_0):

  ```sh
  systemctl show ollama --property=Environment | tr ' ' '\n' | grep OLLAMA
  ```

- Feche aplicativos pesados: a maquina tem 16 GB de RAM e a ingestao vai
  empurrar o excedente para ZRAM/swap.
- Espaco em disco: modelos do MinerU (~alguns GB) + saida de parsing em
  `~/.local/share/obsidian-rag/parser-output/`.

## Comando para deixar rodando

```sh
cd ~/Projects/GitHub/obsidian-rag/backend

nohup systemd-inhibit --what=sleep:idle --why="ingestao obsidian-rag" sh -c '
  uv run obsidian-rag index --ext md,txt,tex
  uv run obsidian-rag index --ext pdf
  uv run obsidian-rag index --ext png,jpg,jpeg,webp,bmp,gif,tiff
  uv run obsidian-rag index
' > ~/obsidian-rag-ingestao.log 2>&1 &

echo "PID: $!"   # anote para poder parar com kill
```

- `nohup ... &` desacopla do terminal: pode fechar a janela que continua.
- `systemd-inhibit --what=sleep:idle` impede o hypridle/systemd de suspender a
  maquina enquanto a ingestao roda (o monitor pode desligar normalmente).

## Acompanhar o progresso

```sh
tail -f ~/obsidian-rag-ingestao.log          # por arquivo: [N/M] caminho ... ok em Xs
uv run obsidian-rag status                   # docs por estado (processed/failed/...)
ollama ps                                    # modelo carregado e uso de GPU
nvidia-smi                                   # VRAM/utilizacao
```

Cada estagio termina com um resumo `Indexados N/M arquivos em X min` e a lista
de falhas, se houver.

## Parar e retomar

```sh
kill <PID>                                   # ou: pkill -f "obsidian-rag index"
```

Para retomar, rode o mesmo comando de novo — arquivos ja processados sao
pulados (dedup por hash de conteudo), entao a retomada e barata.

## Problemas conhecidos

- **CUDA out of memory durante PDFs**: o MinerU disputa a GPU com o Ollama.
  Se PDFs comecarem a falhar com OOM, force o parsing em CPU (mais lento,
  porem estavel) e re-rode o estagio:

  ```sh
  export MINERU_DEVICE_MODE=cpu
  uv run obsidian-rag index --ext pdf
  ```

- **`doc`/`pptx` falhando**: instale o LibreOffice
  (`sudo pacman -S libreoffice-fresh`) e re-rode a passada final.
- **Falhas pontuais em arquivos**: aparecem no resumo do estagio e nao
  interrompem o resto; `uv run obsidian-rag status` mostra o total `failed`.
  Re-rodar tenta de novo apenas os que falharam/mudaram.
- **Audio `.opus`**: fora desta ingestao por enquanto — entra na Fase 1 com a
  transcricao via faster-whisper.
