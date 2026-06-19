/**
 * Enablement Indexer — Apps Script (host on amiracentral@amiralearning.com)
 * =========================================================================
 * Reads the 4 enablement Google Sheets + the "Indexed Docs" Drive folder
 * natively, normalises one record per asset (per briefs/enablement-sheet-configs.md),
 * and POSTs batches to the Artemis ingest webhook. Artemis embeds + upserts into
 * enablement_assets; Kai (Chiron) searches that. The server needs NO Drive scopes.
 *
 * SETUP (one time):
 *   1. Paste this whole file into a new Apps Script project on amiracentral@.
 *   2. Fill in CONFIG below (webhook secret + Cloudflare Access service token).
 *   3. Run installTrigger() once (authorize when prompted).
 *   4. Run runAll() once manually to prove it, then check Kai.
 *  See apps-script/README-deploy.md for the full runbook.
 *
 * Idempotency: each asset has a stable `key`; re-runs upsert in place.
 * full_refresh=true tells Artemis to soft-archive rows of a source that vanish
 * from the sheet (supersession, never delete).
 */

// ── CONFIG ──────────────────────────────────────────────────────────────────
var CONFIG = {
    WEBHOOK_URL: 'https://app.artemisos.me/api/enablement/ingest',
    ENABLEMENT_TOKEN: '3GUqvmsbYyFTGI3NElnbmsB20aqgizdvlNo0-_YCTA0',
    CF_ACCESS_CLIENT_ID: '3ee48a59e7109c1dea509a98b102b071.access',
    CF_ACCESS_CLIENT_SECRET: 'e18afe2b260cf082cb0c902dbf0af2e90869b1838e64c4a4e9e37b87e8be69fa',

  SHEETS: {
    teacher_resources_internal: '1iFS-jKJyjRX1xRQeg9Tzr3yFdXaOxjdo7_97w9wlxfU',
    training_decks: '1178t_lk8mCBZ6S-DQDSICwzIbeHbmVm-rVeixAsjcjo',
    ait_video_library: '1o5pmUfLn0uAtXr5l1opYY7elfQWZCygRR2qgIlRslPM',
    customer_video_walkthroughs: '12f-b1f3JNFWn5NG2ew0dDRxIzkgys_IP288yYq3udTI',
  },
  INDEXED_DOCS_FOLDER_ID: '1cBzZpBT1dsZFuCbNmh4oyOlYJK6EZGuT',

  MAX_TEXT: 8000,   // truncate extracted doc/slide text
  BATCH_SIZE: 100,  // POST chunk size
};

// ── Entry points ──────────────────────────────────────────────────────────────

/** Run every source. Wire this to an hourly time-driven trigger. */
function runAll() {
  var results = [];
  results.push(safe_('teacher_resources_internal', indexTeacherResourcesInternal));
  results.push(safe_('training_decks', indexTrainingDecks));
  results.push(safe_('ait_video_library', indexAitVideoLibrary));
  results.push(safe_('customer_video_walkthroughs', indexCustomerVideoWalkthroughs));
  results.push(safe_('indexed_docs', indexIndexedDocsFolder));
  Logger.log('runAll done: ' + JSON.stringify(results));
}

function safe_(name, fn) {
  try { return { source: name, result: fn() }; }
  catch (e) { Logger.log('ERROR in ' + name + ': ' + e); return { source: name, error: String(e) }; }
}

/** Install the hourly trigger (run once). */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'runAll') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('runAll').timeBased().everyHours(1).create();
  setupTrainingDecksSheet(); // one-time structural setup of the flag + confirmation columns
  Logger.log('Hourly runAll trigger installed.');
}

/**
 * One-time structural setup of the Training Decks sheet so nobody hand-edits cells:
 *  - normalises the col M header to "Ready for Indexing" and col N to "Indexed At"
 *  - turns col M into a real checkbox on every data row
 *  - auto-checks the deck rows that NEED slide-indexing (a deck in E with no script
 *    in F — i.e. the Differentiation deck), unchecks everything else (incl. the
 *    orphan flag on the blank row and the demo-account row)
 * The hourly runAll then slide-indexes the checked rows and writes a timestamp into
 * "Indexed At" as confirmation. Missy can check/uncheck any row afterwards. Idempotent.
 * Run automatically by installTrigger(); safe to re-run manually.
 */
function setupTrainingDecksSheet() {
  var sheet = getSheetLoose_(CONFIG.SHEETS.training_decks, 'Sheet1');
  var lastRow = sheet.getLastRow();
  if (lastRow < 3) return;
  sheet.getRange(2, 13).setValue('Ready for Indexing'); // M header
  sheet.getRange(2, 14).setValue('Indexed At');          // N header

  var n = lastRow - 2; // data rows 3..lastRow
  var flagRange = sheet.getRange(3, 13, n, 1);
  flagRange.setDataValidation(
    SpreadsheetApp.newDataValidation().requireCheckbox().build()
  );

  var checked = 0;
  var values = [];
  for (var r = 3; r <= lastRow; r++) {
    var deckUrl = urlAt_(sheet, r, 5);                       // E
    var scriptUrl = urlAt_(sheet, r, 6);                     // F
    var products = String(textAt_(sheet, r, 2)).toLowerCase(); // B
    var isDemo = products.indexOf('demo account') === 0;
    var want = !!(deckUrl && !scriptUrl && !isDemo);         // deck, no script, not a demo
    values.push([want]);
    if (want) checked++;
  }
  flagRange.setValues(values);
  Logger.log('setupTrainingDecksSheet: ' + n + ' rows, auto-checked ' + checked + ' for slide-indexing.');
}

// ── Source 1: Amira Teacher Resources (INTERNAL) — tab "25-26 AIT" ─────────────
// Header row 3, data row 4+. B=Audience C=Product D=Title A=Date
// F=TRH link (customer/web)  G=PDF (customer)  H=Editable (internal, on request, make copy)
function indexTeacherResourcesInternal() {
  var SOURCE = 'teacher_resources_internal';
  var sheet = getSheetLoose_(CONFIG.SHEETS[SOURCE], '25-26 AIT');
  var lastRow = sheet.getLastRow();
  if (lastRow < 4) return { upserted: 0 };
  var assets = [];
  for (var r = 4; r <= lastRow; r++) {
    var title = textAt_(sheet, r, 4);          // D
    if (!title) continue;
    var audience = textAt_(sheet, r, 2);       // B
    var product = textAt_(sheet, r, 3);        // C
    var webUrl = urlAt_(sheet, r, 6);          // F
    var pdfUrl = urlAt_(sheet, r, 7);          // G
    var editUrl = urlAt_(sheet, r, 8);         // H
    var links = [];
    if (webUrl) links.push(link_('web', 'Web link (share this)', webUrl, 'customer', false, false));
    if (pdfUrl) links.push(link_('pdf', 'PDF', pdfUrl, 'customer', false, false));
    if (editUrl) links.push(link_('editable', 'Editable file (INTERNAL — make a copy)', editUrl, 'internal', true, true));
    if (!links.length) continue;
    assets.push({
      key: SOURCE + ':row' + r,
      asset_type: 'teacher_resource',
      title: title,
      audience: audience,
      tags: compact_([audience, product]),
      searchable_text: compact_([title, audience, product]).join(' '),
      links: links,
      requires_copy: false,
      source_row: String(r),
    });
  }
  return postBatch_(SOURCE, assets);
}

// ── Source 2: Training Decks — tab "Sheet1" ─────────────────────────────────────
// Header row 2, data 3+. A=TrainingType B=Products C=Persona D=Customer (facets)
// E=deck (default, make copy)  F=script (on request; ALSO indexed for content)
// G=editable handout (internal, on request, make copy)  H=customer handout (default)
// I=tinyurl (on request)  J=webinar (customer)  K=lastUpdated  M=Ready-for-Indexing flag
// Slide-text indexing: rows flagged M=true whose deck (E) has no script -> open Slides,
// extract text, write "Indexed At" timestamp to column N.
function indexTrainingDecks() {
  var SOURCE = 'training_decks';
  var sheet = getSheetLoose_(CONFIG.SHEETS[SOURCE], 'Sheet1');
  var lastRow = sheet.getLastRow();
  if (lastRow < 3) return { upserted: 0 };
  ensureHeader_(sheet, 2, 14, 'Indexed At'); // column N header
  var assets = [];
  for (var r = 3; r <= lastRow; r++) {
    var trainingType = textAt_(sheet, r, 1);   // A
    var products = textAt_(sheet, r, 2);       // B
    var persona = textAt_(sheet, r, 3);        // C
    var customer = textAt_(sheet, r, 4);       // D
    var deckTitle = textAt_(sheet, r, 5);      // E (display text)
    var deckUrl = urlAt_(sheet, r, 5);         // E (hyperlink)

    // Demo-account row (piping only; Kai surfacing deferred to iteration 2).
    if (products && products.toLowerCase().indexOf('demo account') === 0) {
      var demoUrl = urlAt_(sheet, r, 5) || textAt_(sheet, r, 5);
      if (demoUrl) {
        assets.push({
          key: SOURCE + ':row' + r,
          asset_type: 'demo_account',
          title: 'Demo Account for Training',
          tags: compact_([trainingType, products]),
          searchable_text: compact_([trainingType, products]).join(' '),
          links: [link_('demo', 'Demo account login', demoUrl, 'internal', true, false)],
          source_row: String(r),
        });
      }
      continue;
    }

    var scriptUrl = urlAt_(sheet, r, 6);       // F
    var editHandoutUrl = urlAt_(sheet, r, 7);  // G
    var custHandoutUrl = urlAt_(sheet, r, 8);  // H
    var tinyUrl = urlAt_(sheet, r, 9);         // I
    var webinarUrl = urlAt_(sheet, r, 10);     // J
    var flag = String(textAt_(sheet, r, 13)).toLowerCase(); // M

    var name = deckTitle || (trainingType + ' / ' + products + ' / ' + persona + ' / ' + customer);
    var links = [];
    if (deckUrl) links.push(link_('deck', 'Training deck (make a copy)', forceCopy_(deckUrl), 'customer', false, true));
    if (custHandoutUrl) links.push(link_('handout_customer', 'Customer handout', custHandoutUrl, 'customer', false, false));
    if (tinyUrl) links.push(link_('handout_tinyurl', 'Short customer link', tinyUrl, 'customer', true, false));
    if (editHandoutUrl) links.push(link_('handout_editable', 'Editable handout (INTERNAL — make a copy)', forceCopy_(editHandoutUrl), 'internal', true, true));
    if (scriptUrl) links.push(link_('script', 'Speaker-notes script (INTERNAL)', scriptUrl, 'internal', true, false));
    if (webinarUrl) links.push(link_('webinar', 'Customer webinar', webinarUrl, 'customer', false, false));
    if (!links.length) continue;

    // Content indexing: prefer the script doc text; if no script but flagged, pull slide text.
    var contentText = '';
    if (scriptUrl) {
      contentText = extractDocText_(scriptUrl);
    } else if (flag === 'true' && deckUrl) {
      contentText = extractSlidesText_(deckUrl);
      if (contentText) setCell_(sheet, r, 14, new Date().toISOString()); // N = Indexed At
    }

    assets.push({
      key: SOURCE + ':row' + r,
      asset_type: 'training_deck',
      title: name,
      audience: persona,
      tags: compact_([trainingType, products, persona, customer]),
      searchable_text: compact_([name, trainingType, products, persona, customer, contentText]).join(' ').substring(0, CONFIG.MAX_TEXT),
      links: links,
      requires_copy: !!deckUrl,
      source_row: String(r),
    });
  }
  return postBatch_(SOURCE, assets);
}

// ── Source 3: AIT Student Experience Video Library — tab "Consolidated Library" ─
// TWO header rows: row 1 banner, row 2 real header. DATA STARTS ROW 3.
// A=Number(key) C=Grade E=Product F=Language J=MicroInterventions
// H=Video Name and Link. The video link in H is a Google/Drive SMART CHIP, not a
// normal hyperlink, so SpreadsheetApp can't read its URL — we use the Sheets API
// (chipRuns) to pull it. Requires the Sheets advanced service (Services + -> Sheets API).
// G hidden -> ignore. Retired videos live in a separate "Retire" tab.
function indexAitVideoLibrary() {
  var SOURCE = 'ait_video_library';
  var tab = 'Consolidated Library';
  var resp = Sheets.Spreadsheets.get(CONFIG.SHEETS[SOURCE], {
    ranges: [tab],
    fields: 'sheets(data(rowData(values(formattedValue,chipRuns(chip(richLinkProperties(uri)))))))',
  });
  var rowData = (resp.sheets[0].data[0].rowData) || [];
  var assets = [];
  for (var i = 2; i < rowData.length; i++) {           // data starts row 3 -> index 2
    var cells = (rowData[i] && rowData[i].values) || [];
    var number = cellText_(cells, 0);                  // A
    if (!number) continue;
    if (cellText_(cells, 3).toLowerCase().indexOf('retire') !== -1) continue; // D
    var grade = cellText_(cells, 2);                   // C
    var product = cellText_(cells, 4);                 // E
    var language = cellText_(cells, 5);                // F
    var videoName = cellText_(cells, 7);               // H (display name)
    var videoUrl = cellChipUri_(cells, 7);             // H (smart-chip URL)
    var micro = cellText_(cells, 9);                   // J
    if (!videoUrl) continue;
    assets.push({
      key: SOURCE + ':' + number,                      // stable id
      asset_type: 'student_video',
      title: videoName || number,
      audience: product,
      tags: compact_([grade, product, language, micro]),
      searchable_text: compact_([videoName, grade, product, language, micro]).join(' '),
      links: [link_('video', 'Video', videoUrl, 'customer', false, false)],
      source_row: number,
    });
  }
  return postBatch_(SOURCE, assets);
}

/** Sheets-API cell helpers (rowData values). */
function cellText_(cells, idx) {
  var c = cells[idx];
  return c && c.formattedValue ? String(c.formattedValue).trim() : '';
}
function cellChipUri_(cells, idx) {
  var c = cells[idx];
  if (!c || !c.chipRuns) return '';
  for (var k = 0; k < c.chipRuns.length; k++) {
    var chip = c.chipRuns[k].chip;
    if (chip && chip.richLinkProperties && chip.richLinkProperties.uri) {
      return chip.richLinkProperties.uri;
    }
  }
  return '';
}

// ── Source 4: Customer Video Walkthroughs — tab "Post-Sale Product Tour Video Scope" ─
// Header row 1, data 2+. A=Category B=Audience C=App D=Title E=NeedsToInclude
// G=CUSTOMER LINK (customer).
function indexCustomerVideoWalkthroughs() {
  var SOURCE = 'customer_video_walkthroughs';
  var sheet = getSheetLoose_(CONFIG.SHEETS[SOURCE], 'Post-Sale Product Tour Video Scope');
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return { upserted: 0 };
  var assets = [];
  for (var r = 2; r <= lastRow; r++) {
    var title = textAt_(sheet, r, 4);          // D
    var custUrl = urlAt_(sheet, r, 7) || textAt_(sheet, r, 7); // G (storylane plain URL)
    if (!title || !custUrl) continue;
    var category = textAt_(sheet, r, 1);       // A
    var audience = textAt_(sheet, r, 2);       // B
    var app = textAt_(sheet, r, 3);            // C
    var needs = textAt_(sheet, r, 5);          // E
    assets.push({
      key: SOURCE + ':row' + r,
      asset_type: 'walkthrough',
      title: title,
      audience: audience,
      tags: compact_([category, audience, app]),
      searchable_text: compact_([title, category, audience, app, needs]).join(' '),
      links: [link_('walkthrough', 'Customer walkthrough', custUrl, 'customer', false, false)],
      source_row: String(r),
    });
  }
  return postBatch_(SOURCE, assets);
}

// ── Source 5: Indexed Docs folder (evergreen) ───────────────────────────────────
// Scan flat for Google Docs, index full text, key by file id, customer-shareable.
function indexIndexedDocsFolder() {
  var SOURCE = 'indexed_docs';
  var folder = DriveApp.getFolderById(CONFIG.INDEXED_DOCS_FOLDER_ID);
  var it = folder.getFiles(); // ALL files (Google Docs, shortcuts, uploads), not just native Docs
  var assets = [];
  while (it.hasNext()) {
    var f = it.next();
    var mime = f.getMimeType();
    var fileId = f.getId();
    var url = f.getUrl();
    var text = '';
    // Native Google Doc -> full text. Shortcut -> resolve target (full text if it's a Doc).
    if (mime === MimeType.GOOGLE_DOCS) {
      try { text = DocumentApp.openById(fileId).getBody().getText(); } catch (e) {}
    } else if (mime === 'application/vnd.google-apps.shortcut') {
      var tgt = resolveShortcut_(fileId); // {id, mimeType} or null
      if (tgt) {
        url = 'https://drive.google.com/open?id=' + tgt.id;
        if (tgt.mimeType === MimeType.GOOGLE_DOCS) {
          try { text = DocumentApp.openById(tgt.id).getBody().getText(); } catch (e) {}
        }
      }
    }
    // Non-Doc files (PDF/Word/etc.) and unresolved shortcuts still get indexed by name + link.
    assets.push({
      key: SOURCE + ':' + fileId,
      asset_type: 'doc',
      title: f.getName(),
      searchable_text: (f.getName() + ' ' + text).substring(0, CONFIG.MAX_TEXT),
      links: [link_('doc', 'Document', url, 'customer', false, false)],
      source_row: fileId,
    });
  }
  return postBatch_(SOURCE, assets);
}

/** Resolve a Drive shortcut to its target {id, mimeType} via the advanced Drive service. */
function resolveShortcut_(shortcutId) {
  try {
    var meta = Drive.Files.get(shortcutId, { fields: 'shortcutDetails', supportsAllDrives: true });
    if (meta && meta.shortcutDetails && meta.shortcutDetails.targetId) {
      return { id: meta.shortcutDetails.targetId, mimeType: meta.shortcutDetails.targetMimeType };
    }
  } catch (e) { Logger.log('resolveShortcut_ failed for ' + shortcutId + ': ' + e); }
  return null;
}

// ── Cell / URL helpers ──────────────────────────────────────────────────────────

function textAt_(sheet, row, col) {
  var v = sheet.getRange(row, col).getDisplayValue();
  return v ? String(v).trim() : '';
}

/** Best-effort hyperlink extraction: rich-text run link, HYPERLINK() formula, or raw URL. */
function urlAt_(sheet, row, col) {
  var range = sheet.getRange(row, col);
  try {
    var rtv = range.getRichTextValue();
    if (rtv) {
      var runs = rtv.getRuns();
      for (var i = 0; i < runs.length; i++) {
        var u = runs[i].getLinkUrl();
        if (u) return u;
      }
      var whole = rtv.getLinkUrl();
      if (whole) return whole;
    }
  } catch (e) {}
  var formula = range.getFormula();
  if (formula) {
    var m = formula.match(/HYPERLINK\(\s*"([^"]+)"/i);
    if (m) return m[1];
  }
  var disp = range.getDisplayValue();
  if (disp && /^https?:\/\//i.test(disp.trim())) return disp.trim();
  return '';
}

/** Rewrite a Google Slides/Docs edit URL to a force-copy URL. */
function forceCopy_(url) {
  if (!url) return url;
  if (/\/copy(\?|$)/.test(url)) return url;
  return url.replace(/\/(edit|view|preview)(\?[^#]*)?(#.*)?$/i, '/copy');
}

function extractDocText_(url) {
  var id = idFromUrl_(url);
  if (!id) return '';
  try { return DocumentApp.openById(id).getBody().getText().substring(0, CONFIG.MAX_TEXT); }
  catch (e) { Logger.log('extractDocText_ failed for ' + url + ': ' + e); return ''; }
}

function extractSlidesText_(url) {
  var id = idFromUrl_(url);
  if (!id) return '';
  try {
    var slides = SlidesApp.openById(id).getSlides();
    var parts = [];
    for (var i = 0; i < slides.length; i++) {
      var shapes = slides[i].getShapes();
      for (var j = 0; j < shapes.length; j++) {
        try {
          var t = shapes[j].getText().asString();
          if (t && t.trim()) parts.push(t.trim());
        } catch (e2) {}
      }
    }
    return parts.join('\n').substring(0, CONFIG.MAX_TEXT);
  } catch (e) { Logger.log('extractSlidesText_ failed for ' + url + ': ' + e); return ''; }
}

function idFromUrl_(url) {
  if (!url) return '';
  var m = url.match(/\/d\/([a-zA-Z0-9_-]+)/);
  return m ? m[1] : '';
}

function link_(role, label, url, visibility, onRequest, makeCopy) {
  return { role: role, label: label, url: url, visibility: visibility, on_request: !!onRequest, make_copy: !!makeCopy };
}

function compact_(arr) {
  return arr.filter(function (x) { return x && String(x).trim(); }).map(function (x) { return String(x).trim(); });
}

function getSheetLoose_(fileId, tabName) {
  var ss = SpreadsheetApp.openById(fileId);
  var sheet = ss.getSheetByName(tabName);
  if (sheet) return sheet;
  var want = tabName.trim().toLowerCase();
  var all = ss.getSheets();
  for (var i = 0; i < all.length; i++) {
    if (all[i].getName().trim().toLowerCase() === want) return all[i];
  }
  throw new Error('Tab not found: "' + tabName + '" in ' + fileId);
}

function ensureHeader_(sheet, headerRow, col, label) {
  var cur = sheet.getRange(headerRow, col).getDisplayValue();
  if (!cur || !String(cur).trim()) sheet.getRange(headerRow, col).setValue(label);
}

function setCell_(sheet, row, col, value) {
  try { sheet.getRange(row, col).setValue(value); } catch (e) {}
}

// ── POST ────────────────────────────────────────────────────────────────────────

function postBatch_(sourceSheet, assets) {
  if (!assets.length) {
    // Empty full_refresh with an empty keep-list -> archive everything for this source.
    return postChunk_(sourceSheet, [], true, []);
  }
  var allKeys = assets.map(function (a) { return a.key; });
  var total = { upserted: 0, archived: 0, embedded: 0 };
  for (var i = 0; i < assets.length; i += CONFIG.BATCH_SIZE) {
    var chunk = assets.slice(i, i + CONFIG.BATCH_SIZE);
    var first = i === 0;
    // full_refresh on the FIRST chunk only, carrying ALL keys so a later chunk's
    // rows are never archived by this one.
    var res = postChunk_(sourceSheet, chunk, first, first ? allKeys : null);
    if (res) { total.upserted += res.upserted || 0; total.archived += res.archived || 0; total.embedded += res.embedded || 0; }
  }
  Logger.log(sourceSheet + ': ' + JSON.stringify(total));
  return total;
}

function postChunk_(sourceSheet, assets, fullRefresh, keepKeys) {
  var payload = { source_sheet: sourceSheet, full_refresh: !!fullRefresh, assets: assets };
  if (keepKeys != null) payload.keep_keys = keepKeys;
  // Defensive: strip any header-name prefix accidentally pasted into the value, and trim.
  var cid = String(CONFIG.CF_ACCESS_CLIENT_ID).replace(/^\s*CF-Access-Client-Id:\s*/i, '').trim();
  var csec = String(CONFIG.CF_ACCESS_CLIENT_SECRET).replace(/^\s*CF-Access-Client-Secret:\s*/i, '').trim();
  var tok = String(CONFIG.ENABLEMENT_TOKEN).replace(/^\s*X-Enablement-Token:\s*/i, '').trim();
  var resp = UrlFetchApp.fetch(CONFIG.WEBHOOK_URL, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
    followRedirects: false, // a CF Access redirect means the token was rejected — surface it clearly
    headers: {
      'X-Enablement-Token': tok,
      'CF-Access-Client-Id': cid,
      'CF-Access-Client-Secret': csec,
    },
  });
  var code = resp.getResponseCode();
  if (code === 301 || code === 302) {
    throw new Error(
      'Cloudflare Access rejected the request for ' + sourceSheet +
      ' (redirected to login). Check CONFIG.CF_ACCESS_CLIENT_ID / _SECRET are the bare token ' +
      'values (no "CF-Access-Client-Id:" prefix) and that the Service Auth policy is saved.'
    );
  }
  if (code !== 200) {
    throw new Error('ingest ' + sourceSheet + ' HTTP ' + code + ': ' + resp.getContentText().substring(0, 300));
  }
  return JSON.parse(resp.getContentText());
}
