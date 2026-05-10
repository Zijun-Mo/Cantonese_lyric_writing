import unittest

from src.frontend.dev_server import _json_response


class _BrokenHeaderHandler:
    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        pass

    def end_headers(self):
        raise BrokenPipeError()


class _BodyWriter:
    def __init__(self):
        self.body = b""

    def write(self, data):
        self.body += data


class _OkHandler:
    def __init__(self):
        self.headers = {}
        self.wfile = _BodyWriter()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass


class JsonResponseTest(unittest.TestCase):
    def test_broken_pipe_during_headers_is_not_raised(self):
        with self.assertLogs("frontend", level="WARNING"):
            ok = _json_response(_BrokenHeaderHandler(), 200, {"ok": True})

        self.assertFalse(ok)

    def test_json_response_returns_true_after_write(self):
        handler = _OkHandler()

        ok = _json_response(handler, 200, {"ok": True})

        self.assertTrue(ok)
        self.assertEqual(handler.status, 200)
        self.assertIn(b'"ok": true', handler.wfile.body)


if __name__ == "__main__":
    unittest.main()
