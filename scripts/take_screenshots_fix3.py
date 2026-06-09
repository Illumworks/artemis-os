"""
Fix 3 dedicated screenshot: shows toolbar with intent input visible.
"""
import time
from playwright.sync_api import sync_playwright
import json, urllib.request

BASE = "http://localhost:8765"
OUT = "briefs/screenshots"


def screenshot(page, name):
    path = f"{OUT}/{name}.png"
    page.screenshot(path=path, full_page=False)
    print(f"  saved: {path}")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        try:
            with urllib.request.urlopen(f"{BASE}/api/writing-studio/overview") as r:
                data = json.loads(r.read())
                drafts = data.get("drafts", [])
                if drafts:
                    page.goto(f"{BASE}/#writing-studio?draft={drafts[0]['id']}", wait_until="networkidle")
                    time.sleep(2)
        except Exception:
            page.goto(f"{BASE}/#writing-studio", wait_until="networkidle")
            time.sleep(2)

        pm = page.locator(".ProseMirror").first
        if pm.count() == 0:
            print("No editor found")
            browser.close()
            return

        # Type some content
        pm.click()
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(
            "The product positioning document lacks clarity. "
            "Too jargony, too long, wrong audience assumption. "
            "Our customers are school principals, not EdTech VCs.\n\n"
            "We need plain language, specifics, and a clear call-to-action."
        )
        time.sleep(0.3)

        # Triple-click to select the first paragraph
        pm.click(click_count=3, position={"x": 200, "y": 20})
        time.sleep(1.0)  # wait longer for selectionchange

        toolbar = page.locator(".cv5-sel-toolbar")
        if toolbar.is_visible() if toolbar.count() > 0 else False:
            print("  Toolbar visible!")
            screenshot(page, "composer-polish-3a-toolbar-with-intent-input")

            # Type in the intent input
            intent = page.locator(".cv5-sel-intent-input")
            if intent.count() > 0:
                intent.click()
                time.sleep(0.2)
                intent.fill("too jargony — rewrite for principals, not VCs")
                time.sleep(0.3)
                screenshot(page, "composer-polish-3b-intent-typed")
                print("  3b saved")
        else:
            print("  Toolbar not visible after triple-click — trying keyboard selection")
            pm.click(position={"x": 5, "y": 20})
            page.keyboard.press("Home")
            page.keyboard.press("Shift+End")
            time.sleep(1.0)
            screenshot(page, "composer-polish-3a-toolbar-with-intent-input")

            intent = page.locator(".cv5-sel-intent-input")
            if intent.is_visible() if intent.count() > 0 else False:
                intent.fill("too jargony — rewrite for principals, not VCs")
                time.sleep(0.3)
                screenshot(page, "composer-polish-3b-intent-typed")
                print("  3b saved via keyboard selection")

        browser.close()


if __name__ == "__main__":
    main()
