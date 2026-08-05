from __future__ import annotations

import builtins

import pytest

from apex_ua_rotator.smoke import ScrapySmokeUnavailable, run_scrapy_smoke


def test_smoke_reports_missing_scrapy_cleanly(monkeypatch):
    original_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "scrapy" or name.startswith("scrapy."):
            raise ImportError("blocked for test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ScrapySmokeUnavailable, match="Scrapy is not installed"):
        run_scrapy_smoke()
