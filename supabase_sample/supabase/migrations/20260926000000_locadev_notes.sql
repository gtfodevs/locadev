-- Sample table so the smoke test can exercise PostgREST.
create table if not exists public.locadev_notes (
  id bigint generated always as identity primary key,
  body text not null,
  created_at timestamptz not null default now()
);
alter table public.locadev_notes enable row level security;
create policy "anon can read notes" on public.locadev_notes for select using (true);
