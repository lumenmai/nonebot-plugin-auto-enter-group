from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_large_blacklist_upload_uses_shared_cache_directory(tmp_path, mocker):
    import nonebot_plugin_auto_enter_group as plugin

    blacklist = [str(user_id) for user_id in range(61)]
    mocker.patch.object(plugin, "CACHE_PATH", tmp_path)
    mocker.patch.object(plugin, "get_group_blacklist", return_value=blacklist)

    uploaded_path: Path | None = None

    async def call_api(api: str, **data):
        nonlocal uploaded_path
        assert api == "upload_group_file"
        uploaded_path = Path(data["file"])
        assert uploaded_path.parent == tmp_path
        assert uploaded_path.read_text(encoding="utf-8") == ",".join(blacklist)

    bot = mocker.MagicMock()
    bot.call_api = call_api
    event = mocker.MagicMock()
    event.group_id = 123456
    finish = mocker.patch.object(
        plugin.view_blacklist, "finish", new=mocker.AsyncMock()
    )

    await plugin.handle_view_blacklist(bot, event)

    assert uploaded_path is not None
    assert not uploaded_path.exists()
    finish.assert_awaited_once_with(
        "当前群组的退群黑名单共有 61 个用户，已生成文件发送。"
    )
