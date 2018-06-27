import click
from collections import namedtuple
from .helm import Status as HelmStatus
from .orbit import OverlayConfig
from .print import print_sections, print_header, print_error
from . import __version__ as zcheck_version
from .check import check_backends, check_buckets
from .bucket import UserBuckets
from .util import NO_PROBLEMS, MONGO_BASE_HOST
import sys
from pprint import pprint
from . import error
from .log import Log

_log = Log('cli')

ZCConf = namedtuple('ZCConf', ('mongo', 'helm_release', 'output', 'verbose'))
NO_HELM_REL = ['help', 'version']
REQ_HELM_REL = ['k8s']

# Root
@click.group()
@click.option('--mongo', default = None, type = str, help = 'Override the default mongo host:port')
@click.option('--helm-release', '-r', type = str, help ='The release name helm was installed under')
@click.option('--output', '-o', type = click.File(mode='w'))
@click.option('--verbose', '-v', is_flag=True, help="Enable verbose output, WARNING: This will print your access keys to the terminal or file!")
@click.pass_context
def zcheck(ctx, **kwargs):
	subcmd = ctx.invoked_subcommand
	if kwargs.get('helm_release') is None:
		if subcmd in REQ_HELM_REL or (kwargs.get('mongo') is None and subcmd not in NO_HELM_REL):
			raise click.UsageError('Missing option "--helm-release" / "-r"', ctx=ctx)
	if 'output' not in kwargs:
		kwargs['output'] = None
	if kwargs.get('mongo') and not ':' in kwargs.get('mongo'):
		kwargs['mongo'] = '%s:27017'%kwargs.get('mongo')
	ctx.obj = ZCConf(**kwargs)

#help
@zcheck.command(help = 'Access usage help for commands')
@click.argument('command', required = False)
@click.pass_context
def help(ctx, command):
	if command:
		cmd = zcheck.get_command(ctx, command)
		if cmd:
			click.echo(cmd.get_help(ctx))
		else:
			click.secho('%s is not a valid command!'%command, fg='red')
	else:
		click.echo(zcheck.get_help(ctx))

# version
@zcheck.command(help='Print version')
def version():
	click.echo('zcheck', nl = False)
	click.secho('\tv%s'%zcheck_version, fg='green')

# k8s
@zcheck.command(help = 'Checks related to the kubernetes cluster')
@click.pass_obj
# @click.option('--check-services', '-c', is_flag = True, help = 'Attempt to connect to defined services and report their status')
def k8s(conf):
	try:
		hs = HelmStatus(conf.helm_release, verbose = conf.verbose)
	except error.RequiredBinaryException as e:
		_log.error(str(e))
		_log.exception(e)
		print_error(str(e))
		sys.exit(1)

	hs.pull_status()
	print_sections(hs.repr, width=100, file=conf.output)

# orbit
@zcheck.command(help = "Check relating to Orbit's configuration")
@click.pass_obj
def orbit(conf):
	try:
		oc = OverlayConfig(helm_release = conf.helm_release, mongo = conf.mongo, verbose=conf.verbose)
	except error.ZCheckBaseException as e:
		_log.error(str(e))
		_log.exception(e)
		print_error(str(e))
		sys.exit(1)
	print_sections(oc.repr, file=conf.output)

# backend
@zcheck.command(help = 'Check backend buckets for existence and configuration')
@click.option('--deep', '-d', is_flag = True, help = 'Enable deep checking. Check every zenko bucket for its respective backend bucket')
@click.pass_context
def backends(ctx, deep):
	conf = ctx.obj
	try:
		oc = OverlayConfig(helm_release = conf.helm_release, mongo = conf.mongo, verbose=conf.verbose)
	except error.ZCheckBaseException as e:
		_log.error(str(e))
		_log.exception(e)
		print_error(str(e))
		sys.exit(1)
	checked = list(check_backends(oc, conf.verbose))
	if len(checked) == 1:
		checked = NO_PROBLEMS
	print_sections({'backends': checked})
	if deep:
		ctx.invoke(buckets)

# Check zenko buckets
@zcheck.command(help = 'Check every Zenko bucket for its respective backing bucket')
@click.pass_obj
def buckets(conf):
	try:
		oc = OverlayConfig(helm_release = conf.helm_release, mongo = conf.mongo, verbose=conf.verbose)
	except error.ZCheckBaseException as e:
		_log.error(str(e))
		_log.exception(e)
		print_error(str(e))
		sys.exit(1)
	ub = UserBuckets(helm_release=conf.helm_release, mongo = conf.mongo)
	checked = list(check_buckets(oc, ub.buckets, conf.verbose))
	if len(checked) == 1:
		checked = NO_PROBLEMS
	print_sections({'buckets': checked})

# Do EVERYTHING!!
@zcheck.command(help = 'Run all checks and tests')
@click.pass_context
def checkup(ctx):
	if not ctx.obj.helm_release:
		raise click.UsageError('Missing option "--helm-release" / "-r"', ctx=ctx)
	ctx.invoke(orbit)
	print_header('')
	ctx.invoke(k8s)
	ctx.invoke(backends)
	ctx.invoke(buckets)
