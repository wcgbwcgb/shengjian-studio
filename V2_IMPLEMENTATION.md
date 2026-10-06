> 历史实现记录。当前流程、素材归属、连接快照及暂停字幕的行为以 README.md 和 CONSISTENCY_VALIDATION.md 为准。

# V2 creation workspace

Incremental migration of the existing vanilla frontend and FastAPI/SQLite application.
Existing data, APIs, version records, media protections, and publishing tools remain supported.

## Implementation sequence

1. Add a workspace service for creation, angle selection, evidence-aware script saves,
   versioned scenes, asset recommendations, and a background first-cut job.
2. Replace the dashboard entry with an idea composer and an evidence-backed opportunity feed.
3. Add one project workspace with Research / Script / Video views and persistent context.
4. Automatically turn approved script paragraphs into scenes and assemble actual media output.
5. Verify existing rules, new orchestration, desktop/mobile UI, and real rendering where available.

## Product boundaries

- Live research uses the existing configured provider and its web tools. Private platform
  data is not available; missing dates and engagement metrics remain unknown.
- Imported links and retrieved sources retain distinct evidence labels. Script citations
  identify reference material, not an automatic fact-check.
- Scene matching uses uploaded metadata and transcripts. Missing visuals become explicitly
  labeled graphic cards. No synthetic voice or stock-media provider is assumed.
- Earlier decisions can change. Derived scenes are rebuilt and previous video versions are
  retained with an update indication; renders never silently replace an existing export.

Validation and remaining limitations are recorded in `V2_VALIDATION.md` after implementation.
