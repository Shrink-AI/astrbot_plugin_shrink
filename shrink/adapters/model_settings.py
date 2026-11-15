"""Model selections live in the host profile used by the conversation."""
import copy

from ..config import HOST_MODELS_MIGRATION


class ModelSettings:
    def __init__(self, context, state):
        self.context, self.state = context, state
        self.persist = None

    def configurations(self):
        return self.context.astrbot_config_mgr.confs

    def config(self, profile="default"):
        config = self.configurations().get(profile)
        if config is None:
            raise ValueError("这份 AstrBot 配置已不存在，请重新选择")
        return config

    def profiles(self):
        names = self.context.astrbot_config_mgr.abconf_data or {}
        return [{"id": identity, "name": "默认配置" if identity == "default"
                 else names.get(identity, {}).get("name", identity),
                 "editable": config.get("agent_runner", {}).get("runner_type") == "local"}
                for identity, config in self.configurations().items()]

    @staticmethod
    def values(config):
        runner = config.get("agent_runner", {}).get("config", {})
        model = runner.get("model", {})
        return {
            "model_provider": model.get("provider_id", ""),
            "fallback_providers": list(model.get("fallback_provider_ids", [])),
            "compression_provider": runner.get("compression", {}).get("provider_id", ""),
            "image_caption_provider": config.get("provider_settings", {}).get(
                "default_image_caption_provider_id", ""),
        }

    def read(self, profile="default"):
        return self.values(self.config(profile))

    def for_session(self, session=""):
        return self.values(self.context.get_config(umo=session or None))

    async def save(self, settings, profile="default"):
        config = self.config(profile)
        values = {key: getattr(settings, key) for key in self.read(profile)}
        if config.get("agent_runner", {}).get("runner_type") != "local":
            if values != self.read(profile):
                raise ValueError("这份配置使用外部对话运行器，请在 AstrBot 后台管理模型")
            return
        if values == self.read(profile):
            return
        updated = copy.deepcopy(dict(config))
        runner = copy.deepcopy(config["agent_runner"])
        runner_config = runner.setdefault("config", {})
        model = runner_config.setdefault("model", {})
        model["provider_id"] = settings.model_provider
        model["fallback_provider_ids"] = list(settings.fallback_providers)
        runner_config.setdefault("compression", {})["provider_id"] = settings.compression_provider
        provider_settings = copy.deepcopy(config.get("provider_settings", {}))
        provider_settings["default_image_caption_provider_id"] = settings.image_caption_provider
        updated["agent_runner"] = runner
        updated["provider_settings"] = provider_settings
        if self.persist:
            await self.persist(profile, updated)
        else:
            original = copy.deepcopy(dict(config))
            config.update(updated)
            try:
                config.save_config()
            except Exception:
                config.clear()
                config.update(original)
                raise

    def migrate(self):
        if not self.state.get(HOST_MODELS_MIGRATION):
            legacy = self.state.get("settings", {}).get("model_provider", "")
            if legacy:
                # Before this upgrade Shrink overrode the host model in every session.
                # Transfer that actual selection to each profile once, then remove it.
                for config in self.configurations().values():
                    if config.get("agent_runner", {}).get("runner_type") != "local":
                        continue
                    config["agent_runner"]["config"].setdefault("model", {})["provider_id"] = legacy
                    config.save_config()
            with self.state.database.transaction():
                self.state.put(HOST_MODELS_MIGRATION, True)
                self.state.put("settings", self.state.get("settings"))
        self.state.settings_overlay = self.read
