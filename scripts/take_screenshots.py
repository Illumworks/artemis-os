"""
Acceptance screenshots for composer-polish-pass.
Run from the worktree root:
  uv run python scripts/take_screenshots.py
"""
import time
from playwright.sync_api import sync_playwright, expect

BASE = "http://localhost:8765"
OUT = "briefs/screenshots"

def get_or_create_draft(page):
    """Navigate to the writing studio and ensure at least one draft exists."""
    page.goto(f"{BASE}/#writing-studio", wait_until="networkidle")
    time.sleep(1)

    # Check if there's a draft already loaded in the composer
    if page.locator('[data-cv5="editor"]').count() > 0:
        return True

    # Try to create a new draft via the picker
    picker_btn = page.locator('[data-cv5="drafts-btn"]')
    if picker_btn.count() > 0:
        picker_btn.click()
        time.sleep(0.4)
        new_draft = page.locator('[data-cv5-create="draft"]')
        if new_draft.count() > 0:
            new_draft.click()
            time.sleep(1.5)
            return True
    return False


def screenshot(page, name):
    path = f"{OUT}/{name}.png"
    page.screenshot(path=path, full_page=False)
    print(f"  saved: {path}")
    return path


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=80)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        print("Loading writing studio...")
        page.goto(f"{BASE}/", wait_until="networkidle")
        time.sleep(1)

        # Navigate to writing studio
        ws_link = page.locator('a[href="#writing-studio"], [data-nav="writing-studio"], '
                               'button:has-text("Writing"), a:has-text("Writing")')
        if ws_link.count() > 0:
            ws_link.first.click()
            time.sleep(1.5)
        else:
            page.goto(f"{BASE}/#writing-studio", wait_until="networkidle")
            time.sleep(1.5)

        # Make sure we have an editor open; if not, create a draft
        editor = page.locator('[data-cv5="editor"]')
        if editor.count() == 0:
            print("  No editor found, trying to create/open a draft...")
            get_or_create_draft(page)
            time.sleep(1.5)

        if editor.count() == 0:
            print("  WARNING: No editor found. Taking baseline screenshot.")
            screenshot(page, "composer-polish-0-baseline")
            browser.close()
            return

        # ── Inject multi-paragraph content into the editor ──────────────────
        pm_editor = page.locator(".ProseMirror").first
        if pm_editor.count() > 0:
            pm_editor.click()
            # Select all and replace with multi-paragraph content
            page.keyboard.press("Meta+a")
            page.keyboard.press("Backspace")
            content = (
                "This is the first paragraph of our sample document. "
                "It contains several sentences to make it substantial enough for selection testing.\n\n"
                "This is the second paragraph. It has different content about product positioning "
                "and market dynamics that we want to test rewriting.\n\n"
                "This is the third paragraph with additional context about our strategy "
                "and competitive advantages."
            )
            page.keyboard.type(content)
            time.sleep(0.5)

        print("\n--- Fix 1: Selection toolbar ---")

        # 1a. Select a single word
        pm_editor.click()
        page.keyboard.press("Meta+a")
        page.keyboard.press("Escape")
        # Triple-click to select first paragraph
        pm_editor.dblclick()
        time.sleep(0.3)
        screenshot(page, "composer-polish-1a-single-word-selection")
        print("  1a: single word selection")

        # 1b. Select full first paragraph (triple-click)
        pm_editor.click()
        page.keyboard.press("Meta+a")
        page.keyboard.press("Escape")
        pm_editor.click(click_count=3)
        time.sleep(0.5)
        screenshot(page, "composer-polish-1b-full-paragraph-selection")
        print("  1b: full paragraph selection")

        # 1c. Select two paragraphs — the key regression case
        pm_editor.click()
        page.keyboard.press("Meta+a")
        page.keyboard.press("Escape")
        # Move to start, select to end of second paragraph
        page.keyboard.press("Meta+Home")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Meta+Shift+ArrowDown")
        time.sleep(0.5)
        screenshot(page, "composer-polish-1c-two-paragraph-selection")
        print("  1c: two-paragraph selection (the regression case)")

        # ── Fix 3: What should change? input ────────────────────────────────
        print("\n--- Fix 3: What should change? input ---")

        # Make a selection so the toolbar is visible
        pm_editor.click(click_count=3)  # select a paragraph
        time.sleep(0.4)

        toolbar = page.locator(".cv5-sel-toolbar")
        if toolbar.count() > 0 and toolbar.is_visible():
            screenshot(page, "composer-polish-3a-toolbar-with-intent-input")
            print("  3a: toolbar visible with What should change? input")

            # Type a critique
            intent_input = page.locator(".cv5-sel-intent-input")
            if intent_input.count() > 0:
                intent_input.fill("too jargony, make it clearer")
                time.sleep(0.3)
                screenshot(page, "composer-polish-3b-intent-typed")
                print("  3b: intent typed in the input")
        else:
            print("  WARNING: toolbar not visible after selection")
            screenshot(page, "composer-polish-3-toolbar-state")

        # ── Fix 2: Deliverable preview ───────────────────────────────────────
        print("\n--- Fix 2: Deliverable preview ---")

        # Send a chat message to get a deliverable
        chat_input = page.locator('[data-cv5="chat-input"]')
        if chat_input.count() > 0:
            # Click away from the editor first
            chat_input.click()
            chat_input.fill("Write me a short 2-paragraph overview about AI in education")
            time.sleep(0.3)
            send_btn = page.locator('[data-cv5="chat-send"]')
            if send_btn.count() > 0:
                send_btn.click()
                print("  Sent chat message, waiting for response (up to 45s)...")
                # Wait for the "Drafting..." to disappear
                try:
                    page.wait_for_selector(".cv5-msg.assistant:not(.pending)", timeout=45000)
                    time.sleep(1.0)

                    # Check if a "Preview & apply" button appeared
                    preview_btn = page.locator(".cv5-preview-apply-btn").first
                    if preview_btn.count() > 0 and preview_btn.is_visible():
                        screenshot(page, "composer-polish-2a-preview-apply-button")
                        print("  2a: Preview & apply button visible")

                        # Click Preview & apply
                        preview_btn.click()
                        time.sleep(0.8)

                        preview_popover = page.locator(".cv5-deliverable-preview")
                        if preview_popover.count() > 0 and preview_popover.is_visible():
                            screenshot(page, "composer-polish-2b-preview-popover")
                            print("  2b: Preview popover open with proposed document")

                            # Check no fence markers
                            popover_text = preview_popover.text_content() or ""
                            if "```artemis-draft" in popover_text:
                                print("  ERROR: fence markers found in preview!")
                            else:
                                print("  OK: no fence markers in preview")

                            # Apply the document
                            apply_btn = preview_popover.locator("[data-cv5-deliverable-apply]")
                            if apply_btn.count() > 0:
                                apply_btn.click()
                                time.sleep(0.8)
                                screenshot(page, "composer-polish-2c-after-apply")
                                print("  2c: after Apply — document updated")

                                # Check no fence markers in editor
                                editor_text = page.locator('[data-cv5="editor"]').text_content() or ""
                                if "```artemis-draft" in editor_text or "```" in editor_text:
                                    print("  ERROR: fence markers found in editor after apply!")
                                else:
                                    print("  OK: no fence markers in editor after apply")

                                # Test Undo
                                undo_btn = page.locator(".cv5-apply-undo").first
                                if undo_btn.count() > 0 and undo_btn.is_visible():
                                    undo_btn.click()
                                    time.sleep(0.6)
                                    screenshot(page, "composer-polish-2d-after-undo")
                                    print("  2d: after Undo — previous content restored")
                                else:
                                    print("  NOTE: Undo button not found after apply")
                            else:
                                print("  WARNING: Apply button not found in preview")
                        else:
                            print("  WARNING: Preview popover not visible after click")
                    else:
                        print("  NOTE: No Preview & apply button found (response may not have had a deliverable)")
                        screenshot(page, "composer-polish-2-chat-response")
                except Exception as e:
                    print(f"  WARNING: chat response timed out or error: {e}")
                    screenshot(page, "composer-polish-2-timeout")
        else:
            print("  WARNING: chat input not found")

        print("\nAll screenshots taken.")
        browser.close()


if __name__ == "__main__":
    main()
