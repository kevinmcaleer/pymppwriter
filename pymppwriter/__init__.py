from .writer import (MppWriter, Project, Task, Relation, Resource, Assignment,
                     Calendar, CalendarException, ScheduleWarning, validate)
from .reader import read_project, MppReadError

try:                                    # the installed distribution's version
    from importlib.metadata import PackageNotFoundError, version as _version
    __version__ = _version("pymppwriter")
except (ImportError, PackageNotFoundError):     # running from a source tree
    __version__ = "0.0.0.dev0"
__all__ = ["MppWriter", "Project", "Task", "Relation", "Resource", "Assignment",
           "Calendar", "CalendarException", "ScheduleWarning", "validate",
           "read_project", "MppReadError", "__version__"]
