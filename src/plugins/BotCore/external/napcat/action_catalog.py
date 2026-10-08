"""Restricted bridge catalog, verified against NapCat v4.18.28 source.

This module has no application dependencies and is shared with MonCore. Limits
below are bridge limits, not claims about QQ's own limits. Credentials and raw
OneBot JSON/XML messages are deliberately absent.
"""

from copy import deepcopy
import base64
import binascii
import ipaddress
import re
from urllib.parse import urlsplit


class ActionValidationError(ValueError):
    def __init__(self, message, code="INVALID_PARAMS"):
        super().__init__(message)
        self.code = code


def text(limit=2000, minimum=0, **extra):
    return {"type": "string", "minLength": minimum, "maxLength": limit, **extra}


def number(minimum=0, maximum=2147483647, **extra):
    return {"type": "integer", "minimum": minimum, "maximum": maximum, **extra}


def array(items, maximum=100, minimum=1, **extra):
    return {"type": "array", "items": deepcopy(items), "minItems": minimum, "maxItems": maximum, **extra}


ID = {"type": ["string", "integer"], "format": "id"}
MSG = {"type": ["string", "integer"], "format": "message_id"}
NONNEG_ID = {"type": ["string", "integer"], "format": "nonnegative_id"}
OPAQUE = text(1024, 1, format="opaque_id")
REQUEST_FLAG = text(2048, 1, format="opaque_id")
URL = text(2048, 1, format="http_url")
NAME = text(120, 1, format="filename")
BOOL = {"type": "boolean"}
RESOURCE_ID = text(512, 1, pattern=r"^[A-Za-z0-9_.-]+$", description="来自 source_message_id 附件的资源标识；不接受路径或URL")
ACTION_CATALOG = {}
SOURCE_BASE = "https://github.com/NapNeko/NapCatQQ/blob/v4.18.28/packages/napcat-onebot/action/"


def add(action, description, mutating, target, source, properties=None, required=(), **metadata):
    fields = {"user": "user_id", "group": "group_id", "message": "message_id", "request": "flag"}
    ACTION_CATALOG[action] = {
        "description": description, "mutating": mutating, "target": target,
        "target_field": fields.get(target), "source": SOURCE_BASE + source + ".ts",
        "parameters": {"type": "object", "properties": deepcopy(properties or {}),
                       "required": list(required), "additionalProperties": False},
        **metadata,
    }
    if metadata.get("one_of_fields"):
        ACTION_CATALOG[action]["parameters"]["oneOf"] = [
            {"required": [field]} for field in metadata["one_of_fields"]
        ]
    if metadata.get("any_of_fields"):
        ACTION_CATALOG[action]["parameters"]["anyOf"] = [
            {"required": [field]} for field in metadata["any_of_fields"]
        ]
    if metadata.get("exclusive_field_groups"):
        ACTION_CATALOG[action]["parameters"]["allOf"] = [
            {"oneOf": [{"required": [field]} for field in group]}
            for group in metadata["exclusive_field_groups"]
        ]


# Message inspection and bounded rich messages. Numbers are normalized to strings
# where upstream requires Type.String, including negative OneBot message IDs.
for action, description, source in [
    ("get_msg", "读取消息", "msg/GetMsg"),
    ("fetch_ptt_text", "将收到的语音消息转写为文字", "msg/FetchPttText"),
]:
    add(action, description, False, "message", source, {"message_id": MSG}, ("message_id",))
add("get_forward_msg", "读取合并转发消息", False, "message", "go-cqhttp/GetForwardMsg",
    {"message_id": OPAQUE, "id": OPAQUE}, one_of_fields=["message_id", "id"])
for action, target in [("send_private_msg", "user"), ("send_group_msg", "group")]:
    field = target + "_id"
    add(action, "发送受限消息段（文字、图片、语音、视频、引用、表情、@）", True, target, "msg/SendMsg",
        {field: ID, "message": {"type": "array", "format": "message_segments", "minItems": 1, "maxItems": 40}},
        (field, "message"))
for action, target in [("send_private_forward_msg", "user"), ("send_group_forward_msg", "group")]:
    field = target + "_id"
    add(action, "发送受限合并转发节点", True, target, "go-cqhttp/SendForwardMsg",
        {field: ID, "messages": {"type": "array", "format": "forward_nodes", "minItems": 1, "maxItems": 20},
         "source": text(100), "summary": text(200), "prompt": text(200)}, (field, "messages"))
add("delete_msg", "撤回消息", True, "message", "msg/DeleteMsg", {"message_id": MSG}, ("message_id",))
add("set_input_status", "设置私聊输入状态（上游示例 event_type=1）", True, "user", "extends/SetInputStatus",
    {"user_id": ID, "event_type": number(0, 10)}, ("user_id", "event_type"))
for action, target in [("mark_private_msg_as_read", "user"), ("mark_group_msg_as_read", "group"), ("mark_msg_as_read", "message")]:
    field = {"user": "user_id", "group": "group_id", "message": "message_id"}[target]
    add(action, "标记会话已读", True, target, "msg/MarkMsgAsRead", {field: MSG if target == "message" else ID}, (field,))
add("_mark_all_as_read", "标记全部会话已读", True, "account", "msg/MarkMsgAsRead")
add("friend_poke", "私聊戳一戳", True, "user", "packet/SendPoke", {"user_id": ID}, ("user_id",))
add("group_poke", "群聊戳一戳", True, "group", "packet/SendPoke", {"group_id": ID, "user_id": ID}, ("group_id", "user_id"))
add("set_msg_emoji_like", "添加或取消消息表情回应", True, "message", "msg/SetMsgEmojiLike",
    {"message_id": MSG, "emoji_id": NONNEG_ID, "set": {**BOOL, "default": True}}, ("message_id", "emoji_id"))
add("send_like", "给好友资料点赞", True, "user", "user/SendLike",
    {"user_id": ID, "times": number(1, 10, default=1)}, ("user_id",))

# Requests must additionally be matched to a captured request event by MonCore.
add("set_friend_add_request", "处理已收到的好友申请", True, "request", "user/SetFriendAddRequest",
    {"flag": REQUEST_FLAG, "approve": BOOL, "remark": text(100)}, ("flag", "approve"))
add("set_group_add_request", "处理已收到的加群申请或邀请", True, "request", "group/SetGroupAddRequest",
    {"flag": REQUEST_FLAG, "sub_type": text(6, 1, enum=["add", "invite"]), "approve": BOOL,
     "reason": text(300), "count": number(1, 100, default=100)}, ("flag", "sub_type", "approve"),
    bridge_only_fields=["sub_type"])
add("get_doubt_friends_add_request", "读取可疑好友申请", False, "account", "new/GetDoubtFriendsAddRequest",
    {"count": number(1, 100, default=50)})
add("set_doubt_friends_add_request", "同意已收到的可疑好友申请（上游仅支持同意）", True, "request", "new/SetDoubtFriendsAddRequest",
    {"flag": REQUEST_FLAG, "approve": {**BOOL, "enum": [True], "default": True}}, ("flag",))
add("set_friend_remark", "修改好友备注", True, "user", "user/SetFriendRemark", {"user_id": ID, "remark": text(100)}, ("user_id", "remark"))
add("delete_friend", "删除好友", True, "user", "go-cqhttp/GoCQHTTPDeleteFriend",
    {"user_id": ID, "temp_block": BOOL, "temp_both_del": BOOL}, ("user_id",))

# Group administration and information.
for action, description, extra, required, source in [
    ("set_group_ban", "禁言或解除禁言", {"user_id": ID, "duration": number(0, 2592000)}, ("user_id", "duration"), "group/SetGroupBan"),
    ("set_group_whole_ban", "设置全员禁言", {"enable": BOOL}, ("enable",), "group/SetGroupWholeBan"),
    ("set_group_admin", "设置群管理员", {"user_id": ID, "enable": BOOL}, ("user_id", "enable"), "group/SetGroupAdmin"),
    ("set_group_card", "修改群名片", {"user_id": ID, "card": text(100)}, ("user_id", "card"), "group/SetGroupCard"),
    ("set_group_name", "修改群名", {"group_name": text(100, 1)}, ("group_name",), "group/SetGroupName"),
    ("set_group_leave", "退出或解散群", {"is_dismiss": {**BOOL, "default": False}}, (), "group/SetGroupLeave"),
    ("set_group_kick", "移出群成员", {"user_id": ID, "reject_add_request": {**BOOL, "default": False}}, ("user_id",), "group/SetGroupKick"),
    ("set_group_special_title", "修改群专属头衔", {"user_id": ID, "special_title": text(100)}, ("user_id", "special_title"), "extends/SetSpecialTitle"),
    ("set_group_remark", "修改本账号的群备注", {"remark": text(100)}, ("remark",), "extends/SetGroupRemark"),
    ("set_group_portrait", "通过图片URL设置群头像", {"file": URL}, ("file",), "go-cqhttp/SetGroupPortrait"),
]:
    add(action, description, True, "group", source, {"group_id": ID, **extra}, ("group_id", *required))
for action, source in [("get_group_info", "group/GetGroupInfo"), ("get_group_member_list", "group/GetGroupMemberList"),
                       ("get_group_signed_list", "extends/GetGroupSignedList")]:
    add(action, "读取群信息或成员/签到列表", False, "group", source, {"group_id": ID}, ("group_id",))
add("get_group_member_info", "读取群成员信息", False, "group", "group/GetGroupMemberInfo",
    {"group_id": ID, "user_id": ID, "no_cache": BOOL}, ("group_id", "user_id"))
add("_send_group_notice", "发布群公告", True, "group", "go-cqhttp/SendGroupNotice",
    {"group_id": ID, "content": text(4000, 1), "image": URL,
     "pinned": number(0, 1, default=0), "type": number(1, 1, default=1),
     "confirm_required": number(0, 1, default=1), "is_show_edit_card": number(0, 1, default=0),
     "tip_window_type": number(0, 1, default=0)}, ("group_id", "content"))
add("_get_group_notice", "读取群公告", False, "group", "group/GetGroupNotice", {"group_id": ID}, ("group_id",))
add("_del_group_notice", "删除群公告", True, "group", "group/DelGroupNotice",
    {"group_id": ID, "notice_id": OPAQUE}, ("group_id", "notice_id"))
add("get_essence_msg_list", "读取群精华消息", False, "group", "group/GetGroupEssence", {"group_id": ID}, ("group_id",))
for action, source in [("set_essence_msg", "group/SetEssenceMsg"), ("delete_essence_msg", "group/DelEssenceMsg")]:
    add(action, "设置或移除精华消息", True, "message", source, {"message_id": MSG}, ("message_id",))

# Group files use opaque server-side identifiers, never local paths.
for action, description, props, required, source in [
    ("get_group_file_system_info", "读取群文件系统信息", {}, (), "go-cqhttp/GetGroupFileSystemInfo"),
    ("get_group_root_files", "读取群根目录文件", {"file_count": number(1, 100, default=50)}, (), "go-cqhttp/GetGroupRootFiles"),
    ("get_group_files_by_folder", "读取群子目录文件", {"folder_id": OPAQUE, "file_count": number(1, 100, default=50)}, ("folder_id",), "go-cqhttp/GetGroupFilesByFolder"),
    ("get_group_file_url", "读取群文件下载链接", {"file_id": OPAQUE}, ("file_id",), "file/GetGroupFileUrl"),
]:
    add(action, description, False, "group", source, {"group_id": ID, **props}, ("group_id", *required))
for action, description, props, required, source in [
    ("upload_group_file", "将HTTP(S)文件上传群文件", {"file": URL, "name": NAME, "folder_id": OPAQUE, "upload_file": {**BOOL, "enum": [True], "default": True}}, ("file", "name"), "go-cqhttp/UploadGroupFile"),
    ("delete_group_file", "删除群文件", {"file_id": OPAQUE}, ("file_id",), "go-cqhttp/DeleteGroupFile"),
    ("create_group_file_folder", "创建群文件目录", {"folder_name": NAME}, ("folder_name",), "go-cqhttp/CreateGroupFileFolder"),
    ("delete_group_folder", "删除群文件目录", {"folder_id": OPAQUE}, ("folder_id",), "go-cqhttp/DeleteGroupFileFolder"),
    ("move_group_file", "移动群文件", {"file_id": OPAQUE, "current_parent_directory": OPAQUE, "target_parent_directory": OPAQUE}, ("file_id", "current_parent_directory", "target_parent_directory"), "extends/MoveGroupFile"),
    ("rename_group_file", "重命名群文件", {"file_id": OPAQUE, "current_parent_directory": OPAQUE, "new_name": NAME}, ("file_id", "current_parent_directory", "new_name"), "extends/RenameGroupFile"),
    ("trans_group_file", "转存群文件", {"file_id": OPAQUE}, ("file_id",), "extends/TransGroupFile"),
]:
    add(action, description, True, "group", source, {"group_id": ID, **props}, ("group_id", *required))
add("upload_private_file", "将HTTP(S)文件上传私聊", True, "user", "go-cqhttp/UploadPrivateFile",
    {"user_id": ID, "file": URL, "name": NAME, "upload_file": {**BOOL, "enum": [True], "default": True}}, ("user_id", "file", "name"))

# Account, OCR and AI voices.
for action, source in [("get_login_info", "system/GetLoginInfo"), ("get_status", "system/GetStatus"),
                       ("get_version_info", "system/GetVersionInfo"), ("get_friend_list", "user/GetFriendList"),
                       ("get_group_list", "group/GetGroupList")]:
    add(action, "读取账号、连接版本或联系人资料", False, "account", source)
add("set_qq_profile", "修改机器人昵称、签名和性别资料", True, "account", "go-cqhttp/SetQQProfile",
    {"nickname": text(100, 1), "personal_note": text(1000), "sex": number(0, 2)}, ("nickname",))
add("set_qq_avatar", "通过图片URL设置机器人头像", True, "account", "extends/SetQQAvatar", {"file": URL}, ("file",))
add("set_qq_avatar_inline", "通过内联PNG/JPEG/WebP头像更新机器人（解码后最多1MiB）", True, "account", "extends/SetQQAvatar",
    {"image_base64": text(1400000, 1, format="inline_image")}, ("image_base64",), bridge_dispatch="inline_avatar", upstream_actions=["set_qq_avatar"])
add("set_self_longnick", "修改机器人签名", True, "account", "extends/SetLongNick", {"longNick": text(1000)}, ("longNick",))
add("set_online_status", "设置机器人在线状态", True, "account", "extends/SetOnlineStatus",
    {"status": number(), "ext_status": number(), "battery_status": number(0, 100)}, ("status", "ext_status", "battery_status"))
add("set_diy_online_status", "设置自定义在线状态", True, "account", "extends/SetDiyOnlineStatus",
    {"face_id": number(), "face_type": number(0, 10, default=1), "wording": text(100)}, ("face_id", "wording"))
add("nc_get_user_status", "读取用户在线状态", False, "user", "extends/GetUserStatus", {"user_id": ID}, ("user_id",))
add("get_profile_like", "读取资料点赞列表", False, "user", "extends/GetProfileLike",
    {"user_id": ID, "start": number(0, 10000, default=0), "count": number(1, 100, default=10)}, ("user_id",))
add("ocr_image", "识别HTTP(S)图片文字", False, "none", "extends/OCRImage", {"image": URL}, ("image",))
add("get_ai_characters", "读取群AI语音音色", False, "group", "extends/GetAiCharacters",
    {"group_id": ID, "chat_type": number(1, 2, default=1)}, ("group_id",))
for action, mutating, source in [("get_ai_record", False, "group/GetAiRecord"), ("send_group_ai_record", True, "group/SendGroupAiRecord")]:
    add(action, "生成或发送群AI语音", mutating, "group", source,
        {"group_id": ID, "character": OPAQUE, "text": text(1000, 1)}, ("group_id", "character", "text"))
add("delete_qzone_msg", "删除本机器人QQ空间说说", True, "account", "extends/DeleteQzoneMsg", {"tid": OPAQUE}, ("tid",))

# Group albums and todos.
add("get_qun_album_list", "读取群相册", False, "group", "extends/GetQunAlbumList",
    {"group_id": ID, "attach_info": text(4096)}, ("group_id",))
add("get_group_album_media_list", "读取群相册图片", False, "group", "extends/GetGroupAlbumMediaList",
    {"group_id": ID, "album_id": OPAQUE, "attach_info": text(4096)}, ("group_id", "album_id"))
add("upload_image_to_qun_album", "上传HTTP(S)图片到群相册", True, "group", "extends/UploadImageToQunAlbum",
    {"group_id": ID, "album_id": OPAQUE, "album_name": text(100, 1), "file": URL}, ("group_id", "album_id", "album_name", "file"))
for action in ("set_group_album_media_like", "cancel_group_album_media_like"):
    add(action, "点赞或取消群相册点赞", True, "group", "extends/SetGroupAlbumMediaLike",
        {"group_id": ID, "album_id": OPAQUE, "batch_id": OPAQUE, "lloc": OPAQUE}, ("group_id", "album_id", "batch_id"))
add("del_group_album_media", "删除群相册图片", True, "group", "extends/DelGroupAlbumMedia",
    {"group_id": ID, "album_id": OPAQUE, "lloc": OPAQUE}, ("group_id", "album_id", "lloc"))
add("do_group_album_comment", "评论群相册图片", True, "group", "extends/DoGroupAlbumComment",
    {"group_id": ID, "album_id": OPAQUE, "lloc": OPAQUE, "content": text(1000, 1)}, ("group_id", "album_id", "lloc", "content"))
for action in ("set_group_todo", "complete_group_todo", "cancel_group_todo"):
    add(action, "设置、完成或取消消息对应的群待办", True, "group", "packet/BaseGroupTodoAction",
        {"group_id": ID, "message_id": MSG, "message_seq": ID}, ("group_id",), one_of_fields=["message_id", "message_seq"])

# Online/flash transfers. Public URL inputs are bridge parameters; files are
# staged with bounded downloads, not passed to upstream as invented URI support.
add("send_online_file", "暂存HTTP(S)文件后发送在线文件（最多8MiB，暂存24小时）", True, "user", "file/online/SendOnlineFile",
    {"user_id": ID, "file_url": URL, "file_name": NAME}, ("user_id", "file_url", "file_name"), bridge="stage_online_file")
add("send_online_folder", "从HTTP(S)文件构建并发送在线文件夹（1至4文件，各8MiB，暂存24小时）", True, "user", "file/online/SendOnlineFolder",
    {"user_id": ID, "folder_name": NAME, "files": {"type": "array", "format": "staged_files", "minItems": 1, "maxItems": 4}},
    ("user_id", "folder_name", "files"), bridge="stage_online_folder")
add("get_online_file_msg", "读取私聊在线文件消息", False, "user", "file/online/GetOnlineFileMessages", {"user_id": ID}, ("user_id",))
for action, source in [("receive_online_file", "ReceiveOnlineFile"), ("refuse_online_file", "RefuseOnlineFile"), ("cancel_online_file", "CancelOnlineFile")]:
    props = {"user_id": ID, "msg_id": OPAQUE}
    if action != "cancel_online_file": props["element_id"] = OPAQUE
    add(action, "接收、拒绝或取消在线文件", True, "user", "file/online/" + source, props, tuple(props))
add("create_flash_task", "暂存HTTP(S)文件后创建闪传（最多4个，各8MiB，暂存24小时）", True, "account", "file/flash/CreateFlashTask",
    {"files": {"type": "array", "format": "staged_files", "minItems": 1, "maxItems": 4}, "name": text(120, 1)}, ("files",), bridge="stage_flash_files")
add("send_flash_msg", "将已创建的闪传文件集发给好友或群", True, "none", "file/flash/SendFlashMsg",
    {"fileset_id": OPAQUE, "user_id": ID, "group_id": ID}, ("fileset_id",), one_of_fields=["user_id", "group_id"])
for action, description, source in [("get_share_link", "读取闪传分享链接", "GetShareLink"),
    ("get_fileset_info", "读取闪传文件集", "GetFilesetInfo"), ("get_flash_file_list", "读取闪传文件列表", "GetFlashFileList")]:
    add(action, description, False, "none", "file/flash/" + source, {"fileset_id": OPAQUE}, ("fileset_id",))
add("get_fileset_id", "由闪传分享码读取文件集ID", False, "none", "file/flash/GetFilesetIdByCode", {"share_code": text(2048, 1)}, ("share_code",))
add("get_flash_file_url", "读取闪传文件下载链接", False, "none", "file/flash/GetFlashFileUrl",
    {"fileset_id": OPAQUE, "file_name": NAME, "file_index": number(0, 10000)}, ("fileset_id",), one_of_fields=["file_name", "file_index"])
add("download_fileset", "将闪传文件集下载到NapCat默认目录", True, "account", "file/flash/DownloadFileset", {"fileset_id": OPAQUE}, ("fileset_id",))

# Extended actions. Schemas describe bridge inputs where upstream paths or raw
# Ark data would otherwise be required. Never accept arbitrary JSON messages.
for action, source in [("fetch_custom_face", "extends/FetchCustomFace"), ("fetch_custom_face_detail", "extends/CustomFace")]:
    add(action, "读取本账号收藏表情", False, "account", source, {"count": number(1, 100, default=48)})
add("add_custom_face", "从HTTP(S)图片添加收藏表情（最多8MiB）", True, "account", "extends/CustomFace",
    {"file_url": URL, "file_name": NAME, "emoji_id": NONNEG_ID, "package_id": NONNEG_ID,
     "is_mark_face": BOOL, "is_origin": {**BOOL, "default": True}}, ("file_url", "file_name"), bridge="stage_custom_face")
add("delete_custom_face", "删除收藏表情；ids来自fetch_custom_face_detail的resId", True, "account", "extends/CustomFace",
    {"ids": array(OPAQUE, 20)}, ("ids",))
add("set_custom_face_desc", "修改收藏表情描述", True, "account", "extends/CustomFace",
    {"emoji_id": NONNEG_ID, "res_id": OPAQUE, "md5": text(32, 32, pattern=r"^[a-fA-F0-9]{32}$"), "desc": text(200)},
    ("emoji_id", "res_id", "md5", "desc"))
add("fetch_emoji_like", "分页读取消息表情回应；cursor是分页游标，不是登录凭据", False, "message", "extends/FetchEmojiLike",
    {"message_id": MSG, "emojiId": NONNEG_ID, "emojiType": NONNEG_ID,
     "count": number(1, 100, default=20), "cursor": text(2048, default="")},
    ("message_id", "emojiId", "emojiType"), parameter_aliases={"cursor": "cookie"}, result_aliases={"cookie": "cursor"})
add("get_emoji_likes", "读取消息表情回应者（最多100人）", False, "message", "extends/GetEmojiLikes",
    {"message_id": MSG, "group_id": ID, "emoji_id": NONNEG_ID, "emoji_type": NONNEG_ID, "count": number(1, 100, default=20)},
    ("message_id", "emoji_id"), scope_targets=[{"field": "message_id", "target": "message"}, {"field": "group_id", "target": "group", "optional": True}])
for action, source in [("get_friends_with_category", "extends/GetFriendWithCategory"),
                       ("get_unidirectional_friend_list", "extends/GetUnidirectionalFriendList")]:
    add(action, "读取好友分类或单向好友", False, "account", source)
add("get_recent_contact", "读取最近会话及最后一条消息（账号级权限，最多100条）", False, "account", "user/GetRecentContact",
    {"count": number(1, 100, default=10)})
add("get_group_system_msg", "读取群系统申请通知（账号级权限，最多100条）", False, "account", "system/GetSystemMsg",
    {"count": number(1, 100, default=50)})
add("get_robot_uin_range", "读取QQ机器人账号号段", False, "account", "extends/GetRobotUinRange")
add("translate_en2zh", "通过QQ翻译英文单词（最多20词）", False, "none", "extends/TranslateEnWordToZn",
    {"words": array(text(100, 1), 20)}, ("words",))
add("nc_get_packet_status", "检查Packet后端；正常返回null，不可用时返回明确错误", False, "account", "packet/GetPacketStatus")
add("get_stranger_info", "读取指定用户资料", False, "user", "go-cqhttp/GetStrangerInfo",
    {"user_id": ID, "no_cache": {**BOOL, "default": False}}, ("user_id",))

for action, source in [("get_group_info_ex", "extends/GetGroupInfoEx"), ("get_group_detail_info", "group/GetGroupDetailInfo"),
                       ("get_group_shut_list", "group/GetGroupShutList"), ("get_group_at_all_remain", "go-cqhttp/GetGroupAtAllRemain")]:
    add(action, "读取群扩展资料、禁言名单或全体提及额度", False, "group", source, {"group_id": ID}, ("group_id",))
add("get_group_honor_info", "读取群荣誉", False, "group", "go-cqhttp/GetGroupHonorInfo",
    {"group_id": ID, "type": text(20, 1, enum=["all", "talkative", "performer", "legend", "strong_newbie", "emotion"], default="all")}, ("group_id",))
add("get_group_share_link", "读取群分享链接", False, "group", "extends/GetGroupShareLink",
    {"group_id": ID, "need_short_url": {**BOOL, "default": True}, "src_id": number(0, 10000, default=73)}, ("group_id",))
for action, source in [("get_group_ignore_add_request", "extends/GetGroupAddRequest"), ("get_group_ignored_notifies", "group/GetGroupIgnoredNotifies")]:
    add(action, "读取本账号群申请通知；处理申请仍需已接收事件flag", False, "account", source)
for action in ("set_group_sign", "send_group_sign"):
    add(action, "执行群签到", True, "group", "extends/SetGroupSign", {"group_id": ID}, ("group_id",))
add("set_group_kick_members", "批量移除指定群成员（最多20人）", True, "group", "extends/SetGroupKickMembers",
    {"group_id": ID, "user_id": array(ID, 20), "reject_add_request": {**BOOL, "default": False}}, ("group_id", "user_id"))
add("set_group_add_option", "设置加群方式及验证问题（add_type遵循QQ客户端取值）", True, "group", "extends/SetGroupAddOption",
    {"group_id": ID, "add_type": number(0, 10), "group_question": text(300), "group_answer": text(300)}, ("group_id", "add_type"))
add("set_group_robot_add_option", "设置群机器人成员与审核开关（0或1）", True, "group", "extends/SetGroupRobotAddOption",
    {"group_id": ID, "robot_member_switch": number(0, 1), "robot_member_examine": number(0, 1)}, ("group_id",),
    any_of_fields=["robot_member_switch", "robot_member_examine"])
add("set_group_search", "设置群搜索标志（上游未解释标志语义，0或1）", True, "group", "extends/SetGroupSearch",
    {"group_id": ID, "no_code_finger_open": number(0, 1), "no_finger_open": number(0, 1)}, ("group_id",),
    any_of_fields=["no_code_finger_open", "no_finger_open"])
add("set_group_member_invite_policy", "设置群成员邀请策略", True, "group", "group/SetGroupMemberInvitePolicy",
    {"group_id": ID, "policy": text(30, 1, enum=["disabled", "require_approval", "no_approval", "no_approval_under_100"])}, ("group_id", "policy"))
_permission_fields = ["allow_member_upload_album", "allow_member_temporary_session", "allow_member_create_group"]
add("set_group_member_permissions", "设置群成员相册、临时会话和建群权限", True, "group", "group/SetGroupMemberPermissions",
    {"group_id": ID, **{field: BOOL for field in _permission_fields}}, ("group_id",), any_of_fields=_permission_fields)
add("set_group_new_member_history_visibility", "设置新成员能否查看最近聊天记录", True, "group", "group/SetGroupNewMemberHistoryVisibility",
    {"group_id": ID, "visible": BOOL}, ("group_id", "visible"))

for action, target, source in [("get_friend_msg_history", "user", "go-cqhttp/GetFriendMsgHistory"),
                               ("get_group_msg_history", "group", "go-cqhttp/GetGroupMsgHistory")]:
    field = target + "_id"
    add(action, "读取指定会话历史消息（最多100条）", False, target, source,
        {field: ID, "message_seq": MSG, "count": number(1, 100, default=20), "reverse_order": {**BOOL, "default": False},
         "disable_get_url": {**BOOL, "default": False}, "parse_mult_msg": {**BOOL, "default": True}, "quick_reply": {**BOOL, "default": False}}, (field,))
for action, target in [("forward_friend_single_msg", "user"), ("forward_group_single_msg", "group")]:
    field = target + "_id"
    add(action, "转发一条已授权来源消息到已授权会话", True, target, "msg/ForwardSingleMsg",
        {field: ID, "message_id": MSG}, (field, "message_id"),
        scope_targets=[{"field": field, "target": target}, {"field": "message_id", "target": "message"}])

add("send_ark_share", "生成好友或群名片Ark内容（此官方动作不发送消息）", False, "none", "extends/ShareContact",
    {"user_id": ID, "group_id": ID}, one_of_fields=["user_id", "group_id"],
    scope_targets=[{"field": "user_id", "target": "user", "optional": True}, {"field": "group_id", "target": "group", "optional": True}])
add("send_group_ark_share", "生成群名片Ark内容（此官方动作不发送消息）", False, "group", "extends/ShareContact", {"group_id": ID}, ("group_id",))
_miniapp_fields = {"type": text(5, 1, enum=["bili", "weibo"]), "title": text(200, 1), "desc": text(1000),
                   "picUrl": URL, "jumpUrl": URL, "webUrl": URL}
_miniapp_required = ("type", "title", "desc", "picUrl", "jumpUrl")
add("get_mini_app_ark", "按bili/weibo模板生成小程序Ark内容（不发送；不接受原始Ark）", False, "none", "extends/GetMiniAppArk",
    _miniapp_fields, _miniapp_required)
_destination = {"target_user_id": ID, "target_group_id": ID}
_destination_scopes = [{"field": "target_user_id", "target": "user", "optional": True}, {"field": "target_group_id", "target": "group", "optional": True}]
add("send_contact_card", "生成并发送受限好友/群名片；名片来源与目标会话均需授权", True, "none", "extends/ShareContact",
    {**_destination, "contact_user_id": ID, "contact_group_id": ID},
    exclusive_field_groups=[["target_user_id", "target_group_id"], ["contact_user_id", "contact_group_id"]],
    scope_targets=_destination_scopes + [{"field": "contact_user_id", "target": "user", "optional": True}, {"field": "contact_group_id", "target": "group", "optional": True}],
    bridge_dispatch="contact_card", upstream_actions=["send_ark_share", "send_private_msg", "send_group_msg"])
add("send_miniapp_card", "按bili/weibo模板生成并发送小程序卡片；不接受任意JSON", True, "none", "extends/GetMiniAppArk",
    {**_destination, **_miniapp_fields}, _miniapp_required,
    one_of_fields=["target_user_id", "target_group_id"], scope_targets=_destination_scopes,
    bridge_dispatch="miniapp_card", upstream_actions=["get_mini_app_ark", "send_private_msg", "send_group_msg"])
add("click_inline_keyboard_button", "点击指定群消息内联按钮", True, "group", "extends/ClickInlineKeyboardButton",
    {"group_id": ID, "bot_appid": ID, "button_id": text(256, 1), "callback_data": text(2048, default=""), "msg_seq": NONNEG_ID},
    ("group_id", "bot_appid", "button_id", "msg_seq"))
add("create_collection", "创建文字收藏（content为纯文本）", True, "account", "extends/CreateCollection",
    {"content": text(4000, 1, format="plain_text"), "brief": text(200, 1)}, ("content", "brief"), parameter_aliases={"content": "rawData"})
add("get_collection_list", "读取本账号收藏列表", False, "account", "extends/GetCollectionList",
    {"category": number(0, 100, default=0), "count": number(1, 100, default=50)}, stringify_fields=["category", "count"])
for action, source in [("can_send_image", "system/CanSendImage"), ("can_send_record", "system/CanSendRecord")]:
    add(action, "读取上游声明的发送能力（v4.18.28固定返回yes=true，非实时验证）", False, "account", source)
for action, source, reason in [
    ("get_guild_list", "guild/GetGuildList", "NapCat v4.18.28频道列表处理函数未实现"),
    ("get_guild_service_profile", "guild/GetGuildProfile", "NapCat v4.18.28频道资料处理函数未实现"),
    ("get_online_clients", "go-cqhttp/GetOnlineClient", "NapCat v4.18.28仅触发设备查询并固定返回空数组，未实现实际结果"),
    ("download_file_stream", "stream/DownloadFileStream", "当前OneBot单响应传输不支持同echo多分片下载"),
    ("download_file_record_stream", "stream/DownloadFileRecordStream", "当前OneBot单响应传输不支持同echo多分片下载"),
    ("download_file_image_stream", "stream/DownloadFileImageStream", "当前OneBot单响应传输不支持同echo多分片下载"),
    ("upload_file_stream", "stream/UploadFileStream", "尚未接入隔离的上传分片会话及文件生命周期；请使用受控URL上传"),
]:
    add(action, reason, action == "upload_file_stream", "account", source, available=False, unavailable_reason=reason)

for action, source, kinds in [("get_file", "file/GetFile", ["file", "image", "record", "video"]), ("get_image", "file/GetImage", ["image"]),
                              ("get_record", "file/GetRecord", ["record"]), ("get_private_file_url", "file/GetPrivateFileUrl", ["file"])]:
    props = {"source_message_id": MSG, "file_id": RESOURCE_ID}
    if action == "get_record": props["out_format"] = text(4, 1, enum=["mp3", "amr", "wma", "m4a", "spx", "ogg", "wav", "flac"], default="mp3")
    add(action, "读取已授权来源消息的附件；file_id必须与该消息附件一致，超384KiB字符的base64省略并保留文件元数据", False, "message", source, props, ("source_message_id", "file_id"),
        target_field="source_message_id", bridge_only_fields=["source_message_id"],
        scope_targets=[{"field": "source_message_id", "target": "message"}],
        resource_fields=[{"field": "file_id", "message_field": "source_message_id", "kinds": kinds}], bounded_media_result=True)
add("send_qzone_msg", "发布QQ空间说说（正文最多2000字，最多9张HTTP(S)图）", True, "account", "extends/SendQzoneMsg",
    {"content": text(2000), "images": array(URL, 9, 0), "ugc_right": number(1, 128, enum=[1, 4, 16, 64, 128], default=4),
     "target_uins": array(ID, 100)}, ("content",), conditional_target_uins=True)
ACTION_CATALOG["send_qzone_msg"]["parameters"]["allOf"] = [
    {"if": {"properties": {"ugc_right": {"enum": [16, 128]}}, "required": ["ugc_right"]},
     "then": {"required": ["target_uins"]}, "else": {"not": {"required": ["target_uins"]}}},
    {"anyOf": [{"properties": {"content": {"pattern": r"\S"}}}, {"properties": {"images": {"minItems": 1}}, "required": ["images"]}]},
]
for action, source in [("set_restart", "system/SetRestart"), ("bot_exit", "extends/BotExit"), ("clean_cache", "system/CleanCache")]:
    add(action, "重启NapCat、退出QQ或清理QQ缓存；仅显式运维操作，结果不明不得重试", True, "account", source,
        operational=True, confirmation_unknown=(action == "clean_cache"))

# Publish complete JSON schemas as well as the shared runtime validator.
def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_segment_types = {"text": ("text", text(4000, 1)), "image": ("file", URL), "record": ("file", URL),
                  "video": ("file", URL), "reply": ("id", MSG), "at": ("qq", ID), "face": ("id", NONNEG_ID)}
_segment_items = {"oneOf": [_object({"type": {"const": kind}, "data": _object({field: schema})})
                            for kind, (field, schema) in _segment_types.items()]}
for _entry in ACTION_CATALOG.values():
    for _field in _entry["parameters"]["properties"].values():
        if _field.get("format") == "message_segments": _field["items"] = deepcopy(_segment_items)
        if _field.get("format") == "forward_nodes":
            _field["items"] = _object({"type": {"const": "node"}, "data": _object({"user_id": ID,
                "nickname": text(100, 1), "content": {"type": "array", "minItems": 1, "maxItems": 40, "items": deepcopy(_segment_items)}})})
        if _field.get("format") == "staged_files": _field["items"] = _object({"url": URL, "name": NAME})


def _segments(value, nodes=False):
    if not isinstance(value, list) or not 1 <= len(value) <= (20 if nodes else 40):
        raise ActionValidationError("消息段或转发节点数量无效")
    result = []
    for segment in value:
        if not isinstance(segment, dict) or set(segment) != {"type", "data"} or not isinstance(segment["data"], dict):
            raise ActionValidationError("消息段必须仅包含 type 和 data")
        kind, data = segment["type"], segment["data"]
        if nodes:
            if kind != "node" or set(data) != {"user_id", "nickname", "content"}:
                raise ActionValidationError("转发节点仅接受 user_id、nickname、content")
            cleaned = {"user_id": _value(data["user_id"], ID), "nickname": _value(data["nickname"], text(100, 1)),
                       "content": _segments(data["content"])}
        else:
            formats = _segment_types
            if kind not in formats:
                raise ActionValidationError("不支持此消息段；禁止 JSON/XML/CQ 代码和嵌套转发")
            field, schema = formats[kind]
            if set(data) != {field}: raise ActionValidationError("消息段包含不支持的字段")
            cleaned = {field: _value(data[field], schema)}
        result.append({"type": kind, "data": cleaned})
    return result


def _value(value, schema):
    kind = schema.get("type")
    fmt = schema.get("format")
    if fmt in {"id", "message_id", "nonnegative_id"}:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ActionValidationError("ID 必须是数字或数字字符串")
        value = str(value)
        if not re.fullmatch(r"-?[0-9]{1,20}" if fmt == "message_id" else r"[0-9]{1,20}", value):
            raise ActionValidationError("ID 格式无效")
        if fmt == "id" and int(value) <= 0: raise ActionValidationError("QQ号或标识必须为正整数")
        return value
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or not schema["minimum"] <= value <= schema["maximum"]:
            raise ActionValidationError("数字参数超出允许范围")
    elif kind == "boolean":
        if not isinstance(value, bool): raise ActionValidationError("布尔参数必须是 true 或 false")
    elif kind == "string":
        # Forward resource IDs may be numeric or opaque strings.
        if fmt == "opaque_id" and isinstance(value, int) and not isinstance(value, bool): value = str(value)
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 4096) or "\x00" in value:
            raise ActionValidationError("字符串参数为空、过长或含无效字符")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ActionValidationError("字符串格式无效")
        if fmt == "http_url":
            try:
                url = urlsplit(value)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None or url.password is not None or "\\" in value or any(c.isspace() for c in value):
                    raise ValueError()
                url.port
                host = url.hostname.rstrip(".").lower()
                if host == "localhost" or host.endswith((".localhost", ".local")): raise ValueError()
                try: address = ipaddress.ip_address(host)
                except ValueError: address = None
                if address is not None and not address.is_global: raise ValueError()
            except ValueError:
                raise ActionValidationError("文件/图片仅支持不带凭据的公网 HTTP(S) URL") from None
        elif fmt == "filename":
            if value.casefold() in {".", "..", ".expires"} or any(c in value for c in '/\\:*?"<>|\r\n') or any(ord(c) < 32 for c in value) or value.endswith((".", " ")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", value):
                raise ActionValidationError("文件名必须是单独的文件名，不能包含路径")
        elif fmt == "opaque_id" and any(c in value for c in "\r\n"):
            raise ActionValidationError("标识含无效控制字符")
        elif fmt == "plain_text" and (value.lstrip().startswith(("{", "[", "<")) or "[CQ:" in value or re.search(r"(?i)(?:file|base64)://|[a-z]:[/\\]", value)):
            raise ActionValidationError("收藏只接受纯文本，不接受结构化代码或本地路径")
        elif fmt == "inline_image":
            try:
                decoded = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error):
                raise ActionValidationError("头像必须是有效的标准Base64") from None
            valid_image = decoded.startswith(b"\x89PNG\r\n\x1a\n") or decoded.startswith(b"\xff\xd8\xff") or (
                len(decoded) >= 12 and decoded[:4] == b"RIFF" and decoded[8:12] == b"WEBP")
            if not valid_image or len(decoded) > 1024 * 1024:
                raise ActionValidationError("头像仅支持PNG/JPEG/WebP，解码后最多1MiB")
    elif kind == "array":
        if fmt in {"message_segments", "forward_nodes"}: return _segments(value, nodes=fmt == "forward_nodes")
        if fmt == "staged_files":
            if not isinstance(value, list) or not 1 <= len(value) <= 4: raise ActionValidationError("闪传一次只支持1至4个文件")
            result = []
            for item in value:
                if not isinstance(item, dict) or set(item) != {"url", "name"}: raise ActionValidationError("闪传文件仅接受 url 和 name")
                result.append({"url": _value(item["url"], URL), "name": _value(item["name"], NAME)})
            if len({item["name"].casefold() for item in result}) != len(result): raise ActionValidationError("闪传文件名不能重复")
            return result
        if not fmt and "items" in schema:
            if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
                raise ActionValidationError("数组长度超出允许范围")
            return [_value(item, schema["items"]) for item in value]
        raise ActionValidationError("不支持此数组格式")
    else:
        raise ActionValidationError("不支持此参数类型")
    if "enum" in schema and value not in schema["enum"]: raise ActionValidationError("参数不在允许的枚举值中")
    return value


def normalize_action(action, params):
    """Validate and normalize only explicitly cataloged actions and fields."""
    if not isinstance(action, str) or action not in ACTION_CATALOG:
        raise ActionValidationError("此 NapCat 动作不在允许列表中", "UNSUPPORTED_ACTION")
    entry = ACTION_CATALOG[action]
    if entry.get("available") is False:
        raise ActionValidationError(entry["unavailable_reason"], "UNSUPPORTED_ACTION")
    if not isinstance(params, dict): raise ActionValidationError("params 必须是对象")
    schema = entry["parameters"]
    if set(params) - set(schema["properties"]): raise ActionValidationError("参数包含未知或禁止字段")
    if any(field not in params for field in schema["required"]): raise ActionValidationError("缺少必需参数")
    result = {}
    for field, field_schema in schema["properties"].items():
        if field in params: result[field] = _value(params[field], field_schema)
        elif "default" in field_schema: result[field] = deepcopy(field_schema["default"])
    if entry.get("one_of_fields") and sum(field in result for field in entry["one_of_fields"]) != 1:
        raise ActionValidationError("这些参数必须且只能选择一个：" + ", ".join(entry["one_of_fields"]))
    if entry.get("any_of_fields") and not any(field in result for field in entry["any_of_fields"]):
        raise ActionValidationError("至少需要提供一个设置项")
    for group in entry.get("exclusive_field_groups", []):
        if sum(field in result for field in group) != 1:
            raise ActionValidationError("这些参数必须且只能选择一个：" + ", ".join(group))
    if action == "send_qzone_msg":
        if not result["content"].strip() and not result.get("images"):
            raise ActionValidationError("说说必须包含正文或图片")
        if result["ugc_right"] in {16, 128} and not result.get("target_uins"):
            raise ActionValidationError("部分好友可见或不可见必须指定target_uins")
        if result["ugc_right"] not in {16, 128} and "target_uins" in result:
            raise ActionValidationError("当前查看权限不接受target_uins")
    if action == "get_forward_msg" and "id" in result: result["message_id"] = result.pop("id")
    return result
