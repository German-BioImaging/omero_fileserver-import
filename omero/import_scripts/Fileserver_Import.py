#!/usr/bin/env python
# -*- coding: utf-8 -*-

import omero
import omero.config
import omero.cli
import omero.scripts as scripts
from omero.gateway import BlitzGateway
from omero.rtypes import rstring, robject, rlong
from omero.model import ProjectI, DatasetI, ProjectDatasetLinkI

import os
import json
from os.path import join
import re
from glob import glob
from collections import defaultdict

VERSION = "1.0.0"
AUTHORS = ["Tom Boissonnet"]
INSTITUTIONS = ["Heinrich Heine Universitat"]
CONTACT = "tom.boissonnet@hhu.de"

# ------------------ CONFIGURATIONS ------------------ #
ADMINISTRATOR = "tom.boissonnet@hhu.de"
CONFIG_FILE_NAME = "fileserver_config.json"
ALLOWED_USERS_FN = "allowed_users.txt"

# Name of the parameters, to rename them in a single place
PARAM_FILESERVER = "Fileserver name"
PARAM_CLIENT_FOLDER = "Main folder path"
PARAM_FILENAMES = "File names"
PARAM_SKIP_MINMAX = "Skip Min/Max"
PARAM_SKIP_THUMBNAIL = "Skip Thumbnail"
PARAM_DRY_RUN = "Dry run"

# Error with >1 if %thread% not in ManagedRepo path
# see https://omero.readthedocs.io/en/stable/sysadmins/config.html#omero-fs-repo-path
PARAM_PARALLEL_FILESET = 1
PARAM_PARALLEL_UPLOAD = 8

with open(join("/opt/omero/server", CONFIG_FILE_NAME)) as f:
    fileserver_config = json.load(f)

MOUNTPOINTS_D = fileserver_config["mountpoints"]
FS_DIR_DICT = fileserver_config["fs_directory_rules"]

# Match Max_Mustermann_mamu100 and captures mamu100 (OMERO user_name)
USER_RE = "(?:[^/_]+_)*(?P<user_name>[^/_]+)"
GROUP_RE = "(?P<group_name>[^/]+)"
if "group_re" in fileserver_config:
    GROUP_RE = fileserver_config["group_re"]
if "user_re" in fileserver_config:
    USER_RE = fileserver_config["user_re"]

for k, mnt in MOUNTPOINTS_D.items():
    if mnt[-1] != "/":
        MOUNTPOINTS_D[k] = f"{mnt}/"

# TODO file attachment?
# PARAM_ATTACH = "Attach non image files"
# PARAM_DEST_ATTACH = "Attach to object type"
# PARAM_ATTACH_FILTER = "Filter attachment by extension"
# PARAM_SKIP_EXISTING = "Skip already imported files"

# TODO Sudo user

DEFAULT_ARGS = [
    "import", "--transfer=ln_s",
    f"--parallel-upload={PARAM_PARALLEL_UPLOAD}",
    f"--parallel-fileset={PARAM_PARALLEL_FILESET}"
]


def map_path_to_server(params):
    client_path = params[PARAM_CLIENT_FOLDER]
    fileserver_name = params[PARAM_FILESERVER]
    win_re = re.compile("^[A-Z]:/")  # Regex to find windows path
    server_path = None
    # TODO path logging
    client_path = client_path.replace("\\", "/")
    assert_no_backward_ref(client_path)
    if client_path[-1] == "/":  # Remove last foldersep
        client_path = client_path[:-1]

    if win_re.match(client_path) is not None:  # It's a windows path
        server_path = win_re.sub(MOUNTPOINTS_D[fileserver_name], client_path)
    else:  # Searching for longest path with linux or mac
        assert client_path[0] == "/", ("The provided folder path was not" +
                                       " understood. Please provide the " +
                                       "complete path of the folder to import")
        for i in range(1, len(client_path.split("/"))):
            concat_path = join(MOUNTPOINTS_D[fileserver_name],
                               "/".join(client_path.split("/")[i:]))
            if os.path.isdir(concat_path):
                server_path = concat_path
                break
    return server_path


def path_match_omero_usergroup(conn, server_path, fs_name):
    omero_grp = conn.getGroupFromContext()

    root_path_re = join(MOUNTPOINTS_D[fs_name],
                        FS_DIR_DICT[fs_name])

    root_path_re = root_path_re.replace("<GROUP>", GROUP_RE)
    root_path_re = root_path_re.replace("<USER>", USER_RE)

    match = re.match(root_path_re, server_path)
    assert match, (
        f"The provided path is not accepted for '{fs_name}'." +
        f"It must match this path: '{FS_DIR_DICT[fs_name]}'")

    allowed_usr_fpath = join(server_path, ALLOWED_USERS_FN)
    if GROUP_RE in root_path_re:
        idx_path = root_path_re.find(GROUP_RE)
        grp_name = match.group('group_name')
        allowed_usr_fpath = join(server_path[:idx_path],
                                 grp_name,
                                 ALLOWED_USERS_FN)

    short_path = allowed_usr_fpath[len(MOUNTPOINTS_D[fs_name]):]
    assert os.path.isfile(allowed_usr_fpath), (
        f"'{fs_name}:{short_path}' does not exist. Please create it and " +
        "list the users allowed to import data from this folder/fileserver " +
        "(one username per line)."
    )

    with open(allowed_usr_fpath, "r") as f:
        allowed_l = [line.strip() for line in f.readlines()]
        assert conn.getUser()._omeName in allowed_l, (
            f"The user '{conn.getUser()._omeName}' is not in the allowed " +
            f"user list of '{fs_name}:{short_path}'. Please add the user to " +
            "the list first (one username per line)."
        )

    if USER_RE in root_path_re:
        # A user is allowed to import data of his group
        grp_summary = omero_grp.groupSummary()
        user_str_l = [u._omeName for u in grp_summary[0] + grp_summary[1]]
        # Current user for suggestion
        user_str = "_".join([conn.getUser()._firstName.split(" ")[0],
                             conn.getUser()._lastName,
                             conn.getUser()._omeName])

        assert match.group('user_name') in user_str_l, (
            f"The data in '{fs_name}' must be placed in a user folder " +
            f" following this template: '{FS_DIR_DICT[fs_name]}'. Use" +
            f" '{user_str}' for your own folder."
        )


def list_files_to_import(server_path, params):
    to_import_l = []
    for image_path in params[PARAM_FILENAMES]:
        assert_no_backward_ref(image_path)
        for full_path in glob(join(server_path, image_path)):
            if (os.path.isfile(full_path)):
                to_import_l.append(full_path)
    to_import_l = list(set(to_import_l))  # Ensure no path is duplicated
    to_import_l.sort()
    return to_import_l


def get_target_container(conn, folder_name, params):
    container_id = params["IDs"][0]
    container = params["Data_Type"]

    grp_name = conn.getGroupFromContext().name
    error_not_exist = (
        f"{folder_name} - The target container {container}:" +
        f"{container_id} does not exist in {grp_name}"
    )

    if container == "Project":
        if params[PARAM_DRY_RUN]:
            return "Dataset:-1", None
        # Generate a dataset in the project
        dataset_obj = DatasetI()
        dataset_obj.name = rstring(folder_name)
        dataset_obj = conn.getUpdateService().saveAndReturnObject(dataset_obj)

        link = ProjectDatasetLinkI()  # Link project to dataset
        link.setParent(ProjectI(container_id, loaded=False))
        link.setChild(dataset_obj)
        conn.getUpdateService().saveObject(link)
        dset_id = dataset_obj.getId().getValue()
        target_obj = conn.getObject("Dataset", dset_id)
        return f"Dataset:{dset_id}", target_obj
    else:
        # Check that container exists
        target_obj = conn.getObject(container, container_id)
        assert target_obj is not None, error_not_exist
        return f"{container}:{target_obj.getId()}", target_obj


def build_cli_import_args(target_arg, file_path_l, params):
    skip_minmax = params[PARAM_SKIP_MINMAX]
    skip_thumbnail = params[PARAM_SKIP_THUMBNAIL]

    args = DEFAULT_ARGS.copy()
    args.append("--skip=checksum")
    if skip_minmax:
        args.append("--skip=minmax")
    if skip_thumbnail:
        args.append("--skip=thumbnails")

    # Can be no target -> defaut to Orphan or Plate for plate kind of images
    if target_arg != "":
        args.extend(["-T", target_arg])

    for file_path in file_path_l:
        args.append(f'{file_path}')

    return args


def cli_do_import(args, client):
    cli_ = omero.cli.CLI()
    cli_.loadplugins()
    cli_.set_client(client.createClient(secure=False))
    cli_.invoke(args)
    cli_.get_client().closeSession()


def assert_no_backward_ref(curr_path):
    message = f"Forbidden backward reference: {curr_path}"
    assert "/../" not in curr_path, message
    assert not curr_path.endswith("/.."), message
    assert not curr_path.startswith("../"), message


def inplace_import(conn, client, server_path, params):
    """
    Pre-upload verifications:
      - the folder path
      - the user is allowed to import the files
      - the JSON is valid
      - the values in JSON are valid (Project ID can be written to, ...)
      - Folder naming convention: YYYY-MM-DD_...

    Parameters:
      - conn: connection object
      - server_path: the path to the folder on the server side
      - fileserver_name: name of the fileserver (script parameter)
    """
    fileserver_name = params[PARAM_FILESERVER]
    folder_name = os.path.split(server_path)[1]

    path_match_omero_usergroup(conn, server_path, fileserver_name)

    assert os.path.isdir(server_path), (
        f"'{folder_name}' - The folder to import does not exist.\nIs " +
        f"'{fileserver_name}' the correct fileserver?"
    )

    file_path_l = list_files_to_import(server_path, params)
    assert len(file_path_l) > 0, 'No image to import were found'

    print("\n\n################### Files to import ###################\n")

    target_dset_d = defaultdict(list)
    if params["Data_Type"] == "Project":
        # Find what datasets to generate
        parent_server_path = os.path.split(server_path[:-1])[0]
        for file_path in file_path_l:
            tmp_path = os.path.split(file_path)[0]
            dir_l = []
            while tmp_path != parent_server_path:
                tmp_path, dir_ = os.path.split(tmp_path)
                dir_l.insert(0, dir_)
            # Only keep first directory if no other (from server_path)
            dir_l = dir_l if len(dir_l) == 1 else dir_l[1:]
            target_dset_d["__".join(dir_l)].append(file_path)
        for k, v in target_dset_d.items():
            print(f"New dataset {k}:")
            for file_path in v:
                print(f"\t- {file_path[file_path.find(folder_name):]}")
    else:
        target_dset_d[folder_name] = file_path_l
        for file_path in file_path_l:
            print(f"{params['Data_Type']}:{params['IDs'][0]}")
            print(f"\t- {file_path[file_path.find(folder_name):]}")

    target_obj, msg = None, "Dry run complete"
    print("\n\n################### Import commands ###################\n")
    for folder_name, file_path_l in target_dset_d.items():
        target_arg, target_obj = get_target_container(
            conn, folder_name, params)
        args = build_cli_import_args(target_arg, file_path_l, params)
        print("omero " + " ".join(args) + "\n")
        if not params[PARAM_DRY_RUN]:
            cli_do_import(args, client)
            msg = "Import done"

    return target_obj, msg  # summary


def run_script():
    """
    The main entry point of the script, as called by the client via the
    scripting service, passing the required parameters.
    """
    src_fileservers = list(MOUNTPOINTS_D.keys())
    data_types = [rstring('Project'), rstring('Dataset'), rstring('Screen')]
    client = scripts.client(
        'Fileserver_Import.py',
        f"""
        Remote in-place import of images from fileservers.\n
        CAUTION: After importing a file in this way, any change in the
        file or parent folder names will break the link between OMERO and
        the fileserver.\n
        Contact your administrator {ADMINISTRATOR} for any related question.
        """,
        scripts.String(
            "Data_Type", optional=False, grouping="1",
            description="The Project to attach a dataset of imported images " +
            "to. Or a dataset for imported images. For high-content" +
            " screening data, use a screen.", values=data_types,
            default="Project"),
        scripts.List(
            "IDs", optional=False, grouping="1.1",
            description="Project, Screen or Dataset ID.").ofType(rlong(0)),
        scripts.String(
            PARAM_FILESERVER, optional=False, grouping="2",
            description="The fileserver to import from.",
            values=src_fileservers),
        scripts.String(
            PARAM_CLIENT_FOLDER, optional=False, grouping="2.2",
            description="The path to the main folder containing " +
            "subfolders and files to import."),
        scripts.List(
            PARAM_FILENAMES, optional=False, grouping="2.3",
            description="The list of file names to import. Use * to " +
            "match multiple names/folders. E.g '*.tiff' or '2024-07-*/*.tiff'"
            ).ofType(rstring("")),
        scripts.Bool(
            PARAM_SKIP_MINMAX, grouping="2.4", default=True,
            description="Skip detection of min and max pixel " +
            "values used for the image display settings."),
        scripts.Bool(
            PARAM_SKIP_THUMBNAIL, grouping="2.5", default=False,
            description="Skip generation of thumbnail at import " +
            "(generated later when browsing images)."),
        scripts.Bool(
            PARAM_DRY_RUN, grouping="2.6", default=False,
            description="Dry run shows the output of what would be " +
            "performed without importing images."),
        namespaces=[omero.constants.namespaces.NSDYNAMIC],
        version=VERSION,
        authors=AUTHORS,
        institutions=INSTITUTIONS,
        contact=CONTACT,
    )

    try:
        params = client.getInputs(unwrap=True)
        conn = BlitzGateway(client_obj=client)
        conn.c.enableKeepAlive(60)

        print("\n\n################## Script parameters ##################\n")
        print(f"Target container: {params['Data_Type']}:{params['IDs'][0]}")
        print(f"{PARAM_FILESERVER}: {params[PARAM_FILESERVER]}")
        print(f"{PARAM_CLIENT_FOLDER}: {params[PARAM_CLIENT_FOLDER]}")
        print(f"{PARAM_FILENAMES}: {params[PARAM_FILENAMES]}")
        print(f"{PARAM_SKIP_MINMAX}: {params[PARAM_SKIP_MINMAX]}")
        print(f"{PARAM_SKIP_THUMBNAIL}: {params[PARAM_SKIP_THUMBNAIL]}")
        print(f"{PARAM_DRY_RUN}: {params[PARAM_DRY_RUN]}")

        for i, filename in enumerate(params[PARAM_FILENAMES]):
            params[PARAM_FILENAMES][i] = filename.replace("\\", "/")

        assert len(params["IDs"]) == 1, (
            "Only one ID can be provided.")

        server_path = map_path_to_server(params)
        robj, message = inplace_import(
            conn, client, server_path, params)

        client.setOutput("Message", rstring(message))
        if robj is not None:
            client.setOutput("Result", robject(robj._obj))

    except AssertionError as err:
        client.setOutput("ERROR", rstring(err))
        raise AssertionError(str(err))
    except ValueError as err:
        client.setOutput("ERROR", rstring(err))
        raise err
    except Exception as err:
        client.setOutput("ERROR", rstring("An error occured"))
        raise err
    finally:
        client.closeSession()


if __name__ == "__main__":
    run_script()
