"""Solution-free task environments (SkillForge v2)."""

from .base import TaskEnvironment, assert_solution_free


def get_env(name: str) -> TaskEnvironment:
    if name in ("rcpsp_scheduling", "scheduling"):
        from .scheduling import SchedulingEnv
        env = SchedulingEnv()
    else:
        raise KeyError(name)
    assert_solution_free(env)
    return env
