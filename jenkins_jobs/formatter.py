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
import lupa
from lupa import LuaRuntime

try:
    from collections import OrderedDict
except ImportError:
    OrderedDict = dict

from jenkins_jobs.errors import JenkinsJobsException
from jenkins_jobs import utils

logger = logging.getLogger(__name__)

def deep_format(obj, paramdict, templating=dict(), allow_empty=False, deep=None):
    """Apply the paramdict via str.format() to all string objects found within
       the supplied obj. Lists and dicts are traversed recursively."""
    # YAML serialisation was originally used to achieve this, but that places
    # limitations on the values in paramdict - the post-format result must
    # still be valid YAML (so substituting-in a string containing quotes, for
    # example, is problematic).
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
                    ret = JinjaFormatter(allow_empty, templating).format(obj, **paramdict)
            except KeyError as exc:
                missing_key = exc.args[0]
                desc = "%s parameter missing to format %s\nGiven:\n%s" % (
                    missing_key, obj, pformat(paramdict))
                raise JenkinsJobsException(desc)
    elif isinstance(obj, list):
        ret = type(obj)()
        for item in obj:
            ret.append(deep_format(item, paramdict, templating, allow_empty, deep - 1 if deep != None else None))
    elif isinstance(obj, dict):
        ret = type(obj)()
        for item in obj:
            try:
                ret[JinjaFormatter(allow_empty, templating).format(item, **paramdict)] = \
                    deep_format(obj[item], paramdict, templating, allow_empty, deep - 1 if deep != None else None)
            except KeyError as exc:
                missing_key = exc.args[0]
                desc = "%s parameter missing to format %s\nGiven:\n%s" % (
                    missing_key, obj, pformat(paramdict))
                raise JenkinsJobsException(desc)
    else:
        ret = obj
    return ret



class JinjaFormatter:
    templating = {}
    env = jinja2.Environment(variable_start_string = '${', variable_end_string = '}')
    def __init__(self, allow_empty=False, tpl=None):
        self.allow_empty = allow_empty
        tplname = tpl.get('name')
        if tplname not in JinjaFormatter.templating:
            lua = LuaRuntime(unpack_returned_tuples=True)
            langeval = {
                'lua' : lua.eval,
                'python': eval,
                'default': lua.eval
            }
            JinjaFormatter.templating[tplname] = dict()
            for key in ['filters', 'tests']:
                JinjaFormatter.templating[tplname][key] = {f['name']: langeval[f['lang'] if 'lang' in f else 'default'](f['func']) for f in tpl.get(key, [])}

        JinjaFormatter.env.filters.update(JinjaFormatter.templating[tplname]['filters'])
        JinjaFormatter.env.tests.update(JinjaFormatter.templating[tplname]['tests'])


    def format(self, format_string, *args, **kwargs):
        t = JinjaFormatter.env.from_string(format_string)
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
