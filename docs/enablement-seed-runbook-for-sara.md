# Enablement Library — Initial Indexing Runbook (for Sara)

**Goal:** read every asset in the Enablement Drive folder and fill in the `ENABLEMENT_DB` sheet — one row per
asset, each with a clear title, a thorough summary, topics/tags, and links. Once this is done, our agent
"Kai" can find any asset and answer the team's questions about them. You only do this **initial pass once** —
after that, an automation keeps it up to date.

You'll use your **Claude Code** (Claude's coding tool on your Mac) and your Claude subscription, so this
**doesn't cost per-use API money**. Take it in batches; it does not have to be done in one sitting.

## What you need (one-time setup)
1. The **Google Drive connector (MCP)** in Claude. Jon + Artemis set up the connector for you (it needs a
   one-time Google Cloud config that's developer-level); you just **connect it and sign in with your Google
   account**. This lets Claude Code read the Enablement folder straight from the cloud (no downloading).
2. **Claude Code** open on your Mac.
3. A **Descript API token from Jon** (he'll send it to you privately — treat it like a password, paste it only into Claude Code, don't put it in the Sheet or share it). You do **not** need your own Descript account.
4. Links you'll work with:
   - Drive folder: https://drive.google.com/drive/folders/1oWVo3v9SogD-8XMCFUtekYmhrllTknNU
   - Sheet `ENABLEMENT_DB` tab: https://docs.google.com/spreadsheets/d/1kcgf06TslHR3IZ8nv6839UHh5Y3kPqHkJoc9gZprY3M/edit

## The columns you're filling in (ENABLEMENT_DB)
| Column | What goes in it |
|---|---|
| **Asset Name** | The file name |
| **Type** | `doc`, `image`, or `video` |
| **Drive Link** | The shareable link to the file in Drive |
| **Title** | A short human title (cleaned up from the filename if needed) |
| **Summary** | A thorough summary (see the prompts below — this is the important part) |
| **Topics/Tags** | 3–8 keywords/topics, comma-separated (e.g. `onboarding, pricing, SSO`) |
| **Audience/Use-case** | Who it's for / when you'd use it (e.g. "new-rep onboarding", "customer-facing") |
| **Transcript Link** | For videos only: link to the transcript file (see Step 2). Blank otherwise |
| **Status** | `indexed` normally; `needs_review` if you're unsure and want a second look |
| **Date Indexed** | Today's date |
| **Indexed By** | Your name |

(If the existing `ENABLEMENT_DB` tab already has slightly different column names, just match what's there —
the important fields are Title, Type, link, Summary, Tags, and Transcript Link for videos.)

## Step 1 — Connect the Google Drive MCP (cloud, no download)
Claude Code reads the folder straight from Google Drive using the **Drive connector (MCP)** — nothing to
download. Jon + Artemis set up the Cloud side (OAuth client). Then:
1. In Claude → **Settings → Connectors → Add custom connector** (if Jon already added it org-wide, skip to
   step 3). Enter **Server name:** `Google Drive`, **Remote MCP server URL:**
   `https://drivemcp.googleapis.com/mcp/v1`, and under **Advanced settings** paste the **OAuth Client ID +
   secret** Jon gives you. Click **Add**.
2. (If Jon added it for the whole org, you won't need the ID/secret — it'll just appear in your Connectors.)
3. **Sign in with your Google account** when the Google/Magic-Link screen appears (use the account that can
   see the Enablement folder). The connector is then available to Claude Code too.
4. The folder you'll work with: https://drive.google.com/drive/folders/1oWVo3v9SogD-8XMCFUtekYmhrllTknNU

**The skill you're learning:** in Claude Code you just *describe what you want* and it uses the Drive tools
(`list`/`search_files`, `read_file_content`, `download_file_content`) to go through the folder for you. You
don't run those commands yourself. If Claude says it can't see the folder, the connector isn't linked — re-do
the sign-in, or tell Jon.

## Step 2 — Videos: transcribe with Descript (via Claude Code + Jon's token)
Claude can't watch video, so we turn each video into text first, using Descript's API. You don't need a
Descript account — Jon gives you an **API token** and a **project name** to use. **Important: all videos go
into ONE Descript project** ("Enablement Library Transcripts") so Descript doesn't fill up with a separate
project per video.

In Claude Code, paste this (fill in your token + the folder path; use the project id Jon gives you if he
pre-made the project, otherwise leave it and the first import creates it):

> Use the Descript API (token: `<PASTE TOKEN>`, docs at https://docs.descriptapi.com). Find every video in
> the Enablement Drive folder using the Google Drive connector and **download each video's bytes** to
> transcribe it. Put them ALL in a single Descript project: if I give you a `project_id`, import
> into it; otherwise on the first video pass `project_name: "Enablement Library Transcripts"` to create the
> project, then reuse the returned `project_id` for every other video. For each video: do a direct upload
> (`POST /jobs/import/project_media` with `content_type` + `file_size` to get a signed URL, `PUT` the file
> bytes, then poll `GET /jobs/{job_id}` until done), then `POST /export/transcript` with format `txt`. Save
> each transcript as `<video-name>.txt` in a `Transcripts` subfolder. Keep filenames unique (Descript requires
> it within a project). List any videos that failed so I can retry them.

Then upload the `Transcripts` files to the `/Transcripts` subfolder in Drive, and grab each one's **Drive
link** for the `Transcript Link` column. After this, a video is just "a transcript text file" for Step 3.

(If the API flow gives you trouble, tell Jon — he can run the transcription himself in Descript and drop the
transcripts into `/Transcripts`, and you just summarize them in Step 3.)

## Step 3 — Run Claude Code over the folder
Open Claude Code, and point it at your local Enablement folder. Then paste it this instruction (edit the
folder path to yours):

> Using the Google Drive connector, read every file in the Enablement Drive folder (and its `Transcripts`
> subfolder). Skip the raw video files themselves. For each **document** (PDF, Word, text, slides), each **image**, and
> each **transcript** file, produce one row of a CSV with these columns: Asset Name, Type, Title, Summary,
> Topics/Tags, Audience/Use-case, Transcript Link (leave blank, I'll add video transcript links), Status,
> Date Indexed, Indexed By. Use the summary rules below. For images, "Summary" is a clear description of what
> the image shows and what it'd be used for. For transcripts, treat the transcript text as the source and
> summarize the video's content. Save the result as `enablement_index.csv`. Set Status to `needs_review` for
> anything you're unsure about, otherwise `indexed`. Indexed By = "Sara". Date Indexed = today.
>
> **Summary rules (be thorough, not a blurb):**
> - For a **research document**: capture its purpose, the key findings, the methodology/approach, the
>   important numbers or data points, and the conclusions/recommendations. Someone should understand what's
>   in it without opening it.
> - For a **regular doc / deck**: what it covers, the main points, and who it's for.
> - For an **image**: what it depicts, any text in it, and likely use.
> - For a **video transcript**: what the video teaches/covers, the main sections, and key takeaways.
> Keep each summary to a solid paragraph or two. Never use em-dashes.

Claude Code will read the files and build the CSV. If you have a lot of files, tell it to do them in batches
(e.g. "do the first 25, then continue") so it stays manageable.

## Step 4 — Put the rows into the sheet
1. Open `enablement_index.csv` (Claude Code saved it in the folder).
2. Add the **Drive Link** for each asset (Claude Code can list filenames; you paste the matching share links —
   or ask Claude Code to include the file paths so you can map them).
3. Paste the rows into the `ENABLEMENT_DB` tab (File → Import → paste, or copy-paste the columns).
4. For **videos**, fill the `Transcript Link` column with the transcript links from Step 2.
5. Skim anything marked `needs_review` and fix/confirm.

## Notes
- **Don't paste full transcripts into the sheet** — only the Summary. The transcript stays as its own file
  (linked in `Transcript Link`); our system reads the full transcript separately for deep search.
- **Cost:** this runs on your Claude subscription, not metered API, so batch freely.
- **When you're done:** tell Jon. We then run a one-time sync that loads everything into Kai's search brain,
  and turn on the automation that keeps it current going forward (you won't have to repeat this).
- **Questions / weird files:** mark them `needs_review` and move on; Jon + the team will sort the edge cases.
