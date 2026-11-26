from . import addresses, connection, flows, shutdown, termination

MODULES = {'termination': termination, 'connection': connection, 'shutdown': shutdown,
           'addresses': addresses, 'flows': flows}
