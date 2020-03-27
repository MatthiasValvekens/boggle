A simple REST-based Boggle server implemented using Flask, with a single-page web frontend built with Bulma.

Configuration is in config.py. Requires PostgreSQL and (optionally) a Celery-compatible message broker such as RabbitMQ or redis.
The Celery setup is not strictly necessary, but not including it may cause request latency & memory usage issues with large grid sizes and dictionaries.

Dictionaries are not included, but a simple line-based wordlist saved with a `*.dic` exteinsion will do.
