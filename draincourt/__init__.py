from . import connection, hookconfig, projection, shutdown, termination

MODULES = {'termination': termination, 'connection': connection, 'shutdown': shutdown,
           'projection': projection, 'hookconfig': hookconfig}
