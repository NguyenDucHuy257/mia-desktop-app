from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace

# Domain constants are shared by crawlers/services, not values of a specific job.
VALID_DIRECTIONS = ('purchase', 'sold')
VALID_QUERY_TYPES = ('query', 'sco-query')
VALID_CATEGORIES = ('electronic', 'cash_register')

CATEGORY_TO_QUERY_TYPE = {
    'electronic': 'query',
    'cash_register': 'sco-query',
}
QUERY_TYPE_TO_CATEGORY = {
    'query': 'electronic',
    'sco-query': 'cash_register',
}

DEFAULT_CRAWL_PROFILE = 'fast_balanced'
VALID_CRAWL_PROFILES = ('fast_balanced', 'safe', 'aggressive')
RATE_LIMIT_ATTEMPTS = 10
RATE_LIMIT_BACKOFF_MS = (2000, 5000, 10000, 20000, 40000, 60000, 80000, 100000, 120000, 140000)


def validate_direction(direction: str) -> None:
    if direction not in VALID_DIRECTIONS:
        raise ValueError(
            f'direction must be one of {VALID_DIRECTIONS}, got {direction!r}'
        )


def validate_query_type(query_type: str) -> None:
    if query_type not in VALID_QUERY_TYPES:
        raise ValueError(
            f'query_type must be one of {VALID_QUERY_TYPES}, got {query_type!r}'
        )


def validate_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f'category must be one of {VALID_CATEGORIES}, got {category!r}'
        )


def category_to_query_type(category: str) -> str:
    validate_category(category)
    return CATEGORY_TO_QUERY_TYPE[category]


def query_type_to_category(query_type: str) -> str:
    validate_query_type(query_type)
    return QUERY_TYPE_TO_CATEGORY[query_type]


def parse_proxy_list(value: str | None) -> tuple[str, ...]:
    """Parse runtime proxy input without storing it in CrawlConfig."""
    if not value:
        return ()
    return tuple(
        item.strip()
        for item in re.split(r'[,;\r\n]+', value)
        if item.strip()
    )


def mask_secret(value: str, visible: int = 4) -> str:
    """Mask a runtime credential before diagnostic logging."""
    if visible < 0:
        raise ValueError('visible must not be negative')
    if not value:
        return ''
    shown = min(visible, max(0, len(value) - 1))
    return value[:shown] + '*' * max(3, len(value) - shown)


def normalize_limit(value: int | None, field_name: str) -> int | None:
    """Validate a runtime limit supplied by a script/UI/API request."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field_name} must be a positive integer or None')
    return value


def ensure_positive_float(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f'{field_name} must be greater than zero')


def ensure_non_negative_float(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f'{field_name} must not be negative')


def _ensure_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field_name} must be a positive integer')


def _ensure_non_negative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{field_name} must be a non-negative integer')


def _validate_int_range(value: tuple[int, int], field_name: str) -> None:
    if len(value) != 2:
        raise ValueError(f'{field_name} must contain exactly two values')
    minimum, maximum = value
    _ensure_non_negative_int(minimum, f'{field_name}[0]')
    _ensure_non_negative_int(maximum, f'{field_name}[1]')
    if minimum > maximum:
        raise ValueError(f'{field_name}[0] must not exceed {field_name}[1]')


def _validate_positive_backoff(value: tuple[int, ...], field_name: str) -> None:
    if not value:
        raise ValueError(f'{field_name} must not be empty')
    for item in value:
        _ensure_positive_int(item, f'{field_name} item')


@dataclass
class CommonCrawlConfig:
    authentication_attempts: int = 3  # Số lần thử đăng nhập/xác thực tối đa.
    proxy_check_timeout: tuple[float, float] = (5.0, 10.0)  # Timeout connect/read khi kiểm tra proxy.

    def validate(self) -> None:
        _ensure_positive_int(
            self.authentication_attempts, 'common.authentication_attempts'
        )
        if len(self.proxy_check_timeout) != 2:
            raise ValueError('common.proxy_check_timeout must contain two values')
        ensure_positive_float(
            self.proxy_check_timeout[0], 'common.proxy_check_timeout[0]'
        )
        ensure_positive_float(
            self.proxy_check_timeout[1], 'common.proxy_check_timeout[1]'
        )


@dataclass
class OverviewCrawlConfig:
    page_schedule: tuple[tuple[int, float], ...] = (
        (50, 4.0),  # Thử 50 dòng/trang, chờ body tối đa 4 giây.
        (30, 4.0),  # Nếu size 50 chậm/lỗi thì giảm còn 30 dòng/trang.
        (15, 3.0),  # Size nhỏ nhất vẫn cho server 3 giây để tránh fail giả.
    )
    page_size_attempts: dict[int, int] = field(
        default_factory=lambda: {
            50: 2,  # Số lần thử tối đa ở page size 50.
            30: 2,  # Số lần thử tối đa ở page size 30.
        }
    )
    connect_timeout_seconds: float = 2.0  # Timeout mở kết nối TCP/TLS.
    minimum_size_attempts: int = 2  # Số lần thử ở page size nhỏ nhất.
    same_size_delay_step_seconds: float = 0.2  # Mức tăng delay khi retry cùng page size.
    same_size_delay_cap_seconds: float = 0.5  # Trần delay khi retry cùng page size.
    successful_page_delay_seconds: float = 0.3  # Nghỉ giữa hai trang thành công; không nghỉ sau trang cuối.
    rate_limit_attempts: int = RATE_LIMIT_ATTEMPTS  # Tổng số HTTP 429 liên tiếp trước khi báo lỗi.
    rate_limit_base_delay_seconds: float = 1.2  # Fallback cooldown nếu chưa dùng lịch backoff.
    rate_limit_max_delay_seconds: float = 20.0  # Trần cooldown cho một lần 429.
    rate_limit_backoff_ms: tuple[int, ...] = RATE_LIMIT_BACKOFF_MS
    min_start_gap_ms: int = 520  # Khoảng cách tối thiểu giữa lúc bắt đầu hai request list.
    min_idle_gap_ms: int = 350  # Khoảng nghỉ tối thiểu sau khi request list kết thúc.
    jitter_ms: tuple[int, int] = (80, 180)  # Khoảng jitter ngẫu nhiên để tránh request dồn nhịp.
    pause_every_success: int = 25  # 0 tắt periodic pause; >0 nghỉ sau từng số request này.
    pause_ms: tuple[int, int] = (1500, 2500)  # Khoảng nghỉ dài sau mỗi chu kỳ thành công.
    adaptive_throttle_enabled: bool = True  # Phân loại range theo total/page count thực tế.
    page_sleep_enabled: bool = True  # Cho phép nghỉ dài theo số trang của range lớn.
    page_sleep_every_pages: int = 50  # Range heavy nghỉ sau mỗi 50 trang thành công.
    page_sleep_ms: tuple[int, int] = (5000, 10000)  # Cooldown heavy giữa các nhóm trang.
    heavy_range_page_threshold: int = 100  # Số trang ước tính để xếp range heavy.
    heavy_range_row_threshold: int = 5000  # Số dòng để xếp range heavy.
    heavy_range_sleep_after_done_ms: tuple[int, int] = (5000, 10000)  # Nghỉ sau range heavy.
    extreme_range_page_threshold: int = 150  # Số trang ước tính để xếp range extreme.
    extreme_range_row_threshold: int = 7500  # Số dòng để xếp range extreme.
    extreme_page_sleep_every_pages: int = 30  # Range extreme nghỉ sau mỗi 30 trang.
    extreme_page_sleep_ms: tuple[int, int] = (10000, 15000)  # Cooldown theo trang extreme.
    extreme_range_sleep_after_done_ms: tuple[int, int] = (15000, 20000)  # Nghỉ sau range extreme.
    continue_on_incomplete: bool = True  # Lưu partial/report và tiếp tục range khác.
    continue_on_rate_limited: bool = True  # Không làm crash toàn bộ job khi 429 exhausted.
    write_page_audit_report: bool = True  # Ghi TXT khi retry/timeout/mismatch/partial.
    incomplete_report_dir_name: str = 'overview_audit'  # Thư mục con trong reports/.

    def validate(self) -> None:
        if not self.page_schedule:
            raise ValueError('overview.page_schedule must not be empty')
        for page_size, read_timeout in self.page_schedule:
            _ensure_positive_int(page_size, 'overview.page_schedule page size')
            ensure_positive_float(read_timeout, 'overview.page_schedule read timeout')
        if not self.page_size_attempts:
            raise ValueError('overview.page_size_attempts must not be empty')
        for page_size, attempts in self.page_size_attempts.items():
            _ensure_positive_int(page_size, 'overview.page_size_attempts page size')
            _ensure_positive_int(attempts, 'overview.page_size_attempts attempts')
        ensure_positive_float(
            self.connect_timeout_seconds, 'overview.connect_timeout_seconds'
        )
        _ensure_positive_int(
            self.minimum_size_attempts, 'overview.minimum_size_attempts'
        )
        ensure_non_negative_float(
            self.same_size_delay_step_seconds,
            'overview.same_size_delay_step_seconds',
        )
        ensure_non_negative_float(
            self.same_size_delay_cap_seconds,
            'overview.same_size_delay_cap_seconds',
        )
        ensure_non_negative_float(
            self.successful_page_delay_seconds,
            'overview.successful_page_delay_seconds',
        )
        _ensure_positive_int(self.rate_limit_attempts, 'overview.rate_limit_attempts')
        ensure_non_negative_float(
            self.rate_limit_base_delay_seconds,
            'overview.rate_limit_base_delay_seconds',
        )
        ensure_non_negative_float(
            self.rate_limit_max_delay_seconds,
            'overview.rate_limit_max_delay_seconds',
        )
        _validate_positive_backoff(
            self.rate_limit_backoff_ms, 'overview.rate_limit_backoff_ms'
        )
        _ensure_non_negative_int(self.min_start_gap_ms, 'overview.min_start_gap_ms')
        _ensure_non_negative_int(self.min_idle_gap_ms, 'overview.min_idle_gap_ms')
        _validate_int_range(self.jitter_ms, 'overview.jitter_ms')
        _ensure_non_negative_int(
            self.pause_every_success, 'overview.pause_every_success'
        )
        _validate_int_range(self.pause_ms, 'overview.pause_ms')
        for field_name in (
            'adaptive_throttle_enabled',
            'page_sleep_enabled',
            'continue_on_incomplete',
            'continue_on_rate_limited',
            'write_page_audit_report',
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f'overview.{field_name} must be a bool')
        for field_name in (
            'page_sleep_every_pages',
            'heavy_range_page_threshold',
            'heavy_range_row_threshold',
            'extreme_range_page_threshold',
            'extreme_range_row_threshold',
            'extreme_page_sleep_every_pages',
        ):
            _ensure_positive_int(getattr(self, field_name), f'overview.{field_name}')
        for field_name in (
            'page_sleep_ms',
            'heavy_range_sleep_after_done_ms',
            'extreme_page_sleep_ms',
            'extreme_range_sleep_after_done_ms',
        ):
            _validate_int_range(getattr(self, field_name), f'overview.{field_name}')
        if self.extreme_range_page_threshold < self.heavy_range_page_threshold:
            raise ValueError(
                'overview.extreme_range_page_threshold must be at least the heavy threshold'
            )
        if self.extreme_range_row_threshold < self.heavy_range_row_threshold:
            raise ValueError(
                'overview.extreme_range_row_threshold must be at least the heavy threshold'
            )
        if not self.incomplete_report_dir_name.strip():
            raise ValueError('overview.incomplete_report_dir_name must not be empty')
        if any(part in self.incomplete_report_dir_name for part in ('/', '\\')):
            raise ValueError('overview.incomplete_report_dir_name must be a directory name')


@dataclass
class DetailCrawlConfig:
    connect_timeout_seconds: float = 2.0  # Timeout mở kết nối cho mỗi lần gọi detail.
    read_timeout_seconds: float = 8.0  # Timeout chờ JSON detail trả về.
    retry_attempts: int = 5  # Tổng số attempt, gồm lần đầu và các retry.
    rate_limit_attempts: int = RATE_LIMIT_ATTEMPTS
    retry_backoff_seconds: float = 1.5  # Delay khởi đầu cho retry lỗi thông thường.
    retry_backoff_max_seconds: float = 8.0  # Trần exponential backoff lỗi thông thường.
    rate_limit_delay_step_seconds: float = 3.0  # Fallback cooldown tuyến tính khi gặp HTTP 429.
    rate_limit_max_delay_seconds: float = 40.0  # Trần cooldown 429.
    rate_limit_backoff_ms: tuple[int, ...] = RATE_LIMIT_BACKOFF_MS
    delay_between_items_seconds: float = 0.5  # Nghỉ giữa hai hóa đơn detail; 0 = gọi ngay.
    min_start_gap_ms: int = 350  # Khoảng cách request tối thiểu cho RateLimiter sau này.
    min_idle_gap_ms: int = 250  # Khoảng nghỉ tối thiểu khi endpoint detail rảnh.
    jitter_ms: tuple[int, int] = (100, 250)  # Jitter ngẫu nhiên giữa các request detail.

    def validate(self) -> None:
        ensure_positive_float(
            self.connect_timeout_seconds, 'detail.connect_timeout_seconds'
        )
        ensure_positive_float(self.read_timeout_seconds, 'detail.read_timeout_seconds')
        _ensure_positive_int(self.retry_attempts, 'detail.retry_attempts')
        _ensure_positive_int(
            self.rate_limit_attempts, 'detail.rate_limit_attempts'
        )
        ensure_non_negative_float(
            self.retry_backoff_seconds, 'detail.retry_backoff_seconds'
        )
        ensure_non_negative_float(
            self.retry_backoff_max_seconds, 'detail.retry_backoff_max_seconds'
        )
        if self.retry_backoff_seconds > self.retry_backoff_max_seconds:
            raise ValueError(
                'detail.retry_backoff_seconds must not exceed retry_backoff_max_seconds'
            )
        ensure_non_negative_float(
            self.rate_limit_delay_step_seconds,
            'detail.rate_limit_delay_step_seconds',
        )
        ensure_non_negative_float(
            self.rate_limit_max_delay_seconds,
            'detail.rate_limit_max_delay_seconds',
        )
        _validate_positive_backoff(
            self.rate_limit_backoff_ms, 'detail.rate_limit_backoff_ms'
        )
        ensure_non_negative_float(
            self.delay_between_items_seconds,
            'detail.delay_between_items_seconds',
        )
        _ensure_non_negative_int(self.min_start_gap_ms, 'detail.min_start_gap_ms')
        _ensure_non_negative_int(self.min_idle_gap_ms, 'detail.min_idle_gap_ms')
        _validate_int_range(self.jitter_ms, 'detail.jitter_ms')


@dataclass
class PackageCrawlConfig:
    connect_timeout_seconds: float = 2.0  # Timeout mở kết nối tải ZIP XML/HTML.
    read_timeout_seconds: float = 20.0  # Timeout chờ toàn bộ ZIP tải xong.
    retry_attempts: int = 2  # Số lần thử tải một package trước khi chuyển item khác.
    rate_limit_attempts: int = RATE_LIMIT_ATTEMPTS
    delay_between_items_seconds: float = 0.8  # Nghỉ giữa hai package; 0 = tải tiếp ngay.
    min_start_gap_ms: int = 800  # Khoảng cách request package tối thiểu cho RateLimiter sau này.
    min_idle_gap_ms: int = 500  # Khoảng nghỉ tối thiểu khi endpoint package rảnh.
    jitter_ms: tuple[int, int] = (150, 350)  # Jitter ngẫu nhiên giữa request package.
    rate_limit_backoff_ms: tuple[int, ...] = RATE_LIMIT_BACKOFF_MS

    def validate(self) -> None:
        ensure_positive_float(
            self.connect_timeout_seconds, 'package.connect_timeout_seconds'
        )
        ensure_positive_float(self.read_timeout_seconds, 'package.read_timeout_seconds')
        _ensure_positive_int(self.retry_attempts, 'package.retry_attempts')
        _ensure_positive_int(
            self.rate_limit_attempts, 'package.rate_limit_attempts'
        )
        ensure_non_negative_float(
            self.delay_between_items_seconds,
            'package.delay_between_items_seconds',
        )
        _ensure_non_negative_int(self.min_start_gap_ms, 'package.min_start_gap_ms')
        _ensure_non_negative_int(self.min_idle_gap_ms, 'package.min_idle_gap_ms')
        _validate_int_range(self.jitter_ms, 'package.jitter_ms')
        _validate_positive_backoff(
            self.rate_limit_backoff_ms, 'package.rate_limit_backoff_ms'
        )


@dataclass
class CrawlConfig:
    """Technical crawl tuning only; contains no account, job, or output data."""

    profile: str = DEFAULT_CRAWL_PROFILE
    common: CommonCrawlConfig = field(default_factory=CommonCrawlConfig)
    overview: OverviewCrawlConfig = field(default_factory=OverviewCrawlConfig)
    detail: DetailCrawlConfig = field(default_factory=DetailCrawlConfig)
    package: PackageCrawlConfig = field(default_factory=PackageCrawlConfig)

    def validate(self) -> None:
        if self.profile not in VALID_CRAWL_PROFILES:
            raise ValueError(
                f'profile must be one of {VALID_CRAWL_PROFILES}, got {self.profile!r}'
            )
        self.common.validate()
        self.overview.validate()
        self.detail.validate()
        self.package.validate()

    @classmethod
    def default(cls) -> CrawlConfig:
        config = apply_crawl_profile(cls(), DEFAULT_CRAWL_PROFILE)
        config.validate()
        return config

    def with_profile(self, profile: str) -> CrawlConfig:
        """Return a validated profile copy without mutating this config."""
        return apply_crawl_profile(self, profile)

    @classmethod
    def from_env(cls) -> CrawlConfig:
        """Load only the non-secret tuning profile from the environment."""
        profile = os.getenv('GDT_CRAWL_PROFILE', DEFAULT_CRAWL_PROFILE).strip()
        return apply_crawl_profile(cls(), profile or DEFAULT_CRAWL_PROFILE)


def apply_crawl_profile(config: CrawlConfig, profile: str) -> CrawlConfig:
    """Return a new config tuned for the selected workload profile."""
    if profile not in VALID_CRAWL_PROFILES:
        raise ValueError(
            f'profile must be one of {VALID_CRAWL_PROFILES}, got {profile!r}'
        )

    if profile == 'fast_balanced':
        # Default profile: high throughput with a buffer above the HAR 429 threshold.
        overview = replace(
            config.overview,
            min_start_gap_ms=520,
            min_idle_gap_ms=350,
            jitter_ms=(80, 180),
            pause_every_success=25,
            pause_ms=(2500, 4500),
        )
        detail = replace(
            config.detail,
            min_start_gap_ms=350,
            min_idle_gap_ms=250,
            jitter_ms=(50, 150),
            delay_between_items_seconds=0.35,
        )
        package = replace(
            config.package,
            min_start_gap_ms=800,
            min_idle_gap_ms=500,
            jitter_ms=(150, 350),
            delay_between_items_seconds=0.8,
        )
    elif profile == 'safe':
        # Use for long jobs, large taxpayers, or observed 429 rates above about 2%.
        overview = replace(
            config.overview,
            min_start_gap_ms=700,
            min_idle_gap_ms=450,
            jitter_ms=(120, 300),
            pause_every_success=20,
            pause_ms=(3000, 6000),
        )
        detail = replace(
            config.detail,
            min_start_gap_ms=500,
            min_idle_gap_ms=350,
            jitter_ms=(100, 250),
            delay_between_items_seconds=0.5,
        )
        package = replace(
            config.package,
            min_start_gap_ms=1200,
            min_idle_gap_ms=800,
            jitter_ms=(250, 600),
            delay_between_items_seconds=1.2,
        )
    else:
        # Aggressive has a higher 429 risk and is intended only for short tests.
        overview = replace(
            config.overview,
            min_start_gap_ms=400,
            min_idle_gap_ms=250,
            jitter_ms=(50, 120),
            pause_every_success=30,
            pause_ms=(1500, 3000),
        )
        detail = replace(
            config.detail,
            min_start_gap_ms=250,
            min_idle_gap_ms=150,
            jitter_ms=(30, 100),
            delay_between_items_seconds=0.25,
        )
        package = replace(
            config.package,
            min_start_gap_ms=650,
            min_idle_gap_ms=400,
            jitter_ms=(100, 250),
            delay_between_items_seconds=0.65,
        )

    overview = replace(
        overview, page_size_attempts=dict(overview.page_size_attempts)
    )
    result = replace(
        config,
        profile=profile,
        common=replace(config.common),
        overview=overview,
        detail=detail,
        package=package,
    )
    result.validate()
    return result
