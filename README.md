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
    "fs_common_name": "/mnt/inplace_import/fs_common_name_4237/",
    "groupfs_common_name": "/mnt/inplace_import/groupfs_common_name_6789/",
  },
  "fs_directory_rules": {
    "fs_common_name": "OMERO_in-place_import/<GROUP>/<USER>",
    "groupfs_common_name": "<USER>"
  }
}
```

In this example there are two fileservers. `fs_common_name` is mounted here `/mnt/inplace_import/fs_common_name_4237/`. The number at the end of the mount point can help prevent unwanted users from guessing the path of fileservers they don't have access to (the available fileservers are listed by the script with their common names).

For the `fs_directory_rules`, this gives the path (starting at the mountpoint) where users are allowed to put data for the in-place import. `<GROUP>` is a placeholder for a group folder (access rules defined for the fileserver). `<USER>` is a placeholder for a folder named after a user (the name of that folder must end with "_omename"). 

Thus in the case of `fs_common_name`, user Max Mustermann with username mamu100 must place his data in his group folder like this: `OMERO_in-place_import/Lab_ABCD/Max_Mustermann_mamu100/any/folder/he/likes`

In the case of `groupfs_common_name`, user Max Mustermann with username mamu100 must place his data in his group folder like this: `Max_Mustermann_mamu100/any/folder/he/likes`

Additionally, images on the fileserver should not be imported for users without access to the data (if the path is guessed, OMERO with access to all images could import the data for any user). For this, a file `allowed_users.txt` in the root of each group lists the users allowed to perform the import of data located in subfolders:
* `OMERO_in-place_import/Lab_ABCD/allowed_users.txt` in the case of `fs_common_name`
* `allowed_users.txt` in the case of `groupfs_common_name`


