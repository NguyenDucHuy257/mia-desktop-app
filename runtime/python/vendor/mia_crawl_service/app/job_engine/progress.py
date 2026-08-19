from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from app.utils.date_utils import split_by_calendar_month


FOUR_PLACES = Decimal('0.0001')
HUNDRED = Decimal('100.0000')
AUTH_WEIGHT = Decimal('5.0000')
EXECUTION_WEIGHT = Decimal('90.0000')
FINALIZE_WEIGHT = Decimal('5.0000')
MODULE_NAMES = ('overview', 'detail', 'ensure_xml', 'mvt')


def decimal4(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def bounded_fraction(value: Decimal | int | float | str) -> Decimal:
    return min(HUNDRED, max(Decimal('0.0000'), decimal4(value)))


def enabled_modules(
    result_scope: str, include_mvt: bool, include_xml: bool = False,
) -> tuple[str, ...]:
    modules = ['overview']
    if result_scope == 'detail':
        modules.append('detail')
    if include_xml or include_mvt:
        modules.append('ensure_xml')
    if include_mvt:
        modules.append('mvt')
    return tuple(modules)


def build_pipeline_plan(
    result_scope: str,
    include_mvt: bool,
    include_xml: bool = False,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    modules = enabled_modules(result_scope, include_mvt, include_xml)
    module_weight = decimal4(EXECUTION_WEIGHT / Decimal(len(modules)))
    # Preserve an exact total of 100 even if a future module count does not
    # divide 90 evenly at four decimal places.
    weights = [module_weight for _ in modules]
    weights[-1] += EXECUTION_WEIGHT - sum(weights, Decimal('0'))
    stages = [('auth', AUTH_WEIGHT), *zip(modules, weights), ('finalize', FINALIZE_WEIGHT)]
    months = []
    if date_from is not None and date_to is not None:
        months = [
            {
                'key': f'{begin.year:04d}-{begin.month:02d}',
                'index': index,
                'from_date': begin.isoformat(),
                'to_date': end.isoformat(),
            }
            for index, (begin, end) in enumerate(
                split_by_calendar_month(date_from, date_to), start=1
            )
        ]
    return {
        'version': 3,
        'stages': [
            {'name': name, 'weight': float(decimal4(weight))}
            for name, weight in stages
        ],
        'modules': list(modules),
        'months': months,
    }


def _month_state(month: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'key': month['key'], 'index': int(month['index']),
        'from_date': month['from_date'], 'to_date': month['to_date'],
        'status': 'pending', 'planned': None, 'processed': 0,
        'percent': '0.0000', 'baseline_processed': 0,
    }


def initial_progress_state(pipeline_plan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    plan = dict(pipeline_plan) if pipeline_plan else None
    if plan and int(plan.get('version', 0)) >= 3:
        modules = [str(name) for name in plan.get('modules', [])]
        months = list(plan.get('months', []))
        return {
            'version': 3,
            'pipeline_plan': plan,
            'current_stage': None,
            'current_month': None,
            'auth_percent': '0.0000',
            'finalize_percent': '0.0000',
            'modules': {
                name: {
                    'status': 'pending', 'completed_months': 0,
                    'total_months': len(months),
                    'months': [_month_state(month) for month in months],
                }
                for name in modules
            },
            'last_progress_at': None,
            'message': 'Đang chờ worker xử lý',
        }

    # Read compatibility for pipeline-v2 jobs created before this release.
    active = ['auth', 'overview', 'detail', 'finalize']
    return {
        'version': 2 if plan else 1,
        'pipeline_plan': plan,
        'current_stage': None,
        'current_unit': None,
        'stage_results': {
            name: {'status': 'pending', 'progress_percent': '0.0000'}
            for name in active
        },
        'message': 'Đang chờ worker xử lý',
    }


def stage_weight(plan: Mapping[str, Any], stage: str) -> Decimal:
    for item in plan.get('stages', []):
        if item.get('name') == stage:
            return Decimal(str(item['weight']))
    return Decimal('0')


def month_percent(processed: int, planned: int | None, *, complete=False) -> Decimal:
    if complete:
        return HUNDRED
    if planned is None or planned <= 0:
        return Decimal('0.0000')
    return min(Decimal('99.9999'), decimal4(
        Decimal(processed) * HUNDRED / Decimal(planned)
    ))


def module_percent(module: Mapping[str, Any]) -> Decimal:
    total = int(module.get('total_months', 0))
    if total <= 0:
        return HUNDRED if module.get('status') == 'completed' else Decimal('0')
    completed = int(module.get('completed_months', 0))
    current = next((
        month for month in module.get('months', [])
        if month.get('status') == 'running'
    ), None)
    current_fraction = (
        bounded_fraction(current.get('percent', 0)) / HUNDRED if current else Decimal('0')
    )
    value = (Decimal(completed) + current_fraction) / Decimal(total) * HUNDRED
    if module.get('status') == 'running':
        value = min(Decimal('99.9999'), value)
    return bounded_fraction(value)


def overall_progress_from_state(state: Mapping[str, Any]) -> Decimal:
    plan = state.get('pipeline_plan') or {}
    if int(state.get('version', 0)) < 3:
        total = Decimal('0')
        stages = plan.get('stages', []) or [
            {'name': name, 'weight': weight}
            for name, weight in (
                ('auth', 10), ('overview', 40), ('detail', 48), ('finalize', 2)
            )
        ]
        for item in stages:
            fraction = bounded_fraction(
                state.get('stage_results', {}).get(
                    item.get('name'), {}
                ).get('progress_percent', 0)
            )
            total += Decimal(str(item.get('weight', 0))) * fraction / HUNDRED
        return min(HUNDRED, decimal4(total))

    total = AUTH_WEIGHT * bounded_fraction(state.get('auth_percent', 0)) / HUNDRED
    for name, module in state.get('modules', {}).items():
        total += stage_weight(plan, name) * module_percent(module) / HUNDRED
    total += (
        FINALIZE_WEIGHT * bounded_fraction(state.get('finalize_percent', 0))
        / HUNDRED
    )
    result = min(HUNDRED, decimal4(total))
    if state.get('current_stage') is not None:
        result = min(Decimal('99.9999'), result)
    return result


def current_month_public(state: Mapping[str, Any]) -> dict[str, Any] | None:
    if state.get('current_stage') not in MODULE_NAMES:
        return None
    month = state.get('current_month')
    if not isinstance(month, Mapping):
        return None
    return {
        'key': str(month['key']), 'index': int(month['index']),
        'total': int(month['total']), 'processed': int(month['processed']),
        'planned': int(month['planned'] or 0),
        'percent': float(bounded_fraction(month.get('percent', 0))),
    }


def validate_progress_transition(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> None:
    if (
        not previous or int(previous.get('version', 0)) < 3
        or int(current.get('version', 0)) < 3
    ):
        return
    if previous.get('pipeline_plan') != current.get('pipeline_plan'):
        raise ValueError('frozen pipeline progress plan cannot change')
    previous_modules = previous.get('modules', {})
    for name, module in current.get('modules', {}).items():
        old_module = previous_modules.get(name, {})
        if int(module.get('completed_months', 0)) < int(
            old_module.get('completed_months', 0)
        ):
            raise ValueError('completed_months cannot decrease')
        old_months = old_module.get('months', [])
        for index, month in enumerate(module.get('months', [])):
            if index >= len(old_months):
                continue
            old = old_months[index]
            if old.get('planned') is not None and month.get('planned') != old.get('planned'):
                raise ValueError('frozen monthly denominator cannot change')
            if int(month.get('processed', 0)) < int(old.get('processed', 0)):
                raise ValueError('monthly processed count cannot decrease')
            if bounded_fraction(month.get('percent', 0)) < bounded_fraction(
                old.get('percent', 0)
            ):
                raise ValueError('monthly progress cannot decrease')


@dataclass(frozen=True)
class ProgressSnapshot:
    overall_percent: Decimal
    stage_percent: Decimal
    state: dict[str, Any]

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> 'ProgressSnapshot':
        if int(state.get('version', 0)) >= 3:
            stage = state.get('current_stage')
            if stage == 'auth':
                stage_percent = bounded_fraction(state.get('auth_percent', 0))
            elif stage == 'finalize':
                stage_percent = bounded_fraction(state.get('finalize_percent', 0))
            else:
                stage_percent = module_percent(
                    state.get('modules', {}).get(stage, {})
                )
            return cls(overall_progress_from_state(state), stage_percent, state)

        stage = state.get('current_stage')
        stage_percent = bounded_fraction(
            state.get('stage_results', {}).get(stage, {}).get('progress_percent', 0)
        ) if stage else Decimal('0')
        return cls(overall_progress_from_state(state), stage_percent, state)
