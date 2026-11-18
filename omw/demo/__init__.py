"""The demo: a synthetic company loaded through the real pipelines, end to end (``make demo``).

``world`` is the company: its calendar, ad campaigns and the campaign keys every source shares. ``funnel``
turns ad clicks into the leads, CRM deals and paying customers the other sources report, so the numbers of
the seven sources tie out. ``fixtures`` writes them as files in the shape each platform API answers with, in
two snapshots (``initial``, then ``restated`` one day later). ``replay`` is fixture mode: a ``requests``
transport that serves those files to the real clients, so nothing reaches the network. ``load`` runs the
seven dlt pipelines against a snapshot; ``examples`` asks ``query_metrics`` five questions, one mood each.
"""
