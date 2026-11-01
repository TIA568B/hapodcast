"""Pytest configuration for the Daily Podcast Queue tests.

Uses pytest-homeassistant-custom-component so the integration module (which
imports Home Assistant at module scope) can be imported and its pure helper
functions exercised directly.
"""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading this custom integration during tests."""
    yield
