-- Leads: one row per booking, all businesses in one table, split by client_slug.
-- Times are business-local (the profile's timezone); created_at is UTC.

create table leads (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  client_slug   text not null,
  tg_user_id    bigint not null,
  tg_username   text,
  tg_name       text,
  service       text not null,
  service_label text not null,
  slot_date     date not null,
  slot_time     time not null,
  phone         text,
  status        text not null default 'new'
    check (status in ('new', 'confirmed', 'done', 'cancelled'))
);

-- Free-slot screen and the capacity check: one business, one day.
create index leads_slot_idx on leads (client_slug, slot_date, slot_time);
-- Per-client limit and attaching a phone to the newest lead.
create index leads_client_idx on leads (client_slug, tg_user_id, created_at desc);
-- /leads and /api/leads: newest first, cursor by id.
create index leads_recent_idx on leads (client_slug, id desc);
