/**
 * tests/manual/test_claim_offset_map.mjs
 *
 * Standalone Node.js unit-test for the single-pass serializer + posMap logic
 * introduced in the charOffset→PMPos fix (Stage 4 bug fix).
 *
 * IMPORTANT: This test does NOT import composer-v5.js (which requires a browser
 * with DOM + ProseMirror).  Instead it reimplements the same algorithm using a
 * minimal mock ProseMirror document model so we can verify the offset-mapping
 * math in a pure-Node environment.
 *
 * The mock doc model mirrors the shape that composer-v5.js's helper functions
 * use:
 *   node.type.name    — "doc" | "paragraph" | "heading" | "bullet_list" |
 *                       "ordered_list" | "list_item" | "text"
 *   node.text         — string (text nodes only)
 *   node.attrs        — { level: 1 } for headings
 *   node.marks        — [{ type: { name: "strong" | "em" } }]
 *   node.isText       — true for text nodes
 *   node.isBlock      — true for block nodes
 *   node.textContent  — concatenated plain text of all descendants
 *   node.content.size — ProseMirror position count for the subtree
 *   node.forEach(cb)  — iterate direct children with (child, offset)
 *   doc.forEach(cb)   — iterate top-level blocks with (blockNode, blockOffset)
 *
 * ProseMirror position model (simplified):
 *   A block node at document-offset O occupies positions:
 *     O       = "before the block's open tag"
 *     O+1     = first position inside the block (start of first child)
 *     O+1+k   = position of the k-th character inside the block
 *     O+1+len = position after the last character (= block's close tag)
 *   content.size of a block = 2 + sum(child content.size)
 *   content.size of a text node = text.length
 *
 * Run:  node tests/manual/test_claim_offset_map.mjs
 */

// ─── Minimal PM document mock ─────────────────────────────────────────────────

function makeText(text, marks = []) {
  const n = {
    type: { name: "text" },
    text,
    marks,
    isText: true,
    isBlock: false,
    textContent: text,
    get "content.size"() { return text.length; },
    forEach(_cb) {},
  };
  // content.size as a property
  Object.defineProperty(n, "contentSize", { get: () => text.length });
  return n;
}

function makeBlock(typeName, children, attrs = {}) {
  const textContent = children.map((c) => c.textContent).join("");
  // content.size = sum of children's content.size (each child is either a
  // text node with size=length, or another block with size=2+inner)
  let innerSize = 0;
  for (const c of children) {
    innerSize += c.isText ? c.text.length : (2 + c._innerSize);
  }
  const node = {
    type: { name: typeName },
    attrs,
    marks: [],
    isText: false,
    isBlock: true,
    textContent,
    _innerSize: innerSize,
    // content.size of a block = 2 + innerSize (open + close tokens)
    content: { size: 2 + innerSize },
    forEach(cb) {
      let offset = 0;
      for (const child of children) {
        cb(child, offset);
        offset += child.isText ? child.text.length : (2 + child._innerSize);
      }
    },
  };
  return node;
}

function makeDoc(blocks) {
  let offset = 0;
  const offsets = [];
  for (const b of blocks) {
    offsets.push(offset);
    offset += 2 + b._innerSize;
  }
  const totalSize = offset;
  return {
    content: { size: totalSize },
    forEach(cb) {
      for (let i = 0; i < blocks.length; i++) {
        cb(blocks[i], offsets[i]);
      }
    },
  };
}

// ─── Reimplementation of the algorithm from composer-v5.js ───────────────────
// Keep this in strict sync with the JS source.

function serializeDocToTextWithMap(doc) {
  const chars = [];
  const posMap = [];
  let pending = [];
  let lastRealPMPos = 0;

  function flush(pmPos) {
    for (const idx of pending) posMap[idx] = pmPos;
    pending = [];
  }
  function emitReal(ch, pmPos) {
    flush(pmPos);
    posMap.push(pmPos);
    chars.push(ch);
    lastRealPMPos = pmPos;
  }
  function emitPhantom(str) {
    for (const ch of str) {
      pending.push(posMap.length);
      posMap.push(-1);
      chars.push(ch);
    }
  }

  let firstBlock = true;
  doc.forEach((blockNode, blockOffset) => {
    const serialized = serializeBlockWithMap(blockNode, blockOffset, 0);
    if (serialized === null) return;
    if (!firstBlock) emitPhantom("\n\n");
    firstBlock = false;
    serialized.forEach(([ch, pmPos]) => {
      if (pmPos === -1) emitPhantom(ch);
      else emitReal(ch, pmPos);
    });
  });

  const terminalPos = chars.length > 0 ? lastRealPMPos + 1 : doc.content.size;
  flush(terminalPos);
  posMap.push(terminalPos);

  const rawText = chars.join("");
  const trimmedText = rawText.trim();
  if (!trimmedText) return { text: "", posMap: [terminalPos] };

  const leadTrim = rawText.length - rawText.trimStart().length;
  const slicedPosMap = posMap.slice(leadTrim, leadTrim + trimmedText.length + 1);

  return { text: trimmedText, posMap: slicedPosMap };
}

function serializeBlockWithMap(node, nodeOffset, indent) {
  const name = node.type.name;

  if (name === "paragraph") {
    return serializeInlineWithMap(node, nodeOffset);
  }

  if (name === "heading") {
    const level = Math.min(6, Math.max(1, node.attrs.level || 1));
    const prefix = "#".repeat(level) + " ";
    const phantom = prefix.split("").map((ch) => [ch, -1]);
    const inlinePairs = serializeInlineWithMap(node, nodeOffset);
    return [...phantom, ...inlinePairs];
  }

  if (name === "bullet_list" || name === "ordered_list") {
    const ordered = name === "ordered_list";
    const result = [];
    let n = 1;
    let firstItem = true;
    node.forEach((child, childOffset) => {
      if (!firstItem) result.push(["\n", -1]);
      firstItem = false;
      const bullet = ordered ? `${n}.` : "-";
      const bulletPrefix = "  ".repeat(indent) + bullet + " ";
      for (const ch of bulletPrefix) result.push([ch, -1]);
      n += 1;
      let firstPara = true;
      child.forEach((blk, blkOffset) => {
        const blkAbsOffset = nodeOffset + 1 + childOffset + 1 + blkOffset;
        if (blk.type.name === "paragraph" && firstPara) {
          firstPara = false;
          result.push(...serializeInlineWithMap(blk, blkAbsOffset));
        } else if (!firstPara) {
          const nested = serializeBlockWithMap(blk, blkAbsOffset, indent + 1);
          if (nested) {
            result.push(["\n", -1]);
            result.push(...nested);
          }
        }
      });
    });
    return result;
  }

  // fallback
  const fallback = node.textContent || "";
  return fallback.split("").map((ch, i) => [ch, nodeOffset + 1 + i]);
}

function serializeInlineWithMap(node, nodeOffset) {
  const result = [];
  node.forEach((child, childOffset) => {
    const childAbsPos = nodeOffset + 1 + childOffset;
    if (child.isText) {
      const text = child.text || "";
      const hasBold   = child.marks.some((m) => m.type.name === "strong");
      const hasItalic = child.marks.some((m) => m.type.name === "em");
      if (hasBold)   for (const ch of "**") result.push([ch, -1]);
      if (hasItalic) for (const ch of "*")  result.push([ch, -1]);
      for (let i = 0; i < text.length; i++) {
        result.push([text[i], childAbsPos + i]);
      }
      if (hasBold)   for (const ch of "**") result.push([ch, -1]);
      if (hasItalic) for (const ch of "*")  result.push([ch, -1]);
    } else if (child.type && child.type.name === "hard_break") {
      result.push(["\n", -1]);
    } else {
      const fallback = child.textContent || "";
      for (let i = 0; i < fallback.length; i++) {
        result.push([fallback[i], childAbsPos + i]);
      }
    }
  });
  let lo = 0;
  while (lo < result.length && result[lo][1] === -1 && result[lo][0].trim() === "") lo += 1;
  let hi = result.length;
  while (hi > lo && result[hi - 1][1] === -1 && result[hi - 1][0].trim() === "") hi -= 1;
  return result.slice(lo, hi);
}

// ─── Helper: textBetween mock (plain text within [from, to)) ─────────────────
// In ProseMirror, doc.textBetween(from, to) returns the plain text of all
// text nodes whose positions fall in [from, to).  We simulate it on our mock
// doc by collecting all text chars with their absolute PM positions.
function collectTextChars(doc) {
  const pairs = []; // [{ch, pmPos}]
  doc.forEach((blockNode, blockOffset) => {
    collectBlockChars(blockNode, blockOffset, pairs);
  });
  return pairs;
}

function collectBlockChars(node, nodeOffset, out) {
  if (node.isText) {
    for (let i = 0; i < node.text.length; i++) {
      out.push({ ch: node.text[i], pmPos: nodeOffset + i });
    }
    return;
  }
  node.forEach((child, childOffset) => {
    const childAbsPos = nodeOffset + 1 + childOffset;
    collectBlockChars(child, childAbsPos, out);
  });
}

function textBetween(doc, from, to) {
  const pairs = collectTextChars(doc);
  return pairs
    .filter(({ pmPos }) => pmPos >= from && pmPos < to)
    .map(({ ch }) => ch)
    .join("");
}

// ─── Tests ────────────────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

function assert(cond, label, detail = "") {
  if (cond) {
    console.log(`  PASS  ${label}`);
    passed += 1;
  } else {
    console.error(`  FAIL  ${label}${detail ? "\n        " + detail : ""}`);
    failed += 1;
  }
}

function assertEq(got, expected, label) {
  const ok = got === expected;
  assert(ok, label, ok ? "" : `expected ${JSON.stringify(expected)}, got ${JSON.stringify(got)}`);
}

// ─── Test 1: 3-paragraph draft, claim in the LAST paragraph ──────────────────
//
// Doc: [paragraph("intro"), paragraph("middle stuff"), paragraph("Amira improves scores by 99%")]
//
// ProseMirror sizes:
//   para "intro"                 = 2 + 5   = 7  (offsets 0..6)
//   para "middle stuff"          = 2 + 12  = 14 (offsets 7..20)
//   para "Amira improves scores by 99%" = 2 + 30 = 32 (offsets 21..52)
//
// Character positions inside para 3 (offset 21):
//   'A' at 22, 'm' at 23, ... 's' at 51   (0-indexed within the para's text)
//
// Serialized text: "intro\n\nmiddle stuff\n\nAmira improves scores by 99%"
//                   01234    56789012345    01234567890123456789012345678901
//   (0-based in trimmed text)
//   "intro"     chars 0..4
//   "\n\n"      chars 5..6
//   "middle stuff" chars 7..18
//   "\n\n"      chars 19..20
//   "Amira improves scores by 99%"  chars 21..50
//
// The scan API returns start=21, end=51 (exclusive) for the last paragraph.
// posMap[21] should be the PM pos of 'A' in "Amira" = 22 (para at offset 21, char 0 = pos 22)
// posMap[51] should be PM pos after '%' = pos 52 (lastRealPMPos=51, terminal=52)

console.log("\nTest 1: 3-paragraph draft — claim in last paragraph");
{
  const introText   = makeText("intro");
  const middleText  = makeText("middle stuff");
  const claimText   = makeText("Amira improves scores by 99%");

  const para1 = makeBlock("paragraph", [introText]);
  const para2 = makeBlock("paragraph", [middleText]);
  const para3 = makeBlock("paragraph", [claimText]);

  const doc = makeDoc([para1, para2, para3]);

  const { text, posMap } = serializeDocToTextWithMap(doc);

  // Verify the serialized text is exactly as expected.
  const expected = "intro\n\nmiddle stuff\n\nAmira improves scores by 99%";
  assertEq(text, expected, "serialized text matches expected");

  // Find the start of "Amira" in the serialized text.
  const claimStart = text.indexOf("Amira improves scores by 99%");
  const claimEnd   = claimStart + "Amira improves scores by 99%".length;
  assert(claimStart >= 0, `found claim in text at offset ${claimStart}`);

  // Map offsets → PM positions.
  const pmFrom = posMap[claimStart];
  const pmTo   = posMap[claimEnd];

  // Verify against expected PM positions.
  // para1 at offset 0: size = 2+5=7.  para2 at offset 7: size = 2+12=14.
  // para3 at offset 21 (= 7 + 14): text starts at pos 22.
  // "Amira improves scores by 99%" = 28 chars.
  // 'A' = pos 22, last char '%' = pos 49, terminal = pos 50.
  assertEq(pmFrom, 22, `pmFrom = 22 (start of "Amira" in para 3)`);
  assertEq(pmTo,   50, `pmTo = 50 (exclusive end after "%", terminal = lastPos+1)`);

  // Verify textBetween extracts the exact claim substring.
  const extracted = textBetween(doc, pmFrom, pmTo);
  assertEq(extracted, "Amira improves scores by 99%",
    `doc.textBetween(${pmFrom}, ${pmTo}) = "Amira improves scores by 99%"`);

  console.log(`  posMap[${claimStart}]=${pmFrom}  posMap[${claimEnd}]=${pmTo}`);
  console.log(`  doc.textBetween(${pmFrom}, ${pmTo}) = "${extracted}"`);
}

// ─── Test 2: Heading + paragraph — claim in the paragraph ─────────────────────
//
// Doc: [heading(1, "Overview"), paragraph("We outperform competitors by 3x")]
//
// ProseMirror sizes:
//   heading "Overview"             = 2 + 8  = 10 (offsets 0..9)
//   paragraph "We outperform competitors by 3x" = 2 + 32 = 34 (offsets 10..43)
//
// Serialized text: "# Overview\n\nWe outperform competitors by 3x"
//   "# "        = phantom chars (pmPos = -1), map to pos of 'O' = 12
//   "Overview"  = chars, 'O' at PM pos 12 (heading at offset 0, text starts at pos 1+0+... wait)
//
// Let me trace more carefully:
//   heading at blockOffset=0:
//     serializeBlockWithMap → prefix "# " (phantom) + serializeInlineWithMap(heading, 0)
//     serializeInlineWithMap(heading, 0): child = textNode("Overview") at childOffset=0
//       childAbsPos = 0 + 1 + 0 = 1
//       chars: 'O'→1, 'v'→2, 'e'→3, 'r'→4, 'v'→5, 'i'→6, 'e'→7, 'w'→8
//   paragraph at blockOffset=10:
//     serializeInlineWithMap(paragraph, 10):
//       child = textNode("We outperform competitors by 3x") at childOffset=0
//       childAbsPos = 10 + 1 + 0 = 11
//       'W'→11, 'e'→12, ' '→13, 'o'→14, ...
//
// Serialized text (before trim):
//   "# Overview\n\nWe outperform competitors by 3x"
//   chars: '#'(ph), ' '(ph), 'O'(1),'v'(2),...,'w'(8), '\n'(ph), '\n'(ph),
//          'W'(11),'e'(12),' '(13),'o'(14),...
//
// After flush():
//   phantom '#' and ' ' get pos = 1 (first real pos flushed = 'O' pos)
//   phantom '\n\n' get pos = 11 (next real pos = 'W')
//
// trimmedText = "# Overview\n\nWe outperform competitors by 3x"  (no leading/trailing ws)
// claimStart in text = index of "We outperform"
// posMap[claimStart] = posMap[12] = 11 (pos of 'W')
// posMap[claimEnd] = posMap[12 + 32] = posMap[44] = terminal = lastRealPMPos+1
//   last real char = 'x' at pos 10+1+0+31 = 42 → terminal = 43
//
// doc.textBetween(11, 43) should return "We outperform competitors by 3x"

console.log("\nTest 2: Heading + paragraph — claim in paragraph");
{
  const overviewText = makeText("Overview");
  const claimBody    = makeText("We outperform competitors by 3x");

  const heading = makeBlock("heading", [overviewText], { level: 1 });
  const para    = makeBlock("paragraph", [claimBody]);

  const doc = makeDoc([heading, para]);

  const { text, posMap } = serializeDocToTextWithMap(doc);

  const expectedText = "# Overview\n\nWe outperform competitors by 3x";
  assertEq(text, expectedText, "serialized text matches expected");

  const claimSubstr = "We outperform competitors by 3x";
  const claimStart = text.indexOf(claimSubstr);
  const claimEnd   = claimStart + claimSubstr.length;
  assert(claimStart >= 0, `found claim in text at offset ${claimStart}`);

  const pmFrom = posMap[claimStart];
  const pmTo   = posMap[claimEnd];

  // Expected:
  //   heading at offset 0, text chars start at pos 1.
  //   paragraph at offset 10 (heading.content.size = 10), text chars start at pos 11.
  //   'W' = pos 11, last char 'x' = pos 11+30 = 41, terminal = 42.
  assertEq(pmFrom, 11, `pmFrom = 11 (start of "We" in paragraph)`);
  assertEq(pmTo,   42, `pmTo = 42 (exclusive end after "x")`);

  // The heading prefix "# " must NOT be included in the range.
  const headingText = textBetween(doc, 0, pmFrom);
  assert(!headingText.includes("We"), `heading range does not bleed into claim`);

  const extracted = textBetween(doc, pmFrom, pmTo);
  assertEq(extracted, claimSubstr,
    `doc.textBetween(${pmFrom}, ${pmTo}) = "${claimSubstr}"`);

  console.log(`  posMap[${claimStart}]=${pmFrom}  posMap[${claimEnd}]=${pmTo}`);
  console.log(`  doc.textBetween(${pmFrom}, ${pmTo}) = "${extracted}"`);
}

// ─── Test 3: Original scan case — flagged claim (single paragraph) ────────────
console.log("\nTest 3: Scan case — flagged claim in single paragraph");
{
  const text1 = makeText("Amira improves reading scores by 99% in a single semester of daily practice.");
  const para   = makeBlock("paragraph", [text1]);
  const doc    = makeDoc([para]);

  const { text, posMap } = serializeDocToTextWithMap(doc);

  // No heading prefix, no separator — chars should be 1:1 with PM positions.
  // 'A' at pos 1, last char '.' at pos 75, terminal = 76.
  assertEq(text, "Amira improves reading scores by 99% in a single semester of daily practice.",
    "single-para text passthrough");
  assertEq(posMap[0], 1, "posMap[0] = 1 (first char 'A')");
  assertEq(posMap.length, text.length + 1, "posMap has text.length + 1 entries");
  // Terminal = last char pos + 1.
  assertEq(posMap[text.length], posMap[text.length - 1] + 1, "terminal = last+1");
}

// ─── Test 4: Suppressed case — exact approved phrasing ───────────────────────
console.log("\nTest 4: Scan case — approved phrasing (single paragraph, no drift)");
{
  const phrasingText = makeText("Students using Amira gain 52% more oral reading fluency in one semester.");
  const para = makeBlock("paragraph", [phrasingText]);
  const doc  = makeDoc([para]);

  const { text, posMap } = serializeDocToTextWithMap(doc);

  assertEq(text, "Students using Amira gain 52% more oral reading fluency in one semester.",
    "approved phrasing text passthrough");
  assertEq(posMap[0], 1, "posMap[0] = 1");
}

// ─── Test 5: Quiet check — on-brand multi-paragraph draft ─────────────────────
console.log("\nTest 5: Quiet check — multi-paragraph on-brand draft (no heading drift)");
{
  const p1 = makeBlock("paragraph", [makeText("Our tutor listens as students read aloud and provides structured support.")]);
  const p2 = makeBlock("paragraph", [makeText("Teachers receive a clear picture of every reader's progress.")]);
  const p3 = makeBlock("paragraph", [makeText("Amira Learning partners with districts across Indiana.")]);
  const doc = makeDoc([p1, p2, p3]);

  const { text, posMap } = serializeDocToTextWithMap(doc);

  // Verify the third paragraph maps correctly.
  const thirdParaText = "Amira Learning partners with districts across Indiana.";
  const idx = text.indexOf(thirdParaText);
  assert(idx >= 0, `found third para in serialized text at offset ${idx}`);

  const pmFrom = posMap[idx];
  const pmTo   = posMap[idx + thirdParaText.length];
  const extracted = textBetween(doc, pmFrom, pmTo);
  assertEq(extracted, thirdParaText,
    `doc.textBetween(${pmFrom}, ${pmTo}) = "${thirdParaText}"`);
}

// ─── Summary ──────────────────────────────────────────────────────────────────
console.log(`\n${"─".repeat(60)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
