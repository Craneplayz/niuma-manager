# Model draft contract

Return JSON only, with exactly this semantic shape:

```json
{
  "status": "needs_clarification | ready_to_plan | no_split",
  "assessment": {
    "summary": "one concise description of the remaining work",
    "complexity": "uncertain | multi_step | atomic",
    "confidence": 0.0,
    "known_facts": [{"text": "supported fact", "source": "task | conversation:0 | source:brief"}],
    "assumptions": ["explicitly labeled assumption"],
    "missing_information": ["fact that would still change the plan"]
  },
  "questions": [
    {
      "id": "stable-question-id",
      "prompt": "one question",
      "why": "how the answer changes the plan",
      "answer_options": ["optional short choice"]
    }
  ],
  "plan": null
}
```

State invariants:

- `needs_clarification`: complexity is `uncertain`, contains one to three questions, and `plan` is null.
- `ready_to_plan`: complexity is `multi_step`, questions is empty, and plan contains at least two subtasks.
- `no_split`: complexity is `atomic`, questions is empty, and plan contains exactly one subtask.

A ready plan has this shape:

```json
{
  "goal": "observable final outcome",
  "strategy": "brief explanation tied to the supplied context",
  "warnings": ["uncertainty or scheduling limitation"],
  "subtasks": [
    {
      "id": "step-1",
      "title": "action-oriented title",
      "outcome": "observable completion criterion",
      "duration_minutes": 30,
      "depends_on": [],
      "scheduled_start": "2026-10-01T19:00:00+08:00",
      "scheduled_end": "2026-10-01T19:30:00+08:00",
      "source_basis": ["task", "source:brief", "conversation:2", "assumption"],
      "assumption": false
    }
  ]
}
```

`source_basis` contains one or more exact references: `task`, `source:<id>`, `conversation:<zero-based-index>`, or `assumption`. Set `assumption=true` exactly when that list contains `assumption`. Use null for both scheduled fields when exact placement is unsupported. IDs must be unique; dependencies must reference IDs in the same plan and cannot reference the current subtask. Durations are positive whole minutes. Do not add prose outside the JSON object.
