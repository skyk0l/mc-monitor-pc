-- Shared server directory and per-account daily votes.
-- Apply after the existing Supabase migrations.

create table if not exists public.server_catalog (
    id bigint generated always as identity primary key,
    address text not null,
    normalized_address text generated always as (lower(btrim(address))) stored,
    name text not null default '',
    protocol text not null default 'java'
        check (protocol in ('java', 'bedrock')),
    version text not null default '',
    motd text not null default '',
    favicon text not null default '',
    submitted_by uuid references auth.users (id) on delete set null,
    created_at timestamptz not null default now(),
    constraint server_catalog_address_valid check (
        length(address) between 1 and 253
        and address = btrim(address)
        and address !~ '[[:space:]/]'
        and address ~ '^[A-Za-z0-9._:-]+$'
    ),
    constraint server_catalog_name_valid check (length(name) <= 60)
);
alter table public.server_catalog
    add column if not exists version text not null default '',
    add column if not exists motd text not null default '',
    add column if not exists favicon text not null default '';

create unique index if not exists server_catalog_normalized_address_unique
    on public.server_catalog (normalized_address);
create index if not exists server_catalog_created_at_idx
    on public.server_catalog (created_at desc);

create table if not exists public.server_catalog_votes (
    server_id bigint not null references public.server_catalog (id) on delete cascade,
    user_id uuid not null references auth.users (id) on delete cascade,
    vote_date date not null default current_date,
    created_at timestamptz not null default now(),
    primary key (server_id, user_id, vote_date)
);

create table if not exists public.server_catalog_status_samples (
    server_id bigint not null references public.server_catalog (id) on delete cascade,
    reporter_id uuid not null references auth.users (id) on delete cascade,
    sampled_at timestamptz not null,
    online boolean not null,
    online_count integer not null check (online_count >= 0),
    max_count integer not null check (max_count >= 0),
    players jsonb not null default '[]'::jsonb
        check (jsonb_typeof(players) = 'array'),
    primary key (server_id, reporter_id, sampled_at)
);
create index if not exists server_catalog_status_latest_idx
    on public.server_catalog_status_samples (server_id, sampled_at desc);

create table if not exists public.server_catalog_collector_samples (
    server_id bigint not null references public.server_catalog (id) on delete cascade,
    sampled_at timestamptz not null,
    online boolean not null,
    online_count integer not null check (online_count >= 0),
    max_count integer not null check (max_count >= 0),
    players jsonb not null default '[]'::jsonb
        check (jsonb_typeof(players) = 'array'),
    primary key (server_id, sampled_at)
);
create index if not exists server_catalog_collector_latest_idx
    on public.server_catalog_collector_samples (server_id, sampled_at desc);

create table if not exists public.server_catalog_collection_state (
    singleton boolean primary key default true check (singleton),
    last_server_id bigint not null default 0
);

alter table public.server_catalog enable row level security;
alter table public.server_catalog_votes enable row level security;
alter table public.server_catalog_status_samples enable row level security;
alter table public.server_catalog_collector_samples enable row level security;
alter table public.server_catalog_collection_state enable row level security;
revoke all on public.server_catalog from public, anon, authenticated;
revoke all on public.server_catalog_votes from public, anon, authenticated;
revoke all on public.server_catalog_status_samples from public, anon, authenticated;
revoke all on public.server_catalog_collector_samples from public, anon, authenticated;
revoke all on public.server_catalog_collection_state from public, anon, authenticated;
grant select, insert, update
    on public.server_catalog_collector_samples to service_role;
grant update (version, motd, favicon)
    on public.server_catalog to service_role;

create or replace function public.add_server_to_catalog(
    server_address text,
    server_name text default '',
    server_protocol text default 'java'
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    clean_address text := lower(btrim(coalesce(server_address, '')));
    clean_name text := btrim(coalesce(server_name, ''));
    clean_protocol text := lower(btrim(coalesce(server_protocol, 'java')));
    catalog_id bigint;
begin
    if length(clean_address) not between 1 and 253
       or clean_address !~ '^[a-z0-9._:-]+$' then
        raise exception 'Invalid server address';
    end if;
    if length(clean_name) > 60 then
        raise exception 'Server name is too long';
    end if;
    if clean_protocol not in ('java', 'bedrock') then
        raise exception 'Invalid server protocol';
    end if;

    insert into public.server_catalog (address, name, protocol, submitted_by)
    values (clean_address, clean_name, clean_protocol, auth.uid())
    on conflict (normalized_address) do nothing
    returning id into catalog_id;
    if catalog_id is null then
        select server.id
        into catalog_id
        from public.server_catalog as server
        where server.normalized_address = clean_address;
    end if;
    return catalog_id;
end;
$$;

drop function if exists public.list_public_server_catalog(text, text, integer);
create or replace function public.list_public_server_catalog(
    search_term text default '',
    sort_by text default 'votes',
    result_limit integer default 50
)
returns table (
    id bigint,
    name text,
    address text,
    protocol text,
    version text,
    motd text,
    favicon text,
    votes bigint,
    subscribers bigint,
    online_count integer,
    has_voted_today boolean,
    created_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
    with subscriber_counts as (
        select
            lower(coalesce(entry.value ->> 'address', '')) as normalized_address,
            count(distinct user_list.user_id)::bigint as subscribers
        from public.server_lists as user_list
        cross join lateral jsonb_array_elements(user_list.servers) as entry(value)
        group by lower(coalesce(entry.value ->> 'address', ''))
    ),
    latest_status as (
        select distinct on (sample.server_id)
            sample.server_id,
            sample.online_count
        from (
            select server_id, sampled_at, online_count
            from public.server_catalog_status_samples
            union all
            select server_id, sampled_at, online_count
            from public.server_catalog_collector_samples
        ) as sample
        where sample.sampled_at >= now() - interval '10 minutes'
        order by sample.server_id, sample.sampled_at desc, sample.online_count desc
    ),
    directory as (
        select
            server.id,
            server.name,
            server.address,
            server.protocol,
            server.version,
            server.motd,
            server.favicon,
            count(vote.server_id)::bigint as votes,
            coalesce(subscriber_counts.subscribers, 0)::bigint as subscribers,
            latest_status.online_count::integer as online_count,
            exists (
                select 1
                from public.server_catalog_votes as own_vote
                where own_vote.server_id = server.id
                  and own_vote.user_id = auth.uid()
                  and own_vote.vote_date = current_date
            ) as has_voted_today,
            server.created_at
        from public.server_catalog as server
        left join public.server_catalog_votes as vote on vote.server_id = server.id
        left join subscriber_counts
            on subscriber_counts.normalized_address = server.normalized_address
        left join latest_status on latest_status.server_id = server.id
        where length(btrim(coalesce(search_term, ''))) <= 80
          and (
              btrim(coalesce(search_term, '')) = ''
              or strpos(lower(server.name), lower(btrim(search_term))) > 0
              or strpos(lower(server.address), lower(btrim(search_term))) > 0
          )
        group by server.id, subscriber_counts.subscribers, latest_status.online_count
    )
    select
        directory.id,
        directory.name,
        directory.address,
        directory.protocol,
        directory.version,
        directory.motd,
        directory.favicon,
        directory.votes,
        directory.subscribers,
        directory.online_count,
        directory.has_voted_today,
        directory.created_at
    from directory
    order by
        case when sort_by = 'newest' then directory.created_at end desc nulls last,
        case when sort_by = 'subscribers' then directory.subscribers end desc nulls last,
        case when sort_by = 'online' then directory.online_count end desc nulls last,
        case when sort_by not in ('newest', 'subscribers', 'online')
            then directory.votes end desc nulls last,
        lower(directory.name),
        directory.id
    limit greatest(1, least(coalesce(result_limit, 50), 100));
$$;

drop function if exists public.report_server_catalog_status(
    text, boolean, integer, integer, jsonb
);
create or replace function public.report_server_catalog_status(
    server_address text,
    is_online boolean,
    player_count integer,
    max_players integer,
    player_names jsonb default '[]'::jsonb,
    server_version text default '',
    server_motd text default '',
    server_icon text default ''
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    clean_address text := lower(btrim(coalesce(server_address, '')));
    clean_version text := btrim(coalesce(server_version, ''));
    clean_motd text := btrim(coalesce(server_motd, ''));
    clean_icon text := btrim(coalesce(server_icon, ''));
    target_server_id bigint;
begin
    if auth.uid() is null then
        raise exception 'Authentication required';
    end if;
    if length(clean_address) not between 1 and 253
       or clean_address !~ '^[a-z0-9._:-]+$'
       or is_online is null
       or player_count is null or player_count < 0
       or max_players is null or max_players < 0
       or (max_players > 0 and player_count > max_players)
       or length(clean_version) > 100
       or length(clean_motd) > 2000
       or length(clean_icon) > 60000
       or (clean_icon <> '' and clean_icon !~ '^[A-Za-z0-9+/]+={0,2}$') then
        raise exception 'Invalid server status';
    end if;
    if jsonb_typeof(coalesce(player_names, '[]'::jsonb)) <> 'array' then
        raise exception 'Invalid player list';
    end if;
    if jsonb_array_length(coalesce(player_names, '[]'::jsonb)) > 100
       or exists (
           select 1
           from jsonb_array_elements(coalesce(player_names, '[]'::jsonb)) as player(value)
           where jsonb_typeof(player.value) <> 'string'
              or length(player.value #>> '{}') > 64
       ) then
        raise exception 'Invalid player list';
    end if;

    select server.id into target_server_id
    from public.server_catalog as server
    where server.normalized_address = clean_address;
    if target_server_id is null then
        raise exception 'Server is not in the public catalog';
    end if;
    if not exists (
        select 1
        from public.server_lists as user_list
        cross join lateral jsonb_array_elements(user_list.servers) as entry(value)
        where user_list.user_id = auth.uid()
          and lower(coalesce(entry.value ->> 'address', '')) = clean_address
    ) then
        raise exception 'Server is not in your server list';
    end if;

    update public.server_catalog as server
    set version = case when clean_version = '' then server.version else clean_version end,
        motd = case when clean_motd = '' then server.motd else clean_motd end,
        favicon = case when clean_icon = '' then server.favicon else clean_icon end
    where server.id = target_server_id;

    delete from public.server_catalog_status_samples
    where server_id = target_server_id
      and sampled_at < now() - interval '90 days';

    insert into public.server_catalog_status_samples (
        server_id,
        reporter_id,
        sampled_at,
        online,
        online_count,
        max_count,
        players
    )
    values (
        target_server_id,
        auth.uid(),
        date_trunc('minute', now()),
        is_online,
        case when is_online then player_count else 0 end,
        max_players,
        coalesce(player_names, '[]'::jsonb)
    )
    on conflict (server_id, reporter_id, sampled_at) do nothing;
end;
$$;

create or replace function public.get_server_catalog_history(
    target_server_id bigint,
    result_limit integer default 300
)
returns table (
    sampled_at timestamptz,
    online_count integer,
    max_count integer,
    players jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
    with all_samples as (
        select server_id, sampled_at, online_count, max_count, players
        from public.server_catalog_status_samples
        union all
        select server_id, sampled_at, online_count, max_count, players
        from public.server_catalog_collector_samples
    )
    select distinct on (sample.sampled_at)
        sample.sampled_at,
        sample.online_count,
        sample.max_count,
        sample.players
    from all_samples as sample
    join public.server_catalog as server on server.id = sample.server_id
    where sample.server_id = target_server_id
      and sample.sampled_at >= server.created_at
      and sample.sampled_at >= now() - interval '30 days'
    order by sample.sampled_at desc, sample.online_count desc
    limit greatest(1, least(coalesce(result_limit, 300), 500));
$$;

create or replace function public.claim_server_catalog_collection_batch(
    result_limit integer default 25
)
returns table (id bigint, address text, protocol text)
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_cursor bigint;
    selected_ids bigint[];
begin
    if coalesce(auth.role(), '') <> 'service_role' then
        raise exception 'Service role required';
    end if;
    if result_limit is null or result_limit < 1 or result_limit > 100 then
        raise exception 'Invalid batch size';
    end if;

    insert into public.server_catalog_collection_state (singleton)
    values (true)
    on conflict (singleton) do nothing;
    select state.last_server_id
    into current_cursor
    from public.server_catalog_collection_state as state
    where state.singleton
    for update;

    select array_agg(selected.id order by selected.segment, selected.id)
    into selected_ids
    from (
        select candidates.id, candidates.segment
        from (
            select server.id, 0 as segment
            from public.server_catalog as server
            where server.id > current_cursor
            union all
            select server.id, 1 as segment
            from public.server_catalog as server
            where server.id <= current_cursor
        ) as candidates
        order by candidates.segment, candidates.id
        limit result_limit
    ) as selected;

    if selected_ids is null then
        return;
    end if;

    update public.server_catalog_collection_state
    set last_server_id = selected_ids[array_length(selected_ids, 1)]
    where singleton;

    return query
    select server.id, server.address, server.protocol
    from public.server_catalog as server
    where server.id = any(selected_ids)
    order by array_position(selected_ids, server.id);
end;
$$;

create or replace function public.vote_for_catalog_server(target_server_id bigint)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    inserted_count integer;
begin
    if auth.uid() is null then
        raise exception 'Authentication required';
    end if;
    if target_server_id is null or target_server_id <= 0 then
        raise exception 'Invalid server id';
    end if;
    insert into public.server_catalog_votes (server_id, user_id, vote_date)
    values (target_server_id, auth.uid(), current_date)
    on conflict (server_id, user_id, vote_date) do nothing;
    get diagnostics inserted_count = row_count;
    return inserted_count = 1;
end;
$$;

revoke all on function public.add_server_to_catalog(text, text, text)
    from public;
revoke all on function public.list_public_server_catalog(text, text, integer)
    from public;
revoke all on function public.report_server_catalog_status(
    text, boolean, integer, integer, jsonb, text, text, text
) from public;
revoke all on function public.get_server_catalog_history(bigint, integer)
    from public;
revoke all on function public.claim_server_catalog_collection_batch(integer)
    from public, anon, authenticated;
revoke all on function public.vote_for_catalog_server(bigint)
    from public, anon;
grant execute on function public.add_server_to_catalog(text, text, text)
    to anon, authenticated;
grant execute on function public.list_public_server_catalog(text, text, integer)
    to anon, authenticated;
grant execute on function public.report_server_catalog_status(
    text, boolean, integer, integer, jsonb, text, text, text
)
    to authenticated;
grant execute on function public.get_server_catalog_history(bigint, integer)
    to anon, authenticated;
grant execute on function public.claim_server_catalog_collection_batch(integer)
    to service_role;
grant execute on function public.vote_for_catalog_server(bigint)
    to authenticated;

notify pgrst, 'reload schema';
