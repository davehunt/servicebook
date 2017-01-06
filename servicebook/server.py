import json
import os
import logging.config
import sys
import argparse
import time
from functools import partial, wraps

from werkzeug.exceptions import HTTPException
from flask import Flask, g, request, Response, abort, current_app, jsonify
from flask.ext.iniconfig import INIConfig
from flask_restless_swagger import SwagAPIManager as APIManager

from servicebook.db import init, Session
from servicebook.mappings import published
from flask_restless import views
from flask_restless import DefaultSerializer
from sqlalchemy.inspection import inspect as sa_inspect


# see https://github.com/jfinkels/flask-restless/issues/619
# hack until flask-restless provides the API class in
# pre/post processors
def _catch_integrity_errors(session):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kw):
            from sqlalchemy.exc import (DataError, IntegrityError,
                                        ProgrammingError)
            try:
                try:
                    g.api = func.im_self
                except Exception:
                    g.api = func.__self__
                return func(*args, **kw)
            except (DataError, IntegrityError, ProgrammingError) as exception:
                session.rollback()
                current_app.logger.exception(str(exception))
                return dict(message=type(exception).__name__), 400
        return wrapped
    return decorator


views.catch_integrity_errors = _catch_integrity_errors


HERE = os.path.dirname(__file__)
DEFAULT_INI_FILE = os.path.join(HERE, '..', 'servicebook.ini')
_DEBUG = True


def add_timestamp(method, *args, **kw):
    # getting the existing last_modified if the resource exists
    model = g.api.model
    session = g.api.session
    query = session.query(model)
    entry = query = query.filter(model.id == kw['instance_id']).one()

    # if this is an update, we want a If-Match header
    if entry is not None:
        etag = str(entry.last_modified)

        if not request.if_match:
            abort(428)

        if etag not in request.if_match:
            abort(412)

    kw['data']['last_modified'] = int(time.time() * 1000)


class NotModified(HTTPException):
    code = 304

    def get_response(self, environment):
        return Response(status=304)


class JsonSerializer(DefaultSerializer):
    def serialize(self, instance, only=None):
        return {'data': instance.to_json()}


def create_app(ini_file=DEFAULT_INI_FILE):
    app = Flask(__name__)
    INIConfig(app)
    app.config.from_inifile(ini_file)
    app.secret_key = app.config['common']['secret_key']
    sqluri = app.config['common']['sqluri']
    app.db = init(sqluri)

    preprocessors = {}
    for method in ('POST', 'PATCH_SINGLE', 'PUT_SINGLE', 'DELETE_SINGLE'):
        preprocessors[method] = [partial(add_timestamp, method)]

    app.db.session = Session()

    manager = APIManager(app, flask_sqlalchemy_db=app.db,
                         preprocessors=preprocessors)


    methods = ['GET', 'POST', 'DELETE', 'PATCH', 'PUT']

    for model in published:
        manager.create_api(model, methods=methods,
                           serializer_class=JsonSerializer,
                           page_size=50)


    @app.route('/api/')
    def get_models():
        models = []
        for model in published:
            name = model.__table__.name
            inspected = sa_inspect(model)
            # XXX collection name may vary
            primary_key = [key.name for key in inspected.primary_key]
            models.append({'name': name, 'primary_key': primary_key,
                           'collection_name': name})

        return jsonify({'models': models})


    @app.after_request
    def set_etag(response):
        if request.blueprint == 'swagger':
            return response

        if response.status_code > 399:
            return response

        # suboptimal: redeserialization
        # XXX use six
        try:
            result = json.loads(str(response.data, 'utf8'))
        except TypeError:
            result = json.loads(str(response.data))

        if 'data' in result and 'last_modified' in result['data']:
            last_modified = str(result['data']['last_modified'])
        else:
            last_modified = None

        # checking If-None-Match on GET calls
        if (request.method == 'GET' and request.if_none_match):
            if last_modified and last_modified in request.if_none_match:
                return NotModified()

        # adding an ETag header
        if last_modified:
            response.set_etag(last_modified)

        return response

    @app.before_request
    def before_req():
        g.debug = _DEBUG

    logging.config.fileConfig(ini_file)
    return app


def main(args=sys.argv[1:]):
    parser = argparse.ArgumentParser(description='ServiceBook Server.')

    parser.add_argument('--config-file', help='Config file',
                        type=str, default=DEFAULT_INI_FILE)
    parser.add_argument(
        '--no-run', action='store_true', default=False)

    args = parser.parse_args(args=args)
    app = create_app(ini_file=args.config_file)
    host = app.config['common'].get('host', '0.0.0.0')
    port = app.config['common'].get('port', 5001)
    debug = app.config.get('DEBUG', _DEBUG)

    if args.no_run:
        return app

    app.run(debug=debug, host=host, port=port)


if __name__ == "__main__":
    main()
