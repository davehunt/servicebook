import json
import os

from flask import Flask, g
from flask_bootstrap import Bootstrap
from flask.ext.iniconfig import INIConfig

from servicebook.db import init
from servicebook.nav import nav
from servicebook.views import blueprints
from servicebook.auth import get_user, GithubAuth
from servicebook.views.auth import unauthorized_view
from servicebook.mozillians import Mozillians


HERE = os.path.dirname(__file__)
DEFAULT_INI_FILE = os.path.join(HERE, '..', 'servicebook.ini')


def create_app(ini_file=DEFAULT_INI_FILE, dump=None):
    app = Flask(__name__, static_url_path='/static')
    INIConfig(app)
    app.config.from_inifile(ini_file)
    app.secret_key = app.config['common']['secret_key']
    sqluri = app.config['common']['sqluri']

    Bootstrap(app)
    GithubAuth(app)
    Mozillians(app)

    if dump is not None:
        with open(dump) as f:
            dump = json.loads(f.read())

    app.db = init(sqluri, dump)

    for bp in blueprints:
        app.register_blueprint(bp)
        bp.app = app

    app.register_error_handler(401, unauthorized_view)
    nav.init_app(app)

    app.add_url_rule(
           app.static_url_path + '/<path:filename>',
           endpoint='static',
           view_func=app.send_static_file)

    @app.before_request
    def before_req():
        g.user = get_user(app)

    return app


def main():
    app = create_app()
    app.run(debug=True)


if __name__ == "__main__":
    main()
