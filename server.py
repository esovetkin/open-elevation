import os
import json
import traceback
import logging
logging.basicConfig(filename = 'data/server.log',
                    level = logging.DEBUG,
                    format = "[%(asctime)s] %(levelname)s: %(filename)s::%(funcName)s %(message)s")

import datetime

import bottle
from bottle import route, run, request, response, hook

from celery.exceptions import TimeoutError

import open_elevation.gdal_interfaces as gdal
import open_elevation.utils as utils
import open_elevation.celery_tasks as tasks
import open_elevation.celery_tasks.app as app
import open_elevation.celery_status as celery_status


interface = gdal.GDALTileInterface\
    ('data/current','data/_polygon_index',1)
logging.info("Amount of RESULTS_CACHE: %.2f" \
             % (app.RESULTS_CACHE.size()/(1024**3),))


def _return_exception(e):
    return {'results':
            {'error': type(e).__name__ + ": " + str(e),
             'traceback': traceback.format_exc()}}


def _parse_args(data, defaults):
    res = {}

    for key, _ in data.items():
        if key not in defaults:
            raise RuntimeError("Unknown argument: '%s'" % key)


    for key, item in defaults.items():
        item = item[0]
        if key not in data:
            res[key] = item
        else:
            new = data[key]
            if type(item) != type(new) \
               and isinstance(new, str) \
               and isinstance(item, (list, dict)):
                new = json.loads(new)
            res[key] = type(item)(new)

    return res


def _serve(data):
    if isinstance(data, dict):
        return data

    if isinstance(data, str) and os.path.exists(data):
        with open(data,'rb') as f:
            return f.read()

    return data


def _get_job_results(job, timeout = 30):
    try:
        if 'SUCCESS' == job.state:
            fn = job.result

        if 'FAILURE' == job.state:
            raise job.result

        if 'PENDING' == job.state:
            fn = job.wait(timeout = timeout)

        return fn
    except utils.TASK_RUNNING:
        return {'results': {'message': 'task is running'}}
    except TimeoutError:
        return {'results': {'message': 'task is running'}}
    except Exception as e:
        return _return_exception(e)


def _lookup_defaults():
    return {'location': \
            ([50.87,6.12],
             '[latitude, longitude] of the desired location'),
            'data_re': \
            (r'.*',
             'regular expression matching the dataset'),
            'stat': \
            ('max',
             'statistic to compute for LAS files ("max","min","mean","idw","count","stdev")')}


def _raster_defaults():
    res = _lookup_defaults()
    del res['location']
    res.update({'box': \
                ([50.865,7.119,50.867,7.121],
                 'bounding box of desired locations, [lat_min, lon_min, lat_max, lon_max]'),
                'step': \
                (float(1),
                 'resolution of the sampling mesh in meters'),
                'mesh_type': \
                ('metric',
                 'type of mesh_type ("metric","wgs84")'),
                'output_type': \
                ('pickle',
                 'type of output ("pickle","geotiff","pnghillshade")')})
    return res


def _shadow_defaults():
    res = _raster_defaults()
    res.update({
        'output_type': \
        ('png',
         'type of output ("png","geotiff")'),
        'what': \
        ('shadow',
         'what to compute: either incidence map (always geotiff) or binary shadow map'),
        'timestr': \
        ("2020-07-01_06:00:00",
         "UTC time of the shadow")})
    return res


def _format_help(data):
    res = []
    for key, item in data.items():
        res += ["""%15s=%s
        %s
        """ % ((key,) + item)]

    return '\n'.join(res)


@route('/api/help', method=['GET'])
def get_help():
    return {'results': """
    Query geo-spatial data

    /api/help            print help
    /api/datasets        list available datasets
    /api/raster          download a raster image of a region
    /api/shadow          compute a shadow at a time of a region
    /api/status          print current active and scheduled jobs
    /api/<what>/help     print help for <what>
    """}


@route('/api/datasets', method=['GET'])
def get_datasets():
    return {'results': interface.get_directories()}


@route('/api/status', method=['GET'])
def get_datasets():
    return {'results': celery_status.status()}


@app.cache_fn_results(link = True,
                      ignore = lambda x: isinstance(x,dict))
def _raster(args):
    try:
        job = tasks.sample_raster\
            (gdal = interface, **args).delay()
    except Exception as e:
        return _return_exception(e)

    return _get_job_results(job)


@app.cache_fn_results(link = True,
                      ignore = lambda x: isinstance(x,dict))
def _shadow(args):
    try:
        job = tasks.shadow\
            (gdal = interface, **args).delay()
    except Exception as e:
        return _return_exception(e)

    return _get_job_results(job)


@route('/api/raster', method=['GET'])
def get_raster():
    try:
        args = _parse_args(data = request.query,
                           defaults = _raster_defaults())
    except Exception as e:
        return _return_exception(e)

    return _serve(_raster(args))


@route('/api/raster', method=['POST'])
def post_raster():
    try:
        args = _parse_args(data = request.json,
                           defaults = _raster_defaults())
    except Exception as e:
        return _return_exception(e)

    return _serve(_raster(args))


@route('/api/shadow', method=['GET'])
def get_shadow():
    try:
        args = _parse_args(data = request.query,
                           defaults = _shadow_defaults())
    except Exception as e:
        return _return_exception(e)

    return _serve(_shadow(args))


@route('/api/shadow', method=['POST'])
def post_shadow():
    try:
        args = _parse_args(data = request.json,
                           defaults = _shadow_defaults())
    except Exception as e:
        return _return_exception(e)

    return _serve(_shadow(args))


@route('/api/raster/help', method=['GET'])
def get_raster_help():
    return {'results': _format_help(_raster_defaults())}


@route('/api/shadow/help', method=['GET'])
def get_shadow_help():
    return {'results': _format_help(_shadow_defaults())}


run(host='0.0.0.0', port=8080,
    server='gunicorn',
    workers=8, timeout=60)
