# Supabase setup for MC Monitor

1. In the Supabase SQL Editor, run [`supabase_setup.sql`](./supabase_setup.sql).
2. Then run [`supabase_public_profiles.sql`](./supabase_public_profiles.sql) to add
   public profiles, avatars, follows, and authenticated search.
3. In Authentication → Providers → Email, turn off email confirmation. Accounts use a
   generated `*.mcmonitor.invalid` email so users can sign in with a nickname and password
   without an email service.
4. Keep the project's publishable key in the clients. Never put a `service_role` or secret
   key in the Android app or desktop executable. Row Level Security on `server_lists`
   restricts each account to its own row.

Nicknames are 3–16 ASCII letters, digits, or underscores; passwords must be at least
8 characters. Password recovery is not available for nickname-only accounts. The clients
store session tokens in Android Keystore and Windows DPAPI respectively.

Profiles are searchable by authenticated users. Avatar files are public JPEGs (up to 2 MB),
but each account can upload only to its own storage folder. Server-list sharing is disabled
by default; a user must explicitly enable it in the profile editor before server addresses
appear in community search or on their profile.

The clients keep a local copy and synchronize on sign-in, on server-list changes, on
app startup/resume, and periodically while running. The first sign-in merges local and
cloud lists. Later synchronization uses a three-way merge; when both devices rename the
same server since the last sync, the local device's name wins. Switching accounts replaces
the current list with the selected account's list to avoid mixing account data.
