# Various tools around backups, mostly to get info about them.

from flask import current_app, json
from os import path, stat, mkdir, makedirs, remove
from glob import glob
from zipfile import ZipFile
import re
import time
import subprocess

from connecttools import getDemoURL, getFromSia, postToSia, putToMineBD
from backupinfo import *

SIA_DIR="/mnt/lower1/sia"

def check_prerequisites():
    # Step 0: Check if prerequisites are met to make backups.
    consdata, cons_status_code = getFromSia('consensus')
    if cons_status_code == 200:
        if not consdata["synced"]:
          return False, "ERROR: sia seems not to be synced. Please again when the consensus is synced."
    else:
        return False, "ERROR: sia daemon needs to be running for any uploads."
    return True, ""

def snapshot_upper(status):
    # TODO: See MIN-104.
    return

def create_lower_snapshots(status):
    # Step 1: Create snapshot.
    snapname = status["snapname"]
    status["message"] = "Creating backup files"
    backupname = status["backupname"]

    metadir = path.join(METADATA_BASE, backupname)
    if not path.isdir(metadir):
      makedirs(metadir)

    current_app.logger.info('Trimming file system to actually remove deleted data from the virtual disk.')
    subprocess.call(['/usr/sbin/fstrim', '/mnt/storage'])
    current_app.logger.info('Flushing file system caches to make sure user data has been written.')
    subprocess.call(['/usr/bin/sync'])
    current_app.logger.info('Creating lower-level data snapshot(s) with name: %s' % snapname)
    # Potentially, we should ensure that those data/ directories are actually subvolumes.
    for subvol in glob(DATADIR_MASK):
        current_app.logger.info('subvol: %s' % subvol)
        if not path.isdir(path.join(subvol, 'snapshots')):
            makedirs(path.join(subvol, 'snapshots'))
        subprocess.call(['/usr/sbin/btrfs', 'subvolume', 'snapshot', '-r', subvol, path.join(subvol, 'snapshots', snapname)])
    current_app.logger.info(
      'Telling MineBD to pause (for 1.5s) to make sure no modified blocks exist with the same timestamp as in our snapshots.'
    )
    mbdata, mb_status_code = putToMineBD('pause', '', [{'Content-Type': 'application/json'}, {'Accept': 'text/plain'}])
    return

def initiate_uploads(status):
    current_app.logger.info('Starting uploads.')
    status["message"] = "Starting uploads"
    snapname = status["snapname"]
    backupname = status["backupname"]

    metadir = path.join(METADATA_BASE, backupname)
    bfinfo_path = path.join(metadir, 'fileinfo')
    if path.isfile(bfinfo_path):
        remove(bfinfo_path)
    sia_filedata, sia_status_code = getFromSia('renter/files')
    if sia_status_code == 200:
        siafiles = sia_filedata["files"]
    else:
        return False, "ERROR: sia daemon needs to be running for any uploads."

    # We have a randomly named subdirectory containing the .dat files.
    # As the random string is based on the wallet seed, we can be pretty sure there
    # is only one and we can ignore the risk of catching multiple directories with
    # the * wildcard.
    status["backupfileinfo"] = []
    status["uploadfiles"] = []
    status["backupsize"] = 0
    status["uploadsize"] = 0
    for filepath in glob(path.join(DATADIR_MASK, 'snapshots', snapname, '*', '*.dat')):
        fileinfo = stat(filepath)
        # Only use files of non-zero size.
        if fileinfo.st_size:
            status["backupsize"] += fileinfo.st_size
            filename = path.basename(filepath)
            (froot, fext) = path.splitext(filename)
            sia_fname = '%s.%s%s' % (froot, int(fileinfo.st_mtime), fext)
            if any(sf['siapath'] == sia_fname and sf['available'] for sf in siafiles):
                current_app.logger.info('%s is part of the set but already uploaded.' % sia_fname)
            elif any(sf['siapath'] == sia_fname for sf in siafiles):
                current_app.logger.info('%s is part of the set but the upload is already in progress.' % sia_fname)
            else:
                status["uploadsize"] += fileinfo.st_size
                status["uploadfiles"].append(sia_fname)
                current_app.logger.info('%s has to be uploaded, starting that.' % sia_fname)
                siadata, sia_status_code = postToSia('renter/upload/%s' % sia_fname,
                                                     {'source': filepath})
                if sia_status_code != 204:
                    return False, "ERROR: sia upload error %s: %s" % (sia_status_code, siadata['message'])
            status["backupfileinfo"].append({"siapath": sia_fname, "size": fileinfo.st_size})

    with open(bfinfo_path, 'w') as outfile:
        json.dump(status["backupfileinfo"], outfile)
    return True, ""

def wait_for_uploads(status):
    status["message"] = "Waiting for uploads to complete"
    uploaded_size = 0
    fully_available = False
    while not fully_available and uploaded_size < status["uploadsize"]:
        sia_filedata, sia_status_code = getFromSia('renter/files')
        if sia_status_code == 200:
            uploaded_size = 0
            fully_available = True
            sia_map = dict((d["siapath"], index) for (index, d) in enumerate(sia_filedata["files"]))
            for bfile in status["backupfileinfo"]:
                if bfile["siapath"] in sia_map:
                    fdata = sia_filedata["files"][sia_map[bfile["siapath"]]]
                    if fdata["siapath"] in status["uploadfiles"]:
                        uploaded_size += fdata["filesize"] * fdata["uploadprogress"] / 100
                    if not fdata["available"]:
                        fully_available = False
                elif re.match(r'.*\.dat$', bfile["siapath"]):
                    fully_available = False
                    current_app.logger.warn('File "%s" not found on Sia!', bfile["siapath"])
                else:
                    current_app.logger.debug('File "%s" not on Sia and not matching watched names.', bfile["siapath"])
            status["uploadprogress"] = uploaded_size / status["uploadsize"] * 100
            if not fully_available and uploaded_size < status["uploadsize"]:
                # Sleep 5 minutes.
                time.sleep(5 * 60)
        else:
            return False, "ERROR: Sia daemon needs to be running for any uploads."
    return True, ""

def save_metadata(status):
    status["message"] = "Saving metadata"
    return

def remove_lower_snapshots(status):
    snapname = status["snapname"]
    status["message"] = "Cleaning up backup data"
    current_app.logger.info('Removing lower-level data snapshot(s) with name: %s' % snapname)
    for snap in glob(path.join(DATADIR_MASK, 'snapshots', snapname)):
        subprocess.call(['/usr/sbin/btrfs', 'subvolume', 'delete', snap])
    return

def remove_old_backups(status):
    status["message"] = "Cleaning up old backups"
    return

