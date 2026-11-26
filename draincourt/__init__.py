from . import connection, images, nodeimage, shutdown, termination

MODULES = {'termination': termination, 'connection': connection, 'shutdown': shutdown,
           'images': images, 'nodeimage': nodeimage}
