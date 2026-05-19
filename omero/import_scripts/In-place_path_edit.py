#!/usr/bin/env python
# -*- coding: utf-8 -*-

import omero
import omero.scripts as scripts
from omero.gateway import BlitzGateway
from omero.sys import Parameters
from omero.rtypes import rlong, rlist, rstring, unwrap

import os, tempfile
import hashlib
from os.path import join
import re
import json

VERSION = "1.0.0"
AUTHORS = ["Tom Boissonnet"]
INSTITUTIONS = ["Heinrich Heine Universitat"]
CONTACT = "tom.boissonnet@hhu.de"

# ------------------ CONFIGURATIONS ------------------ #
ADMINISTRATOR = ""
CONFIG_FILE_PATH = "/opt/omero/server/fileserver_config.json"
ALLOWED_USERS_FN = "allowed_users.txt"
MANAGED_DIR = "/OMERO/ManagedRepository"

# Name of the parameters, to rename them in a single place
PARAM_SRC_REPLACE = "Path segment to replace"
PARAM_DST_REPLACE = "Path segment replacement"
PARAM_DRY_RUN = "Dry run"
PARAM_CHECKSUM = "Validate checksum"

checksum_d = {
    "MD5-128": hashlib.md5,
    "SHA1-160": hashlib.sha1,
    # other algorithms can be added here if needed
    # unfortunately not implemented in hashlib
    # Adler-32 CRC-32 Murmur3-32 Murmur3-128 File-Size-64
}

with open(CONFIG_FILE_PATH) as f:
    fileserver_config = json.load(f)

MOUNTPOINTS_D = fileserver_config["mountpoints"]
FS_DIR_DICT = fileserver_config["fs_directory_rules"]

# Match Max_Mustermann_mamu100 and captures mamu100 (OMERO user_name)
USER_RE = "(?P<fullname>(?:[^/_]+_)*(?P<user_name>[^/_]+))"
GROUP_RE = "(?P<group_name>[^/]+)"
if "group_re" in fileserver_config:
    GROUP_RE = fileserver_config["group_re"]
if "user_re" in fileserver_config:
    USER_RE = fileserver_config["user_re"]


def path_match_omero_usergroup(conn, server_path):
    fs_name = None
    for k, v in MOUNTPOINTS_D.items():
        if server_path.startswith(v):
            fs_name = k
            break
    assert fs_name is not None, f"The new path does not match a configured fileserver. Contact {ADMINISTRATOR} to configure a new fileserver."

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
    if USER_RE in root_path_re:
        # Allowed user is considered to be in the parent folder of users
        allowed_usr_fpath = join(server_path[:match.start("fullname")],
                                 ALLOWED_USERS_FN)

    short_path = allowed_usr_fpath[len(MOUNTPOINTS_D[fs_name]):]
    assert os.path.isfile(allowed_usr_fpath), (
        f"'{fs_name}: {short_path}' does not exist. Please create it and " +
        "list the users allowed to import data from this folder/fileserver " +
        "(one username per line)."
    )

    with open(allowed_usr_fpath, "r") as f:
        allowed_l = [line.strip() for line in f.readlines()]
        assert conn.getUser()._omeName in allowed_l, (
            f"The user '{conn.getUser()._omeName}' is not in the allowed " +
            f"user list of '{fs_name}: {short_path}'. Please add the user to " +
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

    # Return the proofed "base folder".
    # All target files must match this (single target folder allowed at a time)
    return os.path.split(allowed_usr_fpath)[0]


def assert_no_backward_ref(curr_path):
    message = f"Forbidden backward reference: {curr_path}"
    assert "/../" not in curr_path, message
    assert not curr_path.endswith("/.."), message
    assert not curr_path.startswith("../"), message


def symlink(target, link_name):
    '''
    Create a symbolic link named link_name pointing to target.
    If link_name exists then FileExistsError is raised, unless overwrite=True.
    When trying to overwrite a directory, IsADirectoryError is raised.
    Credits Tom Hale https://stackoverflow.com/questions/8299386/modifying-a-symlink-in-python/55742015#55742015
    '''

    # os.replace() may fail if files are on different filesystems
    link_dir = os.path.dirname(link_name)

    # Create link to target with temporary filename
    while True:
        temp_link_name = tempfile.mktemp(dir=link_dir)

        # os.* functions mimic as closely as possible system functions
        # The POSIX symlink() returns EEXIST if link_name already exists
        # https://pubs.opengroup.org/onlinepubs/9699919799/functions/symlink.html
        try:
            os.symlink(target, temp_link_name)
            break
        except FileExistsError:
            pass

    # Replace link_name with temp_link_name
    try:
        # Pre-empt os.replace on a directory with a nicer message
        if not os.path.islink(link_name) and os.path.isdir(link_name):
            raise IsADirectoryError(f"Cannot symlink over existing directory: '{link_name}'")
        os.replace(temp_link_name, link_name)
    except:
        if os.path.islink(temp_link_name):
            os.remove(temp_link_name)
        raise


def inplace_mv(conn, params):
    assert_no_backward_ref(params[PARAM_DST_REPLACE])

    hql_inplace = """
        SELECT fs.id, ann.ns, ann.textValue
        FROM Fileset AS fs
        JOIN fs.annotationLinks AS a_link
        JOIN a_link.child AS ann
        WHERE fs.id IN (
            SELECT distinct(fs.id) from Fileset AS fs
            JOIN fs.images AS img
    """

    hql_fsentry = """
        SELECT fse
        FROM FilesetEntry fse
        JOIN FETCH fse.fileset fs
        JOIN FETCH fse.originalFile ofe
        JOIN FETCH ofe.hasher
        WHERE fs.id = (:fsid)
    """

    hql_fileset = """
        SELECT DISTINCT fs.id
        FROM Fileset fs
        JOIN fs.images img
    """

    if params["Data_Type"] == "Image":
        subquery = """
            WHERE img.id IN (:iids)
        """
    elif params["Data_Type"] == "Plate":
        subquery = """
            JOIN img.wellSamples ws
            JOIN ws.well well
            JOIN well.plate pl
            WHERE pl.id IN (:iids)
        """
    elif params["Data_Type"] == "Dataset":
        subquery = """
            JOIN img.datasetLinks dl
            JOIN dl.parent ds
            WHERE ds.id IN (:iids)
        """

    hql_fileset += subquery
    hql_inplace += subquery + ")"
    id_l = [rlong(i) for i in params["IDs"]]
    img_params = Parameters()
    img_params.map = {"iids": rlist(id_l)}

    service_opts = conn.SERVICE_OPTS.copy()
    qs = conn.getQueryService()
    ups = conn.getUpdateService()

    inplace_res = qs.projection(hql_inplace, img_params, service_opts)
    inplace_filesets = []
    for row in inplace_res:
        fs_id, ns, text_value = row
        if (unwrap(ns) == omero.constants.namespaces.NSFILETRANSFER
            and unwrap(text_value) in ["ome.formats.importer.transfers.SymlinkFileTransfer"]):
            # can support "ome.formats.importer.transfers.HardlinkFileTransfer" but need to implement this in the symlink() function
            inplace_filesets.append(unwrap(fs_id))

    jobs, wrong_permission, not_inplace = [], [], []
    allowed_path = None

    fs_res = qs.projection(hql_fileset, img_params, service_opts)
    for row in fs_res:  # First iterating the fileset, to skip the whole if an error is found
        fileset_id = unwrap(row[0])
        fs_o = conn.getObject("Fileset", fileset_id)
        if not fileset_id in inplace_filesets:
            not_inplace.append(f"Fileset {fileset_id}: The files are not 'in-place imported'.")
            continue
        if not fs_o.canEdit():
            wrong_permission.append(f"Fileset {fileset_id}: User has no permission to edit.")
            continue

        fs_params = Parameters()
        fs_params.map = {"fsid": rlong(fileset_id)}

        # Iterating all fileset entries of the set. Here only checks that move is possible, jobs are run when everything is green
        different_folder, not_found, wrong_sums, multi_occurence, wrong_sizes = [], [], [], [], []
        for fse in qs.findAllByQuery(hql_fsentry, fs_params, service_opts):
            ofe = fse.getOriginalFile()
            ofe_path = ofe.getPath().getValue()
            ofe_name = ofe.getName().getValue()
            ofe_hash = ofe.getHash().getValue()
            ofe_size = ofe.getSize().getValue()
            ofe_hasher = unwrap(ofe.getHasher().getValue())

            client_path = fse.getClientPath().getValue()
            if client_path.count(params[PARAM_SRC_REPLACE]) > 1:
                multi_occurence.append(f"Multiple occurence of {params[PARAM_SRC_REPLACE]} found in {client_path}")
                continue

            new_client_path = client_path.replace(params[PARAM_SRC_REPLACE], params[PARAM_DST_REPLACE])
            if client_path == new_client_path:
                print(f"WARNING: unchanged link for {new_client_path}")
            new_client_path = join("/", new_client_path)

            if allowed_path is None:
                # Validate the target path only once.
                # This throws an assertion error if the validation fails.
                allowed_path = path_match_omero_usergroup(conn, new_client_path)
            if not new_client_path.startswith(allowed_path):  # All moved files must have the same "root"
                different_folder.append(f"{new_client_path} does not match the first detected root {allowed_path}")
                continue

            if not os.path.isfile(new_client_path):
                not_found.append(f"{new_client_path}: File not found\n")
                continue

            new_size = os.path.getsize(new_client_path)
            if new_size != ofe_size:
                wrong_sizes.append(f"{join(ofe_path, ofe_name)}: {ofe_size} bytes\n{new_client_path}: {new_size} bytes\n")
                continue

            if params[PARAM_CHECKSUM]:
                if ofe_hasher not in checksum_d.keys():
                    raise ValueError(f"Unsupported hasher: {ofe_hasher}")
                with open(join(new_client_path), "rb") as f:
                    new_hash = hashlib.file_digest(f, checksum_d[ofe_hasher]).hexdigest()

                if new_hash != ofe_hash:
                    wrong_sums.append(f"{ofe_hasher}\n{join(ofe_path, ofe_name)}: {ofe_hash}\n{new_client_path}: {new_hash}\n")
                    continue

            jobs.append((fse, new_client_path, ofe_path, ofe_name))

        if multi_occurence:
            # Only print something if there's an issue, then fail with the assertion.
            print('\n'.join(multi_occurence))
        if different_folder:
            print('\n'.join(different_folder))
        if wrong_sizes:
            print('\n'.join(wrong_sizes))
        if wrong_sums:
            print('\n'.join(wrong_sums))
        if not_found:
            print('\n'.join(not_found))
        assert len(different_folder) == 0, f"All target files are not in the same group folder. See details in script output."
        assert len(not_found) == 0, f"Target Files not found. See details in script output."
        assert len(wrong_sizes) == 0, f"File size mismatch found. See details in script output."
        assert len(wrong_sums) == 0, f"Checksum mismatch found. See details in script output."

        # If we reach this point, all assertions went ok, no error were printed.
        print(f"Fileset {fileset_id} : All checks passed.")

    if not_inplace:
        print('\n'.join(not_inplace))
    if wrong_permission:
        print('\n'.join(wrong_permission))
    assert len(not_inplace) == 0, f"Some images were not 'in-place imported'. See details in script output."
    assert len(wrong_permission) == 0, f"Permission denied. See details in script output."

    if params[PARAM_DRY_RUN]:
        return f"Dry run complete, {len(jobs)} symlinks can be updated."

    print(f"Preparation complete, starting the update of {len(jobs)} symlinks.")
    ups = conn.getUpdateService()
    for i, (fse, new_client_path, ofe_path, ofe_name) in enumerate(jobs):
        symlink(new_client_path, join(MANAGED_DIR, ofe_path, ofe_name))
        fse.setClientPath(rstring(new_client_path[1:]))
        ups.saveObject(fse)

    return "Moving complete"


def run_script():
    """
    The main entry point of the script, as called by the client via the
    scripting service, passing the required parameters.
    """
    data_types = [rstring('Image'), rstring('Dataset'), rstring('Plate')]
    client = scripts.client(
        'In-place_path_edit.py',
        f"""
        Changes the symlink of in-place imported files and
        synchronizes the client path of each FilesetEntry of the
        corresponding images filesets.\n
        Contact your administrator {ADMINISTRATOR} for any related question.
        """,
        scripts.String(
            "Data_Type", optional=False, grouping="1",
            description="Providing image will work on the full associated Fileset. Use Dataset or Plate to bulk update Filesets of contained images.", values=data_types,
            default="Image"),
        scripts.List(
            "IDs", optional=False, grouping="2",
            description="Image, Plate or Dataset ID.").ofType(rlong(0)),
        scripts.String(
            PARAM_SRC_REPLACE, optional=False, grouping="3",
            description="Segment of the old path that will be replaced. The segment must have a single match to avoid ambiguous replacement."),
        scripts.String(
            PARAM_DST_REPLACE, optional=False, grouping="4",
            description="This will replace the matched segment. Resulting filepath will be checked to ensure it points to an existing file."),
        scripts.Bool(
            PARAM_CHECKSUM, grouping="5", default=False,
            description="New files sizes are matched. This adds an extra validation on the file checksum."),
        scripts.Bool(
            PARAM_DRY_RUN, grouping="6", default=True,
            description="Dry run shows the output of what would be " +
            "performed without modifying."),

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

        print("\n################## Script parameters ##################\n")
        print(f"Target container: {params['Data_Type']}:{params['IDs'][0]}")
        print(f"{PARAM_SRC_REPLACE}: {params[PARAM_SRC_REPLACE]}")
        print(f"{PARAM_DST_REPLACE}: {params[PARAM_DST_REPLACE]}")
        print(f"{PARAM_DRY_RUN}: {params[PARAM_DRY_RUN]}")
        print(f"{PARAM_CHECKSUM}: {params[PARAM_CHECKSUM]}")
        print("\n#######################################################\n\n")

        message = inplace_mv(
            conn, params)

        client.setOutput("Message", rstring(message))

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
