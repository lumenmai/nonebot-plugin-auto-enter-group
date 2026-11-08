def parse_level_value(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def get_user_qq_level(bot: "Bot", user_id: str) -> int | None:
    try:
        user_info = await bot.get_stranger_info(user_id=int(user_id))
    except Exception as e:
        logger.warning(f"获取用户 {user_id} 的 QQ 等级失败: {e}")
        return None

    if parse_level_value(user_info.get("isHideQQLevel")) == 1:
        logger.info(f"用户 {user_id} 已隐藏 QQ 等级，转入手动处理。")
        return None

    for key in ("qqLevel", "qq_level", "level"):
        level = parse_level_value(user_info.get(key))
        if level is not None:
            return level

    logger.warning(f"用户 {user_id} 的陌生人信息中未找到 QQ 等级字段。")
    return None


import re
import tempfile
from pathlib import Path

from nonebot import logger, on_command, on_notice, on_request
from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    Bot,
    GroupDecreaseNoticeEvent,
    GroupMessageEvent,
    GroupRequestEvent,
    Message,
)
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from zhenxun.models.bot_console import BotConsole
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.enum import BlockType

from .utils import (
    CACHE_PATH,
    add_blacklist_share,
    add_keyword_allowed,
    add_keyword_disallowed,
    add_to_blacklist,
    enable_exit_recording,
    ensure_group_data,
    get_group_blacklist,
    get_level_limit_config,
    get_reject_reason,
    get_reject_reasons,
    get_shared_group_ids,
    has_blacklist_share,
    leave_blacklist_share_group,
    load_data,
    record_exit,
    remove_from_blacklist,
    remove_keyword_allowed,
    remove_keyword_disallowed,
    set_level_limit_approve_level,
    set_level_limit_enabled,
    set_level_limit_reject_level,
    set_reject_reason,
    sync_blacklist_from_group,
)

__plugin_meta__ = PluginMetadata(
    name="加群自动审批",
    description="帮助管理员审核入群申请，提供关键词、退群黑名单和等级门槛控制。",
    type="application",
    homepage="https://github.com/lumenmai/nonebot-plugin-auto-enter-group",
    extra={"author": "padoru233", "version": "2.0.0"},
    usage="""
        查看关键词：群主/管理员可查看入群关键词
        添加/删除允许关键词<关键词>：添加/删除自动允许入群关键词，多个关键词用逗号分隔
        添加/删除拒绝关键词<关键词>：添加/删除自动拒绝入群关键词，多个关键词用逗号分隔
        入群答案自动进行关键词模糊匹配
        查看入群等级限制：查看当前群的入群等级限制状态
        启用/禁用入群等级限制：启用/关闭当前群的等级门槛
        设置自动拒绝等级<等级>：用户等级小于等于该值时自动拒绝
        设置自动批准等级<等级>：用户等级大于等于该值时才进入自动批准流程
        查看入群拒绝理由：查看当前群的黑名单、拒绝关键词和等级过低拒绝理由
        设置黑名单拒绝理由<理由>：设置命中退群黑名单时的拒绝理由
        设置拒绝关键词理由<理由>：设置命中拒绝关键词时的拒绝理由
        设置等级过低拒绝理由<理由>：设置等级过低时的拒绝理由
        启用/禁用退群黑名单：启用/禁用本群退群黑名单，启用后退群用户将无法再次加入
        查看黑名单：查看当前群组黑名单
        查询黑名单<QQ号>：查询一个或多个用户是否在当前群组黑名单内
        添加/移除黑名单：将批量用户ID添加/移出黑名单，支持逗号、空格或换行分隔，共享状态下会同步操作
        共享黑名单<群号>：与指定群共享退群黑名单，需同时拥有两个群的管理员身份
        同步黑名单<群号>：一次性拉取指定群退群黑名单到当前群，只添加不删除
        停止共享黑名单 [群号]：当前群退出黑名单共享组，保留原有黑名单数据
    """.strip(),
    supported_adapters={"~onebot.v11"},
)


PLUGIN_MODULE = __name__.split(".")[-1]
PLUGIN_MODULE_PATH = __name__
MAX_REJECT_REASON_LENGTH = 100


load_data()




def format_level_text(level: int | None) -> str:
    return "未获取到" if level is None else str(level)


def format_limit_text(level: int | None, prefix: str) -> str:
    return "未设置" if level is None else f"{prefix}{level}"


def is_level_limit_valid(
    reject_at_or_below: int | None, approve_at_or_above: int | None
) -> bool:
    if reject_at_or_below is None or approve_at_or_above is None:
        return True
    return reject_at_or_below < approve_at_or_above


def parse_group_id_arg(args: Message) -> str:
    return args.extract_plain_text().strip()


def is_valid_group_id(group_id: str) -> bool:
    return group_id.isdigit()


def parse_qq_id_list(text: str) -> list[str]:
    user_ids = []
    for user_id in re.findall(r"\d+", text):
        if user_id not in user_ids:
            user_ids.append(user_id)
    return user_ids


def format_blacklist_members(user_ids: list[str]) -> str:
    return ",".join(user_ids)


def parse_reject_reason(args: Message) -> str:
    return re.sub(r"[\r\n]+", " ", args.extract_plain_text()).strip()


async def get_current_plugin_info() -> PluginInfo | None:
    plugin = await PluginInfo.get_plugin(module=PLUGIN_MODULE)
    if plugin:
        return plugin
    plugin = await PluginInfo.get_plugin(module_path=PLUGIN_MODULE_PATH)
    if plugin:
        return plugin
    return await PluginInfo.get_plugin(name=__plugin_meta__.name)


async def is_auto_enter_group_blocked(bot_id: str, group_id: str) -> bool:
    try:
        plugin = await get_current_plugin_info()
        if not plugin:
            return False

        marker = CommonUtils.format(plugin.module)
        bot_data = await BotConsole.get_or_none(bot_id=bot_id)
        if bot_data and (not bot_data.status or marker in bot_data.block_plugins):
            return True

        group_data = await GroupConsole.get_group(group_id=group_id)
        if group_data:
            if group_data.level < 0 or not group_data.status:
                return True
            if (
                marker in group_data.block_plugin
                or marker in group_data.superuser_block_plugin
            ):
                return True
            if plugin.level > group_data.level:
                return True

        if plugin.block_type == BlockType.GROUP:
            return True
        if (
            not plugin.status
            and plugin.block_type == BlockType.ALL
            and not (group_data and group_data.is_super)
        ):
            return True
    except Exception as e:
        logger.warning(f"检查加群自动审批内置开关失败: {e}")
    return False


async def is_target_group_admin(bot: Bot, user_id: int, target_group_id: str) -> bool | None:
    if str(user_id) in bot.config.superusers:
        return True
    try:
        member_info = await bot.get_group_member_info(
            group_id=int(target_group_id), user_id=user_id
        )
    except Exception as e:
        logger.warning(f"获取用户 {user_id} 在群 {target_group_id} 的成员信息失败: {e}")
        return None
    return member_info.get("role") in ["admin", "owner"]


async def send_request_notice(user_id: str, comment: str):
    await group_request_handler.send(
        f"收到加群请求：\n用户：{user_id}\n验证信息：{comment}"
    )


get_keywords = on_command(
    "查看关键词",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=False,
)


@get_keywords.handle()
async def handle_get_keywords(event: GroupMessageEvent):
    group_id = str(event.group_id)
    group_data = ensure_group_data(group_id)
    allowed_keywords = group_data.get("allowed_keywords", [])
    disallowed_keywords = group_data.get("disallowed_keywords", [])
    message = ""
    if allowed_keywords:
        message += f"当前允许入群关键词：{', '.join(allowed_keywords)}\n"
    else:
        message += "当前没有允许入群关键词\n"
    if disallowed_keywords:
        message += f"当前拒绝入群关键词：{', '.join(disallowed_keywords)}"
    else:
        message += "当前没有拒绝入群关键词"
    await get_keywords.finish(message)


add_allowed_keyword = on_command(
    "添加允许关键词",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@add_allowed_keyword.handle()
async def handle_add_allowed(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = str(event.group_id)
    raw_keywords = args.extract_plain_text().strip().lower().replace("，", ",")
    keywords = [keyword.strip() for keyword in raw_keywords.split(",") if keyword.strip()]
    if not keywords:
        await add_allowed_keyword.finish("关键词不能为空，请输入有效的关键词，多个关键词请用逗号分隔。")
        return
    success_list = []
    exists_list = []
    for keyword in keywords:
        if add_keyword_allowed(group_id, keyword):
            success_list.append(keyword)
        else:
            exists_list.append(keyword)
    message = []
    if success_list:
        message.append(f"允许关键词添加成功：{', '.join(success_list)}")
    if exists_list:
        message.append(f"允许关键词已存在：{', '.join(exists_list)}")
    await add_allowed_keyword.finish("\n".join(message))


remove_allowed_keyword = on_command(
    "删除允许关键词",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@remove_allowed_keyword.handle()
async def handle_remove_allowed(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    keyword = args.extract_plain_text().strip().lower()
    if not keyword:
        await remove_allowed_keyword.finish("关键词不能为空，请输入有效的关键词。")
        return
    if remove_keyword_allowed(group_id, keyword):
        await remove_allowed_keyword.finish(f"允许关键词 '{keyword}' 已从当前群删除。")
    else:
        await remove_allowed_keyword.finish(f"允许关键词 '{keyword}' 不存在于当前群。")


add_disallowed_keyword = on_command(
    "添加拒绝关键词",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@add_disallowed_keyword.handle()
async def handle_add_disallowed(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    raw_keywords = args.extract_plain_text().strip().lower().replace("，", ",")
    keywords = [keyword.strip() for keyword in raw_keywords.split(",") if keyword.strip()]
    if not keywords:
        await add_disallowed_keyword.finish("关键词不能为空，请输入有效的关键词，多个关键词请用逗号分隔。")
        return
    success_list = []
    exists_list = []
    for keyword in keywords:
        if add_keyword_disallowed(group_id, keyword):
            success_list.append(keyword)
        else:
            exists_list.append(keyword)
    message = []
    if success_list:
        message.append(f"拒绝关键词添加成功：{', '.join(success_list)}")
    if exists_list:
        message.append(f"拒绝关键词已存在：{', '.join(exists_list)}")
    await add_disallowed_keyword.finish("\n".join(message))


remove_disallowed_keyword = on_command(
    "删除拒绝关键词",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@remove_disallowed_keyword.handle()
async def handle_remove_disallowed(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    keyword = args.extract_plain_text().strip().lower()
    if not keyword:
        await remove_disallowed_keyword.finish("关键词不能为空，请输入有效的关键词。")
        return
    if remove_keyword_disallowed(group_id, keyword):
        await remove_disallowed_keyword.finish(f"拒绝关键词 '{keyword}' 已从当前群删除。")
    else:
        await remove_disallowed_keyword.finish(f"拒绝关键词 '{keyword}' 不存在于当前群。")


enable_exit_cmd = on_command(
    "启用退群黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@enable_exit_cmd.handle()
async def handle_enable_exit(event: GroupMessageEvent):
    group_id = str(event.group_id)
    enable_exit_recording(group_id, True)
    await enable_exit_cmd.finish(f"群 {group_id} 的退群黑名单功能已启用。")
    logger.info(f"群 {group_id} 的退群黑名单功能已启用。")


disable_exit_cmd = on_command(
    "禁用退群黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@disable_exit_cmd.handle()
async def handle_disable_exit(event: GroupMessageEvent):
    group_id = str(event.group_id)
    enable_exit_recording(group_id, False)
    await disable_exit_cmd.finish(f"群 {group_id} 的退群黑名单功能已禁用。")
    logger.info(f"群 {group_id} 的退群黑名单功能已禁用。")


level_limit_status_cmd = on_command(
    "查看入群等级限制",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@level_limit_status_cmd.handle()
async def handle_level_limit_status(event: GroupMessageEvent):
    group_id = str(event.group_id)
    level_limit = get_level_limit_config(group_id)
    status_text = "开启" if level_limit["enabled"] else "关闭"
    reject_text = format_limit_text(level_limit["reject_at_or_below"], "<=")
    approve_text = format_limit_text(level_limit["approve_at_or_above"], ">=")
    await level_limit_status_cmd.finish(
        "当前群入群等级限制状态："
        f"\n状态：{status_text}"
        "\n等级来源：申请人 QQ 等级（隐藏或无法获取时转人工）"
        f"\n自动拒绝等级：{reject_text}"
        f"\n自动批准等级：{approve_text}"
        "\n规则：等级小于等于自动拒绝等级时自动拒绝；"
        "等级大于等于自动批准等级时才进入关键词自动审批；"
        "其余情况转人工。"
    )


enable_level_limit_cmd = on_command(
    "启用入群等级限制",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@enable_level_limit_cmd.handle()
async def handle_enable_level_limit(event: GroupMessageEvent):
    group_id = str(event.group_id)
    level_limit = get_level_limit_config(group_id)
    if not is_level_limit_valid(
        level_limit["reject_at_or_below"], level_limit["approve_at_or_above"]
    ):
        await enable_level_limit_cmd.finish(
            "当前等级阈值配置无效，请先确保自动拒绝等级小于自动批准等级。"
        )
        return
    set_level_limit_enabled(group_id, True)
    level_limit = get_level_limit_config(group_id)
    await enable_level_limit_cmd.finish(
        "已启用当前群的入群等级限制。"
        f"\n自动拒绝等级：{format_limit_text(level_limit['reject_at_or_below'], '<=')}"
        f"\n自动批准等级：{format_limit_text(level_limit['approve_at_or_above'], '>=')}"
    )


disable_level_limit_cmd = on_command(
    "禁用入群等级限制",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@disable_level_limit_cmd.handle()
async def handle_disable_level_limit(event: GroupMessageEvent):
    group_id = str(event.group_id)
    set_level_limit_enabled(group_id, False)
    await disable_level_limit_cmd.finish("已禁用当前群的入群等级限制。")


set_reject_level_cmd = on_command(
    "设置自动拒绝等级",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@set_reject_level_cmd.handle()
async def handle_set_reject_level(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    raw_level = args.extract_plain_text().strip()
    if not raw_level:
        await set_reject_level_cmd.finish("请输入整数等级，例如：设置自动拒绝等级 5")
        return
    try:
        reject_level = int(raw_level)
    except ValueError:
        await set_reject_level_cmd.finish("等级必须是整数。")
        return

    level_limit = get_level_limit_config(group_id)
    approve_level = level_limit["approve_at_or_above"]
    if approve_level is not None and reject_level >= approve_level:
        await set_reject_level_cmd.finish("自动拒绝等级必须小于自动批准等级。")
        return

    set_level_limit_reject_level(group_id, reject_level)
    await set_reject_level_cmd.finish(f"已将自动拒绝等级设置为 <= {reject_level}。")


set_approve_level_cmd = on_command(
    "设置自动批准等级",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@set_approve_level_cmd.handle()
async def handle_set_approve_level(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    raw_level = args.extract_plain_text().strip()
    if not raw_level:
        await set_approve_level_cmd.finish("请输入整数等级，例如：设置自动批准等级 16")
        return
    try:
        approve_level = int(raw_level)
    except ValueError:
        await set_approve_level_cmd.finish("等级必须是整数。")
        return

    level_limit = get_level_limit_config(group_id)
    reject_level = level_limit["reject_at_or_below"]
    if reject_level is not None and approve_level <= reject_level:
        await set_approve_level_cmd.finish("自动批准等级必须大于自动拒绝等级。")
        return

    set_level_limit_approve_level(group_id, approve_level)
    await set_approve_level_cmd.finish(f"已将自动批准等级设置为 >= {approve_level}。")


reject_reason_status_cmd = on_command(
    "查看入群拒绝理由",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@reject_reason_status_cmd.handle()
async def handle_reject_reason_status(event: GroupMessageEvent):
    reject_reasons = get_reject_reasons(str(event.group_id))
    await reject_reason_status_cmd.finish(
        "当前群入群拒绝理由："
        f"\n黑名单：{reject_reasons['blacklist']}"
        f"\n拒绝关键词：{reject_reasons['disallowed_keyword']}"
        f"\n等级过低：{reject_reasons['level_too_low']}"
    )


set_blacklist_reject_reason_cmd = on_command(
    "设置黑名单拒绝理由",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@set_blacklist_reject_reason_cmd.handle()
async def handle_set_blacklist_reject_reason(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    reason = parse_reject_reason(args)
    if not reason:
        await set_blacklist_reject_reason_cmd.finish("拒绝理由不能为空。")
        return
    if len(reason) > MAX_REJECT_REASON_LENGTH:
        await set_blacklist_reject_reason_cmd.finish(
            f"拒绝理由不能超过 {MAX_REJECT_REASON_LENGTH} 个字符。"
        )
        return
    set_reject_reason(str(event.group_id), "blacklist", reason)
    await set_blacklist_reject_reason_cmd.finish(
        f"已将当前群的黑名单拒绝理由设置为：{reason}"
    )


set_disallowed_keyword_reject_reason_cmd = on_command(
    "设置拒绝关键词理由",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@set_disallowed_keyword_reject_reason_cmd.handle()
async def handle_set_disallowed_keyword_reject_reason(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    reason = parse_reject_reason(args)
    if not reason:
        await set_disallowed_keyword_reject_reason_cmd.finish("拒绝理由不能为空。")
        return
    if len(reason) > MAX_REJECT_REASON_LENGTH:
        await set_disallowed_keyword_reject_reason_cmd.finish(
            f"拒绝理由不能超过 {MAX_REJECT_REASON_LENGTH} 个字符。"
        )
        return
    set_reject_reason(str(event.group_id), "disallowed_keyword", reason)
    await set_disallowed_keyword_reject_reason_cmd.finish(
        f"已将当前群的拒绝关键词理由设置为：{reason}"
    )


set_level_too_low_reject_reason_cmd = on_command(
    "设置等级过低拒绝理由",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@set_level_too_low_reject_reason_cmd.handle()
async def handle_set_level_too_low_reject_reason(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    reason = parse_reject_reason(args)
    if not reason:
        await set_level_too_low_reject_reason_cmd.finish("拒绝理由不能为空。")
        return
    if len(reason) > MAX_REJECT_REASON_LENGTH:
        await set_level_too_low_reject_reason_cmd.finish(
            f"拒绝理由不能超过 {MAX_REJECT_REASON_LENGTH} 个字符。"
        )
        return
    set_reject_reason(str(event.group_id), "level_too_low", reason)
    await set_level_too_low_reject_reason_cmd.finish(
        f"已将当前群的等级过低拒绝理由设置为：{reason}"
    )


view_blacklist = on_command(
    "查看黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@view_blacklist.handle()
async def handle_view_blacklist(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    blacklist = get_group_blacklist(group_id)
    if not blacklist:
        await view_blacklist.finish("当前群组的退群黑名单为空。")
        return

    total_items = len(blacklist)
    if total_items > 60:
        content = format_blacklist_members(blacklist)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".txt",
                prefix=f"{group_id}_blacklist_",
                dir=CACHE_PATH,
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_path = Path(temp_file.name)
            await bot.call_api(
                "upload_group_file",
                group_id=event.group_id,
                file=str(temp_path),
                name=f"{group_id}_blacklist.txt",
            )
        except ActionFailed as e:
            logger.warning(f"群 {group_id} 黑名单文件发送失败: {e}")
            await view_blacklist.finish("黑名单数量较多，文件发送失败，请稍后重试。")
            return
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
        await view_blacklist.finish(
            f"当前群组的退群黑名单共有 {total_items} 个用户，已生成文件发送。"
        )
        return

    await view_blacklist.finish(format_blacklist_members(blacklist))


query_blacklist = on_command(
    "查询黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@query_blacklist.handle()
async def handle_query_blacklist(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    user_ids = parse_qq_id_list(args.extract_plain_text())
    if not user_ids:
        await query_blacklist.finish("请提供要查询的用户 QQ 号，多个用户请用逗号或空格分隔。")
        return

    blacklist = get_group_blacklist(group_id)
    exists_list = [user_id for user_id in user_ids if user_id in blacklist]
    not_exist_list = [user_id for user_id in user_ids if user_id not in blacklist]

    msg = []
    if exists_list:
        msg.append(f"在黑名单: {format_blacklist_members(exists_list)}。")
    if not_exist_list:
        msg.append(f"不在黑名单: {format_blacklist_members(not_exist_list)}。")
    await query_blacklist.finish("\n".join(msg))


add_blacklist = on_command(
    "添加黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@add_blacklist.handle()
async def handle_add_blacklist(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = str(event.group_id)
    user_ids = parse_qq_id_list(args.extract_plain_text())
    if not user_ids:
        await add_blacklist.finish("请提供要添加的用户 QQ 号，多个用户请用逗号或空格分隔。")
        return

    success_list = []
    already_list = []
    fail_list = []
    for user_id in user_ids:
        blacklist = get_group_blacklist(group_id)
        if user_id in blacklist:
            already_list.append(user_id)
            continue
        if add_to_blacklist(group_id, user_id):
            success_list.append(user_id)
        else:
            fail_list.append(user_id)

    msg = []
    if success_list:
        msg.append(f"成功添加: {format_blacklist_members(success_list)}。")
        if has_blacklist_share(group_id):
            msg.append("已同步到共享黑名单群组。")
    if already_list:
        msg.append(f"已在黑名单: {format_blacklist_members(already_list)}。")
    if fail_list:
        msg.append(f"添加失败: {format_blacklist_members(fail_list)}。")
    await add_blacklist.finish("\n".join(msg) if msg else "操作完成。")


remove_blacklist = on_command(
    "移除黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@remove_blacklist.handle()
async def handle_remove_blacklist(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    user_ids = parse_qq_id_list(args.extract_plain_text())
    if not user_ids:
        await remove_blacklist.finish("请提供要移除的用户 QQ 号，多个用户请用逗号或空格分隔。")
        return

    success_list = []
    not_exist_list = []
    fail_list = []
    for user_id in user_ids:
        blacklist = get_group_blacklist(group_id)
        if user_id not in blacklist:
            not_exist_list.append(user_id)
            continue
        if remove_from_blacklist(group_id, user_id):
            success_list.append(user_id)
        else:
            fail_list.append(user_id)

    msg = []
    if success_list:
        msg.append(f"成功移除: {format_blacklist_members(success_list)}。")
        if has_blacklist_share(group_id):
            msg.append("已同步到共享黑名单群组。")
    if not_exist_list:
        msg.append(f"不在黑名单: {format_blacklist_members(not_exist_list)}。")
    if fail_list:
        msg.append(f"移除失败: {format_blacklist_members(fail_list)}。")
    await remove_blacklist.finish("\n".join(msg) if msg else "操作完成。")


share_blacklist = on_command(
    "共享黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@share_blacklist.handle()
async def handle_share_blacklist(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    target_group_id = parse_group_id_arg(args)
    if not target_group_id:
        await share_blacklist.finish("请提供要共享黑名单的群号，例如：共享黑名单 123456")
        return
    if not is_valid_group_id(target_group_id):
        await share_blacklist.finish("群号必须是数字。")
        return
    if target_group_id == group_id:
        await share_blacklist.finish("不能与当前群共享黑名单。")
        return

    is_admin = await is_target_group_admin(bot, event.user_id, target_group_id)
    if is_admin is None:
        await share_blacklist.finish(
            f"无法获取群 {target_group_id} 的成员信息，请确认机器人已加入该群。"
        )
        return
    if not is_admin:
        await share_blacklist.finish(
            f"无法确认你在群 {target_group_id} 的管理员身份，不能建立黑名单共享。"
        )
        return

    try:
        created, group_count, blacklist_count = add_blacklist_share(
            group_id, target_group_id
        )
    except Exception as e:
        logger.error(f"群 {group_id} 与群 {target_group_id} 共享黑名单失败: {e}")
        await share_blacklist.finish("共享黑名单失败，请稍后重试。")
        return

    if created:
        await share_blacklist.finish(
            f"已与群 {target_group_id} 建立黑名单共享。"
            f"\n当前共享组共有 {group_count} 个群，黑名单已合并为 {blacklist_count} 个用户。"
        )
    await share_blacklist.finish(
        f"当前群已与群 {target_group_id} 共享黑名单。"
        f"\n黑名单已重新合并为 {blacklist_count} 个用户。"
    )


sync_blacklist = on_command(
    "同步黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@sync_blacklist.handle()
async def handle_sync_blacklist(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = str(event.group_id)
    target_group_id = parse_group_id_arg(args)
    if not target_group_id:
        await sync_blacklist.finish("请提供要同步黑名单的群号，例如：同步黑名单 123456")
        return
    if not is_valid_group_id(target_group_id):
        await sync_blacklist.finish("群号必须是数字。")
        return
    if target_group_id == group_id:
        await sync_blacklist.finish("不能从当前群同步黑名单。")
        return

    target_blacklist = get_group_blacklist(target_group_id)
    if not target_blacklist:
        await sync_blacklist.finish(f"群 {target_group_id} 的黑名单为空，无需同步。")
        return

    try:
        add_count, total_count = sync_blacklist_from_group(group_id, target_group_id)
    except Exception as e:
        logger.error(f"群 {group_id} 从群 {target_group_id} 同步黑名单失败: {e}")
        await sync_blacklist.finish("同步黑名单失败，请稍后重试。")
        return

    if add_count:
        await sync_blacklist.finish(
            f"已从群 {target_group_id} 同步黑名单。"
            f"\n新增 {add_count} 个用户，当前群黑名单共有 {total_count} 个用户。"
        )
    await sync_blacklist.finish(
        f"群 {target_group_id} 的黑名单已全部存在于当前群。"
        f"\n当前群黑名单共有 {total_count} 个用户。"
    )


stop_share_blacklist = on_command(
    "停止共享黑名单",
    priority=5,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=True,
)


@stop_share_blacklist.handle()
async def handle_stop_share_blacklist(
    event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    target_group_id = parse_group_id_arg(args)
    if target_group_id and not is_valid_group_id(target_group_id):
        await stop_share_blacklist.finish("群号必须是数字。")
        return
    if target_group_id == group_id:
        await stop_share_blacklist.finish("不能停止与当前群的共享。")
        return

    shared_group_ids = get_shared_group_ids(group_id)
    if len(shared_group_ids) <= 1:
        await stop_share_blacklist.finish("当前群未共享黑名单。")
        return
    if target_group_id and target_group_id not in shared_group_ids:
        await stop_share_blacklist.finish(f"当前群未与群 {target_group_id} 共享黑名单。")
        return

    try:
        success, _ = leave_blacklist_share_group(
            group_id, target_group_id if target_group_id else None
        )
    except Exception as e:
        logger.error(f"群 {group_id} 停止共享黑名单失败: {e}")
        await stop_share_blacklist.finish("停止共享黑名单失败，请稍后重试。")
        return

    if not success:
        if target_group_id:
            await stop_share_blacklist.finish(f"当前群未与群 {target_group_id} 共享黑名单。")
        await stop_share_blacklist.finish("当前群未共享黑名单。")

    if target_group_id:
        await stop_share_blacklist.finish(
            f"已退出群 {target_group_id} 所在的黑名单共享组。"
            "\n原有黑名单数据已保留，其他群的共享关系不受影响。"
        )
    await stop_share_blacklist.finish(
        "已退出当前黑名单共享组。"
        "\n原有黑名单数据已保留，其他群的共享关系不受影响。"
    )


group_decrease_handler = on_notice(priority=1, block=False)


@group_decrease_handler.handle()
async def handle_group_decrease(bot: Bot, event: GroupDecreaseNoticeEvent):
    if event.sub_type not in ["leave", "kick"]:
        return

    group_id = str(event.group_id)
    if await is_auto_enter_group_blocked(bot.self_id, group_id):
        return

    user_id = str(event.user_id)
    group_data = ensure_group_data(group_id)
    if not group_data.get("exit_records", {}).get("enabled", False):
        return

    try:
        user_name = (await bot.get_stranger_info(user_id=int(user_id)))["nickname"] or "未知昵称"
    except Exception:
        user_name = "未知昵称"

    record_exit(user_id, group_id)
    await group_decrease_handler.finish(
        f"群友「{user_name}」({user_id})离开了我们，再见，或许再也不见。"
    )


group_request_handler = on_request(priority=1, block=False)


@group_request_handler.handle()
async def handle_first_receive(bot: Bot, event: GroupRequestEvent):
    flag = event.flag
    sub_type = event.sub_type
    if sub_type == "invite":
        return

    group_id = str(event.group_id)
    if await is_auto_enter_group_blocked(bot.self_id, group_id):
        return

    user_id = str(event.user_id)
    comment = (getattr(event, "comment", "") or "").lower()
    group_data = ensure_group_data(group_id)
    allowed_answers = group_data.get("allowed_keywords", [])
    matches_allowed_keyword = any(
        keyword in comment for keyword in allowed_answers
    )

    if group_data.get("exit_records", {}).get("enabled", False):
        if user_id in group_data.get("exit_records", {}).get("members", []):
            await bot.set_group_add_request(
                flag=flag,
                sub_type=sub_type,
                approve=False,
                reason=get_reject_reason(group_id, "blacklist"),
            )
            logger.info(f"用户 {user_id} 被拒绝加入群 {group_id}，原因：已在退群黑名单。")
            return

    disallowed_keywords = group_data.get("disallowed_keywords", [])
    if not matches_allowed_keyword and any(
        keyword in comment for keyword in disallowed_keywords
    ):
        await bot.set_group_add_request(
            flag=flag,
            sub_type=sub_type,
            approve=False,
            reason=get_reject_reason(group_id, "disallowed_keyword"),
        )
        logger.info(f"用户 {user_id} 被拒绝加入群 {group_id}，原因：命中拒绝关键词。")
        return

    level_limit = get_level_limit_config(group_id)
    if level_limit["enabled"]:
        current_level = await get_user_qq_level(bot, user_id)
        if current_level is None:
            await send_request_notice(user_id, comment)
            await group_request_handler.finish("未获取到用户等级，请手动处理申请。")
            return

        reject_level = level_limit["reject_at_or_below"]
        if reject_level is not None and current_level <= reject_level:
            await bot.set_group_add_request(
                flag=flag,
                sub_type=sub_type,
                approve=False,
                reason=get_reject_reason(group_id, "level_too_low"),
            )
            logger.info(
                f"用户 {user_id} 被拒绝加入群 {group_id}，原因：等级 {current_level} 小于等于自动拒绝等级 {reject_level}。"
            )
            return

        approve_level = level_limit["approve_at_or_above"]
        if approve_level is None or current_level < approve_level:
            await send_request_notice(user_id, comment)
            await group_request_handler.finish("用户等级较低，请手动处理申请。")
            return

    await send_request_notice(user_id, comment)

    if allowed_answers and not comment:
        logger.info(
            f"用户 {user_id} 申请加入群 {group_id}，"
            "验证信息为空或尚未获取，转入手动处理。"
        )
        await group_request_handler.finish("未获取到验证信息，请手动处理申请。")
        return

    if not allowed_answers:
        logger.warning(f"用户 {user_id} 尝试加入群 {group_id}，但该群未设置允许关键词。")
        await group_request_handler.finish("本群未设置关键词，无法自动处理。")
        return

    if matches_allowed_keyword:
        await bot.set_group_add_request(
            flag=flag,
            sub_type=sub_type,
            approve=True,
            reason=" ",
        )
        logger.info("请求基于关键词匹配已批准。")
        await group_request_handler.finish("判断成功，已自动批准入群。")
        return

    logger.info("请求因关键词不匹配转入手动处理。")
    await group_request_handler.finish("判断失败，请手动处理申请。")
