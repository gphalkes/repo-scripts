#!/usr/bin/python -B

# Copyright (C) 2018 G.P. Halkes
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3, as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


# Script to create a distribution tarball. Works for both mercurial and git
# repositories. Requires a dist_config.py file with the following configuration
# variables and functions:

# package [string, optional]: name of the package/tarball.
# srcdirs [list, optional]: list of directories to be considered to hold sources.
#    These will have 'make clean' and 'make all' run on them, and their contents
#    will be added to the output tarball. Also, .l/.ll/.g/.gg files will have
#    their generated objects from the corresponding .objects dir included.
# excludesrc [string, optional]: regular expression for files to exclude from
#    the expansion of srcdirs when searching for sources.
# distfiles [list, optional]: list of files to be considered non-source
#    distribution files. Defaults to anything that starts with dist/ and man/
# excludedist [string, optional]: regular expression of files to exclude from
#    automatic inclusion as distribution files, if distfiles is unset.
# prebuild [function, optional]: function called before running make on source
#    directories.
# build [function, optional]: function called after running make on source
#    directories.
# auxfiles [list, optional]: other files and directories that must be copied
#    into the tarball.
# auxsources [list, optional]: other files (will be glob expanded) to include
#    as sources.
# extrabuilddirs [list, optional]: extra directories (other than the srcdirs)
#    which need 'make clean' and 'make all' run on them.

import sys
import os
import imp
import subprocess
import datetime
import tempfile
import shutil
import re
import glob

script_dir = None
config = None

package = None
srcdirs = None
distfiles = None
changelog = None

revision = None
version = None
date = None

tmpdir = None
topdir = None
sources = None
extrabuilddirs = None

def is_hg():
	return os.path.isdir(".hg")

def is_git():
	return os.path.isdir(".git")

def load_config():
	global config

	try:
		res = imp.find_module('dist_config', ['.'])
	except ImportError as e:
		sys.exit("could not open dist_config.py: " + str(e))
	config = imp.load_module('dist_config', *res)

def get_files():
	try:
		if is_hg():
			files = subprocess.check_output(['hg', 'manifest']).split('\n')
		elif is_git():
			files = subprocess.check_output(['git', 'ls-files']).split('\n')
	except subprocess.CalledProcessError as e:
		sys.exit("error retrieving list of files: " + str(e))
	return filter_empty(files)

def filter_empty(seq):
	return [ item for item in seq if item ]

def unique_list(seq):
	set = {}
	map(set.__setitem__, seq, [])
	return set.keys()

def exclude_by_regex(seq, regex):
	exclude = re.compile(regex)
	return [ item for item in seq if not exclude.search(item) ]

def include_by_regex(seq, regex):
	include = re.compile(regex)
	return [ item for item in seq if include.search(item) ]

def regex_replace(seq, regex, replacement):
	regex = re.compile(regex)
	return [ regex.sub(replacement, item) for item in seq ]

def get_dirs():
	return unique_list(filter_empty([ os.path.dirname(name) for name in get_files() ]))

def get_srcdirs():
	return [ name for name in unique_list([ name.split(os.sep, 2)[0] for name in get_dirs() ]) \
		if name.startswith('src') ]

def get_distfiles(excludedist):
	files = [ name for name in get_files() if name.startswith('dist' + os.sep) or name.startswith('man' + os.sep) ]
	if excludedist:
		files = exclude_by_regex(files, excludedist)

	# Replace TXT man pages by generated versions
	files = (regex_replace(include_by_regex(files, '^man/.*\.\d\.txt$'), 'man/(.*)\.txt$', 'man/output/\\1') +
		exclude_by_regex(files, '^man/.*(?:(?<=/)Makefile|\.\d\.txt)$'))
	return files

def in_dirs(name, dirs):
	for d in dirs:
		if name.startswith(d) and name[len(d)] == '/':
			return True
	return False

def glob_expand(seq, basedir=None):
	result = []
	for item in seq:
		if basedir:
			extra_items = map(lambda x: x[len(basedir) + 1:], glob.glob(os.path.join(basedir, item)))
		else:
			extra_items = glob.glob(item)

		if extra_items:
			result.extend(extra_items)
		else:
			result.append(item)
	return result

def sources_to_objects(seq, regex, replacement):
	objects = include_by_regex(seq, regex)
	objects = regex_replace(objects, '/\.objects/', '/')
	objects = regex_replace(objects, regex, replacement)
	return objects

def main():
	global package, srcdirs, distfiles, extrabuilddirs, script_dir, changelog, config, sources

	this_module = sys.modules[__name__]

	if not is_hg() and not is_git():
		sys.exit("Could not find git or mercurial repository")

	script_dir = os.path.abspath(os.path.dirname(sys.argv[0]))

	load_config()

	package = config.package if 'package' in dir(config) else os.path.basename(os.getcwd())
	srcdirs = config.srcdirs if 'srcdirs' in dir(config) else get_srcdirs()
	excludedist = config.excludedist if 'excludedist' in dir(config) else None
	distfiles = glob_expand(config.distfiles) if 'distfiles' in dir(config) else get_distfiles(excludedist)
	extrabuilddirs = config.extrabuilddirs if 'extrabuilddirs' in dir(config) else []

	if os.path.isfile('man/Makefile'):
		extrabuilddirs.append('man')

	for f in distfiles:
		if os.path.basename(f) == 'Changelog':
			changelog = f
			break
	if not changelog:
		sys.exit('no Changelog file found; aborting')

	get_version()
	check_mod()

	nobuild = os.getenv('NOBUILD') and os.getenv('TESTVERSION')
	if not nobuild:
		if 'prebuild' in dir(config):
			config.prebuild(this_module)
		build_all()
		if 'build' in dir(config):
			config.build(this_module)

	get_sources()
	if 'auxsources' in dir(config):
		sources += glob_expand(config.auxsources)

	make_tmpdir()
	copy_sources()
	copy_dist_files()

	if 'auxfiles' in dir(config):
		copy_files(glob_expand(config.auxfiles))

	if 'copy' in dir(config):
		config.copy(this_module)

	if 'get_replacements' in dir(config):
		_replace(config.get_replacements(this_module))

	if 'create_configure' not in dir(config) or config.create_configure:
		create_configure()

	if 'finalize' in dir(config):
		config.finalize(this_module)

	create_tar()
	shutil.rmtree(tmpdir)

def get_version():
	global revision, version, date, changelog
	try:
		if is_hg():
			revision = subprocess.check_output("hg identify | egrep -o '^[a-f0-9]+'", shell=True).strip()
			version = subprocess.check_output("hg parents -r '%s' | egrep '^tag:' | egrep -o '\<version-.*' || true" % revision, shell=True).strip()
			date_str = subprocess.check_output("hg log -r '%s' | egrep '^date:' | sed -r 's/date: *//'" % revision, shell=True).strip()
			date = datetime.datetime.strptime(date_str[:-6], "%a %b %d %H:%M:%S %Y")
		elif is_git():
			revision = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()
			version = subprocess.check_output("git show-ref | egrep '^%s' | egrep ' refs/tags/version-' | egrep -o '\<version-.*' || true" % revision, shell=True).strip()
			date_str = subprocess.check_output("git show -s --format=%%ci '%s'" % revision, shell=True).strip()
			date = datetime.datetime.strptime(date_str[:-6], "%Y-%m-%d %H:%M:%S")
	except subprocess.CalledProcessError as e:
		sys.exit("error getting revision/version/date: " + str(e))

	if os.getenv("TESTVERSION"):
		version = os.getenv("TESTVERSION")
		if subprocess.call(['grep', '-q', '^Version %s:' % version, changelog]) != 0:
			sys.exit('Changelog has no information for version %s; aborting' % version)
	else:
		if version.startswith("version-"):
			version = version[8:]
			if subprocess.call(['grep', '-q', '^Version %s:' % version, changelog]) != 0:
				sys.exit('Changelog has no information for version %s; aborting' % version)
		else:
			version = None

		if not version:
			sys.stderr.write("could not get a version number; using date of commit\n")
			version = date.strftime("%Y%m%d")

def get_version_bin(default='1'):
	global version
	if version.find('.') > 0:
		major, minor = version.split('.', 1)
		if minor.find('.') > 0:
			minor, patch = minor.split('.', 1)
		else:
			patch = 0
		version_bin = '0x%02x%02x%02x' % (int(major), int(minor), int(patch))
	else:
		version_bin = default
	return version_bin

def check_mod():
	global revision

	if os.getenv('TESTVERSION'):
		return

	if is_hg():
		modified = subprocess.call("[ `hg diff -r '%s' | wc -l` -eq 0 ]" % revision, shell=True)
	elif is_git():
		modified = subprocess.call(["git", "diff", revision, "--quiet"])

	if modified != 0:
		sys.exit('it seems you have a modified revision; aboring (use TESTVERSION=xxx for testing)')


def build_all():
	global config, srcdirs, extrabuilddirs
	alldirs = srcdirs + extrabuilddirs
	for d in alldirs:
		if subprocess.call(['make', '-C', d, 'clean']) != 0:
			sys.exit("failed to clean directory " + d)
	for d in alldirs:
		if subprocess.call(['make', '-C', d, 'all']) != 0:
			sys.exit("failed to build directory " + d)

def get_sources():
	global config, srcdirs, sources

	files = [ name for name in get_files() if in_dirs(name, srcdirs) ]

	if 'excludesrc' in dir(config):
		files = exclude_by_regex(files, config.excludesrc)

	sources = []

	for f in unique_list(filter_empty(files)):
		sources.append(f)

		root, ext = os.path.splitext(f)
		if ext == '.l' or ext == '.ll' or ext == '.g' or ext == '.gg':
			srcdir, basename = os.path.split(root)
			gen_root = os.path.join(srcdir, '.objects', basename)
			for n in [ root + '.c', root + '.cc', root + '.h', gen_root + '.c', gen_root + '.cc', gen_root + '.h' ]:
				if os.path.isfile(n):
					sources.append(n)


def make_tmpdir():
	global tmpdir, topdir

	tmpdir = tempfile.mkdtemp(prefix='mkdist')
	topdir = tmpdir + os.sep + package + '-' + version
	os.mkdir(topdir)


def copy_sources():
	global sources, topdir

	for s in sources:
		target = os.path.join(topdir, s.replace('.objects/', '', 1))
		if not os.path.isdir(os.path.dirname(target)):
			os.makedirs(os.path.dirname(target))
		shutil.copy(s, target)


def copy_dist_files():
	global distfiles, topdir

	for d in distfiles:
		target = os.path.join(topdir, d.replace('dist/', '', 1).replace('man/output', 'man', 1))
		if not os.path.isdir(os.path.dirname(target)):
			os.mkdir(os.path.dirname(target))
		shutil.copy(d, target)


def copy_files(files):
	global topdir

	for f in files:
		target = os.path.join(topdir, f)
		if os.path.isdir(f):
			shutil.copytree(f, target)
		else:
			if not os.path.isdir(os.path.dirname(target)):
				os.mkdir(os.path.dirname(target))
			shutil.copy(f, target)

def create_configure():
	global script_dir, topdir

	subprocess.call(script_dir + "/../config/merge_config", cwd=topdir)
	shutil.copy(script_dir + "/../config/install.sh", topdir)

def _replace_in_file(file, replacement):
	# Create an anonymous function which takes a string as input and applies the
	# required replacement. The reason to do it this way is that we have two
	# types of replacement, and this simplifies the looop below.
	if 'regex' in replacement and replacement['regex']:
		regex = re.compile(replacement['tag'])
		replace = lambda x: regex.sub(replacement['replacement'], x)
	else:
		replace = lambda x: x.replace(replacement['tag'], replacement['replacement'])

	with open(file, "rb") as source:
		with tempfile.NamedTemporaryFile(prefix='.', dir=os.path.dirname(file), delete=False) as dest:
			for line in source:
				dest.write(replace(line))
			dest.close()
			shutil.copymode(file, dest.name)
			os.rename(dest.name, file)

def _all_files():
	global topdir
	result = []
	# os.walk yields a list of three-tuples, where each tuple consists of the
	# directory name, the list of directories, and the list of files. What we
	# need is the list of files, but with each file preprended with the
	# directory name. This prepending is what the inner map does, while the
	# outer applies it to every tuple.
	for list in map(lambda x:map(lambda y:os.path.join(x[0], y), x[2]), os.walk(topdir)):
		result.extend(list)
	return result

def _replace(replacements):
	global topdir
	for replacement in replacements:
		for file in glob_expand(replacement['files'], topdir) if 'files' in replacement else _all_files():
			_replace_in_file(os.path.join(topdir, file), replacement)

def create_tar():
	global topdir, tmpdir, changelog, package, version

	if os.getenv('TESTVERSION'):
		f = open(topdir + "/00README.testversion", "w")
		f.write("This is a test version, which is NOT intended for distribution.\n")
		f.close()
		test_tag='-test'
	else:
		test_tag=''

	changelog_target = os.path.join(topdir, changelog.replace('dist/', '', 1))
	changelog_target_file = open(changelog_target + '.tmp', 'w')
	subprocess.call(['expand', '-t4', changelog_target], stdout=changelog_target_file)
	changelog_target_file.close()
	os.rename(changelog_target + '.tmp', changelog_target)

	tarname = "%s-%s%s" % (package, version, test_tag)
	shutil.make_archive(tarname, 'bztar', tmpdir, package + '-' + version)

	print "Created " + tarname + ".tar.bz2"



if __name__ == "__main__":
	main()
