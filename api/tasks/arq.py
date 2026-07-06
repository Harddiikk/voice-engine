"""ARQ worker configuration - setup logging before importing tasks"""

# Setup logging - this is now idempotent and safe to call multiple times
from api.logging_config import setup_logging

setup_logging()

# Now import ARQ and task dependencies
from arq import cron

# Producer-side redis pool + enqueue live in the light `arq_pool` module so
# services can enqueue jobs without the heavy worker imports below. Re-exported
# here for backward compatibility (`from api.tasks.arq import enqueue_job`).
from api.tasks.arq_pool import (  # noqa: F401
    REDIS_SETTINGS,
    enqueue_job,
    get_arq_redis,
)
from api.tasks.campaign_tasks import (
    process_campaign_batch,
    sync_campaign_source,
)
from api.tasks.credit_sweeper import settle_leaked_credit_holds
from api.tasks.knowledge_base_processing import process_knowledge_base_document
from api.tasks.plan_reminders import send_plan_renewal_reminders
from api.tasks.retry_dispatch import dispatch_due_campaign_retries
from api.tasks.run_integrations import run_integrations_post_workflow_run
from api.tasks.s3_upload import upload_voicemail_audio_to_s3
from api.tasks.workflow_completion import process_workflow_completion


class WorkerSettings:
    functions = [
        run_integrations_post_workflow_run,
        upload_voicemail_audio_to_s3,
        process_workflow_completion,
        sync_campaign_source,
        process_campaign_batch,
        process_knowledge_base_document,
        settle_leaked_credit_holds,
        dispatch_due_campaign_retries,
        send_plan_renewal_reminders,
    ]
    # Settle leaked credit reservation holds every 10 minutes so a missed
    # post-call settle can't strand a hold. (VoiceLink clients are provisioned
    # lazily — first KYC entry / number purchase — so no provisioning cron.)
    # Retry dispatch-cron every 5 minutes: a resilience backstop that dispatches
    # due, in-window campaign retries even if the in-process orchestrator loop
    # isn't running (safe vs it — batch claims use FOR UPDATE SKIP LOCKED).
    # Plan renewal reminders once a day (04:00 UTC ≈ 09:30 IST): email clients
    # whose monthly plan is within 5 days of expiry, and on expiry. Idempotent
    # per cycle/stage so a daily run never spams.
    cron_jobs = [
        cron(settle_leaked_credit_holds, minute={5, 15, 25, 35, 45, 55}),
        cron(dispatch_due_campaign_retries, minute=set(range(0, 60, 5))),
        cron(send_plan_renewal_reminders, hour={4}, minute={0}),
    ]
    redis_settings = REDIS_SETTINGS
    max_jobs = 10
    # Campaign batches legitimately block up to 600s waiting on concurrency
    # slots / the DID pool; arq's default job_timeout (300s) cancels them
    # mid-dispatch, stranding claimed contacts. Give jobs headroom past the
    # longest in-batch wait.
    job_timeout = 900


LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    # --- Handlers ---
    "handlers": {
        "console": {  # everything goes to stdout
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "level": "WARNING",  # only WARNING and above
            "formatter": "simple",
        },
    },
    # --- Formatters (optional) ---
    "formatters": {
        "simple": {
            "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        },
    },
    # --- Root logger ---
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    # --- Optionally silence Arq itself explicitly ---
    "loggers": {
        "arq": {  # arq.* loggers
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}


# `get_arq_redis` / `enqueue_job` / `REDIS_SETTINGS` are imported from
# `api.tasks.arq_pool` above and re-exported for existing callers.
