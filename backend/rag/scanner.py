"""Descoberta de arquivos indexaveis no vault do Obsidian."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

EXCLUDED_DIRS = {".obsidian", ".git", ".trash", ".dropbox.cache", ".smart-env"}

TEXT_EXTS = {".md", ".txt", ".tex"}
DOCUMENT_EXTS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"}
INDEXABLE_EXTS = TEXT_EXTS | DOCUMENT_EXTS | IMAGE_EXTS

# Fora do indice por enquanto: .opus (transcricao entra na Fase 1), .svg
# (vetorial, sem OCR), artefatos de build LaTeX e binarios em geral.

MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024


def iter_vault_files(vault_dir: Path) -> Iterator[Path]:
    for path in sorted(vault_dir.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(vault_dir).parts
        if any(part in EXCLUDED_DIRS for part in relative_parts):
            continue
        if path.suffix.lower() not in INDEXABLE_EXTS:
            continue
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            continue
        yield path


def scan_vault(
    vault_dir: Path,
    extensions: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[Path]:
    """Lista arquivos indexaveis, opcionalmente filtrando por extensao e limite."""
    wanted = (
        {f".{e.lower().lstrip('.')}" for e in extensions} if extensions else None
    )
    files: list[Path] = []
    for path in iter_vault_files(vault_dir):
        if wanted and path.suffix.lower() not in wanted:
            continue
        files.append(path)
        if limit and len(files) >= limit:
            break
    return files


def summarize_by_extension(files: Iterable[Path]) -> Counter[str]:
    return Counter(path.suffix.lower() for path in files)
