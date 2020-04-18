import os
from dataclasses import MISSING

from kombu import Exchange, Queue


def get_env_setting(setting, default=MISSING):
    """ Get the environment setting or raise exception """

    try:
        env = os.environ[setting]
    except KeyError:
        if default is MISSING:
            error_msg = "Set the %s env variable" % setting
            raise ValueError(error_msg)
        env = default

    if isinstance(env, str):
        env = env.strip('\" ')  # strip spaces and quotes
    return env


SQLALCHEMY_DATABASE_URI = get_env_setting(
    'SQLALCHEMY_DATABASE_URI', 'postgresql://boggle@localhost:5432/boggle'
)
SQLALCHEMY_TRACK_MODIFICATIONS = False
EFFECTIVE_SCORE_SQL = True
BABEL_DEFAULT_LOCALE = 'nl'
BABEL_SUPPORTED_LOCALES = ['nl', 'en']
ROUND_DURATION_MINUTES = 3
GRACE_PERIOD_SECONDS = 10
DEFAULT_COUNTDOWN_SECONDS = 15
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'max_retries': 3, 'interval_start': 0,
    'interval_step': 0.2, 'interval_max': 0.2,
}
CELERY_TASK_QUEUES = (
    Queue('transient', Exchange('transient', delivery_mode=1),
          routing_key='transient', durable=False),
)
CELERY_TASK_DEFAULT_QUEUE = 'transient'
CELERY_BROKER_URL = get_env_setting(
    'CELERY_BROKER_URL', 'amqp://celery:celery@localhost:5672/celery'
)
CELERY_BROKER_HEARTBEAT = 10
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
DICTIONARY_DIR = 'dictionaries'
DICE_CONFIG_DIR = 'dice'
DEFAULT_DICE_CONFIG = get_env_setting('DEFAULT_DICE_CONFIG', 'International')
API_BASE_URL = get_env_setting('API_BASE_URL', '')
DISABLE_ASYNC_SCORING = False
