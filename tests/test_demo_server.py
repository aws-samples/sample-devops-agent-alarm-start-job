# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Request checks of the local status page. Run: python3 -m unittest discover tests"""
from __future__ import annotations

import http.client
import json
import threading
import unittest

import demo_server


class StubState:
    """Stands in for DemoState so no AWS call is made."""

    config = {"account_id": "000000000000", "region": "us-east-1", "agentSpaceId": "space"}
    profile = "test"

    def __init__(self):
        self.chat_calls = 0

    def chat(self, question: str) -> str:
        self.chat_calls += 1
        return "answer"

    def list_investigations(self, limit: int = 5) -> list[dict]:
        return []


class DemoServerRequestChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = StubState()
        cls.server = demo_server.DemoServer(("127.0.0.1", 0), demo_server.Handler, cls.state)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        sent = {"Host": f"127.0.0.1:{self.port}"}
        sent.update(headers or {})
        sent = {k: v for k, v in sent.items() if v is not None}
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, value in sent.items():
            conn.putheader(key, value)
        data = body.encode() if isinstance(body, str) else body
        if data is not None:
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, payload

    def post_chat(self, body='{"question": "q"}', **headers):
        headers.setdefault("Content-Type", "application/json")
        return self.request("POST", "/api/chat", body, headers)

    def test_same_origin_post_is_accepted(self):
        before = self.state.chat_calls
        status, body = self.post_chat(Origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["answer"], "answer")
        self.assertEqual(self.state.chat_calls, before + 1)

    def test_localhost_host_is_accepted(self):
        status, _ = self.request("GET", "/api/config", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_rebound_host_is_rejected(self):
        for host in (f"attacker.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}"):
            with self.subTest(host=host):
                status, _ = self.request("GET", "/api/config", headers={"Host": host})
                self.assertEqual(status, 403)

    def test_missing_host_is_rejected(self):
        status, _ = self.request("GET", "/api/config", headers={"Host": None})
        self.assertEqual(status, 403)

    def test_foreign_origin_is_rejected(self):
        before = self.state.chat_calls
        for origin in ("https://attacker.example", "null", f"https://127.0.0.1:{self.port}"):
            with self.subTest(origin=origin):
                status, _ = self.post_chat(Origin=origin)
                self.assertEqual(status, 403)
        self.assertEqual(self.state.chat_calls, before)

    def test_text_plain_post_is_rejected(self):
        before = self.state.chat_calls
        for content_type in ("text/plain", None, "application/x-www-form-urlencoded"):
            with self.subTest(content_type=content_type):
                status, _ = self.post_chat(**{"Content-Type": content_type})
                self.assertEqual(status, 415)
        self.assertEqual(self.state.chat_calls, before)

    def test_non_object_body_is_rejected(self):
        for body in ('["question"]', '"q"', "1", "null"):
            with self.subTest(body=body):
                status, _ = self.post_chat(body=body)
                self.assertEqual(status, 400)

    def test_invalid_content_length_is_rejected(self):
        status, _ = self.request(
            "POST", "/api/chat", None,
            {"Content-Type": "application/json", "Content-Length": "abc"},
        )
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
