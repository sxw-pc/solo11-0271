from . import connection, gatewaycheck, routes, shutdown, termination

MODULES = {'termination': termination, 'connection': connection, 'shutdown': shutdown,
           'routes': routes, 'gatewaycheck': gatewaycheck}
