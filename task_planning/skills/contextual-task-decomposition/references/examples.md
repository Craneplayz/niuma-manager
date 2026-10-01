# Context-sensitive examples

These cases demonstrate how context changes the decision. They are not task templates and their wording or steps must not be copied into unrelated work.

## One direct action

Input: “明天下午三点前把已经写完的线代作业打印出来”，the file is final and a printer is available.

Good: `no_split` with one action such as “打印并带走最终版作业”, a short duration, and no fabricated research, outline, or revision phase.

Bad: six generic steps for understanding requirements, collecting material, drafting, reviewing, polishing, and submitting.

## Same title, missing decisive context

Input: “周五交实验报告”, with no report brief and no progress information.

Good: `needs_clarification`, asking whether the experiment/data collection is complete and what sections or rubric the teacher requires. Those answers change the sequence and effort.

Bad: immediately generating a full laboratory workflow or assuming the experiment has not started.

## Same title, late-stage context

Input: “周五交实验报告”, plus the teacher's brief, and the user says the experiment, figures, and first draft are complete.

Good: a short finishing plan grounded in the brief, such as checking required sections, correcting evidence/citations, exporting, and submitting. Do not recreate completed experiment or drafting work.

## Team task with limited responsibility

Input: a long group-presentation brief; the user owns only the results section and another member will merge the deck.

Good: plan only the user's results slides, evidence check, handoff, and any agreed rehearsal. Cite the brief and the user's responsibility.

Bad: assign the user every research, design, merging, and submission step for the whole group.

## Unknown details but user wants a draft

Input: the user cannot find the rubric and asks to continue.

Good: use a small explicit assumption such as “按现有题目说明作为最低完成标准”, mark affected steps with `assumption=true`, and warn that the plan may need revision if the rubric appears.

Bad: invent exact page counts, citation styles, grading weights, or intermediate deadlines.
