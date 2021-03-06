import celery
import pickle

from pvgrip.route.cluster_route_boxes \
    import get_list_rasters
from pvgrip.route.tasks \
    import compute_route, merge_tsv

from pvgrip.raster.calls \
    import check_all_data_available
from pvgrip.raster.tasks \
    import sample_from_box
from pvgrip.raster.utils \
    import check_box_not_too_big

from pvgrip.ssdp.utils \
    import centre_of_box

from pvgrip.storage.cassandra_path \
    import Cassandra_Path


def ssdp_route(tsvfn_uploaded, box, box_delta,
               dhi, ghi, albedo, timestr, nsky, **kwargs):
    kwargs['output_type'] = 'pickle'
    kwargs['mesh_type'] = 'metric'

    rasters_fn = get_list_rasters(route_fn = tsvfn_uploaded,
                                  box = box,
                                  box_delta = box_delta)
    with open(Cassandra_Path(rasters_fn)\
              .get_locally(),'rb') as f:
        rasters = pickle.load(f)

    check_box_not_too_big(box = rasters[0]['box'],
                          step = kwargs['step'],
                          mesh_type = kwargs['mesh_type'])

    tasks = check_all_data_available\
        (rasters = rasters,
         data_re = kwargs['data_re'],
         stat = kwargs['stat'])
    group = []
    for x in rasters:
        lon, lat = centre_of_box(x['box'])
        group += \
            [sample_from_box.signature\
             (kwargs = {'box': x['box'],
                        'data_re': kwargs['data_re'],
                        'stat': kwargs['stat'],
                        'mesh_type': kwargs['mesh_type'],
                        'step': kwargs['step']},
              immutable = True) | \
             compute_route.signature\
             (kwargs = \
              {'route': x['route'],
               'lat': lat,
               'lon': lon,
               'ghi_default': ghi,
               'dhi_default': dhi,
               'time_default': timestr,
               'albedo': albedo,
               'nsky': nsky})]
    tasks |= celery.group(group)
    tasks |= merge_tsv.signature()

    return tasks
