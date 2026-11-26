from . import connection, shutdown, termination, volume

MODULES = {
    'termination': termination,
    'connection': connection,
    'shutdown': shutdown,
    'volume': volume,
}
