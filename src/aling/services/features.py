from aling.core.config import Settings
from aling.core.permissions import PermissionContext, require_group_admin
from aling.core.storage import FEATURES, GroupSettingsStore


class FeatureDisabledError(ValueError):
    """Raised when a group tries to enable a deployment-disabled capability."""


class FeatureService:
    def __init__(self, settings: Settings, store: GroupSettingsStore) -> None:
        self.settings = settings
        self.store = store
        self._global_overrides: dict[str, bool] = {}

    async def initialize(self) -> None:
        self._global_overrides = await self.store.get_global_feature_settings()

    def globally_enabled(self, feature: str) -> bool:
        if feature not in FEATURES:
            raise ValueError("Unknown feature")
        configured = {
            "ai": self.settings.ai_enabled,
            "web": self.settings.web_enabled,
            "meme": self.settings.meme_enabled,
            "memory": self.settings.ai_enabled and self.settings.ai_memory_enabled,
        }[feature]
        return (
            configured
            and self._global_overrides.get(feature, True)
            and (feature != "memory" or self.globally_enabled("ai"))
        )

    async def enabled(self, group_id: int, feature: str) -> bool:
        if not self.settings.group_allowed(group_id):
            return False
        enabled = await self.store.is_feature_enabled(
            group_id,
            feature,
            global_enabled=self.globally_enabled(feature),
            default_enabled=False,
        )
        return enabled and (feature != "memory" or await self.enabled(group_id, "ai"))

    async def available(self, group_id: int) -> set[str]:
        return {feature for feature in FEATURES if await self.enabled(group_id, feature)}

    async def set(self, context: PermissionContext, feature: str, enabled: bool) -> None:
        if feature not in FEATURES:
            raise ValueError("Unknown feature")
        require_group_admin(context, self.settings.superusers, self.settings.allowed_groups)
        if enabled and not self.globally_enabled(feature):
            raise FeatureDisabledError("Feature disabled by deployment configuration")
        await self.store.set_feature_enabled(context.group_id, feature, enabled)

    async def set_global(self, user_id: int, feature: str, enabled: bool) -> None:
        if user_id not in self.settings.superusers:
            raise PermissionError("Only configured superusers can change global features")
        if feature not in FEATURES:
            raise ValueError("Unknown feature")
        if type(enabled) is not bool:
            raise ValueError("Feature state must be boolean")
        configured = {
            "ai": self.settings.ai_enabled,
            "web": self.settings.web_enabled,
            "meme": self.settings.meme_enabled,
            "memory": self.settings.ai_enabled and self.settings.ai_memory_enabled,
        }[feature]
        if enabled and not configured:
            raise FeatureDisabledError("Feature disabled by deployment configuration")
        await self.store.set_global_feature_enabled(feature, enabled)
        self._global_overrides[feature] = enabled
