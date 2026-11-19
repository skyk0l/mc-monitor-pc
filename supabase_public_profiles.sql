-- Public profiles, opt-in server discovery, follows, and avatar storage.
-- Apply after supabase_setup.sql. All search endpoints require authentication.

create table if not exists public.profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    username text not null,
    display_name text not null default '',
    bio text not null default '',
    avatar_path text,
    share_servers boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.profiles
    add column if not exists display_name text not null default '',
    add column if not exists bio text not null default '',
    add column if not exists avatar_path text,
    add column if not exists share_servers boolean not null default false,
    add column if not exists updated_at timestamptz not null default now();

create unique index if not exists profiles_username_lower_unique
    on public.profiles (lower(username));

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.profiles'::regclass
          and conname = 'profiles_avatar_path_matches_owner'
    ) then
        alter table public.profiles
            add constraint profiles_avatar_path_matches_owner
            check (avatar_path is null or avatar_path = id::text || '/avatar.jpg')
            not valid;
    end if;
end;
$$;

alter table public.profiles
    validate constraint profiles_avatar_path_matches_owner;

alter table public.profiles enable row level security;

drop policy if exists "Users can read own profile" on public.profiles;
drop policy if exists "Public profiles are viewable by everyone" on public.profiles;
drop policy if exists "Users can update own profile" on public.profiles;
drop policy if exists "Authenticated users can read profiles" on public.profiles;
create policy "Authenticated users can read profiles"
    on public.profiles for select
    to authenticated
    using (true);

create policy "Users can update own profile"
    on public.profiles for update
    to authenticated
    using (auth.uid() = id)
    with check (auth.uid() = id);

revoke insert, update, delete on public.profiles from authenticated;
grant select on public.profiles to authenticated;
grant update (display_name, bio, avatar_path, share_servers)
    on public.profiles to authenticated;

create or replace function public.touch_profile_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists touch_profile_updated_at on public.profiles;
create trigger touch_profile_updated_at
    before update on public.profiles
    for each row execute procedure public.touch_profile_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    profile_username text;
begin
    profile_username := coalesce(
        nullif(new.raw_user_meta_data ->> 'username', ''),
        split_part(coalesce(new.email, ''), '@', 1)
    );
    if profile_username !~ '^[A-Za-z0-9_]{3,16}$' then
        return new;
    end if;
    insert into public.profiles (id, username)
    values (new.id, profile_username)
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();

insert into public.profiles (id, username)
select
    users.id,
    coalesce(
        nullif(users.raw_user_meta_data ->> 'username', ''),
        split_part(coalesce(users.email, ''), '@', 1)
    )
from auth.users as users
where coalesce(
    nullif(users.raw_user_meta_data ->> 'username', ''),
    split_part(coalesce(users.email, ''), '@', 1)
) ~ '^[A-Za-z0-9_]{3,16}$'
on conflict (id) do nothing;

create table if not exists public.profile_follows (
    follower_id uuid not null references public.profiles (id) on delete cascade,
    followed_id uuid not null references public.profiles (id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (follower_id, followed_id),
    check (follower_id <> followed_id)
);

create index if not exists profile_follows_followed_idx
    on public.profile_follows (followed_id, created_at desc);

alter table public.profile_follows enable row level security;

drop policy if exists "Users can view their follows" on public.profile_follows;
create policy "Users can view their follows"
    on public.profile_follows for select
    to authenticated
    using (auth.uid() = follower_id);

drop policy if exists "Users can follow profiles" on public.profile_follows;
create policy "Users can follow profiles"
    on public.profile_follows for insert
    to authenticated
    with check (auth.uid() = follower_id and follower_id <> followed_id);

drop policy if exists "Users can unfollow profiles" on public.profile_follows;
create policy "Users can unfollow profiles"
    on public.profile_follows for delete
    to authenticated
    using (auth.uid() = follower_id);

revoke all on public.profile_follows from anon;
grant select, insert, delete on public.profile_follows to authenticated;

drop function if exists public.search_public_profiles(text, integer);
create function public.search_public_profiles(
    search_term text,
    result_limit integer default 20
)
returns table (
    id uuid,
    username text,
    display_name text,
    bio text,
    avatar_path text,
    share_servers boolean,
    is_following boolean
)
language sql
stable
security definer
set search_path = ''
as $$
    select
        profile.id,
        profile.username,
        profile.display_name,
        profile.bio,
        profile.avatar_path,
        profile.share_servers,
        exists (
            select 1
            from public.profile_follows as follow
            where follow.follower_id = auth.uid()
              and follow.followed_id = profile.id
        )
    from public.profiles as profile
    where auth.uid() is not null
      and profile.id <> auth.uid()
      and length(btrim(coalesce(search_term, ''))) between 2 and 80
      and (
          strpos(lower(profile.username), lower(btrim(search_term))) > 0
          or strpos(lower(coalesce(profile.display_name, '')),
                    lower(btrim(search_term))) > 0
          or strpos(lower(coalesce(profile.bio, '')),
                    lower(btrim(search_term))) > 0
      )
    order by
        case when lower(profile.username) = lower(btrim(search_term))
            then 0 else 1 end,
        lower(profile.username)
    limit greatest(1, least(coalesce(result_limit, 20), 30));
$$;

drop function if exists public.list_followed_profiles(integer);
create function public.list_followed_profiles(
    result_limit integer default 50
)
returns table (
    id uuid,
    username text,
    display_name text,
    bio text,
    avatar_path text,
    share_servers boolean,
    is_following boolean
)
language sql
stable
security definer
set search_path = ''
as $$
    select
        profile.id,
        profile.username,
        profile.display_name,
        profile.bio,
        profile.avatar_path,
        profile.share_servers,
        true
    from public.profile_follows as follow
    join public.profiles as profile on profile.id = follow.followed_id
    where auth.uid() is not null
      and follow.follower_id = auth.uid()
    order by lower(profile.username)
    limit greatest(1, least(coalesce(result_limit, 50), 100));
$$;

create or replace function public.search_public_servers(
    search_term text,
    result_limit integer default 30
)
returns table (
    owner_id uuid,
    username text,
    display_name text,
    server_name text,
    server_address text,
    protocol text
)
language sql
stable
security definer
set search_path = ''
as $$
    select
        profile.id,
        profile.username,
        profile.display_name,
        coalesce(nullif(btrim(server.value ->> 'name'), ''), server.value ->> 'address'),
        server.value ->> 'address',
        case when server.value ->> 'protocol' = 'bedrock' then 'bedrock' else 'java' end
    from public.profiles as profile
    join public.server_lists as server_list on server_list.user_id = profile.id
    cross join lateral jsonb_array_elements(server_list.servers) as server(value)
    where auth.uid() is not null
      and profile.share_servers
      and length(btrim(coalesce(search_term, ''))) between 2 and 80
      and coalesce(server.value ->> 'address', '') <> ''
      and strpos(
          lower(
              coalesce(server.value ->> 'name', '') || ' ' ||
              coalesce(server.value ->> 'address', '') || ' ' ||
              profile.username || ' ' || coalesce(profile.display_name, '')
          ),
          lower(btrim(search_term))
      ) > 0
    order by lower(profile.username), lower(server.value ->> 'address')
    limit greatest(1, least(coalesce(result_limit, 30), 50));
$$;

create or replace function public.list_public_servers(
    target_user uuid,
    result_limit integer default 50
)
returns table (
    server_name text,
    server_address text,
    protocol text
)
language sql
stable
security definer
set search_path = ''
as $$
    select
        coalesce(nullif(btrim(server.value ->> 'name'), ''), server.value ->> 'address'),
        server.value ->> 'address',
        case when server.value ->> 'protocol' = 'bedrock' then 'bedrock' else 'java' end
    from public.profiles as profile
    join public.server_lists as server_list on server_list.user_id = profile.id
    cross join lateral jsonb_array_elements(server_list.servers) as server(value)
    where auth.uid() is not null
      and profile.id = target_user
      and profile.share_servers
      and coalesce(server.value ->> 'address', '') <> ''
    order by lower(coalesce(server.value ->> 'name', server.value ->> 'address'))
    limit greatest(1, least(coalesce(result_limit, 50), 100));
$$;

revoke all on function public.search_public_profiles(text, integer) from public, anon;
revoke all on function public.list_followed_profiles(integer) from public, anon;
revoke all on function public.search_public_servers(text, integer) from public, anon;
revoke all on function public.list_public_servers(uuid, integer) from public, anon;
grant execute on function public.search_public_profiles(text, integer) to authenticated;
grant execute on function public.list_followed_profiles(integer) to authenticated;
grant execute on function public.search_public_servers(text, integer) to authenticated;
grant execute on function public.list_public_servers(uuid, integer) to authenticated;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('avatars', 'avatars', true, 2097152, array['image/jpeg'])
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "Users can upload their own avatar" on storage.objects;
create policy "Users can upload their own avatar"
    on storage.objects for insert
    to authenticated
    with check (
        bucket_id = 'avatars'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "Users can replace their own avatar" on storage.objects;
create policy "Users can replace their own avatar"
    on storage.objects for update
    to authenticated
    using (
        bucket_id = 'avatars'
        and (storage.foldername(name))[1] = auth.uid()::text
    )
    with check (
        bucket_id = 'avatars'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "Users can delete their own avatar" on storage.objects;
create policy "Users can delete their own avatar"
    on storage.objects for delete
    to authenticated
    using (
        bucket_id = 'avatars'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

grant insert, update, delete on storage.objects to authenticated;
