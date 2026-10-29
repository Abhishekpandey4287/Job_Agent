# Resume profiles

Each subdirectory is one role track. The Resume Agent picks the folder whose
skills/title best match the job, then customizes that folder's `master.tex`.

## Folder layout

```
profiles/
  1-Android/          # priority 1 (preferred in Excel when scores tie)
    master.tex        # required — source of truth for LaTeX resumes
    master_resume.json  # auto-generated from master.tex if missing
    profile.json      # optional metadata
  2-Backend/
    master.tex
    master_resume.json
    profile.json
  3-Flutter/          # add when ready — same layout
    master.tex
    profile.json
```

## profile.json example

```json
{
  "id": "flutter",
  "label": "Flutter",
  "priority": 3,
  "aliases": ["FLUTTER", "Dart", "Cross-Platform"],
  "title_hints": ["flutter", "dart", "cross-platform mobile"]
}
```

Numbered prefixes (`1-`, `2-`, `3-`) control Excel ranking priority (lower = first).

## Turn a profile search on or off

In `.env`:

```
PROFILE_BASED_SEARCH=true
PROFILE_ANDROID=true
PROFILE_BACKEND=false
```

`PROFILE_BASED_SEARCH=false` skips every profile. `PROFILE_<ID>=false` skips that
track only. The id is the folder name without the priority number (`ANDROID`,
`BACKEND`). A profile with no line stays on.

LLM failover for personalization: Gemini → Groq → Cerebras (see `.env`).
