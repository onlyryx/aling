from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionContext:
    """Construct from authenticated OneBot events or a server-authenticated deployer.

    WebUI uses its configured superuser identity with the member role; browser
    input and model output must never supply user IDs or administrator roles.
    """

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
