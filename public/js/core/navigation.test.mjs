import test from "node:test";
import assert from "node:assert/strict";

import { WRITING_STUDIO_VIEW, parseAppHash, parseWritingStudioDraftId, writingStudioDraftHref } from "./navigation.js";

test("writing studio deep links parse the selected draft id", () => {
  const href = writingStudioDraftHref(123);
  assert.equal(href, "#writing-studio?draft=123");
  assert.equal(parseAppHash(href).view, WRITING_STUDIO_VIEW);
  assert.equal(parseWritingStudioDraftId(href), 123);
});

test("legacy slash-prefixed writing studio hashes still parse", () => {
  const route = parseAppHash("#/writing-studio?draft=456");
  assert.equal(route.view, WRITING_STUDIO_VIEW);
  assert.equal(parseWritingStudioDraftId("#/writing-studio?draft=456"), 456);
});
