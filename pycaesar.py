#!/usr/bin/env python3
#PyCaesar - Copyright 2018 Lynnear Software
#PyCaesar is a rewrite/port of Caesar, which was written in PHP.
#It upscales a directory full of images using ECT and Gifsicle,
#using a database to avoid upscaling the same file multiple times.

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import sqlite3, argparse, os, threading, time, math, signal, subprocess, sys, re
bar = " ▏▎▍▌▌▊▊▉█" #TODO: non-unicode version, like ..__--++''
#spin = "◰◳◲◱"
spin = "▟▙▛▜" #non-unicode: /-\|
# spin = "dqpb"

def progressBar(upto, total, frame):
	if upto == 0:
		percent = 0
	else: 
		percent = math.floor((upto / total) * 100)

	#build the progress bar
	printed = percent
	out = ''
	while printed > 10:
		out += bar[-1] #add a full block for every 10 percents
		printed -= 10
	if printed > 0: out += bar[printed-1] #then add the final block
	out = out.ljust(10) #make sure it's 10 chars wide

	spinner = spin[math.floor(frame) % len(spin)]
	if percent == 100:
		spinner = "✓"

	return "{2}% {0} {1} ({3}/{4})".format(out, spinner, str(percent).rjust(3), upto, total)

parser = argparse.ArgumentParser(description = "Optimise compatible images in a directory. Supports JPEG and PNG files through Efficient Compression Tool, and GIF files through Gifsicle.")
parser.add_argument('directories', metavar='dir', nargs='+', default=[os.getcwd()], help='The directories to scan.')
#KEEP THESE IN ALPHABETICAL ORDER!
parser.add_argument('-C', '--no-cleanup', dest='cleanup', action="store_false", help="Leave nonexistent files in the database")
parser.add_argument('-d','--db',dest='db',nargs='?', default=os.path.dirname(__file__) + '/pycaesar.db', help='Specify database location (defaults to pycaesar.db)')
parser.add_argument('-f','--follow-symlinks', dest='followSymlinks', action='store_true', help="Recurse into symlinks, unless --no-recurse is also passed")
parser.add_argument('-i','--ignore-db',dest='ignoreDatabase', action="store_true", help="Optimise all images, even ones that have already been optimised")
parser.add_argument('-K','--no-keep',dest='keep', action='store_false', help="Don't keep file's original modification time")
parser.add_argument('-q','--quiet',dest='quiet', action='store_true', help='Disable non-error output')
parser.add_argument('-R', '--no-recurse', dest='recurse', action="store_false", help="Don't recurse into subdirectories")
parser.add_argument('-t','--threads', dest='threads',nargs=1, default="auto",help="Number of threads to use. Defaults to one for each CPU thread. More threads may be used to speed up processing with ECT.")

args=parser.parse_args()
logfile = os.path.dirname(__file__) + "/error.log"
if os.path.isfile(logfile):
	os.remove(logfile)

if not args.quiet: print("...", end="\r")
fileTypes = ['jpeg','jpg','gif','png']
try:
	subprocess.check_call(['ect', '-h'], stdout=subprocess.PIPE)
except CalledProcessError:
	print("Could not find Efficient Compression Tool. Make sure the ect binary is on your PATH. Support for JPEG and PNG files has been disabled.")
	fileTypes.remove('jpeg')
	fileTypes.remove('jpg')
	fileTypes.remove('png')

try:
	subprocess.check_call(['gifsicle', '-h'],stdout=subprocess.PIPE)
except CalledProcessError:
	print("Could not find Gifsicle. Make sure the gifsicle binary is on your PATH. Support for GIF files has been disabled.")
	fileTypes.remove('gif')

if len(fileTypes) == 0:
	print("No supported filetypes available!")
	sys.exit(1)

if args.threads == "auto":
	#amount of threads not specified, we have to check ourselves
	import multiprocessing
	args.threads = multiprocessing.cpu_count()

args.db = os.path.abspath(args.db)
if not os.path.exists(args.db):
	if not args.quiet: print("Database '" + args.db + "' does not exist, creating...")

db = sqlite3.connect(args.db)
db.text_factory=str
c = db.cursor()
c.execute("CREATE TABLE IF NOT EXISTS `images` (filePath VARCHAR NOT NULL UNIQUE)")
c.execute("CREATE TABLE IF NOT EXISTS `sessionImages` (filePath VARCHAR NOT NULL UNIQUE)")
db.commit()

#first, build a list of valid files
oldlen = 0
for directory in args.directories:
	directory = os.path.abspath(directory) + os.sep
	if not args.quiet: print("Scanning {0}...".format(directory).rjust(oldlen), end = "\r")
	oldlen = len("Scanning {}...".format(directory))
	if args.recurse:
		for root, dirs, files in os.walk(directory, followlinks = args.followSymlinks):
			for file in files: #for every file
				for fileType in fileTypes: #compare it agains every file extension
					if file.lower().endswith("." + fileType): #if it's a match
						c.execute("INSERT INTO `sessionImages` (filePath) VALUES (?)", (root + os.sep + file,)) #add it to the todo list
						break
	else:
		#non-recursive version
		for file in os.scandir(directory):
			for fileType in fileTypes: #compare it agains every file extension
				if file.lower().endswith("." + fileType): #if it's a match
					c.execute("INSERT INTO `sessionImages` (filePath) VALUES (?)", (root + os.sep + file,)) #add it to the todo list
					break

if args.ignoreDatabase:
 fileList = c.execute("SELECT filePath FROM `sessionImages`").fetchall()
else:
	fileList = c.execute("SELECT filePath FROM `sessionImages` EXCEPT SELECT filePath FROM `images`").fetchall()	

fileCount = len(fileList)

class processThread(threading.Thread):
	def __init__(self, image):
		threading.Thread.__init__(self)
		self.image = image
		self.done = False

	def run(self):
		ext = re.match(r".*\.(.{3,4})$", self.image).group(1).lower()
		if ext == "jpeg" or ext == "jpg" or ext == "png":
			cmd = ['ect', '-9', '-keep' if args.keep else "-9", '--mt-deflate={0}'.format(args.threads), '--strict', '-progressive',self.image]
		elif ext == "gif":
			#disable warnings so we don't get that silly 'using local colormaps' rubbish
			cmd = ['gifsicle', '-w', '-O3', self.image, '-o', self.image]
		else:
			self.success = False
			self.done = True
			print("ERROR: unsupported filetype: '{0}' ({1})\n".format(ext, os.path.basename(self.image))) #should never happen ;)

		# cmd = ["ls"]

		self.success = True
		if not self.done:
			try:
				result = subprocess.check_output(cmd, stderr=subprocess.PIPE).decode("utf-8")
			except Exception as e:
				self.success = False
				with open(logfile, 'a') as lf:
					lf.write(str(e.stderr))
					lf.write("\n")
		self.done = True

def finish():
	c.execute("DROP TABLE `sessionImages`") #get rid of temp session storage
	db.commit()
	db.execute("VACUUM") #compact db
	db.commit()
	db.close()

#at this point, ctrl+c'ing could mean that there are some uncommitted database changes to be made.
def handleCtrlC(signal, frame):
	print("\nPREMATURE EVACUATION - Saving chunks")
	finish()
	sys.exit(1)

signal.signal(signal.SIGINT, handleCtrlC)
deleteMe = []

if not args.quiet: print("Processing... ".format(directory).rjust(oldlen))

if args.cleanup: 
	if not args.quiet: print("Cleanup...", end="\r")
	images = c.execute("SELECT filePath FROM `images`").fetchall()
	for image in images:
		if not os.path.exists(image[0]):
			deleteMe.append(image[0])

	while len(deleteMe) > 0:
		upto = 0
		sql = 'DELETE FROM `images` WHERE filePath IN ('
		for i in range(len(deleteMe)):
			sql = sql + "?, "
			upto += 1
			if upto > 500:
				break #only process 500 at a time to avoid the "too many SQL variables" error
		sql = sql[:-2] + ")"
		#this ends up building DELETE etc WHERE filePath IN (?, ?, ?, ?...)
		#then we can fill it with unpacked deleteMe.
		#this is much faster deleting one by one
		tempDeleteMe = deleteMe[0:upto]

		c.execute(sql, (*tempDeleteMe,))

		for i in range(len(tempDeleteMe)):
			deleteMe.pop(0)

if fileCount == 0:
	imageCount = c.execute("SELECT COUNT(*) FROM `sessionImages`").fetchone()[0]
	if imageCount == 0:
		print("No compatible files found.")
	else:
		print("Already processed all {0} compatible files. Did you mean to use --ignore-db?".format(imageCount))
	finish()
	exit()

done = 0
inactiveThreads = []
activeThreads = []
frame = 0
failed = 0
old = ""
for i in range(fileCount):
	inactiveThreads.append(processThread(fileList[i][0]))

while done < fileCount:
	while len(activeThreads) < args.threads:
		try:
			activeThreads.append(inactiveThreads.pop())
			activeThreads[len(activeThreads) - 1].start()
		except:
			break

	for thread in activeThreads:
		if thread.done:
			activeThreads.remove(thread)
			done += 1
			if thread.success:
				c.execute("INSERT OR IGNORE INTO `images` VALUES (?)", (thread.image,))
			else:
				failed = failed + 1

	if not args.quiet:
		frame = (frame + 0.125) % 4
		new = progressBar(done, fileCount, frame)
		if old != new: #only print when necessary!
			print(new, end="\r")
			old = new
	time.sleep(0.050)

if not args.quiet:
	if failed > 0:
		print("\nOptimised {0} files, including {1} failures. Check error.log for more info.".format(done, failed))
	else:
		print("\nOptimised {0} files.".format(done))
elif failed > 0:
	print("There were {} errors. Check error.log for more info.".format(failed))
finish()