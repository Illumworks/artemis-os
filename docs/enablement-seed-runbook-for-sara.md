# Enablement Library — Initial Indexing Runbook (for Sara)

**Goal:** read every asset in the Enablement Drive folder and fill in the `ENABLEMENT_DB` sheet — one row per
asset, each with a clear title, a thorough summary, topics/tags, and links. Once this is done, our agent
"Kai" can find any asset and answer the team's questions about them. You only do this **initial pass once** —
after that, an automation keeps it up to date.

You'll use your **Claude Code** (Claude's coding tool on your Mac) and your Claude subscription, so this
**doesn't cost per-use API money**. Take it in batches; it does not have to be done in one sitting.

## What you need (one-time setup)
1. **Google Drive for Desktop** installed on your Mac, signed in to the account that can see the Enablement
   folder. This makes the Drive folder appear as a **normal folder on your Mac** so Claude Code can read it.
   (Alternative: download the folder as a zip and unzip it — either works.)
2. **Claude Code** open on your Mac.
3. **Descript** (for video transcripts — see Step 2).
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

## Step 1 — Get the files onto your Mac
Open the Enablement Drive folder in Google Drive for Desktop so it shows up as a folder on your Mac (note its
path, e.g. `~/Google Drive/.../Enablement Library`). That folder is what Claude Code will read.

## Step 2 — Videos: make transcripts first (Descript)
Claude can't watch video, so we turn each video into text first:
1. In **Descript**, batch-import the videos and let it transcribe them.
2. **Export each transcript** as a **plain-text (.txt) or Google Doc** file.
3. Put the transcripts in a subfolder named **`Transcripts`** inside the Enablement folder, and **name each
   transcript to match its video** (e.g. `onboarding-walkthrough.mp4` → `onboarding-walkthrough.txt`).
4. Get the shareable **Drive link** for each transcript — that goes in the `Transcript Link` column.

After this, a video is just "a transcript text file" as far as the next step is concerned.

## Step 3 — Run Claude Code over the folder
Open Claude Code, and point it at your local Enablement folder. Then paste it this instruction (edit the
folder path to yours):

> Read every file in the folder `<PATH TO ENABLEMENT FOLDER>` (including the `Transcripts` subfolder).
> Skip the raw video files themselves. For each **document** (PDF, Word, text, slides), each **image**, and
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
