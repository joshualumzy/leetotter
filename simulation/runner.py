"""Run scenarios over many seeds in parallel. Lives outside __main__ so worker processes can import it."""

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from simulation.simulate import Result, Scenario, simulate

SEEDS = 20


def run_all(scenarios: dict[str, Scenario]) -> dict[str, list[Result]]:
    jobs = [(label, sc, seed) for label, sc in scenarios.items() for seed in range(SEEDS)]
    with ProcessPoolExecutor() as pool:
        results = pool.map(_one, jobs, chunksize=8)
    grouped: dict[str, list[Result]] = defaultdict(list)
    for label, result in results:
        grouped[label].append(result)
    return grouped

def _one(job: tuple[str, Scenario, int]) -> tuple[str, Result]:
    label, sc, seed = job
    return label, simulate(sc, seed)
