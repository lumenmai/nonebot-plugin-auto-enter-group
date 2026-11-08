import json
from pathlib import Path
from typing import Any

from nonebot import logger, require

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

DATA_PATH: Path = store.get_plugin_data_file("data.json")
CACHE_PATH: Path = store.get_plugin_cache_dir()

DEFAULT_REJECT_REASONS = {
    "blacklist": "你已在黑名单中，禁止添加本群！",
    "disallowed_keyword": "未正确回答问题",
    "level_too_low": "等级过低",
}

data: dict[str, Any] = {}


def build_default_group_data() -> dict[str, Any]:
    return {
        "allowed_keywords": [],
        "disallowed_keywords": [],
        "exit_records": {"enabled": False, "members": []},
        "level_limit": {
            "enabled": False,
            "reject_at_or_below": None,
            "approve_at_or_above": None,
        },
        "reject_reasons": DEFAULT_REJECT_REASONS.copy(),
        "blacklist_shares": {"groups": []},
    }


def normalize_group_data(group_data: dict[str, Any]) -> bool:
    migrated = False

    if "keywords" in group_data:
        group_data["allowed_keywords"] = group_data.pop("keywords")
        migrated = True
    if "allowed_keywords" not in group_data:
        group_data["allowed_keywords"] = []
        migrated = True
    if "disallowed_keywords" not in group_data:
        group_data["disallowed_keywords"] = []
        migrated = True

    exit_records = group_data.get("exit_records")
    if not isinstance(exit_records, dict):
        group_data["exit_records"] = {"enabled": False, "members": []}
        migrated = True
    else:
        if "enabled" not in exit_records:
            exit_records["enabled"] = False
            migrated = True
        if "members" not in exit_records:
            exit_records["members"] = []
            migrated = True

    level_limit = group_data.get("level_limit")
    if not isinstance(level_limit, dict):
        group_data["level_limit"] = {
            "enabled": False,
            "reject_at_or_below": None,
            "approve_at_or_above": None,
        }
        migrated = True
    else:
        if "min_level" in level_limit:
            level_limit["approve_at_or_above"] = level_limit.pop("min_level")
            if "reject_at_or_below" not in level_limit:
                level_limit["reject_at_or_below"] = None
            migrated = True
        if "enabled" not in level_limit:
            level_limit["enabled"] = False
            migrated = True
        if "reject_at_or_below" not in level_limit:
            level_limit["reject_at_or_below"] = None
            migrated = True
        if "approve_at_or_above" not in level_limit:
            level_limit["approve_at_or_above"] = None
            migrated = True

    reject_reasons = group_data.get("reject_reasons")
    if not isinstance(reject_reasons, dict):
        group_data["reject_reasons"] = DEFAULT_REJECT_REASONS.copy()
        migrated = True
    else:
        for reason_type, default_reason in DEFAULT_REJECT_REASONS.items():
            reason = reject_reasons.get(reason_type)
            if not isinstance(reason, str) or not reason.strip():
                reject_reasons[reason_type] = default_reason
                migrated = True

    blacklist_shares = group_data.get("blacklist_shares")
    if not isinstance(blacklist_shares, dict):
        group_data["blacklist_shares"] = {"groups": []}
        migrated = True
    else:
        groups = blacklist_shares.get("groups")
        if not isinstance(groups, list):
            blacklist_shares["groups"] = []
            migrated = True
        else:
            normalized_groups = []
            for group_id in groups:
                group_id = str(group_id)
                if group_id not in normalized_groups:
                    normalized_groups.append(group_id)
            if normalized_groups != groups:
                blacklist_shares["groups"] = normalized_groups
                migrated = True

    return migrated


def load_data():
    global data
    try:
        if DATA_PATH.exists():
            with DATA_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("groups", {})
            migrated = False
            for _, group_data in data["groups"].items():
                migrated = normalize_group_data(group_data) or migrated
            if migrated:
                save_data(data)
                logger.info("数据已迁移。")
            logger.debug("加载数据成功。")
        else:
            data = {"groups": {}}
            save_data(data)
            logger.debug("未找到数据文件，已创建新文件。")
    except Exception as e:
        data = {"groups": {}}
        logger.error(f"无法加载数据，原因为: {e}")
    return data


def save_data(data):
    try:
        with DATA_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.debug("数据保存成功。")
    except Exception as e:
        logger.error(f"无法保存数据，原因为: {e}")


def ensure_group_data(group_id: str) -> dict[str, Any]:
    global data
    if not data:
        load_data()
    groups = data.setdefault("groups", {})
    group_data = groups.setdefault(group_id, build_default_group_data())
    if normalize_group_data(group_data):
        save_data(data)
    return group_data


def _dedupe_members(members: list[Any]) -> list[str]:
    result = []
    for user_id in members:
        user_id = str(user_id)
        if user_id not in result:
            result.append(user_id)
    return result


def _get_direct_share_ids(group_id: str) -> list[str]:
    group_data = ensure_group_data(group_id)
    return group_data["blacklist_shares"]["groups"]


def _set_direct_share_ids(group_id: str, group_ids: list[str]):
    group_data = ensure_group_data(group_id)
    group_data["blacklist_shares"]["groups"] = _dedupe_members(
        [gid for gid in group_ids if str(gid) != group_id]
    )


def get_shared_group_ids(group_id: str) -> list[str]:
    group_id = str(group_id)
    visited = set()
    pending = [group_id]
    shared_group_ids = []

    while pending:
        current_group_id = pending.pop(0)
        if current_group_id in visited:
            continue
        visited.add(current_group_id)
        shared_group_ids.append(current_group_id)
        for next_group_id in _get_direct_share_ids(current_group_id):
            if next_group_id not in visited:
                pending.append(next_group_id)

    return shared_group_ids


def has_blacklist_share(group_id: str) -> bool:
    return len(get_shared_group_ids(group_id)) > 1


def merge_blacklists(group_ids: list[str]) -> list[str]:
    merged_members = []
    for group_id in group_ids:
        group_data = ensure_group_data(group_id)
        members = group_data["exit_records"].get("members", [])
        for user_id in _dedupe_members(members):
            if user_id not in merged_members:
                merged_members.append(user_id)

    for group_id in group_ids:
        group_data = ensure_group_data(group_id)
        group_data["exit_records"]["members"] = merged_members.copy()

    save_data(data)
    return merged_members


def add_blacklist_share(group_id: str, target_group_id: str) -> tuple[bool, int, int]:
    group_id = str(group_id)
    target_group_id = str(target_group_id)
    current_shares = _get_direct_share_ids(group_id)
    target_shares = _get_direct_share_ids(target_group_id)
    created = False

    if target_group_id not in current_shares:
        current_shares.append(target_group_id)
        created = True
    if group_id not in target_shares:
        target_shares.append(group_id)
        created = True

    _set_direct_share_ids(group_id, current_shares)
    _set_direct_share_ids(target_group_id, target_shares)
    shared_group_ids = get_shared_group_ids(group_id)
    merged_members = merge_blacklists(shared_group_ids)
    return created, len(shared_group_ids), len(merged_members)


def leave_blacklist_share_group(
    group_id: str, target_group_id: str | None = None
) -> tuple[bool, int]:
    group_id = str(group_id)
    target_group_id = str(target_group_id) if target_group_id else None
    shared_group_ids = get_shared_group_ids(group_id)

    if len(shared_group_ids) <= 1:
        return False, 0
    if target_group_id and target_group_id not in shared_group_ids:
        return False, 0

    other_group_ids = [gid for gid in shared_group_ids if gid != group_id]
    _set_direct_share_ids(group_id, [])
    for other_group_id in other_group_ids:
        other_shares = [
            gid for gid in _get_direct_share_ids(other_group_id) if gid != group_id
        ]
        _set_direct_share_ids(other_group_id, other_shares)

    save_data(data)
    return True, len(other_group_ids)


def sync_blacklist_from_group(group_id: str, source_group_id: str) -> tuple[int, int]:
    group_id = str(group_id)
    source_group_id = str(source_group_id)
    group_data = ensure_group_data(group_id)
    source_members = get_group_blacklist(source_group_id)
    current_members = group_data["exit_records"]["members"]
    add_count = 0

    for user_id in source_members:
        if user_id not in current_members:
            current_members.append(user_id)
            add_count += 1

    if add_count:
        save_data(data)
    return add_count, len(current_members)


def _add_members_to_shared_groups(group_id: str, user_ids: list[str]):
    shared_group_ids = get_shared_group_ids(group_id)
    for shared_group_id in shared_group_ids:
        group_data = ensure_group_data(shared_group_id)
        members = group_data["exit_records"]["members"]
        for user_id in user_ids:
            if user_id not in members:
                members.append(user_id)
    save_data(data)


def _remove_members_from_shared_groups(group_id: str, user_ids: list[str]):
    shared_group_ids = get_shared_group_ids(group_id)
    for shared_group_id in shared_group_ids:
        group_data = ensure_group_data(shared_group_id)
        members = group_data["exit_records"]["members"]
        group_data["exit_records"]["members"] = [
            member for member in members if member not in user_ids
        ]
    save_data(data)


def add_keyword_allowed(group_id, keyword):
    group_data = ensure_group_data(group_id)
    if keyword not in group_data["allowed_keywords"]:
        group_data["allowed_keywords"].append(keyword)
        save_data(data)
        logger.info(f"{group_id}添加允许关键词 '{keyword}' 成功。")
        return True
    logger.warning(f"允许关键词 '{keyword}' 已在 {group_id} 的关键词列表中。")
    return False


def remove_keyword_allowed(group_id, keyword):
    group_data = ensure_group_data(group_id)
    if keyword in group_data["allowed_keywords"]:
        group_data["allowed_keywords"].remove(keyword)
        save_data(data)
        logger.info(f"{group_id}删除允许关键词 '{keyword}' 成功。")
        return True
    logger.warning(f"允许关键词 '{keyword}' 不在 {group_id} 的关键词列表中。")
    return False


def add_keyword_disallowed(group_id, keyword):
    group_data = ensure_group_data(group_id)
    if keyword not in group_data["disallowed_keywords"]:
        group_data["disallowed_keywords"].append(keyword)
        save_data(data)
        logger.info(f"{group_id}添加拒绝关键词 '{keyword}' 成功。")
        return True
    logger.warning(f"拒绝关键词 '{keyword}' 已在 {group_id} 的关键词列表中。")
    return False


def remove_keyword_disallowed(group_id, keyword):
    group_data = ensure_group_data(group_id)
    if keyword in group_data["disallowed_keywords"]:
        group_data["disallowed_keywords"].remove(keyword)
        save_data(data)
        logger.info(f"{group_id}删除拒绝关键词 '{keyword}' 成功。")
        return True
    logger.warning(f"拒绝关键词 '{keyword}' 不在 {group_id} 的关键词列表中。")
    return False


def record_exit(user_id: str, group_id: str):
    group_data = ensure_group_data(group_id)
    if group_data["exit_records"]["enabled"]:
        _add_members_to_shared_groups(group_id, [user_id])
        logger.info(f"记录用户 {user_id} 退出群组 {group_id}，并同步到共享黑名单群组。")
    else:
        logger.warning(f"{group_id} 该群组没有开启退群黑名单")


def enable_exit_recording(group_id: str, enabled: bool):
    group_data = ensure_group_data(group_id)
    group_data["exit_records"]["enabled"] = enabled
    save_data(data)
    logger.info(f"群组 {group_id} {'开启' if enabled else '关闭'} 退群黑名单。")


def get_group_blacklist(group_id):
    group_data = ensure_group_data(group_id)
    exit_records = group_data.get("exit_records", {"enabled": False, "members": []})
    return exit_records.get("members", [])


def add_to_blacklist(group_id, user_id):
    group_data = ensure_group_data(group_id)
    try:
        if user_id not in group_data["exit_records"]["members"]:
            _add_members_to_shared_groups(group_id, [user_id])
            return True
        return False
    except Exception:
        return False


def remove_from_blacklist(group_id, user_id):
    group_data = ensure_group_data(group_id)
    try:
        if user_id in group_data["exit_records"]["members"]:
            _remove_members_from_shared_groups(group_id, [user_id])
            return True
        return False
    except Exception:
        return False


def get_level_limit_config(group_id: str) -> dict[str, int | bool | None]:
    group_data = ensure_group_data(group_id)
    level_limit = group_data["level_limit"]
    return {
        "enabled": bool(level_limit.get("enabled", False)),
        "reject_at_or_below": (
            None
            if level_limit.get("reject_at_or_below") is None
            else int(level_limit["reject_at_or_below"])
        ),
        "approve_at_or_above": (
            None
            if level_limit.get("approve_at_or_above") is None
            else int(level_limit["approve_at_or_above"])
        ),
    }


def set_level_limit_enabled(group_id: str, enabled: bool):
    group_data = ensure_group_data(group_id)
    group_data["level_limit"]["enabled"] = enabled
    save_data(data)
    logger.info(f"{group_id} 入群等级限制已{'开启' if enabled else '关闭'}。")


def set_level_limit_reject_level(group_id: str, level: int | None):
    group_data = ensure_group_data(group_id)
    group_data["level_limit"]["reject_at_or_below"] = level
    save_data(data)
    logger.info(f"{group_id} 入群等级限制自动拒绝阈值已设置为 {level}。")


def set_level_limit_approve_level(group_id: str, level: int | None):
    group_data = ensure_group_data(group_id)
    group_data["level_limit"]["approve_at_or_above"] = level
    save_data(data)
    logger.info(f"{group_id} 入群等级限制自动批准阈值已设置为 {level}。")


def get_reject_reason(group_id: str, reason_type: str) -> str:
    if reason_type not in DEFAULT_REJECT_REASONS:
        raise ValueError(f"不支持的拒绝理由类型: {reason_type}")
    group_data = ensure_group_data(group_id)
    reason = group_data.get("reject_reasons", {}).get(reason_type)
    if isinstance(reason, str) and reason.strip():
        return reason
    return DEFAULT_REJECT_REASONS[reason_type]


def get_reject_reasons(group_id: str) -> dict[str, str]:
    return {
        reason_type: get_reject_reason(group_id, reason_type)
        for reason_type in DEFAULT_REJECT_REASONS
    }


def set_reject_reason(group_id: str, reason_type: str, reason: str):
    if reason_type not in DEFAULT_REJECT_REASONS:
        raise ValueError(f"不支持的拒绝理由类型: {reason_type}")
    group_data = ensure_group_data(group_id)
    group_data["reject_reasons"][reason_type] = reason
    save_data(data)
    logger.info(f"{group_id} 入群拒绝理由 {reason_type} 已更新。")
