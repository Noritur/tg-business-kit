-- A row is a claim until done_at is set. A process that dies mid-handler
-- leaves a claim that is never done; once it is older than the window in
-- kit.storage, Telegram's next re-delivery handles the update again.
-- received_at stays the first delivery (cleanup), claimed_at is the last attempt.

alter table processed_updates
  add column claimed_at timestamptz not null default now(),
  add column done_at    timestamptz;

-- Rows from before were handled, or given up on as the old log did.
update processed_updates set claimed_at = received_at, done_at = received_at;
