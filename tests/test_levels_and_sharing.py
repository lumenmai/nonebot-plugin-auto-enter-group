from types import SimpleNamespace

from nonebot.exception import FinishedException
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("level", "expected_approval"),
    [(None, None), (5, False), (10, None), (16, True)],
)
async def test_level_gate_controls_approval_before_answer_matching(
    mocker, level, expected_approval
):
    import nonebot_plugin_auto_enter_group as plugin

    mocker.patch.object(
        plugin,
        "ensure_group_data",
        return_value={
            "allowed_keywords": ["正确答案"],
            "disallowed_keywords": [],
            "exit_records": {"enabled": False, "members": []},
        },
    )
    mocker.patch.object(
        plugin, "is_auto_enter_group_blocked", new=mocker.AsyncMock(return_value=False)
    )
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
        plugin, "get_user_qq_level", new=mocker.AsyncMock(return_value=level)
    )
    mocker.patch.object(plugin, "get_reject_reason", return_value="等级过低")
    mocker.patch.object(plugin, "send_request_notice", new=mocker.AsyncMock())
    mocker.patch.object(
        plugin.group_request_handler,
        "finish",
        new=mocker.AsyncMock(side_effect=FinishedException),
    )
    bot = SimpleNamespace(self_id="10000", set_group_add_request=mocker.AsyncMock())
    event = SimpleNamespace(
        flag="request-flag", sub_type="add", group_id=123456,
        user_id=654321, comment="正确答案",
    )

    try:
        await plugin.handle_first_receive(bot, event)
    except FinishedException:
        pass

    if expected_approval is None:
        bot.set_group_add_request.assert_not_awaited()
    else:
        bot.set_group_add_request.assert_awaited_once()
        assert bot.set_group_add_request.await_args.kwargs["approve"] is expected_approval


@pytest.mark.asyncio
async def test_host_switch_stops_approval_before_reading_group_data(mocker):
    import nonebot_plugin_auto_enter_group as plugin

    mocker.patch.object(
        plugin, "is_auto_enter_group_blocked", new=mocker.AsyncMock(return_value=True)
    )
    group_data = mocker.patch.object(plugin, "ensure_group_data")
    bot = SimpleNamespace(self_id="10000", set_group_add_request=mocker.AsyncMock())
    event = SimpleNamespace(flag="request-flag", sub_type="add", group_id=123456)

    await plugin.handle_first_receive(bot, event)

    group_data.assert_not_called()
    bot.set_group_add_request.assert_not_awaited()


def test_blacklist_changes_propagate_through_connected_groups(mocker, tmp_path):
    from nonebot_plugin_auto_enter_group import utils

    mocker.patch.object(utils, "data", {"groups": {}})
    mocker.patch.object(utils, "DATA_PATH", tmp_path / "data.json")
    utils.add_to_blacklist("1", "101")
    utils.add_blacklist_share("1", "2")
    utils.add_blacklist_share("2", "3")
    utils.add_to_blacklist("3", "102")
    utils.remove_from_blacklist("1", "101")

    assert [utils.get_group_blacklist(group) for group in ("1", "2", "3")] == [
        ["102"], ["102"], ["102"],
    ]


def test_leaving_share_keeps_members_but_stops_future_propagation(mocker, tmp_path):
    from nonebot_plugin_auto_enter_group import utils

    mocker.patch.object(utils, "data", {"groups": {}})
    mocker.patch.object(utils, "DATA_PATH", tmp_path / "data.json")
    utils.add_to_blacklist("1", "101")
    utils.add_blacklist_share("1", "2")
    utils.leave_blacklist_share_group("2")
    utils.add_to_blacklist("1", "102")

    assert utils.get_group_blacklist("1") == ["101", "102"]
    assert utils.get_group_blacklist("2") == ["101"]
    assert not utils.has_blacklist_share("2")


def test_legacy_settings_survive_schema_upgrade():
    from nonebot_plugin_auto_enter_group import utils

    group_data = {
        "keywords": ["旧答案"],
        "exit_records": {"enabled": True, "members": ["101"]},
        "level_limit": {"enabled": True, "min_level": 16},
    }

    utils.normalize_group_data(group_data)

    assert group_data["allowed_keywords"] == ["旧答案"]
    assert group_data["exit_records"] == {"enabled": True, "members": ["101"]}
    assert group_data["level_limit"]["approve_at_or_above"] == 16
    assert group_data["level_limit"]["enabled"] is True
