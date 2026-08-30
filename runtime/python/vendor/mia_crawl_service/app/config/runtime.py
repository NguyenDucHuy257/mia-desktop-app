from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class RuntimeMode(str, Enum):
    SERVER = 'server'
    LOCAL = 'local'


class DataRetentionMode(str, Enum):
    NORMALIZED_ONLY = 'normalized_only'
    RAW_ARTIFACTS = 'raw_artifacts'


def _parse_bool(value: str | None, *, name: str, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f'{name} must be a boolean')


def _parse_enum(enum_type, value: str | None, *, name: str, default):
    normalized = value.strip().casefold() if value and value.strip() else default.value
    try:
        return enum_type(normalized)
    except ValueError as error:
        allowed = ', '.join(item.value for item in enum_type)
        raise ValueError(f'{name} must be one of: {allowed}') from error


@dataclass(frozen=True)
class RuntimeCapabilities:
    mode: RuntimeMode
    data_retention: DataRetentionMode
    enable_excel_export: bool
    retain_overview_records: bool

    @property
    def retain_raw_artifacts(self) -> bool:
        return self.data_retention is DataRetentionMode.RAW_ARTIFACTS

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None,
    ) -> 'RuntimeCapabilities':
        env = os.environ if environment is None else environment
        mode = _parse_enum(
            RuntimeMode, env.get('MIA_RUNTIME_MODE'),
            name='MIA_RUNTIME_MODE', default=RuntimeMode.LOCAL,
        )
        server = mode is RuntimeMode.SERVER
        retention = _parse_enum(
            DataRetentionMode, env.get('MIA_DATA_RETENTION_MODE'),
            name='MIA_DATA_RETENTION_MODE',
            default=(
                DataRetentionMode.NORMALIZED_ONLY if server
                else DataRetentionMode.RAW_ARTIFACTS
            ),
        )
        excel = _parse_bool(
            env.get('MIA_ENABLE_EXCEL_EXPORT'), name='MIA_ENABLE_EXCEL_EXPORT',
            default=not server,
        )
        retain_records = _parse_bool(
            env.get('MIA_RETAIN_OVERVIEW_RECORDS'),
            name='MIA_RETAIN_OVERVIEW_RECORDS',
            default=not server,
        )
        if retention is DataRetentionMode.NORMALIZED_ONLY and retain_records:
            raise ValueError(
                'MIA_RETAIN_OVERVIEW_RECORDS must be false in normalized_only mode'
            )
        if excel and (
            retention is not DataRetentionMode.RAW_ARTIFACTS or not retain_records
        ):
            raise ValueError(
                'MIA_ENABLE_EXCEL_EXPORT requires raw_artifacts retention and '
                'MIA_RETAIN_OVERVIEW_RECORDS=true'
            )
        return cls(mode, retention, excel, retain_records)
