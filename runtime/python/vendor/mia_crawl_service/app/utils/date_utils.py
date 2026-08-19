from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)

BUSINESS_TIMEZONE = ZoneInfo('Asia/Ho_Chi_Minh')


def normalize_business_date(value: Any) -> str | None:
    """Return the Vietnam business date without mutating the source timestamp.

    Aware timestamps are converted to the business timezone. Naive timestamps
    deliberately retain their calendar date because their source timezone is
    unknown. Invalid values fail softly and are logged without their contents.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value.isoformat()
    else:
        text = str(value).strip()
        if not text:
            return None
        for date_format in ('%Y-%m-%d', '%d/%m/%Y'):
            try:
                return datetime.strptime(text, date_format).date().isoformat()
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(
                text[:-1] + '+00:00' if text.endswith(('Z', 'z')) else text
            )
        except ValueError:
            logger.warning(
                'Could not normalize invoice business date value_type=%s length=%d',
                type(value).__name__, len(text),
            )
            return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        parsed = parsed.astimezone(BUSINESS_TIMEZONE)
    return parsed.date().isoformat()


def split_by_calendar_month(
    begin_date: date,
    end_date: date,
) -> list[tuple[date, date]]:
    """Split one inclusive date range into inclusive calendar-month chunks."""
    if begin_date > end_date:
        raise ValueError('begin_date must not be after end_date')

    ranges: list[tuple[date, date]] = []
    current = begin_date
    while current <= end_date:
        month_end = date(
            current.year,
            current.month,
            calendar.monthrange(current.year, current.month)[1],
        )
        chunk_end = min(month_end, end_date)
        ranges.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return ranges
