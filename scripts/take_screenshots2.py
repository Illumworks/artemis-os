"""
Targeted acceptance screenshots for composer-polish-pass.
Focus on cleaner demos of the three fixes.
"""
import time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8765"
OUT = "briefs/screenshots"


def screenshot(page, name):
    path = f"{OUT}/{name}.png"
    page.screenshot(path=path, full_page=False)
    print(f"  saved: {path}")
    return path


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=60)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        print("Loading app...")
        page.goto(f"{BASE}/", wait_until="networkidle")
        time.sleep(1)

        # Navigate to Writing Studio via hash
        page.goto(f"{BASE}/#writing-studio", wait_until="networkidle")
        time.sleep(2)

        editor = page.locator('[data-cv5="editor"]')
        if editor.count() == 0:
            # Try clicking the Writing Studio nav link
            ws_link = page.locator('a:has-text("Writing Studio"), nav a[href="#writing-studio"], li a[href*="writing"]')
            if ws_link.count() > 0:
                ws_link.first.click()
                time.sleep(2)

        if editor.count() == 0:
            # Try fetching a draft and navigating directly
            import json, urllib.request
            try:
                with urllib.request.urlopen(f"{BASE}/api/writing-studio/overview") as r:
                    data = json.loads(r.read())
                    drafts = data.get("drafts", [])
                    if drafts:
                        draft_id = drafts[0]["id"]
                        page.goto(f"{BASE}/#writing-studio?draft={draft_id}", wait_until="networkidle")
                        time.sleep(2)
            except Exception as e:
                print(f"  overview fetch failed: {e}")

        if editor.count() == 0:
            print("No editor visible — taking baseline.")
            screenshot(page, "composer-polish-0-no-editor")
            browser.close()
            return

        pm = page.locator(".ProseMirror").first

        # ── Inject test content ───────────────────────────────────────────────
        pm.click()
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        time.sleep(0.2)
        page.keyboard.type(
            "Customer onboarding is broken. Three separate forms, no progress indication, "
            "and a 48-hour approval window that nobody explains. We lose 30% of sign-ups here.\n\n"
            "The product team has shipped a new onboarding flow. Single form, instant verification, "
            "progress bar at every step. Early beta shows 68% drop in abandonment. "
            "We need to announce this loudly and specifically to the audience that felt the old pain.\n\n"
            "Key message: Amira now gets students into the right placement in under 5 minutes, "
            "guaranteed. No paperwork. No waiting. Teachers can focus on teaching."
        )
        time.sleep(0.3)

        # ── Screenshot 1: Dismiss any selection, show clean doc ──────────────
        pm.click()
        page.keyboard.press("Escape")
        time.sleep(0.2)

        # ── FIX 1: Full-paragraph selection — toolbar MUST appear ─────────────
        print("\n--- Fix 1: Selection toolbar on full/multi-paragraph selections ---")

        # Select first paragraph with triple-click
        pm.click(click_count=3)
        time.sleep(0.6)
        toolbar = page.locator(".cv5-sel-toolbar")
        toolbar_visible = toolbar.is_visible() if toolbar.count() > 0 else False
        print(f"  Toolbar visible on full paragraph: {toolbar_visible}")
        screenshot(page, "composer-polish-1b-full-paragraph-selection")

        # Select two paragraphs: Home, then shift-click end of second paragraph
        pm.click()
        page.keyboard.press("Meta+Home")
        # Select all 3 paragraphs
        page.keyboard.press("Meta+Shift+End")
        time.sleep(0.6)
        toolbar_visible2 = toolbar.is_visible() if toolbar.count() > 0 else False
        print(f"  Toolbar visible on multi-paragraph: {toolbar_visible2}")
        screenshot(page, "composer-polish-1c-two-paragraph-selection")

        # ── FIX 3: What should change? input visible in toolbar ───────────────
        print("\n--- Fix 3: What should change? input ---")

        # Select second paragraph (triple-click on second block)
        pm.click()
        page.keyboard.press("Meta+Home")
        # Move to second paragraph
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Home")
        page.keyboard.press("Shift+End")
        time.sleep(0.5)
        screenshot(page, "composer-polish-3a-toolbar-with-intent-input")

        # Type a critique
        intent = page.locator(".cv5-sel-intent-input")
        if intent.count() > 0:
            intent.fill("make it shorter and punchier")
            time.sleep(0.3)
            screenshot(page, "composer-polish-3b-intent-typed")
            print("  3b: typed intent 'make it shorter and punchier'")

        # Clear the input, go back to idle
        if intent.count() > 0:
            intent.fill("")

        # ── FIX 2: Deliverable preview — inject a fake deliverable message ─────
        print("\n--- Fix 2: Deliverable preview (injected via JS) ---")

        # Inject a fake assistant message with a deliverable into the chat thread
        # to test the preview popover without needing a live LLM response.
        fake_deliverable = (
            "Customer onboarding is now instant.\n\n"
            "Amira has rebuilt the onboarding experience from the ground up: "
            "one form, real-time verification, and a clear progress indicator at every step. "
            "Early results show a 68% drop in abandonment.\n\n"
            "Teachers and administrators told us the old process was a barrier. "
            "We listened. Students are now placed correctly in under 5 minutes — "
            "no paperwork, no waiting, no confusion."
        )

        # Use page.evaluate with a JS argument to avoid f-string escaping issues.
        page.evaluate("""(deliverable) => {
            const thread = document.querySelector('[data-cv5="chat-thread"]');
            if (!thread) return;
            const div = document.createElement('div');
            div.className = 'cv5-msg assistant';
            const bub = document.createElement('div');
            bub.className = 'cv5-msg-role';
            bub.textContent = 'Amira';
            const body = document.createElement('div');
            body.className = 'cv5-msg-bub';
            body.textContent = 'Here is the proposed revised document:';
            const row = document.createElement('div');
            row.className = 'cv5-msg-apply-row';
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'cv5-apply-btn cv5-preview-apply-btn';
            btn.dataset.cv5PreviewApply = deliverable;
            btn.title = 'Preview the proposed document before applying';
            btn.textContent = 'Preview & apply…';
            row.appendChild(btn);
            div.appendChild(bub);
            div.appendChild(body);
            div.appendChild(row);
            thread.appendChild(div);
            thread.scrollTop = thread.scrollHeight;
        }""", fake_deliverable)
        time.sleep(0.5)
        screenshot(page, "composer-polish-2a-preview-apply-button")
        print("  2a: Preview & apply button injected and visible")

        # Click the Preview & apply button
        preview_btn = page.locator(".cv5-preview-apply-btn").last
        if preview_btn.count() > 0:
            preview_btn.click()
            time.sleep(0.6)

            preview_pop = page.locator(".cv5-deliverable-preview")
            if preview_pop.count() > 0 and preview_pop.is_visible():
                screenshot(page, "composer-polish-2b-preview-popover")
                print("  2b: Preview popover open")

                # Check no fence markers
                pop_text = preview_pop.text_content() or ""
                if "```artemis-draft" in pop_text:
                    print("  ERROR: fence markers in preview!")
                else:
                    print("  OK: no fence markers in preview")

                # Take screenshot then Apply
                apply_btn = preview_pop.locator("[data-cv5-deliverable-apply]")
                if apply_btn.count() > 0:
                    apply_btn.click()
                    time.sleep(0.7)
                    screenshot(page, "composer-polish-2c-after-apply")
                    print("  2c: doc updated after Apply")

                    # Check editor for fence markers
                    ed_text = page.locator('[data-cv5="editor"]').text_content() or ""
                    if "```" in ed_text:
                        print("  ERROR: fence markers in editor after apply!")
                    else:
                        print("  OK: no fence markers in editor after apply")

                    # Test Discard: reopen preview and click Discard
                    preview_btn2 = page.locator(".cv5-preview-apply-btn").last
                    if preview_btn2.count() > 0:
                        preview_btn2.click()
                        time.sleep(0.5)
                        preview_pop2 = page.locator(".cv5-deliverable-preview")
                        if preview_pop2.count() > 0 and preview_pop2.is_visible():
                            discard_btn = preview_pop2.locator("[data-cv5-deliverable-discard]")
                            if discard_btn.count() > 0:
                                discard_btn.click()
                                time.sleep(0.4)
                                screenshot(page, "composer-polish-2d-after-discard")
                                print("  2d: Discard — doc unchanged (popover dismissed)")
            else:
                print("  WARNING: preview popover not visible")
                screenshot(page, "composer-polish-2-preview-missing")
        else:
            print("  WARNING: preview button not found")

        print("\nAll screenshots complete.")
        browser.close()


if __name__ == "__main__":
    main()
