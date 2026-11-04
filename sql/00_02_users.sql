-- Users (DESIGN.md §1, §7). Passwords are substituted by deploy/clickhouse/initdb/00_omw_bootstrap.sh from
-- OMW_PIPELINE_PASSWORD and OMW_AGENT_PASSWORD; the placeholders below never reach the server.
-- Re-runnable: CREATE IF NOT EXISTS keeps existing grants, ALTER rotates the password.

CREATE USER IF NOT EXISTS pipeline IDENTIFIED WITH sha256_password BY '__OMW_PIPELINE_PASSWORD__';
ALTER USER pipeline IDENTIFIED WITH sha256_password BY '__OMW_PIPELINE_PASSWORD__';

CREATE USER IF NOT EXISTS agent IDENTIFIED WITH sha256_password BY '__OMW_AGENT_PASSWORD__';
ALTER USER agent IDENTIFIED WITH sha256_password BY '__OMW_AGENT_PASSWORD__';
