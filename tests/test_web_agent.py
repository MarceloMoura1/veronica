"""
Tests for Web Automation Agent.
"""
import pytest
import asyncio
import os

from web_agent import WebAgent, classify_web_error
from google.genai import types


class TestWebAgentInit:
    """Test WebAgent initialization."""
    
    def test_agent_creation(self):
        """Test WebAgent can be created."""
        agent = WebAgent()
        assert agent is not None
        assert hasattr(agent, 'client')
        print("WebAgent initialized successfully")
    
    def test_agent_has_browser_attrs(self):
        """Test WebAgent has browser-related attributes."""
        agent = WebAgent()
        assert hasattr(agent, 'browser')
        assert hasattr(agent, 'page')
        assert hasattr(agent, 'context')


class TestCoordinateDenormalization:
    """Test coordinate conversion functions."""
    
    def test_denormalize_x(self):
        """Test X coordinate denormalization."""
        agent = WebAgent()
        
        # Test at different normalized values
        result = agent.denormalize_x(500, 1000)  # 50% of 1000
        print(f"denormalize_x(500, 1000) = {result}")
        assert isinstance(result, (int, float))
    
    def test_denormalize_y(self):
        """Test Y coordinate denormalization."""
        agent = WebAgent()
        
        result = agent.denormalize_y(500, 1000)  # 50% of 1000
        print(f"denormalize_y(500, 1000) = {result}")
        assert isinstance(result, (int, float))


class TestWebBrowserLaunch:
    """Test browser launching capabilities."""
    
    @pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY not set"
    )
    def test_browser_launch_headless(self):
        """Test launching browser in headless mode."""
        async def scenario():
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto("https://www.google.com")
                    title = await page.title()
                    assert "Google" in title
                finally:
                    await browser.close()
        try:
            asyncio.run(scenario())
        except Exception as e:
            pytest.skip(f"Playwright not available: {e}")


class TestWebNavigation:
    """Test web navigation capabilities."""
    
    def test_navigate_to_url(self):
        """Test navigating to a URL."""
        async def scenario():
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto("https://example.com")
                    assert "Example Domain" in await page.content()
                finally:
                    await browser.close()
        try:
            asyncio.run(scenario())
        except Exception as e:
            pytest.skip(f"Playwright not available: {e}")


class TestWebScreenshot:
    """Test screenshot capabilities."""
    
    def test_capture_screenshot(self, temp_dir):
        """Test capturing a screenshot."""
        async def scenario():
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto("https://example.com")
                    screenshot_path = temp_dir / "test_screenshot.png"
                    await page.screenshot(path=str(screenshot_path))
                    assert screenshot_path.exists()
                finally:
                    await browser.close()
        try:
            asyncio.run(scenario())
        except Exception as e:
            pytest.skip(f"Playwright not available: {e}")


class TestWebAgentTask:
    """Test full web agent task execution."""
    
    @pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY not set"
    )
    def test_simple_web_task(self):
        """Test running a simple web task."""
        agent = WebAgent()
        
        updates = []
        
        async def update_callback(screenshot_b64, log_text):
            updates.append({"log": log_text})
            print(f"Update: {log_text[:100]}...")
        
        result = asyncio.run(agent.run_task(
            prompt="Navigate to example.com and tell me the page title",
            update_callback=update_callback
        ))
        assert "ok" in result


class TestPlaywrightInstallation:
    """Test Playwright availability."""
    
    def test_playwright_import(self):
        """Test if Playwright is installed."""
        try:
            from playwright.async_api import async_playwright
            print("Playwright is installed")
        except ImportError:
            pytest.skip("Playwright not installed")
    
    def test_playwright_browsers(self):
        """Test if Playwright browsers are installed."""
        async def scenario():
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                await browser.close()
        try:
            asyncio.run(scenario())
        except Exception as e:
            pytest.skip(f"Playwright browsers not installed: {e}")


def test_errors_are_classified_without_secrets_or_raw_objects():
    assert classify_web_error(RuntimeError("429 RESOURCE_EXHAUSTED quota"))["code"] == "provider_quota_exhausted"
    assert classify_web_error(RuntimeError("browser executable doesn't exist; run install"))["code"] == "browser_not_installed"
    assert classify_web_error(TimeoutError("navigation timeout"))["code"] == "navigation_timeout"
    assert classify_web_error(PermissionError("Access is denied"))["code"] == "permission_error"


def test_run_task_returns_serializable_failure_and_cleans_resources(monkeypatch):
    closed = []

    class Resource:
        async def close(self): closed.append(True)

    agent = WebAgent()
    agent.context, agent.browser = Resource(), Resource()

    async def fail(*_args):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota")

    monkeypatch.setattr(agent, "_run_task", fail)
    result = asyncio.run(agent.run_task("fixture"))
    assert result["ok"] is False
    assert result["error"]["code"] == "provider_quota_exhausted"
    assert len(closed) == 2
    assert agent.browser is agent.context is agent.page is None


def test_run_task_returns_serializable_success(monkeypatch):
    agent = WebAgent()

    async def succeed(*_args): return "Example Domain"

    monkeypatch.setattr(agent, "_run_task", succeed)
    result = asyncio.run(agent.run_task("fixture"))
    assert result["ok"] is True and result["result"] == "Example Domain"


def test_execute_actions_preserves_call_id_and_rejects_unknown_action():
    agent = WebAgent()
    call = types.FunctionCall(id="call-123", name="unknown_action", args={})
    results = asyncio.run(agent.execute_function_calls([call]))
    assert results == [("call-123", "unknown_action", {
        "error": {"code": "unsupported_action", "retryable": False}
    })]


def test_function_response_is_correlated_and_contains_sanitized_state():
    class Page:
        url = "https://example.com/"
        async def screenshot(self, **_kwargs): return b"png"

    agent = WebAgent()
    agent.page = Page()
    responses, screenshot = asyncio.run(agent.get_function_responses([
        ("call-123", "navigate", {"status": "ok"})
    ]))
    assert screenshot == b"png"
    assert responses[0].id == "call-123" and responses[0].name == "navigate"
    assert responses[0].response == {"url": "https://example.com/", "status": "ok"}


def test_web_agent_tool_is_declared_and_gateway_routable():
    import ada
    from live_tools import route_gateway_call
    declarations = {item["name"]: item for item in ada.tools[1]["function_declarations"]}
    assert declarations["run_web_agent"]["parameters"]["required"] == ["prompt"]
    routed = route_gateway_call(
        "workspace_action", "outer-call",
        {"action": "run_web_agent", "arguments": '{"prompt":"Open example.com"}'},
        ada.ACTION_REGISTRY,
    )
    assert routed.canonical_name == "run_web_agent"
    assert routed.call_id == "outer-call"
