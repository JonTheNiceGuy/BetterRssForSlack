import enum


class Template(str, enum.Enum):
    HEADLINE_LINK = "headline_link"
    HEADLINE_LINK_TRUNCATED = "headline_link_truncated"
    HEADLINE_LINK_THREAD = "headline_link_thread"
    HEADLINE_LINK_FULL = "headline_link_full"


class Visibility(str, enum.Enum):
    OWNER_ONLY = "owner_only"
    GROUP = "group"
    PUBLIC = "public"
