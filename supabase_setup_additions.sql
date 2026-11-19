-- Дополнительные таблицы для MC Monitor (итерация с группами, приоритетами, профилями)
-- Применяется ПОСЛЕ supabase_setup.sql

-- 1. Группы серверов
create table if not exists public.server_groups (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references auth.users (id) on delete cascade not null,
    name text not null check (length(name) >= 1 and length(name) <= 60),
    color text not null default '#7ee0a3',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id, name)
);

alter table public.server_groups enable row level security;

drop policy if exists "Users can read their groups" on public.server_groups;
create policy "Users can read their groups"
    on public.server_groups for select
    to authenticated
    using (auth.uid() = user_id);

drop policy if exists "Users can create groups" on public.server_groups;
create policy "Users can create groups"
    on public.server_groups for insert
    to authenticated
    with check (auth.uid() = user_id);

drop policy if exists "Users can update their groups" on public.server_groups;
create policy "Users can update their groups"
    on public.server_groups for update
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "Users can delete their groups" on public.server_groups;
create policy "Users can delete their groups"
    on public.server_groups for delete
    to authenticated
    using (auth.uid() = user_id);

grant select, insert, update, delete on public.server_groups to authenticated;

-- 2. Привязка серверов к группам (сервер может быть в одной группе)
create table if not exists public.server_group_members (
    group_id uuid references public.server_groups (id) on delete cascade not null,
    server_address text not null,
    user_id uuid references auth.users (id) on delete cascade not null,
    added_at timestamptz not null default now(),
    primary key (group_id, server_address, user_id)
);

create unique index if not exists server_group_members_user_server_unique
    on public.server_group_members (user_id, server_address);

alter table public.server_group_members enable row level security;

drop policy if exists "Users can read group members" on public.server_group_members;
create policy "Users can read group members"
    on public.server_group_members for select
    to authenticated
    using (
        auth.uid() = user_id
        and exists (
            select 1
            from public.server_groups g
            where g.id = server_group_members.group_id
              and g.user_id = auth.uid()
        )
    );

drop policy if exists "Users can manage group members" on public.server_group_members;
create policy "Users can manage group members"
    on public.server_group_members for all
    to authenticated
    using (
        auth.uid() = user_id
        and exists (
            select 1
            from public.server_groups g
            where g.id = server_group_members.group_id
              and g.user_id = auth.uid()
        )
    )
    with check (
        auth.uid() = user_id
        and exists (
            select 1
            from public.server_groups g
            where g.id = server_group_members.group_id
              and g.user_id = auth.uid()
        )
    );

grant select, insert, update, delete on public.server_group_members to authenticated;

-- 3. Приоритетные никнеймы для отслеживания (на сервер)
create table if not exists public.player_priorities (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references auth.users (id) on delete cascade not null,
    server_address text not null,
    username text not null check (length(username) >= 1 and length(username) <= 32),
    created_at timestamptz not null default now(),
    unique (user_id, server_address, username)
);

alter table public.player_priorities enable row level security;

drop policy if exists "Users can read their priorities" on public.player_priorities;
create policy "Users can read their priorities"
    on public.player_priorities for select
    to authenticated
    using (auth.uid() = user_id);

drop policy if exists "Users can manage their priorities" on public.player_priorities;
create policy "Users can manage their priorities"
    on public.player_priorities for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

grant select, insert, update, delete on public.player_priorities to authenticated;

-- 4. Профили пользователей (публичные данные для поиска)
create table if not exists public.profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    username text not null unique check (length(username) >= 3 and length(username) <= 16),
    avatar_url text,
    bio text default '',
    hide_servers boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "Users can read own profile" on public.profiles;
create policy "Users can read own profile"
    on public.profiles for select
    to authenticated
    using (auth.uid() = id);

drop policy if exists "Users can update own profile" on public.profiles;
create policy "Users can update own profile"
    on public.profiles for update
    to authenticated
    using (auth.uid() = id)
    with check (auth.uid() = id);

drop policy if exists "Public profiles are viewable by everyone" on public.profiles;
create policy "Public profiles are viewable by everyone"
    on public.profiles for select
    to authenticated
    using (true);

grant select, insert, update on public.profiles to authenticated;

-- Триггер для авто-создания профиля при регистрации
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (id, username)
    values (
        new.id,
        coalesce(
            nullif(new.raw_user_meta_data ->> 'username', ''),
            split_part(new.email, '@', 1)
        )
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();

-- 5. Настройки приложения (цвет темы, горячие клавиши, виджеты)
create table if not exists public.app_settings (
    user_id uuid primary key references auth.users (id) on delete cascade,
    accent_color text not null default '#7ee0a3',
    widget_bg_color text not null default '#252B30',
    widget_opacity real not null default 0.85 check (widget_opacity >= 0 and widget_opacity <= 1),
    widget_accent_color text not null default '#A7C6A4',
    hotkey_show_widget text not null default 'ctrl+shift+m',
    updated_at timestamptz not null default now()
);

alter table public.app_settings enable row level security;

drop policy if exists "Users can read own settings" on public.app_settings;
create policy "Users can read own settings"
    on public.app_settings for select
    to authenticated
    using (auth.uid() = user_id);

drop policy if exists "Users can manage own settings" on public.app_settings;
create policy "Users can manage own settings"
    on public.app_settings for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

grant select, insert, update on public.app_settings to authenticated;

-- 6. Серверы пользователя с расширенными полями (показывать в виджете, группа)
create table if not exists public.server_settings (
    user_id uuid references auth.users (id) on delete cascade not null,
    server_address text not null,
    show_in_widget boolean not null default false,
    sort_order integer not null default 0,
    primary key (user_id, server_address)
);

alter table public.server_settings enable row level security;

drop policy if exists "Users can read own server settings" on public.server_settings;
create policy "Users can read own server settings"
    on public.server_settings for select
    to authenticated
    using (auth.uid() = user_id);

drop policy if exists "Users can manage own server settings" on public.server_settings;
create policy "Users can manage own server settings"
    on public.server_settings for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

grant select, insert, update on public.server_settings to authenticated;

-- Индексы для поиска
create index if not exists idx_profiles_username on public.profiles (username);
create index if not exists idx_player_priorities_server on public.player_priorities (server_address);
create index if not exists idx_server_group_members_address on public.server_group_members (server_address);