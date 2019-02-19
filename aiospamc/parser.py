#!/usr/bin/env python3

'''Parser object for SPAMC/SPAMD requests and responses.'''


import re
from functools import wraps

from . import headers
from .options import MessageClassOption, ActionOption
from .status import Status


class RawRequest:
    def __init__(self, method, version, headers, body):
        self.method = method
        self.version = version
        self.headers = headers
        self.body = body


class RawResponse:
    def __init__(self, version, status_code, message, headers, body):
        self.version = version
        self.status_code = status_code
        self.message = message
        self.headers = headers
        self.body = body


class ParseError(Exception):
    '''An exception occurring when parsing.

    Attributes
    ----------
    index : :obj:`int`
        Index in the stream the exception occurred.
    message : :obj:`str`
        User readable message.
    '''

    def __init__(self, index, message):
        '''ParseError constructor.

        Parameters
        ----------
        index : :obj:`int`
            Index in the stream the exception occurred.
        message : :obj:`str`
            User readable message.
        '''
        self.index = index
        self.message = message


def checkpoint(func):
    '''A decorator to restore the index if an exception occurred.'''

    @wraps(func)
    def inner(self):
        check = self.index
        try:
            return func(self)
        except ParseError:
            self.index = check
            raise
    return inner


class Parser:
    '''Parser object for requests and responses.'''

    def __init__(self, string=None, request_cls=RawRequest, response_cls=RawResponse):
        '''Parser constructor.

        Parameters
        ----------
        string : :obj:`str`, optional
            The string to parse.
        request_cls : callable

        response_cls : callable

        '''

        self.string = string or b''
        self.index = 0
        self.request_cls = request_cls
        self.response_cls = response_cls

    def advance(self, by):
        '''Advance the current index by number of bytes.

        Parameters
        ----------
        by : :obj:`int`
            Number of bytes in the stream to advance.
        '''

        self.index += by

    def current(self):
        '''The remainder of the string that hasn't been parsed.

        Returns
        -------
        :obj:`str`
        '''

        return self.string[self.index:]

    def end(self):
        '''Whether the parser has parsed the entire string.

        Returns
        -------
        :obj:`bool`
        '''

        return self.index >= len(self.string)

    def match(self, pattern):
        '''Returns the regular expression matches string at the current index.

        Parameters
        ----------
        pattern : :obj:`bytes`

        Returns
        -------
        Regular expression match
        '''

        success = re.match(pattern, self.current())
        if success:
            return success
        else:
            return None

    def consume(self, pattern):
        '''If the pattern matches, advances the index the length of the match.
        Returns the regular expression match.

        Parameters
        ----------
        pattern : :obj:`bytes`

        Returns
        -------
        Regular expression match

        Raises
        ------
        :class:`aiospamc.parser.ParseError`
        '''

        match = self.match(pattern)
        if match:
            self.advance(match.end())
            return match
        else:
            raise ParseError(index=self.index, message='Unable to consume match  with pattern {}'.format(pattern))

    @staticmethod
    def skip(func):
        '''Makes the parser function optional by ignore whether it raises a
        :class:`aiospamc.parser.ParseError` exception or not.

        Parameters
        ----------
        func : function
            Function to execute.
        '''

        try:
            func()
        except ParseError:
            pass

    def whitespace(self):
        '''Consumes spaces or tabs.'''

        self.consume(rb'[ \t]+')

    def newline(self):
        '''Consumes a newline sequence (carriage return and line feed).'''

        self.consume(rb'\r\n')

    def version(self):
        '''Consumes a version pattern.  For example, "1.5".

        Returns
        -------
        :obj:`str`
        '''

        return self.consume(rb'\d+\.\d+').group().decode()

    def body(self):
        '''Consumes the rest of the message and returns the contents.

        Returns
        -------
        :obj:`bytes`
        '''

        return self.current()

    # Header functions

    @checkpoint
    def compress_value(self):
        '''Consumes the Compression header value.

        Returns
        -------
        :obj:`dict`
        '''

        self.skip(self.whitespace)
        self.consume(rb'zlib').group()
        self.skip(self.whitespace)

        return {}

    @checkpoint
    def content_length_value(self):
        '''Consumes the Content-length header value.

        Returns
        -------
        :obj:`dict`
        '''

        self.skip(self.whitespace)
        length = int(self.consume(rb'\d+').group())
        self.skip(self.whitespace)

        return {'length': length}

    @checkpoint
    def message_class_value(self):
        '''Consumes the Message-class header value.

        Returns
        -------
        :obj:`dict`
        '''

        self.skip(self.whitespace)
        m_class = MessageClassOption.ham if self.consume(
            rb'(ham|spam)').group() == b'ham' else MessageClassOption.spam
        self.skip(self.whitespace)

        return {'value': m_class}

    @checkpoint
    def set_remove_value(self):
        '''Consumes the value for the DidRemove, DidSet, Remove and Set headers.

        Returns
        -------
        :obj:`dict`
        '''

        self.skip(self.whitespace)
        action = self.consume(rb'(local|remote)([ \t]*,[ \t]*(local|remote))?').group()
        self.skip(self.whitespace)

        return {'action': ActionOption(local=b'local' in action, remote=b'remote' in action)}

    @checkpoint
    def spam_value(self):
        '''Consumes the Spam header value.

        Returns
        -------
        :obj:`dict`
            Has the keys `value`, `score`, and `threshold`.
        '''

        number = rb'(\d+(\.\d+)?)'

        self.skip(self.whitespace)
        value = True if self.consume(rb'(True|False)').group() == b'True' else False
        self.skip(self.whitespace)
        self.consume(rb';')
        self.skip(self.whitespace)
        score = float(self.consume(number).group())
        self.skip(self.whitespace)
        self.consume(rb'/')
        self.skip(self.whitespace)
        threshold = float(self.consume(number).group())
        self.skip(self.whitespace)

        return {'value': value, 'score': score, 'threshold': threshold}

    @checkpoint
    def user_value(self):
        '''Consumes the User header value.

        Returns
        -------
        :obj:`dict`
        '''

        self.skip(self.whitespace)
        username = self.consume(rb'[a-zA-Z0-9-_]+').group()
        self.skip(self.whitespace)

        return {'name': username.decode()}

    @checkpoint
    def header(self):
        '''Consumes the string and returns an instance of
        :class:`aiospamc.headers.Header`.

        Returns
        -------
        :class:`aiospamc.headers.Header`
        '''

        self.skip(self.whitespace)
        name = self.consume(rb'[a-zA-Z0-9_-]+').group().decode()
        self.skip(self.whitespace)
        self.consume(rb':')

        mappings = {
            'Compress': (headers.Compress, self.compress_value),
            'Content-length': (headers.ContentLength, self.content_length_value),
            'DidRemove': (headers.DidRemove, self.set_remove_value),
            'DidSet': (headers.DidSet, self.set_remove_value),
            'Message-class': (headers.MessageClass, self.message_class_value),
            'Remove': (headers.Remove, self.set_remove_value),
            'Set': (headers.Set, self.set_remove_value),
            'Spam': (headers.Spam, self.spam_value),
            'User': (headers.User, self.user_value)
        }

        if name in mappings:
            header, value_func = mappings[name]
            return header(**value_func())
        else:
            return headers.XHeader(name, self.consume(rb'.+(?=\r\n)').group().decode())

    def headers(self):
        '''Consumes all headers.

        Returns
        -------
        :obj:`list` of :class:`aiospamc.headers.Header`
        '''

        header_list = []
        while not self.match(rb'\r\n'):
            try:
                h = self.header()
                self.newline()
                header_list.append(h)
            except ParseError:
                break

        return header_list

    # Request functions

    def spamc_protocol(self):
        '''Consumes the string "SPAMC".

        Returns
        -------
        :obj:`str`
        '''

        return self.consume(rb'SPAMC').group().decode()

    @checkpoint
    def method(self):
        '''Consumes the method name in a request.

        Returns
        -------
        :obj:`str`
        '''

        name = self.consume(rb'CHECK|'
                            rb'HEADERS|'
                            rb'PING|'
                            rb'PROCESS|'
                            rb'REPORT_IFSPAM|'
                            rb'REPORT|'
                            rb'SKIP|'
                            rb'SYMBOLS|'
                            rb'TELL')
        self.whitespace()

        return name.group().decode()

    @checkpoint
    def request(self):
        '''Consumes a SPAMC request.

        Returns
        -------
        :class:`aiospamc.requests.Request`
        '''

        m = self.method()
        p = self.spamc_protocol()
        self.consume(rb'/')
        v = self.version()
        self.newline()

        if not self.end():
            h = self.headers()
        else:
            h = []

        if not self.end():
            self.newline()
            b = self.body()
        else:
            b = None

        return self.request_cls(method=m, version=v, headers=h, body=b)

    # Response functions

    def spamd_protocol(self):
        '''Consumes the string "SPAMD".

        Returns
        -------
        :obj:`str`
        '''

        return self.consume(rb'SPAMD').group().decode()

    @checkpoint
    def status_code(self):
        '''Consumes the status code.

        Returns
        -------
        :class:`aiospamc.responses.StatusCode` or int
        '''

        code = int(self.consume(rb'\d+').group())
        try:
            return Status(code)
        except ValueError:
            return code

    def message(self):
        '''Consumes a string until it matches a newline.

        Returns
        -------
        :obj:`str`
        '''

        return self.consume(rb'.*(?=\r\n)').group().decode()

    @checkpoint
    def response(self):
        '''Consumes a SPAMD response.

        Returns
        -------
        :class:`aiospamc.responses.Response`
        '''

        p = self.spamd_protocol()
        self.consume(rb'/')
        v = self.version()
        self.whitespace()
        c = self.status_code()
        self.whitespace()
        m = self.message()
        self.newline()

        if not self.end():
            h = self.headers()
        else:
            h = []

        if not self.end():
            self.newline()
            b = self.body()
        else:
            b = None

        return self.response_cls(version=v, status_code=c, message=m, headers=h,
                                           body=b)


def parse(string, request_cls=RawRequest, response_cls=RawResponse):
    '''Parses a request or response.

    Returns
    -------
    :class:`aiospamc.requests.Request` or :class:`aiospamc.responses.Response`
    '''

    parser = Parser(string, request_cls, response_cls)

    try:
        return parser.request()
    except ParseError:
        pass

    try:
        return parser.response()
    except ParseError:
        pass

    raise ParseError(0, 'Unable to parse request or response')
