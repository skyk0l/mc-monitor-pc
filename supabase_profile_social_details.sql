-- Per-section profile privacy and social relationship data.
-- Apply after supabase_public_profiles.sql.

alter table public.profiles
    add column if not exists share_followers boolean not null default false,
    add column if not exists share_friends boolean not null default false,
    add column if not exists share_subscriptions boolean not null default false;

grant update (share_followers, share_friends, share_subscriptions)
    on public.profiles to authenticated;

create or replace function public.get_profile_social_summary(target_user uuid)
returns table (
    followers_count integer,
    subscriptions_count integer,
    friends_count integer,
    is_following boolean,
    is_followed_by boolean,
    can_view_servers boolean,
    can_view_followers boolean,
    can_view_friends boolean,
    can_view_subscriptions boolean
)
language sql
stable
security definer
set search_path = ''
as $$
    select
        case when auth.uid() = profile.id or profile.share_followers
            then (
                select count(*)::integer
                from public.profile_follows as edge
                where edge.followed_id = profile.id
            )
            else 0
        end,
        case when auth.uid() = profile.id or profile.share_subscriptions
            then (
                select count(*)::integer
                from public.profile_follows as edge
                where edge.follower_id = profile.id
            )
            else 0
        end,
        case when auth.uid() = profile.id or profile.share_friends
            then (
                select count(*)::integer
                from public.profile_follows as edge
                join public.profile_follows as reciprocal
                  on reciprocal.follower_id = edge.followed_id
                 and reciprocal.followed_id = edge.follower_id
                where edge.follower_id = profile.id
            )
            else 0
        end,
        exists (
            select 1
            from public.profile_follows as edge
            where edge.follower_id = auth.uid()
              and edge.followed_id = profile.id
        ),
        exists (
            select 1
            from public.profile_follows as edge
            where edge.follower_id = profile.id
              and edge.followed_id = auth.uid()
        ),
        auth.uid() = profile.id or profile.share_servers,
        auth.uid() = profile.id or profile.share_followers,
        auth.uid() = profile.id or profile.share_friends,
        auth.uid() = profile.id or profile.share_subscriptions
    from public.profiles as profile
    where auth.uid() is not null
      and profile.id = target_user;
$$;

create or replace function public.list_profile_relationships(
    target_user uuid,
    relationship text,
    result_limit integer default 50
)
returns table (
    id uuid,
    username text,
    display_name text,
    bio text,
    avatar_path text,
    share_servers boolean,
    is_following boolean,
    is_followed_by boolean
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
            from public.profile_follows as viewer_edge
            where viewer_edge.follower_id = auth.uid()
              and viewer_edge.followed_id = profile.id
        ),
        exists (
            select 1
            from public.profile_follows as viewer_edge
            where viewer_edge.follower_id = profile.id
              and viewer_edge.followed_id = auth.uid()
        )
    from public.profiles as owner
    join public.profile_follows as edge
      on (
          relationship = 'followers'
          and edge.followed_id = owner.id
          and (auth.uid() = owner.id or owner.share_followers)
      ) or (
          relationship = 'subscriptions'
          and edge.follower_id = owner.id
          and (auth.uid() = owner.id or owner.share_subscriptions)
      ) or (
          relationship = 'friends'
          and edge.follower_id = owner.id
          and (auth.uid() = owner.id or owner.share_friends)
          and exists (
              select 1
              from public.profile_follows as reciprocal
              where reciprocal.follower_id = edge.followed_id
                and reciprocal.followed_id = edge.follower_id
          )
      )
    join public.profiles as profile
      on profile.id = case
          when relationship = 'followers' then edge.follower_id
          else edge.followed_id
      end
    where auth.uid() is not null
      and owner.id = target_user
      and relationship in ('followers', 'subscriptions', 'friends')
    order by lower(profile.username)
    limit greatest(1, least(coalesce(result_limit, 50), 100));
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
      and (auth.uid() = profile.id or profile.share_servers)
      and coalesce(server.value ->> 'address', '') <> ''
    order by lower(coalesce(server.value ->> 'name', server.value ->> 'address'))
    limit greatest(1, least(coalesce(result_limit, 50), 100));
$$;

revoke all on function public.get_profile_social_summary(uuid) from public, anon;
revoke all on function public.list_profile_relationships(uuid, text, integer)
    from public, anon;
revoke all on function public.list_public_servers(uuid, integer) from public, anon;
grant execute on function public.get_profile_social_summary(uuid) to authenticated;
grant execute on function public.list_profile_relationships(uuid, text, integer)
    to authenticated;
grant execute on function public.list_public_servers(uuid, integer) to authenticated;
