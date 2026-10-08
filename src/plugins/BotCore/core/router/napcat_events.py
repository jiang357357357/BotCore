"""Forward bounded OneBot notices/requests to Core for authorized administration."""

from nonebot import on_notice, on_request, on_metaevent
from nonebot.adapters.onebot.v11 import NoticeEvent, RequestEvent, MetaEvent

from src.System.Logs import get_logger


logger = get_logger(__name__)
NOTICE_TYPES = {
    "friend_add", "friend_recall", "group_admin", "group_ban", "group_card",
    "group_decrease", "group_increase", "group_recall", "group_upload",
    "group_msg_emoji_like", "essence", "notify", "online_file_receive", "online_file_send", "offline_file",
}
EVENT_FIELDS = {
    "time", "self_id", "post_type", "notice_type", "request_type", "meta_event_type", "sub_type",
    "user_id", "group_id", "operator_id", "target_id", "message_id", "duration",
    "card_new", "card_old", "flag", "comment", "title", "sender_id", "sender_nick",
    "operator_nick", "tip_text", "status", "status_text", "event_type", "emoji_id", "count",
    "peer_id", "is_add", "times", "honor_type",
}


def normalize_napcat_event(event):
    data = event if isinstance(event, dict) else event.model_dump(mode="json")
    if not isinstance(data, dict):
        return None
    post_type = data.get("post_type")
    if post_type == "request":
        if data.get("request_type") not in {"friend", "group"}:
            return None
    elif post_type == "notice":
        if data.get("notice_type") not in NOTICE_TYPES:
            return None
    elif post_type == "meta_event":
        if data.get("meta_event_type") != "lifecycle":
            return None
    else:
        return None
    result = {}
    for key in EVENT_FIELDS:
        value = data.get(key)
        if isinstance(value, (str, int, bool)):
            if isinstance(value, str) and len(value) > 2048:
                # Never corrupt an approval flag into another request identity.
                if key == "flag":
                    return None
                value = value[:2048]
            result[key] = value
    file_data = data.get("file")
    if isinstance(file_data, dict):
        result["file"] = {key: value[:2048] if isinstance(value, str) else value
                          for key in ("id", "name", "size", "busid", "file_id")
                          if isinstance(value := file_data.get(key), (str, int))}
    likes = data.get("likes")
    if isinstance(likes, list):
        result["likes"] = [{key: value for key in ("emoji_id", "count")
                            if isinstance(value := item.get(key), (str, int))}
                           for item in likes[:20] if isinstance(item, dict)]
    return result


notice_matcher = on_notice(priority=20, block=False)
request_matcher = on_request(priority=20, block=False)
lifecycle_matcher = on_metaevent(priority=20, block=False)


async def _report(event):
    from ...app import connection_manager
    from ...external.monCore.event_outbox import get_event_relay

    if str(event.self_id) != str(connection_manager.qq_number):
        return
    payload = normalize_napcat_event(event)
    if payload is not None:
        await get_event_relay().enqueue(payload)


@notice_matcher.handle()
async def handle_napcat_notice(event: NoticeEvent):
    await _report(event)


@request_matcher.handle()
async def handle_napcat_request(event: RequestEvent):
    # Receiving an application never implicitly approves it or adds chat access.
    await _report(event)


@lifecycle_matcher.handle()
async def handle_napcat_lifecycle(event: MetaEvent):
    await _report(event)
