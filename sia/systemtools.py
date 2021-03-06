# Tools for accessing system and machine functionality.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from flask import current_app, json
from glob import glob
import json
import os
import re
import time
import subprocess
import socket

from connecttools import (post_to_adminservice)

MACHINE_AUTH_FILE = "/etc/minebox/machine_auth.json"
BOX_SETTINGS_JSON_PATH="/etc/minebox/minebox_settings.json"
DMIDECODE = "/usr/sbin/dmidecode"
HDPARM = "/usr/sbin/hdparm"
HOSTNAME_TO_CONNECT = "minebox.io"
DF = "/usr/bin/df"
OLD_LOGFILES_MASK = "/var/log/*-*"
YUM = "/usr/bin/yum"
OWN_PACKAGES_LIST = ["minebox*", "MineBD"]
SWAPON = "/usr/sbin/swapon"
SWAPOFF = "/usr/sbin/swapoff"
SED = "/usr/bin/sed"
FSTAB = "/etc/fstab"

def register_machine():
    machine_info = get_machine_info()

    # Second parameter is False because we do not want/need to use a token here.
    admindata, admin_status_code = post_to_adminservice("registerMachine", False,
      {
        "uuid": machine_info["system_uuid"],
        "serialNumber": machine_info["system_serial"],
        "model": machine_info["system_sku"],
        "peripherals": {"disks": machine_info["disks"]},
      }
    )
    if admin_status_code >= 400:
        return False, ("ERROR: admin error %s: %s" %
                       (admin_status_code, admindata["message"]))
    return True, ""

def submit_ip_notification():
    machine_info = get_machine_info()
    ipaddress = get_local_ipaddress()
    if not ipaddress:
        return False, ("ERROR: No IP address found.")

    admindata, admin_status_code = post_to_adminservice("ipNotification", False,
        {"uuid": machine_info["system_uuid"],
         "localIp": ipaddress})
    if admin_status_code >= 400:
        return False, ("ERROR: admin error %s: %s" %
                       (admin_status_code, admindata["message"]))

    return True, ""

def submit_machine_auth():
    machine_info = get_machine_info()
    ipaddress = get_local_ipaddress()
    if not ipaddress:
        return False, ("ERROR: No IP address found.")

    # Second parameter is True because token is required with this one.
    admindata, admin_status_code = post_to_adminservice("authMachine", True,
        {"uuid": machine_info["system_uuid"]})
    if admin_status_code >= 400:
        return False, ("ERROR: admin error %s: %s" %
                       (admin_status_code, admindata["message"]))

    with open(MACHINE_AUTH_FILE, 'w') as outfile:
        json.dump(machine_info, outfile)
    return True, ""

def get_machine_info():
    machine_info = {
      "system_uuid": subprocess
                     .check_output([DMIDECODE, "-s", "system-uuid"])
                     .strip(),
      "system_serial": subprocess
                       .check_output([DMIDECODE, "-s", "system-serial-number"])
                       .strip(),
      "system_sku": None,
    }
    outlines = subprocess.check_output([DMIDECODE, "-t", "system"]).splitlines()
    for line in outlines:
        matches = re.match(r"^\s+SKU Number:\s+(.+)$", line)
        if matches:
            machine_info["system_sku"] = matches.group(1).strip()  # 1st parenthesis expression
    machine_info["disks"] = []
    for devfile in glob("/dev/disk/by-id/ata-*"):
        if re.search(r'\-part\d+$', devfile):
            continue
        try:
          outlines = subprocess.check_output([HDPARM, "-i", devfile]).splitlines()
          for line in outlines:
              matches = re.match(
                r"^\s*Model=([^,]+), FwRev=([^,]+), SerialNo=(.+)$",
                line)
              if matches:
                  machine_info["disks"].append({
                    "model": matches.group(1).strip(),
                    "firmware_rev": matches.group(2).strip(),
                    "serial_number": matches.group(3).strip(),
                  })
        except subprocess.CalledProcessError:
            # We just ignore if hdparm is unsuccessful.
            pass

    return machine_info

def get_local_ipaddress():
    # By opening up a socket to the outside and look at our side, we get our
    # IP address on the local network.
    # If we'd just do socket.gethostbyname(socket.gethostname()) we'd get
    # 127.0.0.1, which isn't too helpful.
    # Note that we need a working Internet connection for this function
    # to work correctly - but we want to submit this to the auth service,
    # which is out there anyhow.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((HOSTNAME_TO_CONNECT, 80))
        ipaddress = sock.getsockname()[0]
        sock.close()
    except: # On *ANY* exception, we error out.
        return False
    return ipaddress

def get_box_settings():
    settings = {}
    try:
        if os.path.isfile(BOX_SETTINGS_JSON_PATH):
            with open(BOX_SETTINGS_JSON_PATH) as json_file:
                settings = json.load(json_file)
    except:
        # If anything fails here, we'll just deliver the defaults set below.
        current_app.logger.warn("Settings file could not be read: %s"
                                % BOX_SETTINGS_JSON_PATH)
    # Set default values.
    if not "sia_upload_limit_kbps" in settings:
        settings["sia_upload_limit_kbps"] = 0
    if not "display_currency" in settings:
        settings["display_currency"] = "USD"
    if not "last_maintenance" in settings:
        settings["last_maintenance"] = 0
    return settings

def write_box_settings(settings):
    try:
        with open(BOX_SETTINGS_JSON_PATH, 'w') as outfile:
            json.dump(settings, outfile)
    except:
        return False, ("Error writing settings to file: %s"
                       % BOX_SETTINGS_JSON_PATH)
    return True, ""

def system_maintenance():
    # See if it's time to run some system maintenance and do so if required.
    settings = get_box_settings()
    timenow = int(time.time())
    if settings["last_maintenance"] < timenow - 24 * 3600:
        current_app.logger.info("Run system maintenance.")
        # Store the fact that we're running a maintenance.
        settings["last_maintenance"] = timenow
        success, errmsg = write_box_settings(settings)
        if not success:
            return False, errmsg
        # *** Logfile disk congestion ***
        # Check if old logs are taking up a large part of the root filesystem
        # and clean them if necessary.
        # Right now, delete them if they take up more space than is free.
        root_space = get_mountpoint_size("/")
        if "free" in root_space:
            if root_space["free"] < get_filemask_size(OLD_LOGFILES_MASK):
                current_app.logger.info("Root free space too low, cleaning up logs.")
                for filepath in glob(OLD_LOGFILES_MASK):
                    os.remove()
        # *** Kernels filling up /boot ***
        # One-off for Kernel 4.8.7, which was obsoleted by the time most
        # Minebox devices shipped. If a different kernel than that is running,
        # remove this old one to make /boot not overflow.
        retcode = subprocess.call([YUM, "info", "kernel-ml-4.8.7"])
        if retcode == 0 and not os.uname()[2].startswith("4.8.7-"):
            current_app.logger.info("Kernel 4.8.7 is installed but not running, removing it.")
            retcode = subprocess.call([YUM, "remove", "-y", "kernel-ml-4.8.7"])
            if retcode != 0:
                current_app.logger.warn("Removing old kernel failed, return code: %s" % retcode)
        # *** Swap wearing down USB flash ***
        # Find if swap on USB stick is activated and deactivate it.
        swapdevs = []
        usbswapuuids = []
        outlines = subprocess.check_output([SWAPON, "--show=NAME"]).splitlines()
        for line in outlines:
            matches = re.match(r"^/dev/(.+)$", line)
            if matches:
                swapdevs.append(matches.group(1).strip())
        # Now see if any of those swap devices are on USB and get the UUIDs.
        for dev in swapdevs:
            for usbdevpath in glob("/dev/disk/by-id/usb-*"):
                if os.readlink(usbdevpath).endswith(dev):
                    for uuidpath in glob("/dev/disk/by-uuid/*"):
                        if os.readlink(uuidpath).endswith(dev):
                            usbswapuuids.append(os.path.basename(uuidpath))
        # Now actually disable those UUIDs directly and in fstab.
        for swapuuid in usbswapuuids:
            current_app.logger.info("Disable USB swap with UUID %s.", swapuuid)
            subprocess.call([SED, "-i", "-e", "s/^UUID=%s/#&/" % swapuuid, FSTAB])
            subprocess.call([SWAPOFF, "-U", swapuuid])
        # *** Updates on own packages ***
        # Check for updates to our own packages and install those if needed.
        # Note: this call may end up restarting our own process!
        # Therefore, this function shouldn't do anything important after this.
        current_app.logger.info("Trying to update our own packages.")
        retcode = subprocess.call([YUM, "upgrade", "-y"] + OWN_PACKAGES_LIST)
        if retcode != 0:
            return False, ("Updating failed, return code: %s" % retcode)
    return True, ""

def get_mountpoint_size(mountpath):
    # Get total, used and free sizes of a mount point.
    #df --block-size=1 --output=target,fstype,size,used,avail /
    spaceinfo = {}
    # See https://btrfs.wiki.kernel.org/index.php/FAQ#Understanding_free_space.2C_using_the_new_tools
    dfcommand = [DF, "--block-size=1", "--output=target,fstype,size,used,avail", mountpath]
    outlines = subprocess.check_output(dfcommand).splitlines()
    for line in outlines:
        matches = re.match(r"^\/[^\s]*\s+([^\s]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)$", line)
        if matches:
            spaceinfo["filesystem"] = matches.group(1)
            spaceinfo["total"] = int(matches.group(2))
            spaceinfo["used"] = int(matches.group(3))
            spaceinfo["free"] = int(matches.group(4))

    return spaceinfo

def get_filemask_size(filemask):
    # Get the summmary size of all files in the given "glob" mask.
    sumsize = 0
    for filepath in glob(filemask):
        fileinfo = os.stat(filepath)
        sumsize += fileinfo.st_size
    return sumsize
