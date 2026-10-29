-- The confirm tap that created a lead: its callback_query.id. A re-delivered
-- update carries the same tap, so the replay after a crash finds the lead the
-- dead attempt saved instead of saving it again. Leads from before have none,
-- and NULLs never collide in a unique index.

alter table leads add column tg_query_id text;

create unique index leads_query_idx on leads (client_slug, tg_query_id);
