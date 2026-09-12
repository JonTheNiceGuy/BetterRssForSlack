from playwright._impl._errors import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def test_login_as_admin_reaches_dashboard_with_admin_badge(live_app):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            content = _attempt_login(browser, live_app, retries=2)
            assert "(admin)" in content
        finally:
            browser.close()


def _attempt_login(browser, live_app: str, *, retries: int) -> str:
    # Chromium in this environment occasionally reports
    # net::ERR_NETWORK_CHANGED / a stalled navigation when the host's Docker
    # network churns (each test run creates/tears down a fresh
    # testcontainers network) -- unrelated to the OIDC flow itself, which a
    # plain requests-based walkthrough of the identical redirect chain
    # completes reliably every time. Retry the browser navigation rather
    # than accept a flaky suite for an environment artifact.
    last_error = None
    for _ in range(retries):
        page = browser.new_page()
        try:
            page.goto(f"{live_app}/login")
            page.get_by_role("button", name="Login as admin").click()
            page.wait_for_url(f"{live_app}/**")
            return page.content()
        except PlaywrightError as exc:
            last_error = exc
        finally:
            page.close()
    raise last_error
