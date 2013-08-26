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

import logging
from gevent import pywsgi, monkey
monkey.patch_all(thread=False, time=False)
from functools import partial
from optparse import OptionParser

from docker import Client as DockerClient
from glock.clock import Clock
import json

from xexecutor.api import API
from xexecutor.container import ContainerStore, Container, PlatformRuntime
from xexecutor.registry import ServiceRegistryClient


class App(object):
    """Class that holds functionality wiring for things together."""

    def __init__(self, clock, store):
        self.clock = clock
        self.store = store

    def start(self):
        """Start the app."""
        #self._registration = self.discovery.register(
        #    self.form_name, self.inst_name, self.inst_data)

    def create_api(self, environ):
        """Create and return API WSGI application."""
        return API(logging.getLogger('api'), self.store, environ)

    def _create_container(self, cont_id, cont_data):
        """Create proc based on provided parameters."""
        print "CREATE CONTAINER", json.dumps(cont_data, indent=2)


def main():
    parser = OptionParser()
    parser.add_option("--sr", dest="registry_nodes", default='',
                      help="service registry nodes", metavar="HOSTS")
    parser.add_option("-p", "--port", dest="port", type=int,
                      help="listen port", metavar="PORT", default=9000)
    parser.add_option('--host', dest="host", default=None,
                      help="public hostname", metavar="HOST")
    (options, args) = parser.parse_args()
    assert options.host, "must specify host with --host"

    # logging
    format = '%(levelname)-8s %(name)s: %(message)s'
    logging.basicConfig(level=logging.DEBUG, format=format)

    clock = Clock()

    service_registry_cluster_nodes = options.registry_nodes.split(',')
    discovery = ServiceRegistryClient(clock, service_registry_cluster_nodes)
    docker = DockerClient()
    runtime = partial(PlatformRuntime, discovery)
    store = ContainerStore(partial(Container, docker, runtime, discovery,
                                   options.host))
    app = App(clock, store)
    pywsgi.WSGIServer(('', options.port), app.create_api({})).serve_forever()


if __name__ == '__main__':
    main()
