"""
tests/test_checkdomain.py - Unit tests for the checkdomain command handler.
Run with: py -3 -m pytest tests/ -q
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "commands"))
from checkdomain import handle


class TestCheckdomain(unittest.IsolatedAsyncioTestCase):

    async def test_missing_domain(self):
        result = await handle({})
        assert result["status"] == "error"
        assert result["domain"] == ""

    async def test_invalid_domain(self):
        result = await handle({"domain": "not_a_domain"})
        assert result["status"] == "error"

    async def test_available_domain(self):
        def fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = await handle({"domain": "definitely-not-registered-xyz.com"})
        assert result["status"] == "available"

    async def test_taken_domain(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = await handle({"domain": "example.com"})
        assert result["status"] == "taken"

    async def test_network_error(self):
        import urllib.error

        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")
        ):
            result = await handle({"domain": "example.com"})
        assert result["status"] == "error"


if __name__ == "__main__":
    unittest.main()
