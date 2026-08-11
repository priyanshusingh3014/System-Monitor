web: python server/manage.py migrate && python server/manage.py collectstatic --noinput && gunicorn --chdir server config.wsgi:application
