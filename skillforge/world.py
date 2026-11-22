"""WorldGenerator — reproducible scenario sets with strict split separation.

Splits are generated from *independent* seed namespaces:

* ``train``       — used by the generator/selection loop; adversarial cases are appended here.
* ``validation``  — used to choose finalists (guards against overfitting to train).
* ``holdout``     — sealed. Generated once, evaluated once at the very end. It
                    additionally contains ``holdout_only`` families (distribution
                    drift) and randomly perturbed variants. The adversary never
                    receives it.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from .domains.base import DomainPlugin, Scenario


def derive_seed(master: int, *parts: object) -> int:
    h = hashlib.sha256(repr((master, *parts)).encode()).digest()
    return int.from_bytes(h[:8], "big")


@dataclass
class WorldConfig:
    train_size: int = 160
    validation_size: int = 120
    holdout_size: int = 300
    holdout_perturbation_rate: float = 0.3


class WorldGenerator:
    def __init__(self, plugin: DomainPlugin, master_seed: int, config: WorldConfig | None = None):
        self.plugin = plugin
        self.master_seed = master_seed
        self.config = config or WorldConfig()

    def _families(self, include_holdout_only: bool):
        return [f for f in self.plugin.families() if include_holdout_only or not f.holdout_only]

    def generate_split(self, split: str, size: int) -> list[Scenario]:
        is_holdout = split.startswith("holdout")
        include_drift = is_holdout
        fams = self._families(include_drift)
        weights = [f.weight for f in fams]
        rng = random.Random(derive_seed(self.master_seed, split, "families"))
        # stratified: every family appears at least twice, the rest by weight
        plan: list[str] = []
        for f in fams:
            plan.extend([f.name] * 2)
        while len(plan) < size:
            plan.append(rng.choices([f.name for f in fams], weights=weights)[0])
        plan = plan[:max(size, 2 * len(fams))]
        rng.shuffle(plan)
        out: list[Scenario] = []
        perts = self.plugin.perturbations()
        for i, fam in enumerate(plan):
            seed = derive_seed(self.master_seed, split, i)
            s = self.plugin.generate(fam, random.Random(seed), f"{split}-{i:04d}", seed)
            if is_holdout and perts and rng.random() < self.config.holdout_perturbation_rate:
                prng = random.Random(derive_seed(self.master_seed, split, i, "perturb"))
                for _ in range(prng.randint(1, 2)):
                    p = prng.choice(perts)
                    s2 = p.apply(s, prng)
                    if s2 is not None:
                        s = s2
                s.origin = "perturbed"
            out.append(s)
        return out

    def build(self) -> dict[str, list[Scenario]]:
        c = self.config
        return {
            "train": self.generate_split("train", c.train_size),
            "validation": self.generate_split("validation", c.validation_size),
            "holdout": self.generate_split("holdout", c.holdout_size),
        }
