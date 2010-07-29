# -*- coding: utf-8 -*-
"""
    webapp2
    =======

    Taking webapp to the next level!

    :copyright: 2010 by tipfy.org.
    :license: Apache Sotware License, see LICENSE for details.
"""
import cgi
import logging
import re
import sys
import traceback
import urllib
import urlparse

from django.utils import simplejson

from google.appengine.ext.webapp import Request
from google.appengine.ext.webapp.util import run_wsgi_app, run_bare_wsgi_app

import webob

#: Allowed request methods.
_ALLOWED_METHODS = frozenset(['get', 'post', 'head', 'options', 'put',
    'delete', 'trace'])

#: Regex for URL definitions.
_URL_REGEX = re.compile(r'''
    \{            # The exact character "{"
    (\w+)         # The variable name (restricted to a-z, 0-9, _)
    (?::([^}]+))? # The optional :regex part
    \}            # The exact character "}"
    ''', re.VERBOSE)

#: Loaded lazy handlers.
_HANDLERS = {}


class Response(webob.Response):
    """Abstraction for an HTTP response.

    Implements most of ``webapp.Response`` interface, except ``wsgi_write()``.
    """
    def __init__(self, *args, **kwargs):
        super(Response, self).__init__(*args, **kwargs)

        # webapp uses self.response.out.write(...)
        self.out = self.body_file

    def set_status(self, code, message=None):
        """Sets the HTTP status code of this response.

        :param message:
            The HTTP status string to use
        :param message:
            A status string. If none is given, uses the default from the
            HTTP/1.1 specification.
        """
        if message:
            self.status = '%d %s' % (code, message)
        else:
            self.status = code

    def clear(self):
        """Clears all data written to the output stream so that it is empty."""
        self.app_iter = []

    @staticmethod
    def http_status_message(code):
        """Returns the default HTTP status message for the given code.

        :param code:
            The HTTP code for which we want a message.
        """
        message = webob.statusreasons.status_reasons.get(code, None)
        if not message:
            raise Error('Invalid HTTP status code: %d' % code)

        return message


class RequestHandler(object):
    """Base HTTP request handler. Clients should subclass this class.

    Subclasses should override get(), post(), head(), options(), etc to handle
    different HTTP methods.

    Implements most of ``webapp.RequestHandler`` interface.
    """
    #: A list of plugin instances. A plugin can implement two methods that
    #: are called before and after the current request method is executed,
    #: except if the chain is stopped.
    #:
    #: before_dispatch(handler)
    #:     Called before the requested method is executed. If returns False,
    #:     stops the plugin chain do not execute the requested method.
    #:
    #: after_dispatch(handler)
    #:     Called after the requested method is executed. If returns False,
    #:     stops the plugin chain. These are called in reverse order.
    plugins = []

    def __init__(self, app, request, response):
        """Initializes the handler.

        :param app:
            A :class:`WSGIApplication` instance.
        :param request:
            A ``webapp.Request`` instance.
        :param response:
            A :class:`Response` instance.
        """
        self.app = app
        self.request = request
        self.response = response

    def dispatch(self, _method_name, **kwargs):
        """Dispatches the requested method. If plugins are set, executes
        ``before_dispatch()`` and ``after_dispatch()`` plugin hooks.

        :param _method_name:
            The method to be dispatched: the request method in lower case
            (e.g., 'get', 'post', 'head', 'put' etc).
        :param kwargs:
            Keyword arguments to be passed to the method, coming from the
            matched :class:`Route`.
        :returns:
            None.
        """
        method = getattr(self, _method_name, None)
        if method is None:
            return self.error(405)

        if not self.plugins:
            # No plugins are set: just execute the method.
            return method(**kwargs)

        # Execute before_dispatch plugins.
        for plugin in self.plugins:
            hook = getattr(plugin, 'before_dispatch', None)
            if hook:
                rv = hook(self)
                if rv is False:
                    break
        else:
            # Execute the requested method.
            method(**kwargs)

        # Execute after_dispatch plugins.
        for plugin in reversed(self.plugins):
            hook = getattr(plugin, 'after_dispatch', None)
            if hook:
                rv = hook(self)
                if rv is False:
                    break

    def get(self, **kwargs):
        """Handler method for GET requests."""
        self.error(405)

    # All other allowed methods, not implemented by default.
    post = head = options = put = delete = trace = get

    def error(self, code=500):
        """Clears the response output stream and sets the given HTTP error
        code.

        :param code:
            HTTP status error code (e.g., 500).
        """
        self.response.set_status(code)
        self.response.clear()

    def redirect(self, uri, permanent=False):
        """Issues an HTTP redirect to the given relative URL.

        :param uri:
            A relative or absolute URI (e.g., '../flowers.html').
        :param permanent:
            If True, uses a 301 redirect instead of a 302 redirect.
        """
        if permanent:
            self.response.set_status(301)
        else:
            self.response.set_status(302)

        absolute_url = urlparse.urljoin(self.request.uri, uri)
        self.response.headers['Location'] = str(absolute_url)
        self.response.clear()

    def handle_exception(self, exception, debug_mode):
        """Called if this handler throws an exception during execution.

        The default behavior is to call self.error(500) and print a stack
        trace if debug_mode is True.

        :param exception:
            The exception that was thrown.
        :debug_mode:
            True if the web application is running in debug mode.
        """
        self.error(500)
        logging.exception(exception)
        if debug_mode:
            #raise
            lines = ''.join(traceback.format_exception(*sys.exc_info()))
            self.response.clear()
            self.response.out.write('<pre>%s</pre>' % (cgi.escape(lines,
                quote=True)))

    def url_for(self, name, _full=False, _secure=False, _anchor=None, **kwargs):
        """Builds and returns a URL for a named :class:`Route`.

        For example, if you have these routes registered in the application:

        .. code-block:: python

           app = WSGIApplication([
               ('/',     'handlers.HomeHandler', 'home/main'),
               ('/wiki', WikiHandler,            'wiki/start'),
           ])

        Here are some examples of how to generate URLs for them:

        >>> url = self.url_for('home/main')
        >>> '/'
        >>> url = self.url_for('home/main', _full=True)
        >>> 'http://localhost:8080/'
        >>> url = self.url_for('wiki/start')
        >>> '/wiki'
        >>> url = self.url_for('wiki/start', _full=True)
        >>> 'http://localhost:8080/wiki'
        >>> url = self.url_for('wiki/start', _full=True, _anchor='my-heading')
        >>> 'http://localhost:8080/wiki#my-heading'

        :param name:
            The route endpoint.
        :param _full:
            If True, returns an absolute URL. Otherwise returns a relative one.
        :param _secure:
            If True, returns an absolute URL using `https` scheme.
        :param _anchor:
            An anchor to append to the end of the URL.
        :param kwargs:
            Keyword arguments to build the URL.
        :returns:
            An absolute or relative URL.
        """
        url = self.request.url_for(name, **kwargs)

        if _full or _secure:
            scheme = 'http'
            if _secure:
                scheme += 's'

            url = '%s://%s%s' % (scheme, self.request.host, url)

        if _anchor:
            url += '#%s' % url_escape(_anchor)

        return url


class RedirectHandler(RequestHandler):
    """Redirects to the given URL for all GET requests. This is meant to be
    used when defining URL routes. You must provide the keyword argument
    *url* in the route. Example:

    .. code-block:: python

       app = WSGIApplication([
           ('/old-url', 'legacy-url', RedirectHandler, {'url': '/new-url'}),
       ])

    Based on idea from `Tornado <http://www.tornadoweb.org/>`_.
    """
    def get(self, **kwargs):
        url = kwargs.get('url', '/')

        if callable(url):
            url = url(self, **kwargs)

        self.redirect(url, permanent=kwargs.get('permanent', True))


class WSGIApplication(object):
    """Wraps a set of webapp RequestHandlers in a WSGI-compatible application.

    To use this class, pass a list of (URI regular expression, RequestHandler)
    pairs to the constructor, and pass the class instance to a WSGI handler.
    See the example in the module comments for details.

    The URL mapping is first-match based on the list ordering.
    """
    #: Default class used for the request object.
    request_class = Request
    #: Default class used for the response object.
    response_class = Response

    def __init__(self, url_map, debug=False, config=None):
        """Initializes the WSGI application.

        :param url_map:
            A list of URL route definitions.
        :param debug:
            True if this is debug mode, False otherwise.
        :param config:
            A configuration dictionary for the application.
        """
        self.set_router(url_map)
        self.debug = debug

    def __call__(self, environ, start_response):
        """Shortcut for :meth:`WSGIApplication.wsgi_app`."""
        try:
            return self.wsgi_app(environ, start_response)
        except Exception, e:
            logging.exception(e)

    def wsgi_app(self, environ, start_response):
        """Called by WSGI when a request comes in."""
        # TODO Exception handling is still unresolved.

        request = self.request_class(environ)
        response = self.response_class()
        method = environ['REQUEST_METHOD'].lower()

        if method not in _ALLOWED_METHODS:
            # TODO raise exception: 405 Method Not Allowed
            pass

        handler_class, kwargs = self.match_route(request)

        if handler_class:
            handler = handler_class(self, request, response)
            try:
                handler.dispatch(method, **kwargs)
            except Exception, e:
                # TODO handle exception
                handler.handle_exception(e, self.debug)
        else:
            response.set_status(404)

        return response(environ, start_response)

    def set_router(self, url_map):
        """Sets a :class:`Router` instance for the given url_map.

        :param url_map:
            A list of URL route definitions.
        """
        self.router = Router()
        for spec in url_map:
            if len(spec) in (2, 3):
                # path, handler, [name]
                self.router.add(*spec)
            elif len(spec) == 4:
                # path, handler, name, defaults
                self.router.add(*spec[:3], **spec[3])

    def match_route(self, request):
        """Matches a route against the current request.

        :param request:
            A ``webapp.Request`` instance.
        :returns:
            A tuple (handler_class, kwargs) for the matched route.
        """
        match = self.router.match(request)
        request.url_route = match
        request.url_for = self.router.build
        if not match:
            return (None, None)

        route, kwargs = match
        handler_class = route.handler

        if isinstance(handler_class, basestring):
            if handler_class not in _HANDLERS:
                _HANDLERS[handler_class] = LazyObject(handler_class)

            handler_class = _HANDLERS[handler_class]

        return handler_class, kwargs

    def handle_exception(self, e):
        pass


class Route(object):
    """A URL route definition."""
    def __init__(self, path, handler, **defaults):
        """Initializes a URL route.

        :param path:
            A path to be matched. Paths can contain variables enclosed in
            curly braces and an optional regular expression to be evaluated.
            Some examples:

            >>> Route('/blog', BlogHandler)
            >>> Route('/blog/archive', BlogArchiveHandler)
            >>> Route('/blog/archive/{year:\d\d\d\d}', BlogArchiveHandler)
            >>> Route('/blog/archive/{year:\d\d\d\d}/{slug}', BlogItemHandler)

        :param handler:
            A :class:`RequestHandler` class to be executed when this route
            matches.
        :param defaults:
            Default or extra keywords to be returned by this route. Default
            values present in the route variables are used to build the URL
            if the value is not passed.
        """
        self.path = path
        self.handler = handler
        self.defaults = defaults
        self.variables = {}

        last = 0
        regex = ''
        template = ''
        for match in _URL_REGEX.finditer(path):
            part = path[last:match.start()]
            name = match.group(1)
            expr = match.group(2) or '[^/]+'
            last = match.end()

            regex += '%s(?P<%s>%s)' % (re.escape(part), name, expr)
            template += '%s%%(%s)s' % (part, name)
            self.variables[name] = re.compile('^%s$' % expr)

        # The regex used to match URLs.
        self.regex = re.compile('^%s%s$' % (regex, re.escape(path[last:])))
        # The template used to build URLs.
        self.template = template + path[last:]

    def match(self, request):
        """Matches a route against the current request.

        :param request:
            A ``webapp.Request`` instance.
        :returns:
            A tuple (route, route_values), including the default values.
        """
        match = self.regex.match(request.path)
        if match:
            values = self.defaults.copy()
            values.update(match.groupdict())
            return (self, values)

    def build(self, **kwargs):
        """Builds a URL for this route.

        :param kwargs:
            Keyword arguments to build the URL. All route variables that are
            not set as defaults must be passed, and they must conform to the
            format set in the route. Extra keywords are appended as URL
            arguments.
        :returns:
            A formatted URL.
        """
        required = self.variables.keys()
        values = {}
        for name in required:
            value = kwargs.pop(name, self.defaults.get(name))
            if not value:
                raise ValueError('Missing keyword "%s" to build URL.' % name)

            if not isinstance(value, basestring):
                value = str(value)

            value = url_escape(value)
            match = self.variables[name].match(value)
            if not match:
                raise ValueError('URL buiding error: Value "%s" is not '
                    'supported for keyword "%s".' % (value, name))

            values[name] = value

        url = self.template % values
        if kwargs:
            # Append extra keywords as URL arguments.
            url += '?%s' % urllib.urlencode(kwargs)

        return url


class Router(object):
    """A simple URL router. This is used to match the current URL and build
    URLs for other resources.

    This router doesn't intend to do fancy things such as automatic URL
    redirect or subdomain matching. It should stay as simple as possible.

    Based on `Another Do-It-Yourself Framework <ttp://pythonpaste.org/webob/do-it-yourself.html#routing>`_
    by Ian Bicking. We added URL building and separate :class:`Route` objects.
    """
    def __init__(self):
        self.routes = []
        self.route_names = {}

    def add(self, path, handler, _name=None, **kwargs):
        """Adds a route to this router.

        :param path:
            The route path. See :meth:`Route.__init__`.
        :param handler:
            A :class:`RequestHandler` class to be executed when this route
            matches.
        :param _name:
            The route name.
        """
        route = Route(path, handler, **kwargs)
        self.routes.append(route)
        if _name:
            self.route_names[_name] = route

    def match(self, request):
        """Matches all routes against the current request. The first one that
        matches is returned.

        :param request:
            A ``webapp.Request`` instance.
        :returns:
            A tuple (route, route_values), including the default values.
        """
        for route in self.routes:
            match = route.match(request)
            if match:
                return match

    def build(self, name, **kwargs):
        """Builds a URL for a named :class:`Route`.

        :param name:
            The route name, as registered in :meth:`add`.
        :param kwargs:
            Keyword arguments to build the URL. All route variables that are
            not set as defaults must be passed, and they must conform to the
            format set in the route. Extra keywords are appended as URL
            arguments.
        :returns:
            A formatted URL.
        """
        route = self.route_names.get(name, None)
        if not route:
            raise KeyError('Route %s is not defined.' % name)

        return route.build(**kwargs)


class LazyObject(object):
    """An object that is only imported when called."""
    def __init__(self, import_name):
        """"""
        self.import_name = import_name
        self.obj = None

    def __call__(self, *args, **kwargs):
        if self.obj is None:
            module_name, handler_name = self.import_name.rsplit('.', 1)
            self.obj = getattr(__import__(module_name), handler_name)

        return self.obj(*args, **kwargs)


def json_encode(value):
    """JSON-encodes the given Python object."""
    # JSON permits but does not require forward slashes to be escaped.
    # This is useful when json data is emitted in a <script> tag
    # in HTML, as it prevents </script> tags from prematurely terminating
    # the javscript.  Some json libraries do this escaping by default,
    # although python's standard library does not, so we do it here.
    # http://stackoverflow.com/questions/1580647/json-why-are-forward-slashes-escaped
    return simplejson.dumps(value).replace("</", "<\\/")


def json_decode(value):
    """Returns Python objects for the given JSON string."""
    return simplejson.loads(to_unicode(value))


def url_escape(value):
    """Returns a valid URL-encoded version of the given value."""
    return urllib.quote_plus(to_utf8(value))


def url_unescape(value):
    """Decodes the given value from a URL."""
    return to_unicode(urllib.unquote_plus(value))


def to_utf8(value):
    if isinstance(value, unicode):
        return value.encode('utf-8')

    assert isinstance(value, str)
    return value


def to_unicode(value):
    if isinstance(value, str):
        return value.decode('utf-8')

    assert isinstance(value, unicode)
    return value