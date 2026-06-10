"""CLI da Fase 0: indexa o vault (ou um subconjunto) e pergunta com fontes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from rag import scanner
from rag.config import Settings, load_settings

cli = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Chatbot RAG sobre o vault do Obsidian (Fase 0: prova de conceito).",
)
console = Console()


def _parse_exts(ext: str | None) -> list[str] | None:
    return [e.strip() for e in ext.split(",") if e.strip()] if ext else None


def _configure_logging(verbose: bool) -> None:
    """Por padrao esconde o INFO do pipeline; --verbose mostra o progresso
    interno (chunking, extracao por chunk, embeddings)."""
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    for name in ("lightrag", "raganything"):
        logging.getLogger(name).setLevel(level)


@cli.command()
def scan(
    ext: str | None = typer.Option(None, help="Filtra extensoes, ex.: md,pdf"),
    limit: int | None = typer.Option(None, help="Para apos N arquivos"),
) -> None:
    """Lista o que seria indexado, agrupado por extensao (nao toca o indice)."""
    settings = load_settings()
    files = scanner.scan_vault(settings.vault_dir, _parse_exts(ext), limit)
    table = Table(title=f"Indexaveis em {settings.vault_dir}")
    table.add_column("Extensao")
    table.add_column("Arquivos", justify="right")
    for suffix, count in scanner.summarize_by_extension(files).most_common():
        table.add_row(suffix, str(count))
    console.print(table)
    console.print(f"Total: [bold]{len(files)}[/bold] arquivos")


@cli.command()
def index(
    ext: str | None = typer.Option(None, help="Filtra extensoes, ex.: md,pdf"),
    limit: int | None = typer.Option(None, help="Indexa no maximo N arquivos"),
    dry_run: bool = typer.Option(False, help="So lista o que seria indexado"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Mostra o progresso interno do pipeline"
    ),
) -> None:
    """Indexa arquivos do vault no indice local (incremental por documento)."""
    _configure_logging(verbose)
    settings = load_settings()
    files = scanner.scan_vault(settings.vault_dir, _parse_exts(ext), limit)
    if not files:
        console.print("[yellow]Nenhum arquivo encontrado com esses filtros.[/yellow]")
        raise typer.Exit(1)
    if dry_run:
        for path in files:
            console.print(str(path.relative_to(settings.vault_dir)))
        console.print(f"Total: {len(files)} arquivos (dry-run, nada indexado)")
        return
    asyncio.run(_index_files(settings, files))


async def _index_files(settings: Settings, files: list[Path]) -> None:
    from rag.engine import build_rag, ensure_ready, index_text_file, shutdown

    rag = build_rag(settings)
    await ensure_ready(rag)

    failures: list[tuple[str, str]] = []
    started = time.perf_counter()
    try:
        for position, path in enumerate(files, start=1):
            relative = path.relative_to(settings.vault_dir)
            console.print(f"[cyan][{position}/{len(files)}][/cyan] {relative}")
            file_started = time.perf_counter()
            try:
                if path.suffix.lower() in scanner.TEXT_EXTS:
                    status = await index_text_file(rag, path, relative)
                    ok = status in ("processed", "vazio")
                    outcome = (
                        f"ok ({status})" if ok else f"[red]extracao: {status}[/red]"
                    )
                    if not ok:
                        failures.append((str(relative), f"status={status}"))
                else:
                    await rag.process_document_complete(
                        str(path), file_name=str(relative)
                    )
                    outcome = "ok"
                elapsed = time.perf_counter() - file_started
                console.print(f"    {outcome} em {elapsed:.1f}s")
            except Exception as error:  # PoC: segue para o proximo arquivo
                failures.append((str(relative), str(error)))
                console.print(f"    [red]FALHOU:[/red] {error}")
    finally:
        await shutdown(rag)

    total = time.perf_counter() - started
    console.print(
        f"\nIndexados [bold]{len(files) - len(failures)}/{len(files)}[/bold] "
        f"arquivos em {total / 60:.1f} min"
    )
    for name, error in failures:
        console.print(f"[red]falha[/red] {name}: {error}")


@cli.command()
def ask(
    question: str = typer.Argument(..., help="Pergunta em linguagem natural"),
    mode: str = typer.Option("hybrid", help="local | global | hybrid | naive | mix"),
    vlm: bool = typer.Option(
        False, help="Consulta VLM (carrega o modelo de visao; mais lenta)"
    ),
    show_context: bool = typer.Option(False, help="Mostra o contexto recuperado"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Mostra o progresso interno do pipeline"
    ),
) -> None:
    """Pergunta ao indice e mostra a resposta com as fontes do vault."""
    _configure_logging(verbose)
    settings = load_settings()
    answer, references, chunks = asyncio.run(_ask(settings, question, mode, vlm))
    console.print(Panel(answer or "(resposta vazia)", title="Resposta"))
    if references:
        console.print("[bold]Fontes:[/bold]")
        for ref in references:
            ref_id = ref.get("reference_id", "?")
            console.print(f"  [{ref_id}] {ref.get('file_path', '?')}")
    else:
        console.print("[yellow]Nenhuma fonte identificada no contexto.[/yellow]")
    if show_context:
        for chunk in chunks[:10]:
            excerpt = (chunk.get("content") or "")[:300]
            console.print(Panel(excerpt, title=chunk.get("file_path", "?")))


async def _ask(
    settings: Settings, question: str, mode: str, vlm: bool
) -> tuple[str, list[dict], list[dict]]:
    from rag.engine import build_rag, ensure_ready, query_with_sources, shutdown

    rag = build_rag(settings)
    await ensure_ready(rag)
    try:
        return await query_with_sources(rag, question, mode, vlm=vlm)
    finally:
        await shutdown(rag)


@cli.command()
def status() -> None:
    """Mostra a configuracao efetiva e o estado do indice."""
    settings = load_settings()
    table = Table(title="obsidian-rag: configuracao")
    table.add_column("Chave")
    table.add_column("Valor")
    table.add_row("Vault", str(settings.vault_dir))
    table.add_row("Indice", str(settings.working_dir))
    table.add_row("Ollama", settings.ollama_host)
    table.add_row("LLM (extracao/resposta)", settings.llm_model)
    table.add_row("Embeddings", f"{settings.embed_model} ({settings.embed_dim} dims)")
    table.add_row("Visao", settings.vision_model)
    table.add_row("num_ctx", str(settings.num_ctx))
    console.print(table)

    doc_status_file = settings.working_dir / "kv_store_doc_status.json"
    if doc_status_file.exists():
        documents = json.loads(doc_status_file.read_text())
        by_status = Counter(
            entry.get("status", "?") for entry in documents.values()
        )
        summary = ", ".join(f"{status}: {count}" for status, count in by_status.items())
        console.print(f"Documentos no indice: [bold]{len(documents)}[/bold] ({summary})")
    else:
        console.print("Indice ainda vazio (nada indexado).")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
