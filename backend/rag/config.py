"""Configuracao central do backend.

Tudo e sobrescrevivel por variavel de ambiente OBSIDIAN_RAG_* (ou .env no
diretorio de trabalho), com defaults pensados para a maquina de referencia
(Arch + RTX 3050 8 GB + Ollama local).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


@dataclass(frozen=True)
class Settings:
    vault_dir: Path
    working_dir: Path
    parser_output_dir: Path
    ollama_host: str
    llm_model: str
    embed_model: str
    embed_dim: int
    vision_model: str
    num_ctx: int

    @property
    def vault_name(self) -> str:
        return self.vault_dir.name


def load_settings() -> Settings:
    working_dir = _env_path(
        "OBSIDIAN_RAG_WORKING_DIR", "~/.local/share/obsidian-rag/index"
    )
    return Settings(
        vault_dir=_env_path("OBSIDIAN_RAG_VAULT", "~/Dropbox/Obsidian/v1cferr"),
        working_dir=working_dir,
        parser_output_dir=_env_path(
            "OBSIDIAN_RAG_PARSER_OUTPUT", str(working_dir.parent / "parser-output")
        ),
        ollama_host=os.environ.get(
            "OBSIDIAN_RAG_OLLAMA_HOST", "http://localhost:11434"
        ),
        llm_model=os.environ.get("OBSIDIAN_RAG_LLM_MODEL", "qwen3:4b"),
        embed_model=os.environ.get("OBSIDIAN_RAG_EMBED_MODEL", "bge-m3"),
        embed_dim=int(os.environ.get("OBSIDIAN_RAG_EMBED_DIM", "1024")),
        vision_model=os.environ.get("OBSIDIAN_RAG_VISION_MODEL", "gemma4:e4b-it-qat"),
        num_ctx=int(os.environ.get("OBSIDIAN_RAG_NUM_CTX", "32768")),
    )
