"""Write a project with the Python implementation, deterministically.

The TypeScript parity test builds the same project and compares bytes. GUIDs
come from a counter and the clock is fixed, so the only thing that can differ
is the two implementations disagreeing.
"""
import sys
from datetime import date, datetime as D

sys.path.insert(0, sys.argv[1])          # the repo root
from pymppwriter import (MppWriter, Project, Task, Relation, Resource,       # noqa: E402
                         Assignment, Calendar, CalendarException, set_baseline,
                         clear_baseline)

template, out = sys.argv[2], sys.argv[3]

guid = lambda n: bytes([n] * 16)         # noqa: E731

# calendars need explicit guids too: the dataclass default is a fresh uuid4,
# which would differ between the two runs
STD = Calendar(week={2: [(480, 720)]},                       # Wednesday half day
               exceptions=[CalendarException(date(2027, 3, 8), name="Team offsite")],
               guid=guid(30))
NIGHTS = Calendar("Nights", week={0: [(1080, 1320)], 1: [(1080, 1320)],
                                  2: [(1080, 1320)], 3: [(1080, 1320)],
                                  4: None, 5: None, 6: None}, guid=guid(31))

tasks = [
    Task(1, "Design phase", D(2027, 3, 1, 8), D(2027, 3, 5, 12), outline_level=1, guid=guid(1)),
    Task(2, "Sketch the robot", D(2027, 3, 1, 8), D(2027, 3, 2, 17), duration_days=2,
         outline_level=2, parent_uid=1, notes="Pencil first,\nCAD later {ok}", wbs="1.1",
         text={1: "design", 30: "T30"}, number={2: 42.5}, date={1: D(2027, 12, 25, 8)},
         flag={3: True}, percent_complete=50, priority=800, guid=guid(2)),
    Task(3, "CAD model", D(2027, 3, 3, 8), D(2027, 3, 5, 12), duration_days=2, outline_level=2,
         parent_uid=1, task_type="fixed_duration", deadline=D(2027, 3, 5, 17),
         constraint="SNET", constraint_date=D(2027, 3, 3, 8), guid=guid(3)),
    Task(4, "Design complete", D(2027, 3, 5, 12), D(2027, 3, 5, 12), duration_days=0,
         outline_level=2, parent_uid=1, guid=guid(4)),
    Task(5, "Build phase", D(2027, 3, 9, 13), D(2027, 3, 15, 22), outline_level=1, guid=guid(5)),
    Task(6, "Print parts", D(2027, 3, 9, 13), D(2027, 3, 12, 17), duration_days=3,
         outline_level=2, parent_uid=5, estimated=True, guid=guid(6)),
    Task(7, "Assemble", D(2027, 3, 15, 18), D(2027, 3, 15, 22), duration_days=0.5,
         outline_level=2, parent_uid=5, calendar="Nights", guid=guid(7)),
    Task(8, "Film the video", D(2027, 3, 16, 8), D(2027, 3, 18, 12), duration_days=2,
         outline_level=1, manual=True, guid=guid(8)),
]
rels = [Relation(2, 3), Relation(3, 4), Relation(4, 6, lag_days=1.0),
        Relation(6, 7), Relation(6, 8, type="SS")]
resources = [Resource(1, "Kevin McAleer", initials="KM", email="kev@example.com", guid=guid(20)),
             Resource(2, "Robot Arm", initials="RA", max_units=2.0, guid=guid(21))]
assns = [Assignment(2, 1), Assignment(3, 1, units=0.5), Assignment(6, 2)]

project = Project("Parity build", D(2027, 3, 1, 8), tasks, rels,
                  resources=resources, assignments=assns,
                  calendar=STD, calendars=[NIGHTS], default_calendar="Standard",
                  author="Kevin McAleer", subject="Robotics", keywords="robot;video",
                  comments="Written twice", manager="Kev", company="Kev's Robots",
                  category="Build", status_date=D(2027, 3, 8, 17),
                  currency_symbol="£", currency_code="GBP")

# baselines on tasks, resources and assignments: slot 0 and a numbered slot,
# plus a slot set then cleared so the cleared-entry shape is compared too
set_baseline(project)
set_baseline(project, 4)
set_baseline(project, 7)
clear_baseline(project, 7)

counter = iter(range(1, 1000))
writer = MppWriter(template,
                   new_guid=lambda: next(counter).to_bytes(16, "little"),
                   now=lambda: D(2026, 9, 4, 12, 0))
import warnings                                                       # noqa: E402
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    writer.write(project, out)
