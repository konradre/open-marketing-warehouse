"""The three ad platforms and the campaign key each campaign maps to."""

from __future__ import annotations

from omw.eval.generate.campaigns import CAMPAIGNS
from omw.eval.generate.google import build_google
from omw.eval.generate.linkedin import build_linkedin
from omw.eval.generate.meta import build_meta
from omw.eval.generate.rng import Rng
from omw.eval.world import World


def build_ads(rng: Rng, world: World) -> None:
    for campaign in CAMPAIGNS:
        world.campaign_keys[(campaign.source, campaign.campaign_id)] = campaign.key
        if campaign.manual_key:
            world.manual_keys.append(
                {
                    "source": campaign.source,
                    "native_id": campaign.campaign_id,
                    "campaign_key": campaign.key,
                    "campaign_name": campaign.name,
                }
            )
    build_meta(rng, world)
    build_google(rng, world)
    build_linkedin(rng, world)
