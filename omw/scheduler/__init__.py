"""Running the warehouse on one host: every source on its cadence, checks after every sync, a hook on failure.

``jobs`` is the job table (one job per source and cadence, plus the backup and its restore drill) and the
schedule they run on (``[schedule]`` in ``.dlt/config.toml``). ``sync`` runs one job the way every trigger
does: the pipeline, then the post-sync checks of its source, a row in ``ops.sync_runs``, a hook and a
non-zero exit code on failure (``omw sync <job>``). ``loop`` is the scheduler container's process
(``omw schedule run``): it starts whatever is due, one job at a time, each in its own process. ``systemd``
renders the same schedule as timer units for a host that would rather use systemd. ``notify`` is the hook:
a webhook and/or an e-mail.
"""
