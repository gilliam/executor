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
    """Build a container representation.

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


def _build_proc(url, proc):
    """Build a proc representation.

    @param url: URL generator.

    @return: C{dict}
    """
    return dict(id=proc.id,
                formation=proc.formation,
                env=proc.env,
                image=proc.image,
                command=proc.command,
                state=proc.state,
                reason=proc.reason,
                status=proc.status_code)


class RunResource(object):
    """Resource for 'one off' processes."""

    def __init__(self, log, store):
        self.log = log
        self.store = store

    def create(self, request, url):
        """Create new container."""
        data = self._assert_request_data(request, 'image', 'command',
            'formation')
        container = self.store.create(data['image'], data['command'],
            data.get('env', {}), data.get('ports', []),
            data['formation']).start()
        response = Response(json=_build_proc(url, container), status=201)
        response.headers.add('Location', url('run', id=container.id,
                                             qualified=True))
        return response

    def attach(self, request, url, id):
        container = self._get(id)
        app_iter = container.attach(request.body_file)
        response = Response(status=200)
        response.app_iter = app_iter
        return response

    def commit(self, request, url, id):
        container = self._get(id)
        data = self._assert_request_data(request, 'repository')
        container.commit(data['repository'], data.get('tag'))
        return Response(status=204)

    def index(self, request, url):
        """Return a representation of all procs."""
        collection = {}
        for id, container in self.store.index():
            collection[id] = _build_proc(url, container)
        return Response(json=collection, status=200)

    def show(self, request, url, id):
        """Return a presentation of a proc."""
        return Response(json=_build_proc(url, self._get(id)), status=200)

    def delete(self, request, url, id):
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
        container = self.store.lookup(id)
        if container is None:
            raise HTTPNotFound()
        return container


class ContResource(object):
    """Resource for our processes."""

    def __init__(self, log, store):
        self.log = log
        self.store = store

    def create(self, request, url):
        """Create new container."""
        data = self._assert_request_data(request, 'image', 'command',
            'formation', 'service', 'instance')
        container = self.store.create(data['image'], data['command'],
            data.get('env', {}), data.get('ports', []),
            data['formation'], data['service'], data['instance']).start()
        response = Response(json=_build_cont(url, container), status=201)
        response.headers.add('Location', url('container', id=container.id,
                                             qualified=True))
        return response

    def index(self, request, url):
        """Return a representation of all procs."""
        collection = {}
        for id, container in self.store.index():
            collection[id] = _build_cont(url, container)
        return Response(json=collection, status=200)

    def show(self, request, url, id):
        """Return a presentation of a proc."""
        print "SHOW"
        return Response(json=_build_cont(url, self._get(id)), status=200)

    def delete(self, request, url, id):
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

    def __init__(self, log, cont_store, run_store, mapper):
        self.resources = {
            'container': ContResource(log, cont_store),
            'run': RunResource(log, run_store)
            }

        mapper.collection("containers", "container",
                               controller='container',
                               path_prefix='/container',
                               collection_actions=['index', 'create'],
                               member_actions=['show', 'delete'],
                               formatted=False)

        run_collection = mapper.collection("runs", "run",
                               controller='run',
                               path_prefix='/run',
                               collection_actions=['index', 'create'],
                               member_actions=['show', 'delete'],
                               formatted=False)
        run_collection.member.link(
            'commit', 'commit', action='commit',
            method='POST', formatted=False)
        run_collection.member.link(
            'attach', 'attach', action='attach',
            method='POST', formatted=False)


    @wsgify
    def __call__(self, request):
        # handle incoming call.  depends on the routes middleware.
        url, match = request.environ['wsgiorg.routing_args']
        if match is None:
            raise HTTPNotFound()
        resource = self.resources[match.pop('controller')]
        action = match.pop('action')
        return getattr(resource, action)(request, url, **match)
