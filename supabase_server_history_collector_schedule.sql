-- Run after supabase_server_catalog.sql, deploying the Edge Function, and
-- storing the two secrets in Supabase Vault.

select cron.unschedule(jobid)
from cron.job
where jobname = 'mcmonitor-server-history';

select cron.schedule(
    'mcmonitor-server-history',
    '*/5 * * * *',
    $job$
    select net.http_post(
        url := (
            select decrypted_secret
            from vault.decrypted_secrets
            where name = 'mcmonitor_project_url'
        ) || '/functions/v1/collect-server-history',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'Authorization', 'Bearer ' || (
                select decrypted_secret
                from vault.decrypted_secrets
                where name = 'mcmonitor_service_role_key'
            )
        ),
        body := '{}'::jsonb
    );
    $job$
);
