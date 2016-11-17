#!/usr/bin/env python
# Copyright (C) 2015 OpenStack, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

# Manage interpolation of JJB variables into template strings.

import logging
from pprint import pformat
import re
import io
from string import Formatter
import jinja2
import yaml
import lupa
from lupa import LuaRuntime

try:
    from collections import OrderedDict
except ImportError:
    OrderedDict = dict

from jenkins_jobs.errors import JenkinsJobsException

from jenkins_jobs import utils

logger = logging.getLogger(__name__)
jinja2_env = jinja2.Environment(variable_start_string = '${', variable_end_string = '}')
formatter = None

def deep_format(obj, paramdict, allow_empty=False, deep=None, jinja_filters='filters.yml'):
    """Apply the paramdict via str.format() to all string objects found within
       the supplied obj. Lists and dicts are traversed recursively."""
    # YAML serialisation was originally used to achieve this, but that places
    # limitations on the values in paramdict - the post-format result must
    # still be valid YAML (so substituting-in a string containing quotes, for
    # example, is problematic).
    global formatter
    if not formatter:
        formatter = JinjaFormatter(allow_empty, jinja_filters)

    if deep and deep < 0:
        return obj
    if hasattr(obj, 'format'):
        try:
            result = re.match('^{obj:(?P<key>\w+)}$', obj)
        except TypeError:
            ret = obj.format(**paramdict)
        else:
            try:
                if result is not None:
                    ret = paramdict[result.group("key")]
                else:
                    ret = formatter.format(obj, **paramdict)
            except KeyError as exc:
                missing_key = exc.args[0]
                desc = "%s parameter missing to format %s\nGiven:\n%s" % (
                    missing_key, obj, pformat(paramdict))
                raise JenkinsJobsException(desc)
    elif isinstance(obj, list):
        ret = type(obj)()
        for item in obj:
            ret.append(deep_format(item, paramdict, allow_empty, deep - 1 if deep != None else None))
    elif isinstance(obj, dict):
        ret = type(obj)()
        for item in obj:
            try:
                ret[formatter.format(item, **paramdict)] = \
                    deep_format(obj[item], paramdict, allow_empty, deep - 1 if deep != None else None)
            except KeyError as exc:
                missing_key = exc.args[0]
                desc = "%s parameter missing to format %s\nGiven:\n%s" % (
                    missing_key, obj, pformat(paramdict))
                raise JenkinsJobsException(desc)
    else:
        ret = obj
    return ret



class JinjaFormatter:
    def __init__(self, allow_empty=False, filters=None):
        self.allow_empty = allow_empty
        if filters:
            with io.open(filters, 'r', encoding='utf-8') as fp:
                data = yaml.load(utils.wrap_stream(fp))
                lua = LuaRuntime(unpack_returned_tuples=True)
                langeval = {
                    'lua' : lua.eval,
                    'python': eval,
                    'default': lua.eval
                }
                jinja2_filters = {f['name']: langeval[f['lang'] if 'lang' in f else 'default'](f['func']) for f in data['filters']}
                jinja2_env.filters.update(jinja2_filters)
                jinja2_tests = {f['name']: langeval[f['lang'] if 'lang' in f else 'default'](f['func']) for f in data['tests']}
                jinja2_env.tests.update(jinja2_tests)


    def format(self, format_string, *args, **kwargs):
        t = jinja2_env.from_string(format_string)
        val = t.render(**kwargs)
        if val.startswith("="):
            return eval(val[1:])
        return val

class CustomFormatter(Formatter):
    """
    Custom formatter to allow non-existing key references when formatting a
    string
    """
    def __init__(self, allow_empty=False):
        super(CustomFormatter, self).__init__()
        self.allow_empty = allow_empty

    def get_value(self, key, args, kwargs):
        try:
            return Formatter.get_value(self, key, args, kwargs)
        except KeyError:
            if self.allow_empty:
                logger.debug(
                    'Found uninitialized key %s, replaced with empty string',
                    key
                )
                return ''
            raise
