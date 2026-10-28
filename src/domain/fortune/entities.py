"""Fortune domain entities and value objects.

Extracts domain logic from FortuneCore into proper DDD entities.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from .detail_dto import FortuneDetail

# Default weights array (corresponding to 0-7 stars)
DEFAULT_WEIGHTS = [0.1, 0.15, 0.2, 0.25, 0.15, 0.12, 0.07, 0.005]

# Default fortune titles (corresponding to stars 0-7)
DEFAULT_TITLES = ["凶", "末吉", "末小吉", "小吉", "中吉", "吉", "大吉", "超大吉"]

# Default fortune descriptions (corresponding to stars 0-7)
DEFAULT_MESSAGES = [
    "长夜再暗，火种仍在，转机终会到来。",
    "微光不灭，步步向前，黎明就在眼前。",
    "心怀希冀，顺流而行，好事悄然靠近。",
    "逆境翻篇，机遇迎面，惊喜不期而至。",
    "小吉随身，难题化易，幸运与你并肩。",
    "吉星高照，所行皆坦，所愿皆如愿。",
    "福泽深厚，大吉加身，一路花开有声。",
    "七星同耀，奇迹频现，今日万事皆成。",
]


@dataclass
class FortuneConfig:
    """Fortune feature configuration.

    Attributes:
        enabled: Whether fortune feature is enabled
        api_type: API type for fortune images
        default_tags: Default tags for fortune images
        content_mode: Default content mode
        allow_user_refresh: Whether users can refresh their own fortune
        auto_refresh: Whether to auto-refresh daily
    """

    enabled: bool = True
    api_type: str = "inherit"
    default_tags: str = ""
    content_mode: str = "sfw"
    allow_user_refresh: bool = False
    auto_refresh: bool = True


@dataclass(frozen=True, slots=True)
class FortuneRecord:
    """Entity representing a user's fortune record.

    Attributes:
        user_id: User identifier
        username: User display name
        date_str: Date string in ISO format
        title: Fortune title (e.g., "大吉")
        star_count: Star rating (0-7)
        description: Fortune description
        extra_message: Extra message (usually empty)
        theme_color: Theme color class
        image_cached: Whether image is cached
        img_url: URL of background image
        last_view_date: Last date this fortune was viewed
        group_id: Group ID where fortune was generated
    """

    user_id: str
    username: str
    date_str: str
    title: str
    star_count: int
    description: str
    extra_message: str
    theme_color: str
    image_cached: bool
    img_url: str | None
    last_view_date: str
    group_id: str | None = None
    nonce: int = 0
    detail: FortuneDetail | None = None

    @property
    def max_stars(self) -> int:
        """Maximum possible stars."""
        return 7

    @property
    def is_expired(self) -> bool:
        """Check if fortune is from a previous day."""
        try:
            record_date = date.fromisoformat(self.date_str)
            today = date.today()
            return record_date < today
        except ValueError:
            return False

    def with_last_view_date(self, last_view_date: str) -> FortuneRecord:
        """Return new record with updated last_view_date.

        Args:
            last_view_date: New last view date string

        Returns:
            New FortuneRecord with updated last_view_date
        """
        return replace(self, last_view_date=last_view_date)

    def with_refreshed_data(
        self, title: str, star_count: int, description: str, theme_color: str
    ) -> FortuneRecord:
        """Return new record with refreshed fortune data.

        Args:
            title: New title
            star_count: New star count
            description: New description
            theme_color: New theme color

        Returns:
            New FortuneRecord with updated data
        """
        return replace(
            self,
            title=title,
            star_count=star_count,
            description=description,
            theme_color=theme_color,
            image_cached=False,
            img_url=None,
        )

    @classmethod
    def create_new(
        cls,
        *,
        user_id: str,
        username: str,
        date_str: str,
        title: str,
        star_count: int,
        description: str,
        extra_message: str,
        theme_color: str,
        group_id: str | None = None,
        nonce: int = 0,
        detail: FortuneDetail | None = None,
    ) -> FortuneRecord:
        """Create a new fortune record with defaults for image fields."""
        return cls(
            user_id=user_id,
            username=username,
            date_str=date_str,
            title=title,
            star_count=star_count,
            description=description,
            extra_message=extra_message,
            theme_color=theme_color,
            image_cached=False,
            img_url=None,
            last_view_date=date_str,
            group_id=group_id,
            nonce=nonce,
            detail=detail,
        )

    def with_image_cache(self, img_url: str | None) -> FortuneRecord:
        """Return new record with image cache marked as cached.

        Args:
            img_url: Image URL

        Returns:
            New FortuneRecord with image_cached=True
        """
        return replace(self, image_cached=True, img_url=img_url)

    def with_presentation(
        self,
        *,
        username: str,
        group_id: str | None,
        invalidate_image_cache: bool = False,
    ) -> FortuneRecord:
        """Return new record with current display fields."""
        changes: dict[str, Any] = {"username": username, "group_id": group_id}
        if invalidate_image_cache:
            changes["image_cached"] = False
            changes["img_url"] = None
        return replace(self, **changes)

    def with_detail(self, detail: FortuneDetail | None, nonce: int) -> FortuneRecord:
        """Return new record carrying freshly derived detail and nonce."""
        return replace(self, detail=detail, nonce=nonce)


@dataclass(frozen=True, slots=True)
class FortuneWeights:
    """Value object for fortune calculation weights.

    Attributes:
        weights: Weight array for each star level (0-7)
    """

    weights: tuple[float, ...] = tuple(DEFAULT_WEIGHTS)

    def calculate_star(self, rng: Any | None = None) -> int:
        """Calculate fortune star based on weights.

        Args:
            rng: Seeded ``random.Random``.  When omitted a module-level RNG is
                used, which keeps the old behaviour for ad-hoc callers while the
                fortune service always passes a deterministic one.

        Returns:
            Star rating (0-7)
        """
        import random

        picker = rng if rng is not None else random
        total_weight = sum(self.weights)
        random_value = picker.random() * total_weight
        current_weight = 0.0

        for i, w in enumerate(self.weights):
            current_weight += w
            if random_value <= current_weight:
                return i
        return 0

    @classmethod
    def default(cls) -> FortuneWeights:
        """Get default weights."""
        return cls()


@dataclass(frozen=True, slots=True)
class FortuneTheme:
    """Value object for fortune theme configuration.

    Attributes:
        titles: Title array for each star level
        messages: Message array for each star level
        extra_message: Default extra message
    """

    titles: tuple[str, ...] = tuple(DEFAULT_TITLES)
    messages: tuple[str, ...] = tuple(DEFAULT_MESSAGES)
    extra_message: str = ""

    def get_title(self, star_count: int) -> str:
        """Get title for star count.

        Args:
            star_count: Star rating

        Returns:
            Title string
        """
        idx = min(star_count, len(self.titles) - 1)
        return self.titles[idx]

    def get_message(self, star_count: int) -> str:
        """Get message for star count.

        Args:
            star_count: Star rating

        Returns:
            Message string
        """
        idx = min(star_count, len(self.messages) - 1)
        return self.messages[idx]

    def get_theme_color(self, star_count: int) -> str:
        """Get theme color class for star count.

        Args:
            star_count: Star rating

        Returns:
            Theme color class name
        """
        if star_count in (7, 6):
            return "theme-red"
        elif star_count in (5, 4):
            return "theme-gold"
        elif star_count in (1, 0):
            return "theme-gray"
        else:
            return "theme-blue"

    @classmethod
    def default(cls) -> FortuneTheme:
        """Get default theme."""
        return cls()


@dataclass
class FortuneGenerationRequest:
    """Value object representing a fortune generation request.

    Attributes:
        user_id: User identifier
        username: User display name
        date_str: Date string (ISO format)
        group_id: Optional group ID
    """

    user_id: str
    username: str
    date_str: str
    group_id: str | None = None

    @classmethod
    def for_today(
        cls, user_id: str, username: str, group_id: str | None = None
    ) -> FortuneGenerationRequest:
        """Create request for today's fortune.

        Args:
            user_id: User identifier
            username: User display name
            group_id: Optional group ID

        Returns:
            New FortuneGenerationRequest
        """
        today_str = date.today().isoformat()
        return cls(user_id, username, today_str, group_id)
