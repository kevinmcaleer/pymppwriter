"""Build an .mpp with the Python API instead of the CLI."""
from datetime import datetime as D
from pymppwriter import MppWriter, Project, Task, Relation

project = Project(
    title="Robot build plan",
    start=D(2026, 10, 5, 8, 0),
    tasks=[
        Task(1, "Design", D(2026, 10, 5, 8), D(2026, 10, 9, 17), duration_days=5),
        Task(2, "Print parts", D(2026, 10, 12, 8), D(2026, 10, 14, 17), duration_days=3),
        Task(3, "Assemble", D(2026, 10, 15, 8), D(2026, 10, 16, 17), duration_days=2),
    ],
    relations=[Relation(1, 2), Relation(2, 3)],
)
MppWriter("templates/template.mpp").write(project, "robot-build.mpp")
print("wrote robot-build.mpp")
