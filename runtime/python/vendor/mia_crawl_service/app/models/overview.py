from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.config.crawl_config import validate_category, validate_direction


class OverviewCheckpointError(RuntimeError):
    """Durable overview state cannot be safely written or resumed."""

    code = 'overview_checkpoint_error'
    retryable = True


@dataclass(frozen=True)
class OverviewDownloadRequest:
    """Shared application input for the existing overview pipeline."""

    begin_date: date
    end_date: date
    output_dir: Path | str
    directions: tuple[str, ...] = ('purchase', 'sold')
    categories: tuple[str, ...] = ('electronic', 'cash_register')
    overwrite: bool = False
    combined_workbook_name: str | None = None

    def __post_init__(self) -> None:
        if self.begin_date > self.end_date:
            raise ValueError('begin_date must not be after end_date')
        if not self.directions:
            raise ValueError('directions must not be empty')
        if not self.categories:
            raise ValueError('categories must not be empty')
        for direction in self.directions:
            validate_direction(direction)
        for category in self.categories:
            validate_category(category)
        if self.combined_workbook_name is not None:
            name = self.combined_workbook_name.strip()
            if not name or Path(name).name != name:
                raise ValueError(
                    'combined_workbook_name must be a file name without directories'
                )


@dataclass(frozen=True)
class OverviewPageCommit:
    """One validated portal page ready for durable persistence."""

    page_number: int
    page_size: int
    records: tuple[Any, ...]
    total: int | None
    next_state: str | None
    fetched_count: int
    checkpoint_status: str


@dataclass(frozen=True)
class OverviewResumeState:
    """Durable cursor and records needed to continue after a process restart."""

    records: tuple[Any, ...]
    next_state: str | None
    page_number: int
    first_page_total: int | None
    last_page_size: int
    seen_states: tuple[str, ...] = ()
    completed: bool = False
    checkpoint_status: str = 'in_progress'
    fetched_count: int | None = None
