__version__ = '0.1.1'


from .cli import zcheck
def entry():
	from . import log

	log.setupLogging('zcheck',
		level = log.CRITICAL,
		blacklist = [
			'azure.storage.common.storageclient'
		])
	zcheck()
