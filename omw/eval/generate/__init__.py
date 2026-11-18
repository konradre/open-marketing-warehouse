"""Build the synthetic world for a seed: every source, every report grain, the campaign key mapping."""

from __future__ import annotations

from omw.eval.generate.ads import build_ads
from omw.eval.generate.crm import build_leads, build_twenty
from omw.eval.generate.rng import Rng
from omw.eval.generate.stripe import build_stripe
from omw.eval.generate.web import build_web
from omw.eval.world import SEED, World


def build_world(seed: int = SEED) -> World:
    rng = Rng(seed)
    world = World(seed=seed)
    build_ads(rng, world)
    build_web(rng, world)
    build_stripe(rng, world)
    build_twenty(rng, world)
    build_leads(rng, world)
    return world
