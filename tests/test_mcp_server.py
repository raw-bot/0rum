"""MCP stdio protocol behaviour of orum_mcp.server (JSON-RPC 2.0)."""

import unittest

from orum_mcp import server


def rpc(method, params=None, msg_id=1):
    message = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        message["id"] = msg_id
    if params is not None:
        message["params"] = params
    return server.handle_message(message)


class ProtocolTests(unittest.TestCase):
    def test_initialize_negotiates_supported_version(self):
        response = rpc("initialize", {"protocolVersion": "2025-06-18"})
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "orum-mcp")
        self.assertIn("tools", result["capabilities"])

    def test_initialize_falls_back_on_unknown_version(self):
        response = rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")

    def test_ping_and_initialized_notification(self):
        self.assertEqual(rpc("ping")["result"], {})
        self.assertIsNone(rpc("notifications/initialized", msg_id=None))

    def test_tools_list_exposes_all_sixteen_read_only_tools(self):
        result = rpc("tools/list")["result"]
        names = [tool["name"] for tool in result["tools"]]
        self.assertEqual(len(names), 16)
        for tool in result["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_unknown_method_returns_method_not_found(self):
        response = rpc("resources/list")
        self.assertEqual(response["error"]["code"], server.JSONRPC_METHOD_NOT_FOUND)

    def test_unknown_notification_is_ignored(self):
        self.assertIsNone(rpc("something/odd", msg_id=None))

    def test_invalid_jsonrpc_envelope(self):
        response = server.handle_message({"id": 7, "method": "ping"})
        self.assertEqual(response["error"]["code"], server.JSONRPC_INVALID_REQUEST)

    def test_tools_call_unknown_tool_is_structured_tool_error(self):
        response = rpc("tools/call", {"name": "place_order", "arguments": {}})
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("unknown tool", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
