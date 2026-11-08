"""Load the plugin with real NoneBot and isolated host/storage interfaces.

The host stubs model only the Zhenxun interfaces used by this plugin. They
avoid loading a running bot or reading/writing its database during tests.
"""

from enum import IntEnum
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Adapter
import pytest


@pytest.fixture(scope="session", autouse=True)
def init_plugin(tmp_path_factory):
    storage = tmp_path_factory.mktemp("plugin-storage")
    nonebot.init(
        driver="~fastapi",
        superusers={"10000"},
        command_start={""},
        localstore_data_dir=storage / "data",
        localstore_cache_dir=storage / "cache",
        localstore_config_dir=storage / "config",
    )
    nonebot.get_driver().register_adapter(Adapter)

    names = [
        "zhenxun",
        "zhenxun.models",
        "zhenxun.models.bot_console",
        "zhenxun.models.group_console",
        "zhenxun.models.plugin_info",
        "zhenxun.utils",
        "zhenxun.utils.common_utils",
        "zhenxun.utils.enum",
    ]
    previous = {name: sys.modules.get(name) for name in names}
    for name in names:
        module = ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module

    class BotConsole:
        get_or_none = AsyncMock(return_value=None)

    class GroupConsole:
        get_group = AsyncMock(return_value=None)

    class PluginInfo:
        get_plugin = AsyncMock(return_value=None)

    class CommonUtils:
        @staticmethod
        def format(name):
            return f"<{name},"

    class BlockType(IntEnum):
        ALL = 0
        GROUP = 1

    sys.modules["zhenxun.models.bot_console"].BotConsole = BotConsole
    sys.modules["zhenxun.models.group_console"].GroupConsole = GroupConsole
    sys.modules["zhenxun.models.plugin_info"].PluginInfo = PluginInfo
    sys.modules["zhenxun.utils.common_utils"].CommonUtils = CommonUtils
    sys.modules["zhenxun.utils.enum"].BlockType = BlockType

    plugin = nonebot.load_plugin("nonebot_plugin_auto_enter_group")
    assert plugin is not None, "Plugin failed to load"
    yield plugin

    for name, module in previous.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
