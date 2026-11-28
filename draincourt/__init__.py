from . import connection, gatewaycheck, gatewaymerge, routes, shutdown, termination

MODULES = {'termination': termination, 'connection': connection, 'shutdown': shutdown,
           'routes': routes, 'gatewaycheck': gatewaycheck, 'gatewaymerge': gatewaymerge}
