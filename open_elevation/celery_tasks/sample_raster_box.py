import os
import time
import pickle
import logging
import tempfile
import itertools
import numpy as np

import celery

import open_elevation.celery_tasks.app \
    as app
import open_elevation.celery_tasks.save_geotiff \
    as save_geotiff
import open_elevation.celery_tasks.save_hillshade \
    as save_hillshade
import open_elevation.utils \
    as utils
import open_elevation.mesh \
    as mesh
import open_elevation.gdal_interfaces \
    as gdal
import open_elevation.polygon_index \
    as polygon_index


def _query_coordinate(lon, lat, gdal_data):
    nearest = list(gdal_data._index.nearest((lon,lat)))
    data = gdal.choose_highest_resolution(nearest)

    interface = None
    while not interface:
        try:
            interface = gdal_data.open_gdal_interface\
                (data['file'])
        except utils.TASK_RUNNING:
            time.sleep(5)
            continue
    return float(interface.lookup(lat, lon))


@app.CELERY_APP.task()
@app.one_instance(expire = 360)
def _check_path_available(index_fn, path):
    if os.path.exists(path):
        return

    gdal_data = gdal.GDALTileInterface(tiles_folder = None,
                                       index_file = index_fn,
                                       use_only_index = True)
    interface = None
    while not interface:
        try:
            interface = gdal_data.open_gdal_interface(path)
        except utils.TASK_RUNNING:
            time.sleep(5)
            continue


def check_all_data_available(index_fn):
    index = polygon_index.Polygon_File_Index()
    index.load(index_fn)
    tasks = [_check_path_available.signature\
             (args = (),
              kwargs = {'index_fn': index_fn, 'path': x},
              immutable = True)
             for x in index.files()]
    return celery.group(tasks)


@app.CELERY_APP.task()
@app.cache_fn_results(keys = ['box','data_re',
                              'mesh_type','step'])
@app.one_instance(expire = 200)
def sample_from_box(index_fn, box, data_re,
                    mesh_type = 'metric', step = 1,
                    max_points = 2e+7):
    gdal_data = gdal.GDALTileInterface(tiles_folder = None,
                                       index_file = index_fn,
                                       use_only_index = True)
    grid = mesh.mesh(box = box, step = step,
                     which = mesh_type)

    if len(grid['mesh'][0])*len(grid['mesh'][1]) \
       > max_points:
        raise RuntimeError\
            ("either box or resolution is too high!")

    res = []
    for lon, lat in itertools.product(*grid['mesh']):
        try:
            res += [_query_coordinate(lon = lon, lat = lat,
                                      gdal_data = gdal_data)]
        except Exception as e:
            res += [-9999]
            continue
    res = np.array(res).reshape(len(grid['mesh'][0]),
                                len(grid['mesh'][1]))
    res = np.transpose(res)[::-1,]

    ofn = utils.get_tempfile()
    try:
        with open(ofn, 'wb') as f:
            pickle.dump({'raster': res, 'mesh': grid}, f)
    except Exception as e:
        utils.remove_file(ofn)
        raise e
    return ofn


def sample_raster(gdal, box, data_re,
                  mesh_type, step, output_type):
    if output_type not in ('geotiff', 'pickle', 'pnghillshade'):
        raise RuntimeError("Invalid 'output_type' argument!")

    index_fn = gdal.subset(box = box, data_re = data_re)
    tasks = celery.chain\
        (check_all_data_available(index_fn),\
         sample_from_box.signature\
         (kwargs = {'index_fn': index_fn, 'box': box,
                    'data_re': data_re, 'mesh_type': mesh_type,
                    'step': step},
          immutable = True))

    if output_type in ('geotiff', 'pnghillshade'):
        tasks |= save_geotiff.save_geotiff.signature()

    if output_type == 'pnghillshade':
        tasks |= save_hillshade.save_pnghillshade.signature()

    return tasks