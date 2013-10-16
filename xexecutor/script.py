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

from gevent import monkey
monkey.patch_all()
from gevent import pywsgi 

import logging
from functools import partial
from optparse import OptionParser
import json
import os



from docker import Client as DockerClient
from glock.clock import Clock
from gilliam.service_registry import ServiceRegistryClient
from geventwebsocket.handler import WebSocketHandler
from routes.middleware import RoutesMiddleware
from routes import Mapper
import shortuuid

from xexecutor.api import API
from xexecutor.container import (ContainerStore, Container, PlatformRuntime,
                                 _ProxyResolver)
from xexecutor.proxy import ProxyApp


class App(object):
    """Class that holds functionality wiring for things together."""

    def __init__(self, clock, cont_store, run_store, docker, register,
                 announcement):
        self.clock = clock
        self.cont_store = cont_store
        self.run_store = run_store
        self.docker = docker
        self.register = register
        self.announcement = announcement
        self._reg = None

    def start(self):
        """Start the app."""
        self._reg = self.register(self.announcement)

    def create_api(self):
        """Create and return API WSGI application."""
        mapper = Mapper()
        return RoutesMiddleware(API(logging.getLogger('api'), self.cont_store,
                   self.run_store, self.docker, mapper), mapper, use_method_override=False,
                   singleton=False)


def main():
    parser = OptionParser()
    parser.add_option("-s", "--service-registry",
                      dest="registry_nodes",
                      default=os.getenv('GILLIAM_SERVICE_REGISTRY', ''),
                      help="service registry nodes", metavar="HOSTS")
    parser.add_option('--name', dest="name")
    parser.add_option("-p", "--port", dest="port", type=int,
                      help="listen port", metavar="PORT", default=9000)
    parser.add_option("--proxy-port", dest="proxy_port", type=int,
                      metavar="PORT", default=9001)
    parser.add_option('--host', dest="host", default=None,
                      help="public hostname", metavar="HOST")
    (options, args) = parser.parse_args()
    assert options.host, "must specify host with --host"

    # logging
    format = '%(levelname)-8s %(name)s: %(message)s'
    logging.basicConfig(level=logging.INFO, format=format)

    formation = os.getenv('GILLIAM_FORMATION', 'executor')
    service = os.getenv('GILLIAM_SERVICE', 'api')
    instance = options.name or shortuuid.uuid()
    clock = Clock()

    base_url = os.getenv('DOCKER')
    docker = DockerClient(base_url) if base_url else DockerClient()

    service_registry_cluster_nodes = options.registry_nodes.split(',')
    service_registry = ServiceRegistryClient(
        clock, service_registry_cluster_nodes)

    resolver = _ProxyResolver(service_registry)
    proxy_server = pywsgi.WSGIServer(('', options.proxy_port), ProxyApp(resolver))
    proxy_server.start()

    cont_runtime = partial(PlatformRuntime, options.host, options.proxy_port,
                           service_registry, options.registry_nodes)
    cont_store = ContainerStore(partial(Container, docker, cont_runtime,
                                        service_registry, options.host))


    # set-up runtime and store for the one-off containers:
    proc_runtime = partial(PlatformRuntime, options.host, options.proxy_port,
                           service_registry, options.registry_nodes, attach=True)
    proc_factory = lambda image, command, env, ports, opts, formation, **kw: Container(
        docker, proc_runtime, None, None, image, command, env, ports, opts,
        formation, None, shortuuid.uuid(), restart=False, **kw)
    proc_store = ContainerStore(proc_factory)

    register = partial(service_registry.register, formation, service, instance)
    announcement = service_registry.build_announcement(
        formation, service, instance, ports={options.port: str(options.port)},
        host=options.host)

    app = App(clock, cont_store, proc_store, docker, register, announcement)
    app.start()

    pywsgi.WSGIServer(('', options.port), app.create_api(),
                      handler_class=WebSocketHandler).serve_forever()


if __name__ == '__main__':
    main()
