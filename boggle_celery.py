from celery import Celery

app = Celery('boggle')
app.config_from_object('config', namespace='CELERY')
app.conf.imports = ('boggle',)
