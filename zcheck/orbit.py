import sys
from itertools import chain
from pprint import pprint

from pymongo import MongoClient, errors

from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from collections import namedtuple
import base64
from .s3 import BackendWrapper
from .util import MONGO_BASE_HOST
from . import error
from .log import Log

_log = Log('orbit')

BACKEND_TYPES = {
	"location-mem-v1" : 'MEM',
	"location-file-v1": 'FILE',
	"location-azure-v1": 'AZURE',
	"location-aws-s3-v1": 'AWS',
	"location-gcp-v1": 'GCP',
	"location-b2-v1": 'BACKBLAZE B2',
	"location-wasabi-v1": 'WASABI',
	"location-do-spaces-v1": 'DIGITAL OCEAN',
	'location-scality-ring-s3-v1': 'SCALITY RING S3C',
	'location-scality-sproxyd-v1': 'SCALITY RING SPD',
	'location-ceph-radosgw-s3-v1': 'CEPH'
}

User = namedtuple('User', ['name', 'access_key', 'secret_key'])
Backend = namedtuple('Backend', ['name', 'type', 'access_key', 'secret_key', 'endpoint', 'bucket', 'transient'])

class ReplicationStream:
	_headings = ('name', 'source', 'prefix', 'destinations', 'enabled')
	_heading_color = 'cyan'
	def __init__(self, **kwargs):
		self._name = kwargs.get('name')
		self._stream_id = kwargs.get('streamId')
		self._enabled = kwargs.get('enabled', False)
		self._source = kwargs.get('source', {}).get('bucketName')
		self._prefix = kwargs.get('source', {}).get('prefix')
		self._destinations = [(l['name'], l['storageClass']) for l in kwargs.get('destination', {}).get('locations', [])]

	@property
	def destinations(self):
		return ','.join(n for n, c in self._destinations)

	@property
	def repr(self):
		color = 'green' if self._enabled else 'red'
		return self._name, self._source, self._prefix if self._prefix else '----', self.destinations, (str(self._enabled), color)

	@classmethod
	def headings(cls):
		return tuple((k.upper(), cls._heading_color) for k in cls._headings)

class OverlayConfig:
	_heading_color = 'cyan'
	_instance_headings = ('instance id',)
	_user_headings = ('username', 'account type', 'access key', 'canonicalId')
	_location_headings = ('name', 'type', 'access key', 'bucket', 'transient', 'endpoint')
	_endpoint_headings = ('hostname', 'location', 'builtin')
	_backend_check_headings = ('backend', 'bucket', 'transient', 'exists', 'owned')

	def __init__(self, helm_release = None, mongo = None, verbose = False):
		if mongo is None:
			if helm_release is None:
				raise Exception('You must provide either monge or helm_release!')
			self._host ='%s-%s'%(helm_release, MONGO_BASE_HOST)
		else:
			self._host = mongo
		self._client = self._build_client(self._host)
		self._verbose = verbose
		self._pulled = False
		self._pull_config()

	def _build_client(self, host):
		return MongoClient(host,
			connectTimeoutMS = 5000,
			serverSelectionTimeoutMS = 5000)

	@property
	def mongo(self):
		return self._host

	def _pull_priv_key(self):
		if not 'metadata' in self._client.database_names():
			raise error.DBNotFoundException('metadata')
		db = self._client['metadata']
		if not 'PENSIEVE' in db.collection_names():
					raise error.CollectionNotFoundException('PENSIEVE', 'metadata')
		col = db['PENSIEVE']
		auth = col.find_one({'_id': 'auth/zenko/remote-management-token'})
		if auth is None:
			raise NoOverlayPrivateKeyException
		return RSA.importKey(auth['value']['privateKey'])

	def _build_cypher(self):
		key = self._pull_priv_key()
		return PKCS1_OAEP.new(key, hashAlgo=SHA256.new(), label='')

	@property
	def cypher(self):
		return self._build_cypher()

	def decrypt(self, data):
		return self.cypher.decrypt(
			base64.b64decode(data)
		).decode('utf-8')

	def _pull_config(self):
		if not self._pulled:
			try:
				if not 'metadata' in self._client.database_names():
					raise error.DBNotFoundException('metadata')
				db = self._client['metadata']
				if not 'PENSIEVE' in db.collection_names():
					raise error.CollectionNotFoundException('PENSIEVE', 'metadata')
				col = db['PENSIEVE']
				version = col.find_one({'_id':'configuration/overlay-version'})
				if version is None:
					raise error.NoOverlayConfigException
				config = col.find_one({'_id': 'configuration/overlay/%s'%version['value']})
				if config is None:
					raise error.InvalidOverlayConfigVersionException('"%s"'%version['value'])
				self._pulled = True
				self._parse_config(config['value'])
				return True, None
			except errors.ServerSelectionTimeoutError:
				_log.error('Failed to connect to mongodb')
				raise error.MongoConnectionError(self.mongo)

	def _parse_config(self, config):
		self._instance_id = config['instanceId']
		self._users = config['users']
		self._locations = list(config['locations'].values())
		self._replication_streams = [ReplicationStream(**rs) for rs in config['replicationStreams']]
		self._endpoints = config['endpoints']

	@property
	def _repr_users(self):
		users = []
		for user in self._users:
			cid = user['canonicalId'] if self._verbose else '%s...%s'%(user['canonicalId'][:10], user['canonicalId'][-15:])
			ak = user['accessKey'] if self._verbose else '%s...%s'%(user['accessKey'][:6], user['accessKey'][-5:])
			users.append((user['userName'], user['accountType'], ak, cid))
		return users

	@property
	def _repr_locations(self):
		locations = []
		for loc in self._locations:
			details = loc.get('details', None)
			transient = ('True', 'green') if loc.get('isTransient', False) else ('False', 'red')
			if details:
				ak = details.get('accessKey', '----')
				ak = ak if self._verbose or ak == '----' else '%s...%s'%(ak[:6], ak[-5:])
				ep = details.get('endpoint', '----')
				bucket = details.get('bucketName', '----')
			else:
				ep, ak, bucket = '----', '----', '----'
			locations.append((loc['name'], BACKEND_TYPES.get(loc['locationType'], 'UNKNOWN'), ak, bucket, transient, ep))
		return locations

	@property
	def _repr_replication_streams(self):
		return [rs.repr for rs in self._replication_streams]

	@property
	def _repr_endpoints(self):
		eps = []
		for ep in self._endpoints:
			# builtin = ('True', 'magenta') if ep.get('isBuiltin', False) else 'False'
			eps.append((ep['hostname'], ep['locationName'], str('isBuiltin' in ep)))
		return eps

	@property
	def _repr_instace_id(self):
		return [((self._instance_id, 'green'),)]

	def headings(self, section):
		headings = getattr(self, '_%s_headings'%section, None)
		if headings is None:
			raise Exception('No headings found for %s!'%section)
		return tuple((k.upper(), self._heading_color) for k in headings)

	@property
	def repr(self):
		self._pull_config()
		rpr = {
			'instance id': self._repr_instace_id,
		}
		if self._users:
			rpr['users'] = [self.headings('user')] + self._repr_users
		if self._locations:
			rpr['locations'] = [self.headings('location')] + self._repr_locations
		if self._endpoints:
			rpr['endpoints'] = [self.headings('endpoint')] + self._repr_endpoints
		if self._replication_streams:
			rpr['replication streams'] = [ReplicationStream.headings()] + self._repr_replication_streams
		return rpr

	@property
	def users(self):
		for user in self._users:
			yield User(user['userName'], user['accessKey'], self.decrypt(user['secretKey']))

	@property
	def backends(self):
		self._pull_config()
		for backend in self._locations:
			transient = backend.get('isTransient', False)
			if not 'details' in backend or not backend.get('details'):
				yield Backend(
					backend['name'], BACKEND_TYPES.get(backend['locationType'], 'UNKNOWN'),
					'----', '----', '----', '----', transient
				)
				continue
			details = backend['details']
			if backend['locationType'] == 'location-file-v1' or backend['locationType'] == 'location-scality-sproxyd-v1':
				yield Backend(
					backend['name'], BACKEND_TYPES.get(backend['locationType'], 'UNKNOWN'),
					'----', '----', details.get('endpoint', 'None'), '----', transient
				)
			else:
				yield Backend(
					backend['name'], BACKEND_TYPES.get(backend['locationType'], 'UNKNOWN'),
					details['accessKey'], self.decrypt(details['secretKey']),
					details.get('endpoint', 'None'), details['bucketName'], transient
				)
