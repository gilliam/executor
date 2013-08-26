# Copyright 2013 Johan Rydberg.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""API and interface against Docker."""

import uuid
from functools import partial
from routes import Mapper, URLGenerator
from webob import Response
from webob.dec import wsgify
from webob.exc import HTTPBadRequest, HTTPNotFound
import gevent


def _build_cont(url, container):
    """Build a proc representation.

    @param url: URL generator.

    @return: C{dict}
    """
    return dict(id=container.id,
                formation=container.formation,
                service=container.service,
                instance=container.instance,
                env=container.env,
                image=container.image,
                command=container.command,
                state=container.state,
                reason=container.reason,
                status_code=container.status_code)


class ContResource(object):
    """Resource for our processes."""

    def __init__(self, log, url, store):
        self.log = log
        self.url = url
        self.store = store

    def create(self, request):
        """Create new container."""
        data = self._assert_request_data(request, 'image', 'command',
            'formation', 'service', 'instance')
        container = self.store.create(data['image'], data['command'],
            data.get('env', {}), data['formation'], data['service'],
            data['instance']).start()
        response = Response(json=_build_cont(self.url, container), status=201)
        response.headers.add('Location', self.url('container', id=container.id,
                                                  qualified=False))
        return response

    def index(self, request):
        """Return a representation of all procs."""
        collection = {}
        for id, container in self.store.index():
            collection[id] = _build_cont(self.url, container)
        return Response(json=collection, status=200)

    def show(self, request, id):
        """Return a presentation of a proc."""
        print "SHOW"
        return Response(json=_build_cont(self.url, self._get(id)), status=200)

    def delete(self, request, id):
        """Stop and delete process."""
        container = self._get(id)
        self.store.remove(container.id)
        container.dispose()
        return Response(status=204)

    def _assert_request_data(self, request, *required):
        if not request.json:
            raise HTTPBadRequest()
        data = request.json
        for key in required:
            if not key in data:
                raise HTTPBadRequest()
        return data

    def _get(self, id):
        """Return container with given ID or C{None}."""
        print "GET", id
        container = self.store.lookup(id)
        if container is None:
            raise HTTPNotFound()
        return container


class API(object):
    """The REST API that we expose."""

    def __init__(self, log, store, environ):
        self.mapper = Mapper()
        self.url = URLGenerator(self.mapper, environ)
        self.resources = {
            'container': ContResource(log, self.url, store)
            }
        self.mapper.collection("containers", "container",
                               controller='container',
                               path_prefix='/container',
                               collection_actions=['index', 'create'],
                               member_actions=['show', 'delete'],
                               formatted=False)

    @wsgify
    def __call__(self, request):
        route = self.mapper.match(request.path, request.environ)
        if route is None:
            raise HTTPNotFound()
        resource = self.resources[route.pop('controller')]
        action = route.pop('action')
        return getattr(resource, action)(request, **route)
