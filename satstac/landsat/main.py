import gzip
import logging
import os
import requests
import sys

from datetime import datetime
from dateutil.parser import parse
from satstac import Collection, Item, utils

from .version import __version__


logger = logging.getLogger(__name__)

collection_l8l1 = Collection.open(os.path.join(os.path.dirname(__file__), 'landsat-8-l1.json'))


# pre-collection
# entityId,acquisitionDate,cloudCover,processingLevel,path,row,min_lat,min_lon,max_lat,max_lon,download_url
# collection-1
# productId,entityId,acquisitionDate,cloudCover,processingLevel,path,row,min_lat,min_lon,max_lat,max_lon,download_url


def add_items(catalog, collections='all', start_date=None, end_date=None):
    """ Stream records to a collection with a transform function """
    
    cols = {c.id: c for c in catalog.collections()}
    if 'landsat-8-l1' not in cols.keys():
        catalog.add_catalog(collection_l8l1)
        cols = {c.id: c for c in catalog.collections()}
    collection = cols['landsat-8-l1']

    for i, record in enumerate(records(collections=collections)):
        now = datetime.now()
        dt = record['datetime'].date()
        if (i % 10000) == 0:
            logger.info('%s: %s records scanned' % (datetime.now(), i))
        if (start_date is not None and dt < start_date) or (end_date is not None and dt > end_date):
            # skip to next if before start_date or after end_date
            continue
        fname = os.path.join(os.path.dirname(collection.filename), record['filename'])
        if os.path.exists(fname):
            item = Item.open(fname)
            if item['landsat:tier'] != 'RT':
                continue
        try:
            item = transform(record)
        except Exception as err:
            fname = record['url'].replace('index.html', '%s_MTL.txt' % record['id'])
            logger.error('Error getting %s: %s' % (fname, err))
            continue
        try:
            collection.add_item(item, path='${eo:column}/${eo:row}/${date}')
        except Exception as err:
            logger.error('Error adding %s: %s' % (item.id, err))


def records(collections='all'):
    """ Return generator function for list of scenes """

    filenames = {}
    if collections in ['pre', 'all']:
        filenames['scene_list.gz'] = 'https://landsat-pds.s3.amazonaws.com/scene_list.gz'
    if collections in ['c1', 'all']:
        filenames['scene_list-c1.gz'] ='https://landsat-pds.s3.amazonaws.com/c1/L8/scene_list.gz'

    for fout in filenames:
        filename = filenames[fout]
        fout = utils.download_file(filename, filename=fout)
        with gzip.open(fout,'rt') as f:
            header = f.readline()
            for line in f:
                data = line.replace('\n', '').split(',')
                if len(data) == 12:
                    product_id = data[0]
                    data = data[1:]
                else:
                    product_id = data[0]
                yield {
                    'id': data[0],
                    'product_id': product_id,
                    'datetime': parse(data[1]),
                    'url': data[-1],
                    'filename': os.path.join(data[4], data[5], str(parse(data[1]).date()), data[0] + '.json')
                }


def transform(data):
    """ Transform Landsat metadata into a STAC item """
    root_url = os.path.join(data['url'].replace('index.html', ''), data['product_id'])
    # get metadata
    md = get_metadata(root_url + '_MTL.txt')

    # geo
    coordinates = [[
        [float(md['CORNER_UL_LON_PRODUCT']), float(md['CORNER_UL_LAT_PRODUCT'])],
        [float(md['CORNER_UR_LON_PRODUCT']), float(md['CORNER_UR_LAT_PRODUCT'])],
        [float(md['CORNER_LR_LON_PRODUCT']), float(md['CORNER_LR_LAT_PRODUCT'])],
        [float(md['CORNER_LL_LON_PRODUCT']), float(md['CORNER_LL_LAT_PRODUCT'])],
        [float(md['CORNER_UL_LON_PRODUCT']), float(md['CORNER_UL_LAT_PRODUCT'])]
    ]]

    lats = [c[1] for c in coordinates[0]]
    lons = [c[0] for c in coordinates[0]]
    bbox = [min(lons), min(lats), max(lons), max(lats)]

    assets = collection_l8l1.data['assets']
    assets = utils.dict_merge(assets, {
        'index': {'href': data['url']},
        'thumbnail': {'href': root_url + '_thumb_large.jpg'},
        'B1': {'href': root_url + '_B1.TIF'},
        'B2': {'href': root_url + '_B2.TIF'},
        'B3': {'href': root_url + '_B3.TIF'},
        'B4': {'href': root_url + '_B4.TIF'},
        'B5': {'href': root_url + '_B5.TIF'},
        'B6': {'href': root_url + '_B6.TIF'},
        'B7': {'href': root_url + '_B7.TIF'},
        'B8': {'href': root_url + '_B8.TIF'},
        'B9': {'href': root_url + '_B9.TIF'},
        'B10': {'href': root_url + '_B10.TIF'},
        'B11': {'href': root_url + '_B11.TIF'},
        'ANG': {'href': root_url + '_ANG.txt'},
        'MTL': {'href': root_url + '_MTL.txt'},
        'BQA': {'href': root_url + '_BQA.TIF'},
    })

    props = {
        'collection': 'landsat-8-l1',
        'datetime': parse('%sT%s' % (md['DATE_ACQUIRED'], md['SCENE_CENTER_TIME'])).isoformat(),
        'eo:sun_azimuth': float(md['SUN_AZIMUTH']),
        'eo:sun_elevation': float(md['SUN_ELEVATION']),
        'eo:cloud_cover': int(float(md['CLOUD_COVER'])),
        'eo:row': md['WRS_ROW'].zfill(3),
        'eo:column': md['WRS_PATH'].zfill(3),
        'landsat:product_id': md.get('LANDSAT_PRODUCT_ID', None),
        'landsat:scene_id': md['LANDSAT_SCENE_ID'],
        'landsat:processing_level': md['DATA_TYPE'],
        'landsat:tier': md.get('COLLECTION_CATEGORY', 'pre-collection')
    }

    if 'UTM_ZONE' in md:
        center_lat = (min(lats) + max(lats))/2.0
        props['eo:epsg'] = int(('326' if center_lat > 0 else '327') + md['UTM_ZONE'])

    _item = {
        'type': 'Feature',
        'id': data['id'],
        'bbox': bbox,
        'geometry': {
            'type': 'Polygon',
            'coordinates': coordinates
        },
        'properties':props,
        'assets': assets
    }
    return Item(_item)


def get_metadata(url):
    """ Convert Landsat MTL file to dictionary of metadata values """
    # Read MTL file remotely
    #r = requests.get(url, stream=True)
    mtl = dict()
    #for line in r.iter_lines():
    for line in read_remote(url):
        meta = line.replace('\"', "").strip().split('=')
        if len(meta) > 1:
            key = meta[0].strip()
            item = meta[1].strip()
            if key != "GROUP" and key != "END_GROUP":
                mtl[key] = item
    return mtl


def read_remote(url):
    """ Return a line iterator for a remote file """
    r = requests.get(url, stream=True)
    if r.status_code != 400:
        print('Error: %s not found')
    for line in r.iter_lines():
        yield line.decode()
