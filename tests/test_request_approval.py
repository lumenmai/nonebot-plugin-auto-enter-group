from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException
import pytest


def test_legacy_group_data_gets_default_reject_reasons():
    from nonebot_plugin_auto_enter_group import utils

    group_data = {}

    assert utils.normalize_group_data(group_data)
    assert group_data["reject_reasons"] == {
        "blacklist": "你已在黑名单中，禁止添加本群！",
        "disallowed_keyword": "未正确回答问题",
        "level_too_low": "等级过低",
    }


def test_custom_reject_reason_is_saved(mocker):
    from nonebot_plugin_auto_enter_group import utils

    group_data = utils.build_default_group_data()
    mocker.patch.object(utils, "ensure_group_data", return_value=group_data)
    save_data = mocker.patch.object(utils, "save_data")

    utils.set_reject_reason("123456", "level_too_low", "等级不符合要求")

    assert utils.get_reject_reason("123456", "level_too_low") == "等级不符合要求"
    save_data.assert_called_once_with(utils.data)


@pytest.mark.asyncio
async def test_blacklist_rejection_uses_configured_reason(mocker):
    import nonebot_plugin_auto_enter_group as plugin

    group_data = {
        "allowed_keywords": [],
        "disallowed_keywords": [],
        "exit_records": {"enabled": True, "members": ["654321"]},
    }
    mocker.patch.object(plugin, "ensure_group_data", return_value=group_data)
    mocker.patch.object(
        plugin, "is_auto_enter_group_blocked", new=mocker.AsyncMock(return_value=False)
    )
    get_reject_reason = mocker.patch.object(
        plugin, "get_reject_reason", return_value="自定义黑名单理由"
    )

    bot = mocker.MagicMock()
    bot.self_id = "10000"
    bot.set_group_add_request = mocker.AsyncMock()
    event = mocker.MagicMock()
    event.flag = "request-flag"
    event.sub_type = "add"
    event.group_id = 123456
    event.user_id = 654321
    event.comment = ""

    await plugin.handle_first_receive(bot, event)

    get_reject_reason.assert_called_once_with("123456", "blacklist")
    bot.set_group_add_request.assert_awaited_once_with(
        flag="request-flag",
        sub_type="add",
        approve=False,
        reason="自定义黑名单理由",
    )


@pytest.mark.asyncio
async def test_disallowed_keyword_rejection_uses_configured_reason(mocker):
    import nonebot_plugin_auto_enter_group as plugin

    group_data = {
        "allowed_keywords": ["正确答案"],
        "disallowed_keywords": ["错误答案"],
        "exit_records": {"enabled": False, "members": []},
    }
    mocker.patch.object(plugin, "ensure_group_data", return_value=group_data)
    mocker.patch.object(
        plugin, "is_auto_enter_group_blocked", new=mocker.AsyncMock(return_value=False)
    )
    get_reject_reason = mocker.patch.object(
        plugin, "get_reject_reason", return_value="自定义关键词理由"
    )

    bot = mocker.MagicMock()
    bot.self_id = "10000"
    bot.set_group_add_request = mocker.AsyncMock()
    event = mocker.MagicMock()
    event.flag = "request-flag"
    event.sub_type = "add"
    event.group_id = 123456
    event.user_id = 654321
    event.comment = "错误答案"

    await plugin.handle_first_receive(bot, event)

    get_reject_reason.assert_called_once_with("123456", "disallowed_keyword")
    bot.set_group_add_request.assert_awaited_once_with(
        flag="request-flag",
        sub_type="add",
        approve=False,
        reason="自定义关键词理由",
    )


@pytest.mark.asyncio
async def test_level_rejection_uses_configured_reason(mocker):
    import nonebot_plugin_auto_enter_group as plugin

    group_data = {
        "allowed_keywords": [],
        "disallowed_keywords": [],
        "exit_records": {"enabled": False, "members": []},
    }
    mocker.patch.object(plugin, "ensure_group_data", return_value=group_data)
    mocker.patch.object(
        plugin,
        "get_level_limit_config",
        return_value={
            "enabled": True,
            "reject_at_or_below": 5,
            "approve_at_or_above": 16,
        },
    )
    mocker.patch.object(
        plugin, "get_user_qq_level", new=mocker.AsyncMock(return_value=5)
    )
    mocker.patch.object(
        plugin, "is_auto_enter_group_blocked", new=mocker.AsyncMock(return_value=False)
    )
    get_reject_reason = mocker.patch.object(
        plugin, "get_reject_reason", return_value="自定义等级理由"
    )

    bot = mocker.MagicMock()
    bot.self_id = "10000"
    bot.set_group_add_request = mocker.AsyncMock()
    event = mocker.MagicMock()
    event.flag = "request-flag"
    event.sub_type = "add"
    event.group_id = 123456
    event.user_id = 654321
    event.comment = ""

    await plugin.handle_first_receive(bot, event)

    get_reject_reason.assert_called_once_with("123456", "level_too_low")
    bot.set_group_add_request.assert_awaited_once_with(
        flag="request-flag",
        sub_type="add",
        approve=False,
        reason="自定义等级理由",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "matcher_name", "reason_type"),
    [
        (
            "handle_set_blacklist_reject_reason",
            "set_blacklist_reject_reason_cmd",
            "blacklist",
        ),
        (
            "handle_set_disallowed_keyword_reject_reason",
            "set_disallowed_keyword_reject_reason_cmd",
            "disallowed_keyword",
        ),
        (
            "handle_set_level_too_low_reject_reason",
            "set_level_too_low_reject_reason_cmd",
            "level_too_low",
        ),
    ],
)
async def test_reject_reason_commands_update_current_group(
    mocker, handler_name, matcher_name, reason_type
):
    import nonebot_plugin_auto_enter_group as plugin

    set_reject_reason = mocker.patch.object(plugin, "set_reject_reason")
    matcher = getattr(plugin, matcher_name)
    mocker.patch.object(matcher, "finish", new=mocker.AsyncMock())
    event = mocker.MagicMock()
    event.group_id = 123456

    await getattr(plugin, handler_name)(event, Message("  自定义理由  "))

    set_reject_reason.assert_called_once_with("123456", reason_type, "自定义理由")


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_comment", [None, ""])
async def test_empty_verification_can_be_approved_when_answer_arrives(
    mocker, empty_comment
):
    import nonebot_plugin_auto_enter_group as plugin

    mocker.patch.object(
        plugin,
        "ensure_group_data",
        return_value={
            "allowed_keywords": ["月狐屋"],
            "disallowed_keywords": [],
            "exit_records": {"enabled": False, "members": []},
        },
    )
    mocker.patch.object(
        plugin, "is_auto_enter_group_blocked", new=mocker.AsyncMock(return_value=False)
    )
    mocker.patch.object(
        plugin, "get_level_limit_config", return_value={"enabled": False}
    )
    notice = mocker.patch.object(plugin, "send_request_notice", new=mocker.AsyncMock())
    finish = mocker.patch.object(
        plugin.group_request_handler,
        "finish",
        new=mocker.AsyncMock(side_effect=FinishedException),
    )
    bot = mocker.MagicMock()
    bot.self_id = "10000"
    bot.set_group_add_request = mocker.AsyncMock()
    event = mocker.MagicMock()
    event.flag = "add:123456:requester-uid"
    event.sub_type = "add"
    event.group_id = 123456
    event.user_id = 654321
    event.comment = empty_comment

    with pytest.raises(FinishedException):
        await plugin.handle_first_receive(bot, event)

    bot.set_group_add_request.assert_not_awaited()
    notice.assert_awaited_once_with("654321", "")
    finish.assert_awaited_once_with("未获取到验证信息，请手动处理申请。")

    event.flag = "slreq:1:1794022576884514:123456:2:0"
    event.comment = "问题：AA官号是什么\n答案：B站月狐屋"

    with pytest.raises(FinishedException):
        await plugin.handle_first_receive(bot, event)

    bot.set_group_add_request.assert_awaited_once_with(
        flag=event.flag, sub_type="add", approve=True, reason=" "
    )
    assert finish.await_args.args == ("判断成功，已自动批准入群。",)


@pytest.mark.asyncio
async def test_allowed_keyword_takes_priority_over_disallowed_keyword(mocker):
    import nonebot_plugin_auto_enter_group as plugin

    group_data = {
        "allowed_keywords": ["正确答案"],
        "disallowed_keywords": ["拒绝词"],
        "exit_records": {"enabled": False, "members": []},
    }
    mocker.patch.object(plugin, "ensure_group_data", return_value=group_data)
    mocker.patch.object(
        plugin,
        "get_level_limit_config",
        return_value={
            "enabled": False,
            "reject_at_or_below": None,
            "approve_at_or_above": None,
        },
    )
    mocker.patch.object(
        plugin, "is_auto_enter_group_blocked", new=mocker.AsyncMock(return_value=False)
    )
    mocker.patch.object(plugin, "send_request_notice", new=mocker.AsyncMock())
    finish = mocker.patch.object(
        plugin.group_request_handler,
        "finish",
        new=mocker.AsyncMock(side_effect=FinishedException),
    )

    bot = mocker.MagicMock()
    bot.self_id = "10000"
    bot.set_group_add_request = mocker.AsyncMock()
    event = mocker.MagicMock()
    event.flag = "request-flag"
    event.sub_type = "add"
    event.group_id = 123456
    event.user_id = 654321
    event.comment = "正确答案里同时包含拒绝词"

    with pytest.raises(FinishedException):
        await plugin.handle_first_receive(bot, event)

    bot.set_group_add_request.assert_awaited_once_with(
        flag="request-flag",
        sub_type="add",
        approve=True,
        reason=" ",
    )
    finish.assert_awaited_once_with("判断成功，已自动批准入群。")
