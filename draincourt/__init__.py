from . import connection, retryattempt, retrybudget, shutdown, termination

MODULES = {'termination': termination, 'connection': connection, 'shutdown': shutdown,
           'retrybudget': retrybudget, 'retryattempt': retryattempt}
