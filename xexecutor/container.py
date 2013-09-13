from contextlib import contextmanager
import logging
import shlex
import random

from gevent.event import Event
from gevent.wsgi import WSGIServer
import gevent
from gilliam.service_registry import Resolver as ServiceRegistryResolver
import six
import shortuuid
import json


from xexecutor.proxy import ProxyApp

_DOCKER_GATEWAY = '172.17.42.1'


def _convert_environment_dict_to_array(environment):
    return ['%s=%s' % (k, v) for (k, v) in environment.items()]


class _ProxyResolver(object):
    """Class that resolves host names for the proxy."""

    def __init__(self, registry):
        self.resolver = ServiceRegistryResolver(registry)

    def __call__(self, netloc):
        try:
            host, port = netloc.split(':', 1)
        except ValueError:
            host, port = netloc, 80
        host, port = self.resolver.resolve_host_port(host, int(port))
        return '%s:%d' % (host, port)


class PlatformRuntime(object):

    def __init__(self, registry, srnodes, container, tty=False, attach=False):
        self.registry = registry
        self.container = container
        self.srnodes = srnodes
        self.tty = tty
        self.attach = attach
        self._resolve = _ProxyResolver(self.registry)
        self._init()

    def _init(self):
        self._proxy = WSGIServer(('', 0), ProxyApp(self._resolve))
        self._proxy.start()

    def dispose(self):
        self._proxy.stop()

    def _make_port_specs(self, ports):
        return [str(port) for port in ports]
    
    def make_config(self):
        """Given a L{Container}, construct a Docker config."""
        ports = self._make_port_specs(self.container.ports)
        return self._container_config(self.container.image,
            self.container.command, hostname=self.container.instance,
            environment=self._make_environment(), tty=self.tty,
            stdin_open=self.attach, ports=ports)

    def _make_environment(self):
        proxy_netloc = 'http://%s:%d' % (_DOCKER_GATEWAY, self._proxy.server_port)
        environment = self.container.env or {}
        environment = environment.copy()
        for (n, v) in (
            ('GILLIAM_FORMATION', self.container.formation),
            ('GILLIAM_SERVICE', self.container.service),
            ('GILLIAM_INSTANCE', self.container.instance),
            ('GILLIAM_SERVICE_REGISTRY_NODES', self.srnodes),
            ('GILLIAM_SERVICE_REGISTRY', self.srnodes),
            ('HTTP_PROXY', proxy_netloc),
            ('http_proxy', proxy_netloc),
            ('HTTPS_PROXY', proxy_netloc)):
            if v is not None:
                environment[n] = v
        return _convert_environment_dict_to_array(environment)

    def _container_config(self, image, command, hostname=None, user=None,
                          detach=False, stdin_open=False, tty=False, mem_limit=0,
                          ports=None, environment=None, dns=None, volumes=None,
                          volumes_from=None):
        if isinstance(command, six.string_types):
            command = shlex.split(str(command))
        d =  {
            'Hostname':     hostname,
            'PortSpecs':    ports,
            'User':         user,
            'Tty':          tty,
            'OpenStdin':    stdin_open,
            'Memory':       mem_limit,
            'AttachStdin':  self.attach,
            'AttachStdout': self.attach,
            'AttachStderr': self.attach,
            'Env':          environment,
            'Cmd':          command,
            'Dns':          dns,
            'Image':        image,
            'Volumes':      volumes,
            'VolumesFrom':  volumes_from,
        }
        print "CONFIG", d
        return d


def _port_mappings_from_inspect_data(data):
    """Return a port mapping announcement based on the data from
    the container.
    """
    container_mappings = data['NetworkSettings']['PortMapping']['Tcp']
    for source, forwarded in container_mappings.items():
        yield str(source), str(forwarded)


class Container(object):
    """."""

    def __init__(self, docker, runtime, registry, host,
                 image, command, env, ports,
                 formation, service, instance):
        self.docker = docker
        self.runtime = runtime
        self.registry = registry
        self.host = host
        self.id = shortuuid.uuid()
        self.log = logging.getLogger('container:%s' % (self.id,))
        self.image = image
        self.command = command
        self.env = env
        self.ports = ports
        self.formation = formation
        self.service = service
        self.instance = instance
        self.state = 'init'
        self.reason = None
        self.status_code = None
        self._stopped = Event()
        self._cont_id = None
        self._registration = None
        self._runtime = None

    def start(self):
        self.log.info("start called")
        gevent.spawn(self._provision_and_start)
        return self

    def dispose(self):
        """Dispose of the container."""
        if not self._stopped.is_set():
            if self._cont_id is not None:
                self.docker.stop(self._cont_id)
            self._stopped.set()

    def commit(self, repository, tag):
        data = self.docker.inspect_container(self._cont_id)
        self.docker.commit(self._cont_id, repository=repository, tag=tag,
                           conf=data['Config'])

    def attach(self, input, logs=0, stream=1):
        """Attach to container."""
        params = {'stdout': 1, 'stderr': 1, 'stream': stream, 'logs': logs}
        socket = self.docker.attach_socket(self._cont_id, params, input)
        while True:
            chunk = socket.recv(4096)
            if chunk:
                yield chunk
            else:
                break

    def _register_with_service_registry(self):
        data = self.docker.inspect_container(self._cont_id)
        announcement = self.registry.build_announcement(
            self.formation, self.service, self.instance,
            dict(_port_mappings_from_inspect_data(data)),
            host=self.host)
        self._registration = self.registry.register(
            self.formation, self.service, self.instance, announcement)
    
    def _provision_and_start(self):
        with self._update_state('pulling'):
            self.docker.pull(self.image)
        with self._update_state('running'):
            self._create_container()

        if self.registry is not None:
            self._register_with_service_registry()

        self.status_code = self.docker.wait(self._cont_id)

        # kill the container completely and invalidate our handle.
        #cont_id, self._cont_id = self._cont_id, None
        with self._update_state('done'):
            if self._registration is not None:
                self._registration.stop(timeout=10)
            self.docker.kill(self._cont_id)
            self._runtime.dispose()

    def _create_container(self):
        """Create container."""
        self._runtime = self.runtime(self)
        result = self.docker.create_container_from_config(
            self._runtime.make_config())
        self._cont_id = result['Id']
        self.docker.start(self._cont_id)

    @contextmanager
    def _update_state(self, state):
        self.log.info('change state to %s from %s' % (state, self.state))
        self.state = state
        try:
            yield
        except Exception, err:
            self.reason = str(err)
            self.state = 'error'
            self.log.error('error: %s' % (self.reason,))
            raise


class ContainerStore(object):
    """Simple store for containers.  Indexed by container ID."""

    def __init__(self, factory):
        self.factory = factory
        self._store = {}

    def create(self, *args, **kw):
        container = self.factory(*args, **kw)
        self._store[container.id] = container
        return container

    def lookup(self, cont_id):
        return self._store.get(cont_id)

    def remove(self, cont_id):
        del self._store[cont_id]

    def index(self):
        return self._store.items()
