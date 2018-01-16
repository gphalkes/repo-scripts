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

import datetime
import glob
import os
import re
import subprocess
import sys
import tempfile
import time

COPYRIGHT_YEAR_RE = re.compile(r'([0-9]{4}(?:[,-][0-9]{4})*)')

#TODO: include more than just the latest update

def get_object_year(file=None):
	until = time.time()
	base_cmd = ['git', 'log', '-n1', '-i', '--invert-grep', '--grep=copyright', '--pretty=format:%at']

	cmd = list(base_cmd)
	cmd.append('--until=@%d' % until)
	if file:
		cmd.append(file)
	timestamp = int(subprocess.check_output(cmd))

	date = datetime.date.fromtimestamp(timestamp)
	return date.year

def update_years(years, new_year):
	last_year = int(years[-4:])
	if last_year == new_year:
		return None
	elif last_year == new_year - 1:
		if len(years) == 4 or years[-5] == ',':
			return years + '-' + str(new_year)
		else:
			return years[:-4] + str(new_year)
	else:
		return years + ',' + str(new_year)

def update_copyright(file, repo_year):
	handle = open(file, 'r')
	output_handle = None
	line = handle.next()

	if 'Copyright' in line:
		years_match = COPYRIGHT_YEAR_RE.search(line)
		if years_match:
			updated_years = update_years(years_match.group(0), get_object_year(file))
			if updated_years:
				print 'Updating header for %s from %s to %s' % (file, years_match.group(0), updated_years)

				line = line[:years_match.start(0)] + updated_years + line[years_match.end(0):]
				output_handle = tempfile.NamedTemporaryFile(dir=os.path.dirname(file), delete=False)
				output_handle.write(line)

	for line in handle:
		if '\\(co' in line or '@copyright' in line:
			years_match = COPYRIGHT_YEAR_RE.search(line)
			if years_match:
				updated_years = update_years(years_match.group(0), repo_year)
				if updated_years:
					if not output_handle:
						# If we were not updating this file yet, open a new temp file for output and start over
						output_handle = tempfile.NamedTemporaryFile(dir=os.path.dirname(file), delete=False)
						handle.seek(0, 0)
						continue
					print 'Updating global copyright in %s from %s to %s' % (file, years_match.group(0), updated_years)
					line = line[:years_match.start(0)] + updated_years + line[years_match.end(0):]

		if output_handle:
			output_handle.write(line)

	if output_handle:
		output_handle.close()
		os.rename(output_handle.name, file)

def main():
	files = subprocess.check_output(['git', 'ls-files'])
	repo_year = get_object_year()

	if os.path.exists('.copyright_noupdate'):
		noupdate = open('.copyright_noupdate')
		noupdate_list = []
		for line in noupdate:
			line = line.strip()
			if line.startswith('#'):
				continue
			noupdate_list.extend(glob.glob(line))
	else:
		noupdate_list = []

	for file in files.split('\n'):
		if not os.path.isfile(file) or file in noupdate_list:
			continue
		update_copyright(file, repo_year)

if __name__ == "__main__":
	main()
