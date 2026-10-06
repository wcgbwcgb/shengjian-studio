# V2 implementation and validation

The subsequent local Claude Code CLI integration is documented and validated in
[CLAUDE_CLI_VALIDATION.md](CLAUDE_CLI_VALIDATION.md). The original V2 flow below remains available.

Verified locally on 2026-10-05. The existing application was incrementally refactored;
no new frontend framework, package manager, or Python dependency was introduced.

## Product changes

- Idea-first homepage, quick starts, transparent inspiration seeds, research-backed
  opportunities, and a compact continue-working list replace administrative metrics.
- A persistent workspace joins research, angle selection, writing, scenes, assets,
  video preview, history, and export. Tabs allow returning to earlier decisions.
- Research is summarized for short videos: facts, source material, competing narratives,
  content gaps, audience reactions when available, and three proposed angles.
- Choosing an angle starts script generation. Paragraph-level source references survive
  editing; modified claims are marked for review. Unknown references cannot become evidence.
- Script edits autosave with a local recovery draft. Concurrent updates require an
  explicit choice, and background generation cannot overwrite newer manual edits.
- Accepted scripts become versioned scenes with narration, visual suggestions,
  subtitles, timing, source URLs and recommended assets. Direct edits synchronize scenes;
  unchanged manual timing and asset selections remain intact.
- Scene replacement supports uploaded video/audio/images and reuse from other projects.
  Recommendations use filenames, descriptions and available transcripts, with honest
  fallback labels when there is no semantic match.
- The first-cut worker automatically analyzes selected media, assembles scene timing,
  splits readable subtitles, creates designed text cards for missing visuals, and renders
  a playable MP4. Protected music remains complete and at its original gain.
- Existing advanced timeline editing, script/version APIs, templates, settings, publishing
  records and export packs remain available. Adopting a result through the existing editor
  updates the V2 workspace and marks derived scenes for synchronization when required.
- Research opportunities start independent videos, copying evidence into the new project
  without modifying the earlier work.

## Executed checks

### Python

Command: `.venv/Scripts/python.exe -m unittest discover -s tests -v`

**40 tests passed; none skipped.** Includes existing persistence, billing/model selection,
credential privacy, timeline bounds, source mapping, music protection and real-render tests,
plus V2 workflow, citations, timing synchronization, stale-editor protection, restoration,
cross-project isolation, legacy adoption and background generation races.

Real-render checks generate synthetic video and audio, reorder clips, retain protected
music, render Chinese subtitles, decode the output, generate a cover and export a ZIP.
These fixtures do not replace human review of a real creator's recordings.

### Browser

Command: `node scripts/check-ui.mjs`

**12 browser groups passed** against real FastAPI, SQLite, worker and media processing.
Only AI responses are deterministic fixtures. Each run uses a new database under
`artifacts/v2-test-*`; the production project's database is not used for these tests.

1. Homepage hierarchy, truthful inspiration seeds and empty-input validation.
2. Automatic research, three angles and source inspection.
3. Angle-to-script generation, persistent direct edits and citation review flags.
4. Contextual rewriting without modifying other paragraphs.
5. Script-to-scenes conversion and downstream timing recalculation.
6. Real PNG upload and scene asset replacement.
7. Real video rendering, browser playback and seeking, Chinese captions, HD export,
   and downloading a nonempty publishing ZIP.
8. Editing earlier script decisions synchronizes scenes and retains old video versions.
9. Restoring a script from history creates a new branch.
10. Concurrent script updates preserve the user's selected draft.
11. 390px mobile home, research, script and video layouts have no horizontal overflow.
12. Projects, library, films, settings and publishing routes render without uncaught errors.

Screenshots were inspected for desktop home, research, script, scenes, video preview and
mobile layouts. Results are in `artifacts/v2-browser-results.json` and `artifacts/v2-*.png`.

### Real local service

Command: `node scripts/check-live-ui.mjs`

The production service runs at **http://127.0.0.1:8765**. The read-only smoke check passed
for the real homepage, the existing project list, opening an existing workspace and settings.
The existing production project remains present; no fixture research was inserted into it.

JavaScript syntax checks and Python compilation also passed. There is no TypeScript or
bundler in this repository. The existing test-client deprecation warning remains upstream.

## Local media setup

The available local video executables were discovered and configured in the existing machine
settings. Their minimal build lacks libx264, libass and PNG support. Encoder discovery now
supports available H.264 encoders; Windows-native text rasterization and image conversion
cover subtitle, image and cover rendering without adding dependencies. Standard FFmpeg builds
continue using the existing libx264/libass path. No third-party executable was copied into
the repository. If the configured installation is removed, choose an available installation
in advanced settings; existing scenes and source media remain preserved.

## Remaining limitations

- No valid AI credential is configured. Real-provider research, live source access and
  model-generated copy still require verification with the creator's service connection.
  Test research is explicitly fixture data, not evidence that those services work.
- Research uses the existing provider's public web tools. Private/blocked platform data,
  exhaustive Xiaohongshu/Douyin/Bilibili engagement, and live trend monitoring are not available.
  The homepage shows saved research or labeled inspiration seeds, not invented live trends.
- Citations make claims traceable, but do not automatically prove that the source supports
  each sentence. Source provenance labels and changed-claim review indicators remain visible.
- Asset matching uses metadata and transcripts, not a new multimodal vision model. There
  is no stock-media search, generative-video provider, voice synthesis, or automatic music bed.
- Uploaded media keeps its recorded sound. Text-card/image-only drafts have a silence track
  and are labeled as lacking voiceover; they are not presented as fully voiced productions.
- Rendering supports cuts, containment, timing, captions and gain. More advanced transitions,
  effects, music mixing and an interactive drag-and-drop timeline remain future work.
- Optional transcription still needs faster-whisper and its downloaded model. It is not
  required for the verified image/text-card creation loop.
- The application continues to be a local, single-process workspace with a single worker.
