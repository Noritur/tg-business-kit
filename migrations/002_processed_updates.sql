-- Webhook deduplication: Telegram re-delivers an update it believes failed.
-- The primary key makes "have we seen it" and "remember it" one statement.

create table processed_updates (
  bot         text not null,
  update_id   bigint not null,
  received_at timestamptz not null default now(),
  primary key (bot, update_id)
);

-- Old rows are deleted from time to time; this keeps that delete cheap.
create index processed_updates_received_idx on processed_updates (received_at);
