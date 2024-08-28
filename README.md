# Fileserver import script

This is a script for OMERO intended for in-place importing data from fileservers mounted on the server.

## Installation

The script can be added to the official scripts via this command from the repo root folder:

```bash
omero script upload --official ./omero/import_scripts/Fileserver_Import.py
```

or via the OMERO.web interface

### Configuration

A configuration file (fileserver_config.json) must be placed on the server and be readable for the omero-server linux user. 
This file describes where the different fileservers are mounted, and where should the image data be located in each.

Example configurations from `fileserver_config.json` of this repo:
```json
{
  "mountpoints": {
    "facility_fs_name": "/mnt/inplace_import/facility_fs_name_4237/",
    "group_fs_name": "/mnt/inplace_import/group_fs_name_6789/",
  },
  "fs_directory_rules": {
    "facility_fs_name": "OMERO_in-place_import/<GROUP>/<USER>",
    "group_fs_name": "<USER>"
  }
}
```

and example entries in `/etc/fstab` where omero-server user on the VM has uid=988, and fs_user_credentials are username and password for a read-only account on both fileservers.
```
//facility.uni.de/fs_name  /mnt/inplace_import/facility_fs_name_4237  cifs  credentials=/etc/fs_user_credentials,uid=988,gid=988  0 0
//group.uni.de/fs_name     /mnt/inplace_import/group_fs_name_6789     cifs  credentials=/etc/fs_user_credentials,uid=988,gid=988  0 0
```

In this example there are two fileservers. `facility_fs_name` is mounted here `/mnt/inplace_import/facility_fs_name_4237/`. The number at the end of the mount point can help prevent unwanted users from guessing the path of fileservers they don't have access to (the available fileservers are listed by the script with their common names).

`fs_directory_rules` gives the path (prefixed by the mountpoint) where users are allowed to put data for their in-place import. `<GROUP>` is a placeholder for a group folder (access rules defined for the fileserver). `<USER>` is a placeholder for a folder named after a user (the name of that folder must end with "_omename"). 

Thus in the case of `facility_fs_name`, user Max Mustermann with username mamu100 must place his data in his group folder like this: `OMERO_in-place_import/Lab_ABCD/Max_Mustermann_mamu100/any/folder/he/likes`

In the case of `group_fs_name`, user Max Mustermann with username mamu100 must place his data in his group folder like this: `Max_Mustermann_mamu100/any/folder/he/likes`

Because the server is allowed to access all images on every fileserver, there is an additional mechanism to prevent users from importing data not belonging to them (data they normally have no access to on the fileserver, but which they can guess the path of). 
This is done with a file `allowed_users.txt` containing a list of omero users. Currently, this file must be placed in the root of the folder `<USER>` as defined in `fs_directory_rules`:
* `OMERO_in-place_import/Lab_ABCD/allowed_users.txt` in the case of `facility_fs_name`
* `allowed_users.txt` in the case of `group_fs_name`

This works only if the file `allowed_users.txt` is write restricted from the fileserver permissions managing also the access to the files (if unwanted users have access to the image files, they can copy them anyway). 

