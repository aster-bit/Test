"""Solution-free task environments (SkillForge v2)."""

from .base import TaskEnvironment, assert_solution_free


def get_env(name: str) -> TaskEnvironment:
    if name in ("rcpsp_scheduling", "scheduling"):
        from .scheduling import SchedulingEnv
        env = SchedulingEnv()
    elif name in ("calendar_multimode_scheduling", "calsched"):
        from .calsched import CalendarSchedulingEnv
        env = CalendarSchedulingEnv()
    elif name in ("degraded_manifest_extraction", "docrepair"):
        from .docrepair import DocRepairEnv
        env = DocRepairEnv()
    elif name in ("session_log_reconstruction", "streamrec"):
        from .streamrec import StreamRecEnv
        env = StreamRecEnv()
    else:
        raise KeyError(name)
    assert_solution_free(env)
    return env
