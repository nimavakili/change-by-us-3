# -*- coding: utf-8 -*-
"""
    :copyright: (c) 2013 Local Projects, all rights reserved
    :license: Affero GNU GPL v3, see LICENSE for more details.
"""


import os
import yaml
import inspect
import bson

from logging.handlers import RotatingFileHandler
import logging

from flask_mail import Mail

from flask import Flask, request, render_template, current_app, g, jsonify

from flask.ext.security import Security, MongoEngineUserDatastore, login_required
from flask.ext.principal import identity_loaded, Identity, Permission, RoleNeed, UserNeed
from flask.ext.login import LoginManager, current_user
from flaskext.uploads import UploadSet, configure_uploads, IMAGES

from flask_oauth import OAuth

from .post import post_view, post_api
from .project import project_view, project_api
from .facebook import facebook_view
from .twitter import twitter_view
from .stream import stream_view, stream_api
from .user import user_view, user_api
from .frontend import frontend_view

from .encryption import assemble_key
from .extensions import db, login_manager

from helpers import Flask, get_shared_config

from .stripe import stripe_view

"""
========
CBU App
========

An effort was made to create CBU in a modular fashion using flask
blueprints whenever possible.  Flask-bone was used as a major influence
for the structure of this system.

Quite a few of the blueprints rely on routines found in the helpers.py
file, which should be kept in mind if ever pulling those modules into
different applications.
"""

# for import *
__all__ = ['create_app']

DEFAULT_BLUEPRINTS = (
    frontend_view,
    post_view,
    post_api,
    project_view, 
    project_api,
    facebook_view,
    twitter_view,
    stripe_view,
    stream_view,
    stream_api,
    user_view,
    user_api,
)

def create_app(app_name=None, blueprints=None):
    """
    ABOUT
        Flask app entry point.  This is called at application
        initialization. 
    """

    if app_name is None:
        app_name = __name__
    if blueprints is None:
        blueprints = DEFAULT_BLUEPRINTS

    app = Flask(app_name)
    configure_app(app)
    configure_logging(app)
    configure_database(app)
    configure_hook(app)
    configure_blueprints(app, blueprints)
    configure_mail(app)
    configure_security(app)
    configure_error_handlers(app)
    configure_media_uploads(app)
    configure_encryption(app)

    if app.config['DEBUG'] == True:
        app.debug = True

    return app


def configure_database(app):
    """
    ABOUT
        Configure the app database given the db object we import
        from our extensions file
    """
    db.init_app(app)
    app.db = db


def configure_logging(app):
    """
    ABOUT
        Sets up our logging style, file rotations, etc.
    TODO
        Think of a clever way to pull in requests that occured
        around the same time some logging happened, to avoid any manual
        comparison between a web log file and this log file
    """

    megabyte = 1048576

    logsize = 25 * megabyte
    rotations = 10
    # default name flask.log
    name = 'flask.log'

    if app.settings['DEBUG']:
        level = logging.DEBUG
    else:
        level = logging.INFO


    if app.settings['LOGGING']:
        if app.settings['LOGGING']['SIZE_MB']:
            logsize = int(app.settings['LOGGING']['SIZE_MB']) * megabyte
        
        if app.settings['LOGGING']['LEVEL']:
            level = logging.__getattribute__(app.settings['LOGGING']['LEVEL'])
        
        if app.settings['LOGGING']['NAME']:
            name = app.settings['LOGGING']['NAME']
        
        if app.settings['LOGGING']['ROTATIONS']:
            rotations = app.settings['LOGGING']['ROTATIONS']

    handler = RotatingFileHandler(app.settings['LOGGING']['NAME'], 
                                  maxBytes=(25 * megabyte), 
                                  backupCount=rotations)

    handler.setLevel(level)

    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s '
        '[%(pathname)s:%(funcName)s:%(lineno)d]')
    )
    
    app.logger.addHandler(handler)


def configure_app(app):
    """
    ABOUT
        This routine loads configuration files
    """
    app.config.from_yaml(get_shared_config('config/config.yml'))
    app.settings = yaml.load(file(get_shared_config('config/config.yml')))


def configure_hook(app):
    """
    ABOUT
        Hook that is called before all requests, left in for good measure
    """
    @app.before_request
    def before_request():
        pass


def configure_blueprints(app, blueprints):
    """
    ABOUT
        Registers each blueprint
    """
    for blueprint in blueprints:
        app.register_blueprint(blueprint)


def configure_mail(app):
    """
    ABOUT
        Configures our mail client, mostly needed for Flask-Security
    """
    app.mail = Mail(app)


# Setup Flask-Security
def configure_security(app):
    """
    ABOUT
        Configures Flask-Security with our app
    """
    from .user.models import Role, User

    user_datastore = MongoEngineUserDatastore(db, User, Role)
    security = Security(app, user_datastore)

    @identity_loaded.connect_via(app)
    def on_identity_loaded(sender, identity):
        # Set the default state
        g.user = None

        if identity.name is None:
            return

        # Ok we're ok to proceed now ...
        if isinstance(identity.name, (bson.ObjectId)):
            user = User.objects(id=identity.name)
        elif isinstance(identity.name, (str, unicode)):
            user = User.objects(email=identity.name)

        if user.count() > 1:
            app.logger.error("Got more than one match for user login %s" % identity.name)
            raise Exception("[on_identity_loaded] Error getting login information. Please contact an administrator")
            g.user = None
        elif user.count() == 0:
            g.user = None
        else:
            user = user.first()

            if hasattr(user, 'id'):
                identity.provides.add(UserNeed(user.id))

            # Assuming the User model has a list of roles, update the
            # identity with the roles that the user provides
            if hasattr(user, 'roles'):
                for role in user.roles:
                    identity.provides.add(RoleNeed(role.name))

            # Assuming the User model has a list of posts the user
            # has authored, add the needs to the identity
            if hasattr(user, 'posts'):
                for post in user.posts:
                    identity.provides.add(EditBlogPostNeed(unicode(post.id)))

            identity.user = user
            g.user = user

    # set the user load clalback
    @login_manager.user_loader
    def load_user(userid):
        return User.objects.with_id(userid)

    @app.before_request
    def before_request():
        """
        ABOUT
            This was inserted when we started to see errors from
            g.user.is_anonymous() for non-registered users, this seemed
            to fix that
        """
        g.user = current_user


def configure_media_uploads(app=None):
    """
    ABOUT
        This configures the uploading and hosting of our image
        files.  UploadSet is pretty flexible, but we don't do
        a whole lot to customize this.
    """
    uploaded_photos = UploadSet('photos', IMAGES)
    configure_uploads(app, uploaded_photos)
    app.uploaded_photos = uploaded_photos
    

def configure_encryption(app=None):
    """
    ABOUT
        For encryption and decryption of social tokens and third party
        app tokens, we use a combination of a local and remote key to
        encrypt/decrypt the data.
    """
    key = assemble_key(app.config.get('ENCRYPTION').get('LOCAL_KEY'),
                       app.config.get('ENCRYPTION').get('REMOTE_KEY_URL'))
    app.config['ENCRYPTION']['KEY'] = key    
    

def configure_error_handlers(app=None):
    """
    ABOUT
        Error handlers for various errors.  This allows us to capture
        exceptions, print the info, and then display a nice friendly page
        to the user stating an error occured, rather than a flask dump.

        With these enabled you can do something like
        abort(400) in your code, you'll get a log of the abort exception,
        and you'll get a nice friendly "Sorry, the page is missing" render

        We only capture exceptions if Debug=False.  So for a dev machine
        you'll still see exceptions in the browser for development purposes
    """

    @app.errorhandler(400)
    def bad_request(error=""):
        userStr = '' if g.user.is_anonymous() else ' user[' + str(g.user.id) + '] '
        current_app.logger.error("400 error occured " + userStr + " : " + str(error))
        current_app.logger.exception(error)

        return render_template('error.html', error="Sorry, this page is missing.")


    # TODO discuss how these errors are handled
    @app.errorhandler(403)
    def forbidden_request(error=""):
        userStr = '' if g.user.is_anonymous() else ' user[' + str(g.user.id) + '] '
        current_app.logger.error("403 error occured " + userStr + " : " + str(error))
        current_app.logger.exception(error)

        return render_template('error.html', error="Sorry, forbidden request.")

    # TODO discuss how these errors are handled
    @app.errorhandler(404)
    def not_found(error=""):
        current_app.logger.error(str(error))
        return render_template('error.html', error="Sorry, this page doesn't exist.")
    
    @app.errorhandler(405)
    def no_permission(error=""):
        userStr = '' if g.user.is_anonymous() else ' user[' + str(g.user.id) + '] '
        current_app.logger.error("405 error occured " + userStr + " : " + str(error))
        current_app.logger.exception(error)

        return render_template('error.html', error="Sorry, you're not authorized to see this page.")

    @app.errorhandler(500)
    def internal_server_error(error=""):
        userStr = '' if g.user.is_anonymous() else ' user[' + str(g.user.id) + '] '
        current_app.logger.error("500 error occured " + userStr + " : " + str(error))
        current_app.logger.exception(error)

        return render_template('error.html', error="Sorry, there was a server error.")


    # TODO only enable this on production
    if app.config['DEBUG'] is False:
        @app.errorhandler(Exception)
        def internal_exception_handler(error=""):
            userStr = '' if g.user.is_anonymous() else ' user[' + str(g.user.id) + '] '
            current_app.logger.error("Exception occured " + userStr + " : " + str(error))
            current_app.logger.exception(error)
    
            return render_template('error.html', error="Sorry, there was a server error.")


