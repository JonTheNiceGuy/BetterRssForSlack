from playwright.sync_api import sync_playwright


def test_login_as_admin_reaches_dashboard_with_admin_badge(live_app):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(f"{live_app}/login")
            page.get_by_role("button", name="Login as admin").click()
            page.wait_for_url(f"{live_app}/**")

            assert "(admin)" in page.content()
        finally:
            browser.close()
