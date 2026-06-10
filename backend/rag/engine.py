"""Monta o RAGAnything ligado ao Ollama local (LLM, embeddings e visao)."""

from __future__ import annotations

import re
from functools import partial
from pathlib import Path
from typing import Any, cast

from lightrag import QueryParam
from lightrag.llm.ollama import _ollama_model_if_cache, ollama_embed
from lightrag.utils import EmbeddingFunc
from raganything import RAGAnything, RAGAnythingConfig

from rag.config import Settings

# Contexto menor para captioning de imagens: nao precisa dos 32k da extracao
# e economiza VRAM quando o modelo de visao sobe junto com o de texto.
VISION_NUM_CTX = 8192


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str) -> str:
    """Remove o raciocinio de modelos "thinking" da resposta.

    O parametro think=False da API do Ollama nao surte efeito no template do
    qwen3 (o raciocinio vaza no content, as vezes sem a tag de abertura), por
    isso o soft switch /no_think no prompt + esta limpeza defensiva.
    """
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    return _THINK_BLOCK.sub("", text).strip()


def build_rag(settings: Settings) -> RAGAnything:
    settings.working_dir.mkdir(parents=True, exist_ok=True)
    settings.parser_output_dir.mkdir(parents=True, exist_ok=True)

    # qwen3 e hibrido: sem o soft switch ele "pensa" paragrafos antes de cada
    # resposta — estourava o timeout de 360s por chunk na extracao.
    qwen_hybrid = "qwen3" in settings.llm_model.lower()

    async def llm_model_func(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list | None = None,
        **kwargs: Any,
    ) -> Any:
        if qwen_hybrid:
            prompt = f"{prompt}\n/no_think"
        result = await _ollama_model_if_cache(
            settings.llm_model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            host=settings.ollama_host,
            options={"num_ctx": settings.num_ctx},
            **kwargs,
        )
        return _strip_think(result) if isinstance(result, str) else result

    async def vision_model_func(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list | None = None,
        image_data: str | list[str] | None = None,
        messages: list[dict] | None = None,
        **kwargs: Any,
    ) -> Any:
        # O RAG-Anything chama de tres formas: prompt + image_data (caption de
        # imagem), apenas prompt (fallback texto) e messages estilo OpenAI
        # (consulta VLM). Normalizamos tudo para a API nativa do Ollama.
        images: list[str] = []
        if messages:
            system_prompt, prompt, images = _split_openai_messages(messages)
        elif image_data:
            images = [image_data] if isinstance(image_data, str) else list(image_data)
        result = await _ollama_model_if_cache(
            settings.vision_model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            image_inputs=images or None,
            host=settings.ollama_host,
            options={"num_ctx": VISION_NUM_CTX},
            **kwargs,
        )
        return _strip_think(result) if isinstance(result, str) else result

    embedding_func = EmbeddingFunc(
        embedding_dim=settings.embed_dim,
        max_token_size=8192,
        func=partial(
            ollama_embed.func,
            embed_model=settings.embed_model,
            host=settings.ollama_host,
        ),
    )

    config = RAGAnythingConfig(
        working_dir=str(settings.working_dir),
        parser_output_dir=str(settings.parser_output_dir),
        parser="mineru",
        parse_method="auto",
        max_concurrent_files=1,
    )

    return RAGAnything(
        config=config,
        llm_model_func=llm_model_func,
        vision_model_func=vision_model_func,
        embedding_func=embedding_func,
    )


async def ensure_ready(rag: RAGAnything) -> None:
    """Inicializa o LightRAG interno (necessario antes de aquery sem insert)."""
    await rag._ensure_lightrag_initialized()


async def shutdown(rag: RAGAnything) -> None:
    """Persiste e fecha os storages do LightRAG.

    Obrigatorio antes de encerrar o event loop: sem isso, upserts de vetores
    pendentes sao descartados no atexit ("Failed to finalize chunks_vdb") e o
    indice fica sem os embeddings dos chunks.
    """
    if rag.lightrag is not None:
        await rag.lightrag.finalize_storages()


async def index_text_file(rag: RAGAnything, path: Path, relative: Path) -> str:
    """Insere um arquivo de texto direto no LightRAG, sem parser de documento.

    O caminho padrao do RAG-Anything converte .md/.txt em PDF (ReportLab) para
    entao parsear com MinerU — lento, exige modelos de layout/OCR e perde a
    estrutura original. Notas ja sao texto: entram inteiras, com o caminho
    relativo ao vault como file_path (insumo das citacoes).

    Retorna o status final do documento ("processed", "failed", "vazio", ...).
    O pipeline do LightRAG nao lanca excecao quando a extracao falha — marca o
    doc como failed no doc_status — entao o status precisa ser consultado.
    """
    if rag.lightrag is None:
        raise RuntimeError("LightRAG nao inicializado; chame ensure_ready() antes")
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        return "vazio"
    track_id = await rag.lightrag.ainsert(content, file_paths=str(relative))
    docs = await rag.lightrag.aget_docs_by_track_id(track_id)
    statuses = {
        getattr(doc.status, "value", str(doc.status)) for doc in docs.values()
    }
    if not statuses:
        return "processed"  # conteudo identico ja indexado (dedup por hash)
    if statuses == {"processed"}:
        return "processed"
    return ",".join(sorted(statuses))


def _split_openai_messages(
    messages: list[dict],
) -> tuple[str | None, str, list[str]]:
    """Converte messages estilo OpenAI em (system, prompt, imagens base64)."""
    system_prompt: str | None = None
    prompt_parts: list[str] = []
    images: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str):
                system_prompt = content
            continue
        if isinstance(content, str):
            prompt_parts.append(content)
            continue
        for part in content or []:
            kind = part.get("type")
            if kind == "text":
                prompt_parts.append(part.get("text", ""))
            elif kind == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                # aceita tanto data URL quanto base64 puro
                images.append(url.split("base64,", 1)[-1])
    return system_prompt, "\n".join(p for p in prompt_parts if p), images


async def query_with_sources(
    rag: RAGAnything, question: str, mode: str, vlm: bool = False
) -> tuple[str, list[dict], list[dict]]:
    """Pergunta ao indice e retorna (resposta, referencias, chunks).

    Usa lightrag.aquery_llm, que faz a recuperacao e a geracao numa passada so
    e devolve as referencias estruturadas ([{reference_id, file_path}]) — os
    marcadores [n] da resposta apontam para esses reference_id.

    Com vlm=True a resposta sai pelo caminho VLM do RAG-Anything (imagens do
    contexto vao para o modelo de visao) e as referencias vem de uma segunda
    recuperacao via aquery_data.
    """
    if rag.lightrag is None:
        raise RuntimeError("LightRAG nao inicializado; chame ensure_ready() antes")
    valid_modes = ("local", "global", "hybrid", "naive", "mix", "bypass")
    if mode not in valid_modes:
        raise ValueError(f"modo invalido: {mode!r}; use um de {valid_modes}")
    param = QueryParam(mode=cast(Any, mode), enable_rerank=False)
    if vlm:
        answer = await rag.aquery(question, mode=mode, vlm_enhanced=True)
        result = await rag.lightrag.aquery_data(question, param=param)
    else:
        result = await rag.lightrag.aquery_llm(question, param=param)
        answer = (result.get("llm_response") or {}).get("content", "")
    data = result.get("data") or {}
    return answer, data.get("references") or [], data.get("chunks") or []
