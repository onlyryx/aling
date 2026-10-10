from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionContext:
    """Construct only from authenticated OneBot events, never from model output."""

    user_id: int
    group_id: int | None
    role: str


def require_group_admin(
    context: PermissionContext, superusers: set[int], allowed_groups: set[int]
) -> None:
    if context.group_id is None or (allowed_groups and context.group_id not in allowed_groups):
        raise PermissionError("Group is outside the configured scope")
    if context.user_id not in superusers and context.role not in {"owner", "admin"}:
        raise PermissionError("Group administrator permission required")
