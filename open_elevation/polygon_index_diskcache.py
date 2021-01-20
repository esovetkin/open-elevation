import os
import time
import diskcache

from open_elevation.polygon_index \
    import Polygon_File_Index


class Polygon_File_Index_Diskcache(Polygon_File_Index):

    def __init__(self, path, size_limit = 2):
        """Same as Polygon_File_Index just store data in diskcache

        :path: where to keep the diskcache

        :size_limit: size limit in GB for the cache

        :check_every: check content not more often than this number of
        seconds

        """
        super().__init__()
        self._path = path
        self._cache = diskcache.Cache\
            (directory = os.path.join(self._path,"_polygon_index"),
             size_limit = size_limit*(1024**3))
        self._lock = diskcache.RLock(self._cache, '_lock')


    def _get_data(self, x):
        return self._cache[x]


    def _set_data(self, x):
        with self._lock:
            self._cache[x['file']] = x

        return x['file']


    def insert(self, data):
        self._check_data(data)

        if '_lock' == data['file']:
            raise RuntimeError\
                ('File must not be named "_lock"!')

        return super().insert(data)


    def check_cache(self):
        """

        """
        for key in self._cache.iterkeys():
            if '_lock' == key:
                continue

            data = self._cache[key]

            if data['file'] not in self._polygons:
                self.update(data)
