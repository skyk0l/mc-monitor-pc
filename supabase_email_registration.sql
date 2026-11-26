-- Finalize the username for users who verified a real email via Supabase Auth OTP.
-- Apply after supabase_public_profiles.sql.

create or replace function public.complete_email_registration(target_username text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_user_id uuid := auth.uid();
    current_email text;
    email_confirmed_at timestamptz;
    clean_username text := btrim(coalesce(target_username, ''));
begin
    if current_user_id is null then
        raise exception 'Authentication required';
    end if;
    select user_record.email, user_record.email_confirmed_at
    into current_email, email_confirmed_at
    from auth.users as user_record
    where user_record.id = current_user_id;
    if current_email is null
       or current_email like '%@users.mcmonitor.invalid'
       or email_confirmed_at is null then
        raise exception 'A verified email account is required';
    end if;
    if clean_username !~ '^[A-Za-z0-9_]{3,16}$' then
        raise exception 'Invalid username';
    end if;
    if exists (
        select 1
        from public.profiles as profile
        where lower(profile.username) = lower(clean_username)
          and profile.id <> current_user_id
    ) then
        raise exception 'Username is already in use';
    end if;

    insert into public.profiles (id, username)
    values (current_user_id, clean_username)
    on conflict (id) do update
        set username = excluded.username;
end;
$$;

revoke all on function public.complete_email_registration(text)
    from public, anon;
grant execute on function public.complete_email_registration(text)
    to authenticated;
