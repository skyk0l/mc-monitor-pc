create table if not exists public.server_lists (
    user_id uuid primary key references auth.users (id) on delete cascade,
    servers jsonb not null default '[]'::jsonb
        check (jsonb_typeof(servers) = 'array'),
    updated_at timestamptz not null default now()
);

alter table public.server_lists enable row level security;

drop policy if exists "Users can read their server list" on public.server_lists;
create policy "Users can read their server list"
    on public.server_lists for select
    to authenticated
    using (auth.uid() = user_id);

drop policy if exists "Users can create their server list" on public.server_lists;
create policy "Users can create their server list"
    on public.server_lists for insert
    to authenticated
    with check (auth.uid() = user_id);

drop policy if exists "Users can update their server list" on public.server_lists;
create policy "Users can update their server list"
    on public.server_lists for update
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

grant select, insert, update on public.server_lists to authenticated;
