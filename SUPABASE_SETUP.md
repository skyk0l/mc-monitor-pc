# Supabase setup for MC Monitor

1. In the Supabase SQL Editor, run [`supabase_setup.sql`](./supabase_setup.sql).
2. Then run [`supabase_public_profiles.sql`](./supabase_public_profiles.sql) to add
   public profiles, avatars, follows, and authenticated search.
3. Then run [`supabase_profile_social_details.sql`](./supabase_profile_social_details.sql)
   to enable profile privacy controls and follower/friend/subscription data.
4. Then run [`supabase_email_registration.sql`](./supabase_email_registration.sql)
   to allow verified-email signups to claim a unique profile nickname.
5. Then run [`supabase_server_catalog.sql`](./supabase_server_catalog.sql) to add the
   shared server catalog, daily voting, and online-history storage/RPCs.
6. In Authentication → Providers → Email, enable email confirmations and configure
   Supabase SMTP and the confirmation template to include `{{ .Token }}` so the app can
   verify the one-time code. The built-in test email service only delivers to
   project-authorized addresses. Existing nickname-only accounts
   remain accessible using their nickname and password; new registrations use a verified
   real email and can sign in by either email or nickname.
7. Keep the project's publishable key in the clients. Never put a `service_role` or secret
   key in the Android app or desktop executable. Row Level Security on `server_lists`
   restricts each account to its own row.

Nicknames are 3–16 ASCII letters, digits, or underscores; passwords must be at least
8 characters. Password recovery is not available for legacy nickname-only accounts. The
clients store session tokens in Android Keystore and Windows DPAPI respectively.

Profiles are searchable by authenticated users. Avatar files are public JPEGs (up to 2 MB),
but each account can upload only to its own storage folder. Server-list sharing is disabled
by default; a user must explicitly enable it in the profile editor before server addresses
appear in community search or on their profile. Follower, friend, and user-subscription lists
are separately opt-in; profile owners can always see their own lists. Friends are reciprocal
follows.

The clients keep a local copy and synchronize on sign-in, on server-list changes, on
app startup/resume, and periodically while running. The first sign-in merges local and
cloud lists. Later synchronization uses a three-way merge; when both devices rename the
same server since the last sync, the local device's name wins. Switching accounts replaces
the current list with the selected account's list to avoid mixing account data.

Catalog status samples, version, MOTD, and server icon are reported by signed-in clients
for servers in their own lists. History therefore starts with the first client report and
continues only while a reporting client is running; a scheduled Supabase Edge Function
collector has not been configured.
