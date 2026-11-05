"""Twenty CRM source (docs/THREADS.md T9): dlt ``rest_api`` resources over a self-hosted Twenty.

``companies``, ``people`` and ``opportunities`` page with Twenty's ``starting_after`` cursor, load
incrementally on ``updatedAt`` (the filter is one ``filter=updatedAt[gte]:"..."`` parameter) and merge on
``id``. Attribution lives in optional custom fields (``campaignKey``, ``utmSource``, ``utmMedium``,
``utmCampaign``) that the marts resolve opportunity → person → company, so the pipeline can be read by
stage and by campaign key.
"""

from omw.sources.twenty.shapes import ATTRIBUTION_FIELDS, shape_company, shape_opportunity, shape_person
from omw.sources.twenty.source import SOURCE_NAME, twenty

__all__ = [
    "ATTRIBUTION_FIELDS",
    "SOURCE_NAME",
    "shape_company",
    "shape_opportunity",
    "shape_person",
    "twenty",
]
