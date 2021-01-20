import os
import shutil

from open_elevation.polygon_index_diskcache \
    import Polygon_File_Index_Diskcache


def touch(fname, times=None, size = 1024):
    with open(fname, 'wb') as f:
        f.seek(size - 1)
        f.write(b'\0')


def test_polygon_index_diskcache():
    path = "test_polygon_index_diskcache"
    try:
        os.makedirs(path, exist_ok = True)
        idx = Polygon_File_Index_Diskcache(path = path)
        p = os.path.join(path,'one')
        touch(p)
        idx.insert({'file': p,
                    'polygon': [(0,0),(0,1),(1,1),(1,0)]})
        assert 1 == len(list(idx.nearest((0.5,0.5))))

        idx2 = Polygon_File_Index_Diskcache(path = path)
        idx2.check_cache()
        assert 1 == len(list(idx2.nearest((0.5,0.5))))

        p = os.path.join(path,'two')
        idx.insert({'file': p,
                    'polygon': [(0,0),(0,1),(1,1),(1,0)]})
        assert 1 == len(list(idx.nearest((0.5,0.5))))

        touch(p)
        idx.insert({'file': p,
                    'polygon': [(0,0),(0,1),(1,1),(1,0)]})
        assert 2 == len(list(idx.nearest((0.5,0.5))))
        idx2.check_cache()
        assert 2 == len(list(idx2.nearest((0.5,0.5))))
    finally:
        shutil.rmtree(path)
