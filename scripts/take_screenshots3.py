"""
Final acceptance screenshots — uses mouse for selection to ensure reliable selectionchange events.
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
    return path


def navigate_to_composer(page):
    """Navigate to Writing Studio and ensure the composer is open with a draft."""
    # Try the API to find an existing draft
    try:
        with urllib.request.urlopen(f"{BASE}/api/writing-studio/overview") as r:
            data = json.loads(r.read())
            drafts = data.get("drafts", [])
            if drafts:
                draft_id = drafts[0]["id"]
                page.goto(f"{BASE}/#writing-studio?draft={draft_id}", wait_until="networkidle")
                time.sleep(2)
                return
    except Exception as e:
        print(f"  overview fetch: {e}")

    page.goto(f"{BASE}/#writing-studio", wait_until="networkidle")
    time.sleep(2)


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        print("Loading Writing Studio...")
        page.goto(f"{BASE}/", wait_until="networkidle")
        time.sleep(1)
        navigate_to_composer(page)

        editor_host = page.locator('[data-cv5="editor"]')
        pm = page.locator(".ProseMirror").first

        if editor_host.count() == 0:
            print("No composer editor found.")
            screenshot(page, "composer-polish-0-no-editor")
            browser.close()
            return

        print("Composer found. Injecting test content...")

        # Inject multi-paragraph content
        pm.click()
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        time.sleep(0.2)
        page.keyboard.type(
            "Customer onboarding is broken. Three separate forms, no progress indication, "
            "and a 48-hour approval window that nobody explains. We lose 30% of sign-ups here.\n\n"
            "The product team has shipped a new onboarding flow. Single form, instant verification, "
            "progress bar at every step. Early beta shows 68% drop in abandonment. "
            "We need to announce this loudly to the audience that felt the old pain.\n\n"
            "Key message: Amira gets students into the right placement in under 5 minutes, "
            "guaranteed. No paperwork. No waiting. Teachers can focus on teaching."
        )
        time.sleep(0.5)

        # Dismiss selection
        pm.click()
        page.keyboard.press("Meta+Home")
        time.sleep(0.3)

        # ── Fix 1: Single-word selection ─────────────────────────────────────
        print("\n--- Fix 1: Toolbar on selections ---")
        # Get bounding box of ProseMirror for mouse selection
        pm_box = pm.bounding_box()
        if not pm_box:
            print("  Cannot get PM bounding box")
        else:
            # Single word: double-click on "broken"
            pm.dblclick(position={"x": 100, "y": 20})
            time.sleep(0.6)
            toolbar = page.locator(".cv5-sel-toolbar")
            print(f"  Single word toolbar visible: {toolbar.is_visible() if toolbar.count() > 0 else False}")
            screenshot(page, "composer-polish-1a-single-word-selection")

            # Full paragraph: triple-click first paragraph
            pm.click(click_count=3, position={"x": pm_box["width"] // 2, "y": 20})
            time.sleep(0.6)
            tb_vis = toolbar.is_visible() if toolbar.count() > 0 else False
            print(f"  Full paragraph toolbar visible: {tb_vis}")
            screenshot(page, "composer-polish-1b-full-paragraph-selection")

            # Two paragraphs: click start, shift-click end of second para
            pm.click(position={"x": 5, "y": 20})
            time.sleep(0.2)
            # Shift-click somewhere in the second paragraph
            page.keyboard.down("Shift")
            pm.click(position={"x": pm_box["width"] - 20, "y": 65})
            page.keyboard.up("Shift")
            time.sleep(0.8)
            tb_vis2 = toolbar.is_visible() if toolbar.count() > 0 else False
            print(f"  Two-paragraph toolbar visible: {tb_vis2}")
            screenshot(page, "composer-polish-1c-two-paragraph-selection")

        # ── Fix 3: What should change? input ─────────────────────────────────
        print("\n--- Fix 3: Intent input in toolbar ---")
        # Select second paragraph word
        pm.click(position={"x": 100, "y": 60})
        page.keyboard.press("Home")
        page.keyboard.press("Shift+End")
        time.sleep(0.6)
        toolbar = page.locator(".cv5-sel-toolbar")
        if toolbar.is_visible() if toolbar.count() > 0 else False:
            intent = page.locator(".cv5-sel-intent-input")
            placeholder_visible = intent.is_visible() if intent.count() > 0 else False
            print(f"  Intent input visible: {placeholder_visible}")
            screenshot(page, "composer-polish-3a-toolbar-with-intent-input")
            # Type critique
            if intent.count() > 0:
                intent.fill("make it shorter and punchier")
                time.sleep(0.3)
                screenshot(page, "composer-polish-3b-intent-typed")
                print("  3b: intent typed")
                intent.fill("")  # reset
        else:
            print("  Toolbar not visible — taking state screenshot")
            screenshot(page, "composer-polish-3-toolbar-state")

        # ── Fix 2: Deliverable preview via injected message ──────────────────
        print("\n--- Fix 2: Deliverable preview ---")

        fake_deliverable = (
            "Customer onboarding is now instant.\n\n"
            "Amira has rebuilt the onboarding experience from the ground up: "
            "one form, real-time verification, and a clear progress indicator at every step. "
            "Early results show a 68% drop in abandonment.\n\n"
            "Students are now placed correctly in under 5 minutes — "
            "no paperwork, no waiting, no confusion. Teachers can focus on teaching."
        )

        page.evaluate("""(deliverable) => {
            const thread = document.querySelector('[data-cv5="chat-thread"]');
            if (!thread) return;
            const div = document.createElement('div');
            div.className = 'cv5-msg assistant';

            const roleEl = document.createElement('div');
            roleEl.className = 'cv5-msg-role';
            roleEl.textContent = 'Amira';

            const bubEl = document.createElement('div');
            bubEl.className = 'cv5-msg-bub';
            bubEl.textContent = 'Here is the revised document:';

            const rowEl = document.createElement('div');
            rowEl.className = 'cv5-msg-apply-row';

            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'cv5-apply-btn cv5-preview-apply-btn';
            btn.dataset.cv5PreviewApply = deliverable;
            btn.title = 'Preview the proposed document before applying';
            btn.textContent = 'Preview & apply…';

            rowEl.appendChild(btn);
            div.appendChild(roleEl);
            div.appendChild(bubEl);
            div.appendChild(rowEl);
            thread.appendChild(div);
            thread.scrollTop = thread.scrollHeight;
        }""", fake_deliverable)
        time.sleep(0.5)

        screenshot(page, "composer-polish-2a-preview-apply-button")
        print("  2a: Preview & apply button injected")

        preview_btn = page.locator(".cv5-preview-apply-btn").last
        if preview_btn.count() > 0:
            # Scroll chat thread to show the button
            page.evaluate("document.querySelector('.cv5-chat-thread').scrollTop = 99999")
            time.sleep(0.3)
            preview_btn.scroll_into_view_if_needed()
            time.sleep(0.3)
            preview_btn.click()
            time.sleep(0.8)

            preview_pop = page.locator(".cv5-deliverable-preview")
            pop_visible = preview_pop.is_visible() if preview_pop.count() > 0 else False
            print(f"  Preview popover visible: {pop_visible}")

            if pop_visible:
                screenshot(page, "composer-polish-2b-preview-popover")
                print("  2b: Preview popover with proposed document")

                # Verify no fence markers
                pop_text = preview_pop.text_content() or ""
                if "```artemis-draft" in pop_text:
                    print("  ERROR: fence markers in preview!")
                else:
                    print("  OK: no fence markers in preview")

                # Apply
                apply_btn = preview_pop.locator("[data-cv5-deliverable-apply]")
                if apply_btn.count() > 0:
                    apply_btn.click()
                    time.sleep(0.8)
                    screenshot(page, "composer-polish-2c-after-apply")
                    print("  2c: doc updated after Apply")

                    ed_text = page.locator('[data-cv5="editor"]').text_content() or ""
                    if "```" in ed_text:
                        print("  ERROR: fence markers in editor!")
                    else:
                        print("  OK: no fence markers in editor after apply")

                    # Test Discard: inject another message and open preview, then Discard
                    page.evaluate("""(deliverable) => {
                        const thread = document.querySelector('[data-cv5="chat-thread"]');
                        if (!thread) return;
                        const div = document.createElement('div');
                        div.className = 'cv5-msg assistant';
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'cv5-apply-btn cv5-preview-apply-btn';
                        btn.dataset.cv5PreviewApply = deliverable;
                        btn.textContent = 'Preview & apply…';
                        div.appendChild(btn);
                        thread.appendChild(div);
                        thread.scrollTop = thread.scrollHeight;
                    }""", "This is a DISCARDED document that should not appear in the editor.")
                    time.sleep(0.3)

                    page.evaluate("document.querySelector('.cv5-chat-thread').scrollTop = 99999")
                    time.sleep(0.3)
                    last_btn = page.locator(".cv5-preview-apply-btn").last
                    last_btn.scroll_into_view_if_needed()
                    time.sleep(0.3)
                    last_btn.click()
                    time.sleep(0.6)
                    pop2 = page.locator(".cv5-deliverable-preview")
                    if pop2.is_visible() if pop2.count() > 0 else False:
                        discard = pop2.locator("[data-cv5-deliverable-discard]")
                        if discard.count() > 0:
                            discard.click()
                            time.sleep(0.5)
                            screenshot(page, "composer-polish-2d-after-discard")
                            print("  2d: Discard — popover dismissed, doc unchanged")
                            # Check doc didn't change to the discarded content
                            ed_text2 = page.locator('[data-cv5="editor"]').text_content() or ""
                            if "DISCARDED" in ed_text2:
                                print("  ERROR: Discard didn't work — doc contains discarded text!")
                            else:
                                print("  OK: Discard worked — doc unchanged")
            else:
                print("  WARNING: preview popover not visible")
                screenshot(page, "composer-polish-2b-popover-missing")
        else:
            print("  WARNING: preview button not found")

        print("\nAll screenshots complete.")
        browser.close()


if __name__ == "__main__":
    main()
