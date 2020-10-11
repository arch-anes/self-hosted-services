#!/bin/bash

if [ -n "$MAYAN_REDIS_PASSWORD_FILE" ]; then export MAYAN_REDIS_PASSWORD="$(cat $MAYAN_REDIS_PASSWORD_FILE)"; fi;
if [ -n "$MAYAN_DATABASE_PASSWORD_FILE" ]; then export MAYAN_DATABASE_PASSWORD="$(cat $MAYAN_DATABASE_PASSWORD_FILE)"; fi;

export MAYAN_CELERY_BROKER_URL="redis://:$MAYAN_REDIS_PASSWORD@results:6379/0"
export MAYAN_CELERY_RESULT_BACKEND="redis://:$MAYAN_REDIS_PASSWORD@results:6379/1"
export MAYAN_DATABASES="{'default':{'ENGINE':'django.db.backends.postgresql','NAME':'mayan','PASSWORD':'$MAYAN_DATABASE_PASSWORD','USER':'postgres','HOST':'db'}}"

source /usr/local/bin/entrypoint.sh
