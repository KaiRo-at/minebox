# Various tools around backups, mostly to get info about them.

from flask import current_app
from os.path import isfile, isdir, join
import os
from glob import glob
from zipfile import ZipFile
import re
from connecttools import getFromSia


DATADIR_MASK="/mnt/lower*/data"
METADATA_BASE="/mnt/lower1/mineboxmeta"
UPLOADER_CMD="/usr/lib/minebox/uploader-bg.sh"


def get_status(backupname):
    backupfiles, is_finished = get_files(backupname)
    if backupfiles is None:
        return {"message": "No backup found with that name."}, 404

    status_code = 200
    if len(backupfiles) < 1:
        # Before uploads are scheduled, we find a backup but no files.
        files = -1
        total_size = -1
        rel_size = -1
        progress = 0
        rel_progress = 0
        status = "PENDING"
        metadata = "PENDING"
        fully_available = False
    else:
        backuplist = get_list()
        currentidx = backuplist.index(backupname)
        if currentidx > 0:
            prev_backupfiles, prev_finished = get_files(backuplist[currentidx - 1])
        else:
            prev_backupfiles = None
        sia_filedata, sia_status_code = getFromSia('renter/files')
        if sia_status_code == 200:
            # create a dict generated from the JSON response.
            files = 0
            total_size = 0
            total_pct_size = 0
            rel_size = 0
            rel_pct_size = 0
            fully_available = True
            sia_map = dict((d["siapath"], index) for (index, d) in enumerate(sia_filedata["files"]))
            for fname in backupfiles:
                if fname in sia_map:
                    files += 1
                    fdata = sia_filedata["files"][sia_map[fname]]
                    # For now, report all files.
                    # We may want to only report files not included in previous backups.
                    total_size += fdata["filesize"]
                    total_pct_size += fdata["filesize"] * fdata["uploadprogress"] / 100
                    if prev_backupfiles is None or not fdata["siapath"] in prev_backupfiles:
                        rel_size += fdata["filesize"]
                        rel_pct_size += fdata["filesize"] * fdata["uploadprogress"] / 100
                    if not fdata["available"]:
                        fully_available = False
                elif re.match(r'.*\.dat$', fname):
                    files += 1
                    fully_available = False
                    current_app.logger.warn('File %s not found on Sia!', fname)
                else:
                    current_app.logger.debug('File "%s" not on Sia and not matching watched names.', fname)
            # If size is 0, we report 100% progress.
            # This is really needed for relative as otherwise a backup with no
            # difference to the previous would never go to 100%.
            progress = total_pct_size / total_size * 100 if total_size else 100
            rel_progress = rel_pct_size / rel_size * 100 if rel_size else 100
            # We don't upload metadata atm, so always flag it as pending.
            metadata = "PENDING"
            if is_finished and fully_available:
                status = "FINISHED"
            elif is_finished and not fully_available:
                status = "DAMAGED"
            elif total_pct_size:
                status = "UPLOADING"
            else:
                status = "PENDING"
        else:
            current_app.logger.error("Error %s getting Sia files: %s", status_code, str(sia_filedata))
            status_code = 503
            files = -1
            total_size = -1
            rel_size = -1
            progress = 0
            rel_progress = 0
            status = "ERROR"
            metadata = "ERROR"
            fully_available = False

    return {
      "name": backupname,
      "time_snapshot": backupname,
      "status": status,
      "metadata": metadata,
      "numFiles": files,
      "size": total_size,
      "progress": progress,
      "relative_size": rel_size,
      "relative_progress": rel_progress,
    }, status_code


def get_list():
    backuplist = [re.sub(r'.*backup\.(\d+)(\.zip)?', r'\1', f)
                  for f in glob(join(METADATA_BASE, "backup.*"))
                    if (isfile(f) and f.endswith(".zip")) or
                       isdir(f) ]
    # Sort most-recent-first.
    backuplist.sort(reverse=True)
    return backuplist


def get_latest():
    backuplist = get_list()
    return backuplist[0] if backuplist else None


def get_backups_to_restart():
    # Look at existing backups and find out which ones are unfinished and
    # should be restarted.
    restartlist = []
    backuplist = get_list()
    prevsnap = None
    prevsnap_exists = None
    for snapname in backuplist:
        backupfiles, is_finished = get_files(snapname)
        snapshot_exists = False
        if glob(os.path.join(DATADIR_MASK, 'snapshots', snapname)):
            snapshot_exists = True
        # Always add the most recent backup if it's unfinished and the
        # lower-level snapshot exists.
        if snapshot_exists and not prevsnap and not is_finished:
            restartlist.append(snapname)
        # Break on the first finished backup, add previous one (oldest
        # unfinished) unless it's already in the list.
        if is_finished or snapname == backuplist[-1]:
            if prevsnap_exists and prevsnap and not prevsnap in restartlist:
                 restartlist.append(prevsnap)
            break
        # If we arrive at the last item of the list, add if it's unfinished.
        if snapname == backuplist[-1]:
            if snapshot_exists and not snapname in restartlist:
                 restartlist.append(snapname)
            break
        # Remember snapname for next cycle.
        prevsnap = snapname
        prevsnap_exists = snapshot_exists
    return restartlist


def get_files(backupname):
    backupfiles = None
    is_finished = None
    zipname = join(METADATA_BASE, "backup.%s.zip" % backupname)
    dirname = join(METADATA_BASE, "backup.%s" % backupname)
    if isfile(zipname):
        backupfiles = []
        is_finished = True
        with ZipFile(zipname, 'r') as backupzip:
            backupfiles = [re.sub(r'.*backup\.\d+\/(.*)\.sia$', r'\1', f)
                           for f in backupzip.namelist()
                             if f.endswith(".sia")]
    elif isdir(dirname):
        backupfiles = []
        is_finished = False
        flist = join(dirname, "files")
        if isfile(flist):
            with open(flist) as f:
                backupfiles = [line.rstrip('\n') for line in f]
    return backupfiles, is_finished
