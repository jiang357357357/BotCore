import asyncio
import unittest

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI


class MonCoreErrorResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_resolves_matching_request_with_user_message(self):
        api = MonCoreAPI.__new__(MonCoreAPI)
        request_id = "request-1"
        future = asyncio.get_running_loop().create_future()
        api.pending_requests = {request_id: future}

        await api._handle_error(
            {
                "command": "error",
                "data": {
                    "request_id": request_id,
                    "code": "AI_NOT_CONFIGURED",
                    "message": "No usable AI entity",
                    "user_message": "请先配置模型。",
                },
            }
        )

        result = await asyncio.wait_for(future, timeout=0.1)
        self.assertEqual(result["content"], "请先配置模型。")
        self.assertEqual(result["error_code"], "AI_NOT_CONFIGURED")
        self.assertNotIn(request_id, api.pending_requests)

    async def test_error_without_user_message_still_finishes_request(self):
        api = MonCoreAPI.__new__(MonCoreAPI)
        request_id = "request-2"
        future = asyncio.get_running_loop().create_future()
        api.pending_requests = {request_id: future}

        await api._handle_error(
            {"command": "error", "data": {"request_id": request_id, "message": "failed"}}
        )

        result = await asyncio.wait_for(future, timeout=0.1)
        self.assertEqual(result["content"], "")
        self.assertEqual(result["error_code"], "UNKNOWN")
        self.assertNotIn(request_id, api.pending_requests)


if __name__ == "__main__":
    unittest.main()
