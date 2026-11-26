from . import connection, milestone, nodeclock, shutdown, termination

MODULES = {'termination': termination, 'connection': connection, 'shutdown': shutdown,
           'nodeclock': nodeclock, 'milestone': milestone}
