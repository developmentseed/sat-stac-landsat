import boto3
import gzip
import json
import logging
import os
import requests
import sys

from botocore.exceptions import ClientError
from datetime import datetime
from dateutil.parser import parse
from satstac import Collection, Item, utils

from .version import __version__

s3 = boto3.client('s3')

logger = logging.getLogger(__name__)

collection_l8l1 = Collection.open(os.path.join(os.path.dirname(__file__), 'landsat-8-l1.json'))

pr2coords = None

# pre-collection
# entityId,acquisitionDate,cloudCover,processingLevel,path,row,min_lat,min_lon,max_lat,max_lon,download_url
# collection-1
# productId,entityId,acquisitionDate,cloudCover,processingLevel,path,row,min_lat,min_lon,max_lat,max_lon,download_url


def add_items(catalog, collections='all', realtime=False, missing=False, start_date=None, end_date=None):
    """ Stream records to a collection with a transform function """
    
    cols = {c.id: c for c in catalog.collections()}
    if 'landsat-8-l1' not in cols.keys():
        catalog.add_catalog(collection_l8l1)
        cols = {c.id: c for c in catalog.collections()}
    collection = cols['landsat-8-l1']

    bucket = catalog.filename.replace('https://', '').replace('http://', '').split('.')[0]

    for i, record in enumerate(records(collections=collections, realtime=realtime)):
        now = datetime.now()
        dt = record['datetime'].date()
        if (i % 10000) == 0:
            logger.info('%s: %s records scanned' % (datetime.now(), i))
        if (start_date is not None and dt < start_date) or (end_date is not None and dt > end_date):
            # skip to next if before start_date or after end_date
            continue
        # check if item already exists in catalog
        if missing:
            parts = record['url'].split('/')
            # this key should match the same key generated by `collection.add_item` below
            key = os.path.join(collection.id, parts[5], parts[6], str(record['datetime'].date()), record['id']) + '.json'
            if exists_on_s3(bucket, key):
                continue
        try:
            fname = record['url'].replace('index.html', '%s_MTL.txt' % record['id'])
            item = transform(fname)
        except Exception as err:
            logger.error('Error transforming %s: %s' % (fname, err))
            continue
        try:
            collection.add_item(item, path='${eo:column}/${eo:row}', filename='${date}/${id}')
        except Exception as err:
            logger.error('Error adding %s: %s' % (item.id, err))


def records(collections='all', realtime=False):
    """ Return generator function for list of scenes """
    # allows us to defer reading this big file unless we need to
    global pr2coords

    filenames = {}
    if collections in ['pre', 'all']:
        filenames['scene_list.gz'] = 'https://landsat-pds.s3.amazonaws.com/scene_list.gz'
        with open(os.path.join(os.path.dirname(__file__), 'pr2coords.json')) as f:
            pr2coords = json.loads(f.read())
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
                    id = data[0]
                    tier = id.split('_')[-1]
                    if tier == 'RT' and realtime is False:
                        continue
                    data = data[1:]
                else:
                    id = data[0]
                yield {
                    'id': id,
                    'datetime': parse(data[1]),
                    'url': data[-1],
                    #'filename': os.path.join(data[4], data[5], str(parse(data[1]).date()), data[0] + '.json')
                }


def coords_from_ANG(url, bbox):
    try:
        sz = []
        coords = []
        for line in read_remote(url):
            if 'BAND01_NUM_L1T_LINES' in line or 'BAND01_NUM_L1T_SAMPS' in line:
                sz.append(float(line.split('=')[1]))
            if 'BAND01_L1T_IMAGE_CORNER_LINES' in line or 'BAND01_L1T_IMAGE_CORNER_SAMPS' in line:
                coords.append([float(l) for l in line.split('=')[1].strip().strip('()').split(',')])
            if len(coords) == 2:
                break
        dlon = bbox[2] - bbox[0]
        dlat = bbox[3] - bbox[1]
        lons = [c/sz[1] * dlon + bbox[0] for c in coords[1]]
        lats = [((sz[0] - c)/sz[0]) * dlat + bbox[1] for c in coords[0]]
        coordinates = [[
            [lons[0], lats[0]], [lons[1], lats[1]], [lons[2], lats[2]], [lons[3], lats[3]], [lons[0], lats[0]]
        ]]
        return coordinates
    except:
        logger.warning('Problem reading ANG file, may be pre-collection1 data: %s' % url)
        # TODO - retrieve from WRS-3 using path/row
        return None    

def transform(url, collection=collection_l8l1):
    """ Transform Landsat metadata (URL to MTL File) into a STAC item """
    # get metadata
    root_url = url.replace('_MTL.txt', '')
    md = get_metadata(url)

    # needed later
    path = md['WRS_PATH'].zfill(3)
    row = md['WRS_ROW'].zfill(3)
    tier = md.get('COLLECTION_CATEGORY', 'pre-collection')

    # geo
    coords = [[
        [float(md['CORNER_UL_LON_PRODUCT']), float(md['CORNER_UL_LAT_PRODUCT'])],
        [float(md['CORNER_UR_LON_PRODUCT']), float(md['CORNER_UR_LAT_PRODUCT'])],
        [float(md['CORNER_LR_LON_PRODUCT']), float(md['CORNER_LR_LAT_PRODUCT'])],
        [float(md['CORNER_LL_LON_PRODUCT']), float(md['CORNER_LL_LAT_PRODUCT'])],
        [float(md['CORNER_UL_LON_PRODUCT']), float(md['CORNER_UL_LAT_PRODUCT'])]
    ]]
    lats = [c[1] for c in coords[0]]
    lons = [c[0] for c in coords[0]]
    bbox = [min(lons), min(lats), max(lons), max(lats)]

    coordinates = None
    if tier != 'pre-collection':
        coordinates = coords_from_ANG(root_url + '_ANG.txt', bbox)
    if coordinates is None:
        coordinates = pr2coords[path+row]

    assets = collection.data['assets']
    assets = utils.dict_merge(assets, {
        'index': {'href': url},
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
        'collection': collection.id,
        'datetime': parse('%sT%s' % (md['DATE_ACQUIRED'], md['SCENE_CENTER_TIME'])).isoformat(),
        'eo:sun_azimuth': float(md['SUN_AZIMUTH']),
        'eo:sun_elevation': float(md['SUN_ELEVATION']),
        'eo:cloud_cover': int(float(md['CLOUD_COVER'])),
        'eo:row': row,
        'eo:column': path,
        'landsat:product_id': md.get('LANDSAT_PRODUCT_ID', None),
        'landsat:scene_id': md['LANDSAT_SCENE_ID'],
        'landsat:processing_level': md['DATA_TYPE'],
        'landsat:tier': tier,
        'landsat:revision': md['LANDSAT_SCENE_ID'][-2:]
    }

    if 'UTM_ZONE' in md:
        center_lat = (min(lats) + max(lats))/2.0
        props['eo:epsg'] = int(('326' if center_lat > 0 else '327') + md['UTM_ZONE'])

    _item = {
        'type': 'Feature',
        'id': md['LANDSAT_SCENE_ID'][:-5],
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
    if 's3://' in url:
        parts = url.replace('s3://', '').split('/')
        obj = s3.get_object(Bucket=parts[0], Key='/'.join(parts[1:]))
        for line in obj['Body'].read().decode().split('\n'):
            yield line
    else:
        r = requests.get(url, stream=True)
        if r.status_code != 200:
            print('Error: %s not found' % url)
        for line in r.iter_lines():
            yield line.decode()


def exists_on_s3(bucket, key):
    """ Check if this URL exists on S3 """
    try:
        obj = s3.head_object(Bucket=bucket, Key=key)
        return obj['ContentLength']
    except ClientError as exc:
        if exc.response['Error']['Code'] != '404':
            raise
    #except Exception as e:
    #    if e.response['Error']['Code'] == 'NoSuchKey':
    #        return False
    #    else:
    #        raise
