# Supabase setup for MC Monitor

1. In the Supabase SQL Editor, run [`supabase_setup.sql`](./supabase_setup.sql).
2. In Authentication → Providers → Email, turn off email confirmation. Accounts use a
   generated `*.mcmonitor.invalid` email so users can sign in with a nickname and password
   without an email service.
3. Keep the project's publishable key in the clients. Never put a `service_role` or secret
   key in the Android app or desktop executable. Row Level Security on `server_lists`
   restricts each account to its own row.

Nicknames are 3–16 ASCII letters, digits, or underscores; passwords must be at least
8 characters. Password recovery is not available for nickname-only accounts. The clients
store session tokens in Android Keystore and Windows DPAPI respectively.

The clients keep a local copy and synchronize on sign-in, on server-list changes, on
app startup/resume, and periodically while running. The first sign-in merges local and
cloud lists. Later synchronization uses a three-way merge; when both devices rename the
same server since the last sync, the local device's name wins. Switching accounts replaces
the current list with the selected account's list to avoid mixing account data.
