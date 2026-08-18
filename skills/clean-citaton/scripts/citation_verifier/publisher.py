from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .exporters import export_bibtex, export_markdown
from .io import sha256_file, write_audit, write_hints, write_json
from .models import PaperHint, VerificationResult
from .planner import RunPlan


class OutputPublisher:
    """Publish item-by-item read-only snapshots owned by the Python program."""

    artifact_names = (
        "titles.json",
        "run-plan.json",
        "progress.json",
        "verification.json",
        "references.bib",
        "references.md",
        "manual-review-queue.json",
    )

    def __init__(self, output_dir: str | Path, hints: list[PaperHint], sources: list[str]) -> None:
        self.output_dir = Path(output_dir)
        self.hints = hints
        self.sources = sources
        self.results: dict[int, VerificationResult] = {}
        self.progress = [
            {"index": index + 1, "title": hint.title, "state": "queued", "status": None}
            for index, hint in enumerate(hints)
        ]

    def initialize(self, plan: RunPlan, mode: str = "host-model") -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        write_hints(self.output_dir / "titles.json", self.hints, mode)
        write_json(
            self.output_dir / "run-plan.json",
            {
                "schema_version": "1.0",
                "generated_by": "clean-citaton",
                "read_only": True,
                **plan.to_dict(),
            },
        )
        self._publish_manual_review_queue([])
        self._publish_progress()
        self._publish_manifest()

    def update(self, index: int, total: int, stage: str, result: VerificationResult | None) -> None:
        item = self.progress[index]
        item["state"] = stage
        if result is not None:
            item["status"] = result.status
            item["source"] = result.record.source if result.record else None
            self.results[index] = result
            ordered = [self.results[key] for key in sorted(self.results)]
            write_audit(self.output_dir / "verification.json", ordered, self.sources)
            export_bibtex(self.output_dir / "references.bib", ordered)
            export_markdown(self.output_dir / "references.md", ordered)
            self._publish_manual_review_queue(ordered)
        self._publish_progress()
        self._publish_manifest()

    def finalize(self, results: list[VerificationResult]) -> None:
        self.results = {index: result for index, result in enumerate(results)}
        for index, result in enumerate(results):
            self.progress[index]["state"] = "complete"
            self.progress[index]["status"] = result.status
            self.progress[index]["source"] = result.record.source if result.record else None
        write_audit(self.output_dir / "verification.json", results, self.sources)
        export_bibtex(self.output_dir / "references.bib", results)
        export_markdown(self.output_dir / "references.md", results)
        self._publish_manual_review_queue(results)
        self._publish_progress()
        self._publish_manifest()

    def _publish_progress(self) -> None:
        completed = sum(item["state"] == "complete" for item in self.progress)
        write_json(
            self.output_dir / "progress.json",
            {
                "schema_version": "1.0",
                "generated_by": "clean-citaton",
                "read_only": True,
                "completed": completed,
                "total": len(self.progress),
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "items": self.progress,
            },
        )

    def _publish_manual_review_queue(self, results: list[VerificationResult]) -> None:
        items = []
        for index, result in enumerate(results, 1):
            if result.is_citable:
                continue
            items.append(
                {
                    "index": index,
                    "title": result.hint.title,
                    "authors": result.hint.authors,
                    "year": result.hint.year,
                    "venue": result.hint.venue,
                    "doi": result.hint.doi,
                    "arxiv_id": result.hint.arxiv_id,
                    "official_url": result.hint.official_url,
                    "original_text": result.hint.original_text,
                    "status": result.status,
                    "reason": result.reason,
                    "source_failures": result.source_failures,
                    "required_credential": result.required_credential,
                }
            )
        write_json(
            self.output_dir / "manual-review-queue.json",
            {
                "schema_version": "1.0",
                "generated_by": "clean-citaton",
                "read_only": True,
                "purpose": "Structured queue for high-confidence official-site candidate research and human review.",
                "candidate_output": "../manual-review/candidates.json",
                "count": len(items),
                "items": items,
            },
        )

    def _publish_manifest(self) -> None:
        files = {}
        for name in self.artifact_names:
            path = self.output_dir / name
            if path.exists():
                files[name] = {"sha256": sha256_file(path), "read_only": True}
        write_json(
            self.output_dir / "manifest.json",
            {
                "schema_version": "1.0",
                "generated_by": "clean-citaton",
                "ownership": "program-only",
                "instruction": "Program-owned snapshot; apply changes through input/config and rerun.",
                "files": files,
            },
        )
