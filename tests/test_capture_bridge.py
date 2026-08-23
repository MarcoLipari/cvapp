import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from capture_bridge import CaptureBridge


class CaptureBridgeTests(unittest.TestCase):
    def setUp(self):
        self.captured = []
        self.ready = threading.Event()
        self.bridge = CaptureBridge(lambda payload: (self.captured.append(payload), self.ready.set()))
        self.endpoint = self.bridge.start()

    def tearDown(self):
        self.bridge.stop()

    def request(self, payload, token=None):
        request = Request(self.endpoint, data=json.dumps(payload).encode("utf-8"), method="POST")
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-CV-Manager-Token", token)
        return urlopen(request, timeout=2)

    def test_accepts_authenticated_valid_payload(self):
        with self.request({"company": "Acme", "role": "Engineer", "posting_url": "https://example.com/jobs/1"}, self.bridge.token) as response:
            self.assertEqual(response.status, 201)
        self.assertTrue(self.ready.wait(1))
        self.assertEqual(self.captured[0]["company"], "Acme")
        self.assertEqual(self.captured[0]["status"], "Applied")

    def test_rejects_missing_token_and_bad_payload(self):
        with self.assertRaises(HTTPError) as unauthorized:
            self.request({"company": "Acme", "role": "Engineer"})
        self.assertEqual(unauthorized.exception.code, 401)
        unauthorized.exception.close()
        with self.assertRaises(HTTPError) as invalid:
            self.request({"company": "Acme"}, self.bridge.token)
        self.assertEqual(invalid.exception.code, 400)
        invalid.exception.close()


if __name__ == "__main__":
    unittest.main()
