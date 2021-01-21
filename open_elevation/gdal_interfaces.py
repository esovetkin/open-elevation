import os
import re
import sys
import json
import logging
import tempfile
import threading

import numpy as np
import osgeo.gdal as gdal
import osgeo.osr as osr

from cachetools import LRUCache
from lazy import lazy

from tqdm import tqdm

import open_elevation.utils as utils
import open_elevation.nrw_las as nrw_las
import open_elevation.celery_tasks.app as app
import open_elevation.polygon_index_diskcache as polygon_index

from open_elevation.results_lrucache \
    import ResultFiles_LRUCache


def _polygon_from_box(box):
    return [(box[1],box[0]),
            (box[3],box[0]),
            (box[3],box[2]),
            (box[1],box[2])]


def _subset_filter_how(x, data_re, stat):
    data_re = re.compile(data_re)

    if 'stat' not in x and \
       not data_re.match(x['file']):
        return False

    if 'stat' in x and \
       not data_re.match(x['las_meta']):
        return False

    if 'stat' in x and \
       stat != x['stat']:
        return False

    return True


def in_directory(fn, paths):
    fn = os.path.abspath(fn)

    for path in paths:
        path = os.path.abspath(path)

        if os.path.commonprefix([fn, path]) == path:
            return path

    return None


def choose_highest_resolution(nearest):
    if not nearest:
        raise Exception('No data for coordinate exist')

    if len(nearest) == 1:
        return nearest[0]

    return nearest[np.argmin([np.prod(x['resolution']) \
                              for x in nearest])]


# Originally based on https://stackoverflow.com/questions/13439357/extract-point-from-raster-in-gdal
class GDALInterface(object):
    SEA_LEVEL = -9999
    def __init__(self, path):
        super(GDALInterface, self).__init__()
        self.path = path
        self.loadMetadata()


    def get_corner_coords(self):
        ulx, xres, _, uly, _, yres = self.geo_transform
        lrx = ulx + (self.src.RasterXSize * xres)
        lry = uly + (self.src.RasterYSize * yres)
        return {
            'TOP_LEFT': \
            self._coordinate_transform_inv\
            .TransformPoint(ulx, uly, 0),
            'TOP_RIGHT': \
            self._coordinate_transform_inv\
            .TransformPoint(lrx, uly, 0),
            'BOTTOM_LEFT': \
            self._coordinate_transform_inv\
            .TransformPoint(ulx, lry, 0),
            'BOTTOM_RIGHT': \
            self._coordinate_transform_inv\
            .TransformPoint(lrx, lry, 0),
        }


    def get_centre(self):
        ulx, xres, _, uly, _, yres = self.geo_transform
        cx = ulx + (self.src.RasterXSize/2 * xres)
        cy = uly + (self.src.RasterYSize/2 * yres)
        res = self._coordinate_transform_inv\
                  .TransformPoint(cx, cy, 0)
        return {'lon': res[0],
                'lat': res[1]}


    def get_resolution(self):
        _, xres, _, _, _, yres = self.geo_transform
        return (abs(xres), abs(yres))


    def loadMetadata(self):
        # open the raster and its spatial reference
        self.src = gdal.Open(self.path)

        if self.src is None:
            raise Exception\
                ('Could not load GDAL file "%s"' % self.path)
        spatial_reference_raster = osr.SpatialReference\
            (self.src.GetProjection())
        spatial_reference_raster.SetAxisMappingStrategy\
            (osr.OAMS_TRADITIONAL_GIS_ORDER)

        self.epsg = int(spatial_reference_raster\
                        .GetAttrValue('AUTHORITY',1))

        spatial_reference = osr.SpatialReference()
        spatial_reference.ImportFromEPSG(4326) # WGS84
        spatial_reference.SetAxisMappingStrategy\
            (osr.OAMS_TRADITIONAL_GIS_ORDER)

        # coordinate transformation
        self._coordinate_transform = \
            osr.CoordinateTransformation\
            (spatial_reference, spatial_reference_raster)
        self._coordinate_transform_inv = \
            osr.CoordinateTransformation\
            (spatial_reference_raster, spatial_reference)
        gt = self.geo_transform = self.src.GetGeoTransform()
        dev = (gt[1] * gt[5] - gt[2] * gt[4])
        self.geo_transform_inv = \
            (gt[0], gt[5] / dev, -gt[2] / dev,
             gt[3], -gt[4] / dev, gt[1] / dev)


    @lazy
    def points_array(self):
        b = self.src.GetRasterBand(1)
        return b.ReadAsArray()


    def print_statistics(self):
        print(self.src.GetRasterBand(1)\
              .GetStatistics(True, True))


    def _get_pixels(self, points):
        data = self._coordinate_transform\
                   .TransformPoints(points)
        data = [(u - self.geo_transform_inv[0],
                 v - self.geo_transform_inv[3])
                for u,v,_ in data]
        data = [(int(self.geo_transform_inv[4] * u +\
                     self.geo_transform_inv[5] * v),
                 int(self.geo_transform_inv[1] * u + \
                     self.geo_transform_inv[2] * v)) \
                for u,v in data]
        return data


    def lookup(self, points):
        points = self._get_pixels(points)
        res = []
        for y,x in points:
            if 0 <= y < self.src.RasterYSize \
               and 0 <= x < self.src.RasterXSize:
                res += [self.points_array[y,x]]
            else:
                res += [self.SEA_LEVEL]

        return res


    def lookup_one(self, lat, lon):
        return self.lookup([lon, lat])[0]


    def close(self):
        self.src = None

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()


class Interface_LRUCache(LRUCache):
    def popitem(self):
        key, value = super().popitem()
        value.close()

        logging.info("""
        pop GDAL Interface from memory: %s
        """ % key)
        return key, value


class GDALTileInterface(object):
    def __init__(self, tiles_folder, index_path,
                 open_interfaces_size=5):
        """The class keeps cache of the read files

        :open_interfaces_size: size of the LRU cache of the loaded interfaces. The interfaces are read lazily.

        """
        super(GDALTileInterface, self).__init__()
        self.path = tiles_folder
        self._index = polygon_index\
            .Polygon_File_Index_Diskcache(path = index_path)
        self._index.check_cache()
        self._interfaces = Interface_LRUCache\
            (maxsize = open_interfaces_size)
        self._interfaces_lock = threading.RLock()

        self._las_dirs = dict()
        self._all_coords = []

        self._find_las_dirs(path = self.path)
        self._fill_all_coords()
        self._build_index()


    def _files_timestamp(self):
        files = {}
        res = 0
        for x in self._all_coords:
            fn = os.path.dirname\
                (os.path.abspath(x['file']))
            if fn in files:
                continue
            mtime = os.stat(fn).st_mtime
            files[fn] = mtime
            if mtime > res:
                res = mtime

        for path, _ in self._las_dirs.items():
            if path in files:
                continue
            mtime = os.stat(path).st_mtime
            files[path] = mtime
            if mtime > res:
                res = mtime

        return res


    def _find_las_dirs(self, path):
        for fn in utils.list_files\
            (path = path,
             regex = '.*/las_meta\.json$'):
            dn = os.path.abspath(os.path.dirname(fn))
            self._las_dirs[dn] = nrw_las.NRWData(path = dn)


    def open_gdal_interface(self, path):
        if path not in self._interfaces:
            with self._interfaces_lock:
                self._interfaces[path] = GDALInterface(path)

        return self._interfaces[path]


    def _get_index_data(self, fn, interface):
        coords = interface.get_corner_coords()
        return {'file': fn,
                'resolution': interface.get_resolution(),
                'polygon': \
                [ coords['BOTTOM_LEFT'],
                  coords['TOP_LEFT'],
                  coords['TOP_RIGHT'],
                  coords['BOTTOM_RIGHT'],
                ]}


    def _fill_all_coords(self):
        for fn in tqdm(utils.list_files(self.path, regex = '.*'),
                       desc = "Searching for Geo files"):
            # ignore las directories
            if in_directory(fn, self._las_dirs.keys()):
                continue

            try:
                i = self.open_gdal_interface(fn)
                coords = i.get_corner_coords()
                self._all_coords += \
                    [self._get_index_data(fn, i)]
            except Exception as e:
                logging.error("""
                Could not process file: %s
                Error: %s
                Skipping...
                """ % (fn, str(e)))
                continue


    def get_directories(self):
        res = list(set([os.path.dirname(fn['file']) \
                        for fn in self._all_coords]))
        res += list(self._las_dirs.keys())
        return res


    @app.cache_fn_results(keys = ['box','data_re','stat'])
    def subset(self, box, data_re, stat):
        index = self._index.intersect\
            (polygon = _polygon_from_box(box))
        index = index.filter\
            (how = lambda x:
             _subset_filter_how(x, data_re, stat))

        ofn = utils.get_tempfile()
        try:
            index.save(ofn)
        except Exception as e:
            utils.remove_file(ofn)
            raise e
        return ofn


    def _build_index(self):
        for e in tqdm(self._all_coords,
                      desc = "Building index"):
            self._index.insert(data=e)

        for _,v in self._las_dirs.items():
            self._index = v.update_index(self._index)
