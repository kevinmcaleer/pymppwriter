"""Read an MPP with MPXJ (Java) and print a compact summary for diffing."""
import sys, jpype, jpype.imports, mpxj
jpype.startJVM()
from org.mpxj.reader import UniversalProjectReader

def summary(path):
    pf = UniversalProjectReader().read(path)
    pp = pf.getProjectProperties()
    lines = [f"file-type {pp.getFileType()} app {pp.getFileApplication()} start {pp.getStartDate()} title {pp.getProjectTitle()}"]
    for t in pf.getTasks():
        lines.append(f"T uid={t.getUniqueID()} id={t.getID()} lvl={t.getOutlineLevel()} '{t.getName()}' {t.getStart()} -> {t.getFinish()} dur={t.getDuration()} preds={[str(r.getPredecessorTask().getUniqueID()) for r in t.getPredecessors()]}")
    for r in pf.getResources():
        lines.append(f"R uid={r.getUniqueID()} '{r.getName()}'")
    for a in pf.getResourceAssignments():
        lines.append(f"A task={a.getTaskUniqueID()} rsc={a.getResourceUniqueID()} units={a.getUnits()}")
    return "\n".join(lines)

if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(f"== {p}"); print(summary(p))
