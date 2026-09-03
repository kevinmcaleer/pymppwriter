# Example gallery

Every spec below lives in [`examples/`](https://github.com/kevinmcaleer/pymppwriter/tree/main/examples)
and builds with:

```bash
pymppwriter build examples/<name>.json --template templates/template.mpp --out out.mpp
```

Each was verified by opening the result in Microsoft Project M365.

## `example_project.json` — the basics
Two tasks, a summary, one finish-to-start link.

## `durations.json` — durations showcase
A summary phase that rolls up from its children, durations displayed in days
and weeks, a half-day task, an estimated duration (`4 days?`) and a milestone.
*Check in Project:* the Duration column reads 8d / 3d / 5d / 2 wks / 0.5d /
4 days? / 0d, and the milestone renders as a diamond.

## `resources.json` — resources and assignments
Two resources (one at 200% max units), three assignments including a 50%
allocation. *Check in Project:* the Resource Sheet lists both; Gantt bars are
labelled `Kevin McAleer` and `CNC Robot[50%]`; durations are unaffected by
the assignments.

## `calendars.json` — working time
The Standard calendar customized to half-day Wednesdays with named holidays
(one single-day, one range), plus a second base calendar (`Nights`,
18:00–22:00) assigned to one task. *Check in Project:* Project tab → Change
Working Time.

## `full_project.json` — the works
Percent complete with actuals, notes, a constraint + deadline, priority and
task type, a manually-scheduled task, WBS codes, custom Text/Number/Date/Flag
fields, document metadata (author/company), a status date and £ currency.
